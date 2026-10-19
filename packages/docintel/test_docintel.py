import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import docintel as di


class FakeBackend(di.Backend):
    def __init__(self, name, blocks=None, ok=True, avail=True, version="0.0"):
        self.name = name
        self._blocks = blocks or []
        self._ok = ok
        self._avail = avail
        self._version = version
        self.calls = 0

    def available(self):
        return self._avail

    def extract(self, path, doc_class):
        self.calls += 1
        if not self._ok:
            raise di.Error("{} boom".format(self.name))
        for b in self._blocks:
            b.engine = self.name
        return list(self._blocks), self._version


def good_block(text="Haemoglobin 12.1 g/dL", conf=0.95):
    return di.Block(text, confidence=conf)


class TestClassifyAndChain(unittest.TestCase):
    def test_classify_lab_report(self):
        self.assertEqual(
            di.classify("lab.pdf", "Laboratory result: Hb 12, WBC 5.4, reference range"),
            "lab-report")

    def test_classify_id_card(self):
        self.assertEqual(di.classify("nin-slip.png", "National Identity Number NIN"), "id-card")

    def test_chain_prefers_docling_for_structured(self):
        self.assertEqual(di.chain_for("lab-report")[0], "docling")
        self.assertEqual(di.chain_for("scan")[0], "paddleocr")


class TestFallbackChain(unittest.TestCase):
    def test_falls_through_unavailable_and_failing(self):
        backends = {
            "docling": FakeBackend("docling", avail=False),
            "paddleocr": FakeBackend("paddleocr", ok=False),
            "tesseract": FakeBackend("tesseract", blocks=[good_block()], version="5.3"),
        }
        doc = di.process("lab.pdf", first_text="laboratory result hb wbc reference range",
                         backends=backends)
        self.assertEqual(doc.engine_used, "tesseract")
        self.assertEqual(doc.blocks[0].engine_version, "5.3")
        self.assertEqual(doc.fallbacks[0], "docling:unavailable")
        self.assertTrue(doc.fallbacks[1].startswith("paddleocr:"))

    def test_no_backend_raises(self):
        backends = {"docling": FakeBackend("docling", avail=False),
                    "paddleocr": FakeBackend("paddleocr", avail=False),
                    "tesseract": FakeBackend("tesseract", avail=False)}
        with self.assertRaises(di.Error):
            di.process("x.png", backends=backends)

    def test_quality_gate_escalates_to_vlm(self):
        weak = [di.Block("gibberish", confidence=0.2, page=1),
                di.Block("noise", confidence=0.3, page=1),
                di.Block("ok line", confidence=0.9, page=1)]
        backends = {
            "paddleocr": FakeBackend("paddleocr", blocks=weak),
            "tesseract": FakeBackend("tesseract", blocks=weak),
            "docling": FakeBackend("docling", blocks=weak),
            "vlm": FakeBackend("vlm", blocks=[di.Block("Hb 12.1 g/dL", confidence=0.93, page=1)],
                               version="qwen2-vl-2b"),
        }
        doc = di.process("scan.png", backends=backends, chain=["paddleocr", "tesseract"])
        upgraded = [b for b in doc.blocks if b.engine == "vlm"]
        self.assertTrue(upgraded, "weak blocks should be re-read by VLM")
        self.assertIn("vlm:escalated", doc.fallbacks)
        self.assertEqual(upgraded[0].engine_version, "qwen2-vl-2b")

    def test_provenance_and_review_queue(self):
        blocks = [good_block(), di.Block("faint text", confidence=0.4)]
        backends = {"docling": FakeBackend("docling", blocks=blocks, version="2.4"),
                    "paddleocr": FakeBackend("paddleocr", avail=False),
                    "tesseract": FakeBackend("tesseract", avail=False)}
        doc = di.process("lab.pdf", first_text="laboratory result hb wbc",
                         backends=backends, chain=["docling"])
        out = di.handoff(doc)
        self.assertEqual(len(out["review_queue"]), 1)
        self.assertIn("Haemoglobin", out["docclean_text"])
        self.assertEqual(out["semsearch_documents"][0]["meta"]["engine"], "docling")


class TestTesseractTSV(unittest.TestCase):
    def test_tsv_aggregation(self):
        tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
               "left\ttop\twidth\theight\tconf\ttext\n"
               "5\t1\t1\t1\t1\t1\t10\t10\t50\t12\t95.5\tHaemoglobin\n"
               "5\t1\t1\t1\t1\t2\t65\t10\t30\t12\t88.0\t12.1\n"
               "5\t1\t1\t1\t2\t1\t10\t30\t20\t12\t-1\t\n")
        blocks = di.TesseractBackend._tsv_to_blocks(tsv)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].text, "Haemoglobin 12.1")
        self.assertAlmostEqual(blocks[0].confidence, (95.5 + 88.0) / 200, places=3)
        self.assertEqual(blocks[0].bbox, [10, 10, 95, 22])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("tesseract"),
                     "needs ffmpeg+tesseract binaries")
class TestRealOCR(unittest.TestCase):
    def test_fixture_roundtrip(self):
        tmp = Path(tempfile.mkdtemp())
        img = tmp / "fixture.png"
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=white:640x120",
            "-vf", "drawtext=text='Haemoglobin 12.1 g/dL':fontsize=28:"
                   "fontcolor=black:x=10:y=40",
            "-frames:v", "1", str(img)], check=True,
            capture_output=True)
        doc = di.process(str(img), backends=di.default_backends(),
                         chain=["tesseract"])
        self.assertIn("Haemoglobin", doc.text)


if __name__ == "__main__":
    unittest.main()
