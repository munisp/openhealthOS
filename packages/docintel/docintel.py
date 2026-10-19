"""Document intelligence orchestrator (ADR-0028).

Clean-room, stdlib-only routing layer for OCR + document parsing. Heavy
engines are composed, never embedded:

  * Tesseract (Apache-2.0) — subprocess default; always available in the
    imaging container. Same process-boundary doctrine as ffmpeg.
  * PaddleOCR (Apache-2.0) — HTTP sidecar (ml/docai). Strong on degraded
    scans, mixed Latin/indigenous-script handwriting context.
  * Docling (MIT) — HTTP sidecar (ml/docai). Layout-aware parsing:
    tables, reading order, section structure for lab reports / forms.
  * VLM (ml/ fence, torch exception) — escalation only: low-confidence
    blocks or visual questions ("which arm is fractured?") go to a
    vision-language model behind the same sidecar. Never autonomous
    diagnosis — outputs land in the review queue with provenance.

Every extracted block carries provenance: engine, engine_version,
confidence, page, bbox. Downstream: semsearch indexes the text;
docclean SimHash-dedupes near-identical scans; anything under the
confidence floor is flagged for human review.
"""

import hashlib
import json
import shutil
import subprocess
import urllib.request

CONFIDENCE_FLOOR = 0.60          # below this a block goes to review
DEFAULT_CHAIN = ["docling", "paddleocr", "tesseract"]


class Error(Exception):
    pass


# ------------------------------------------------------------- document IR

class Block:
    """One extracted text block with full provenance."""

    def __init__(self, text, page=1, bbox=None, confidence=1.0,
                 engine="unknown", engine_version="", kind="text"):
        self.text = text
        self.page = page
        self.bbox = bbox or [0, 0, 0, 0]
        self.confidence = confidence
        self.engine = engine
        self.engine_version = engine_version
        self.kind = kind            # text | table | heading | form-field

    def to_dict(self):
        return {
            "text": self.text, "page": self.page, "bbox": self.bbox,
            "confidence": round(self.confidence, 4),
            "engine": self.engine, "engine_version": self.engine_version,
            "kind": self.kind,
        }


class Document:
    def __init__(self, doc_id, doc_class="unknown"):
        self.doc_id = doc_id
        self.doc_class = doc_class  # lab-report | referral | id-card | form | scan | unknown
        self.blocks = []
        self.engine_used = ""
        self.fallbacks = []          # engines tried and abandoned, in order

    @property
    def text(self):
        return "\n".join(b.text for b in self.blocks)

    def needs_review(self):
        return [b for b in self.blocks if b.confidence < CONFIDENCE_FLOOR]

    def to_dict(self):
        return {
            "doc_id": self.doc_id, "doc_class": self.doc_class,
            "engine_used": self.engine_used, "fallbacks": self.fallbacks,
            "blocks": [b.to_dict() for b in self.blocks],
            "review_count": len(self.needs_review()),
        }


# ------------------------------------------------------------- classification

_CLASS_KEYWORDS = {
    "lab-report": ("laboratory", "specimen", "reference range", "result",
                   "hb", "wbc", "pcv"),
    "referral": ("referral", "referred", "referring facility",
                 "kindly see", "thanks for seeing"),
    "id-card": ("national identity", "nin", "date of birth", "surname",
                "nhia", "enrollee"),
    "form": ("signature", "tick where applicable", "fill in", "checkbox"),
}


def classify(filename, first_text=""):
    """Cheap heuristic routing. The classifier only picks the first
    backend; the quality gate decides whether to escalate."""
    hay = (filename + " " + first_text[:500]).lower()
    best, best_hits = "scan", 0
    for cls, kws in _CLASS_KEYWORDS.items():
        hits = sum(1 for k in kws if k in hay)
        if hits > best_hits:
            best, best_hits = cls, hits
    return best


def chain_for(doc_class):
    """Docling earns first place on structured documents (tables/forms);
    scans and IDs start with PaddleOCR (degraded-image strength)."""
    if doc_class in ("lab-report", "form", "referral"):
        return ["docling", "paddleocr", "tesseract"]
    if doc_class in ("id-card", "scan"):
        return ["paddleocr", "tesseract", "docling"]
    return list(DEFAULT_CHAIN)


# ------------------------------------------------------------- backends

class Backend:
    name = "base"

    def available(self):
        raise NotImplementedError

    def extract(self, path, doc_class):
        """Return (blocks, version) or raise Error."""
        raise NotImplementedError


class TesseractBackend(Backend):
    """Apache-2.0 via subprocess — always-on floor of the chain."""

    name = "tesseract"

    def __init__(self, binary="tesseract"):
        self.binary = binary

    def available(self):
        return shutil.which(self.binary) is not None

    def version(self):
        try:
            out = subprocess.run([self.binary, "--version"],
                                 capture_output=True, text=True, timeout=10)
            return (out.stdout.splitlines() or ["tesseract"])[0].strip()
        except Exception:
            return "tesseract"

    def extract(self, path, doc_class):
        try:
            out = subprocess.run(
                [self.binary, path, "stdout", "--psm", "6", "tsv"],
                capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            raise Error("tesseract binary not found")
        if out.returncode != 0:
            raise Error("tesseract failed: " + out.stderr.strip()[:200])
        return self._tsv_to_blocks(out.stdout), self.version()

    @staticmethod
    def _tsv_to_blocks(tsv):
        """Aggregate Tesseract TSV word rows into line-level blocks with
        mean confidence (Tesseract confidence is 0-100; we normalise)."""
        lines = {}
        for row in tsv.splitlines()[1:]:
            cols = row.split("\t")
            if len(cols) != 12:
                continue
            conf = float(cols[10])
            word = cols[11].strip()
            if not word or conf < 0:
                continue
            key = (int(cols[2]), int(cols[3]), int(cols[4]))  # block, par, line
            rec = lines.setdefault(key, {"words": [], "confs": [],
                                         "left": int(cols[6]), "top": int(cols[7]),
                                         "right": 0, "bottom": 0})
            rec["words"].append(word)
            rec["confs"].append(conf)
            rec["right"] = max(rec["right"], int(cols[6]) + int(cols[8]))
            rec["bottom"] = max(rec["bottom"], int(cols[7]) + int(cols[9]))
        blocks = []
        for (page, _par, _line), rec in sorted(lines.items()):
            blocks.append(Block(
                " ".join(rec["words"]), page=page,
                bbox=[rec["left"], rec["top"], rec["right"], rec["bottom"]],
                confidence=sum(rec["confs"]) / (100.0 * len(rec["confs"])),
                engine="tesseract"))
        return blocks


class HTTPBackend(Backend):
    """PaddleOCR / Docling / VLM sidecar client (ml/docai)."""

    def __init__(self, base_url, name, path, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.name = name
        self.path = path
        self.timeout = timeout

    def available(self):
        try:
            with urllib.request.urlopen(self.base_url + "/healthz",
                                        timeout=3) as r:
                health = json.loads(r.read().decode())
            return self.name in health.get("engines", [])
        except Exception:
            return False

    def extract(self, path, doc_class):
        payload = json.dumps({"path": path, "doc_class": doc_class}).encode()
        req = urllib.request.Request(
            self.base_url + self.path, data=payload,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
        except Exception as e:
            raise Error("{} sidecar: {}".format(self.name, e))
        blocks = [
            Block(b["text"], page=b.get("page", 1), bbox=b.get("bbox"),
                  confidence=b.get("confidence", 1.0), engine=self.name,
                  engine_version=data.get("version", ""),
                  kind=b.get("kind", "text"))
            for b in data.get("blocks", [])
        ]
        if not blocks:
            raise Error("{} returned no blocks".format(self.name))
        return blocks, data.get("version", "")


def default_backends(sidecar_url=None, tesseract_bin="tesseract"):
    return {
        "tesseract": TesseractBackend(tesseract_bin),
        "paddleocr": HTTPBackend(sidecar_url or "http://docai:8100",
                                 "paddleocr", "/ocr/paddle"),
        "docling": HTTPBackend(sidecar_url or "http://docai:8100",
                               "docling", "/parse/docling"),
        "vlm": HTTPBackend(sidecar_url or "http://docai:8100",
                           "vlm", "/vqa", timeout=120),
    }


# ------------------------------------------------------------- orchestration

def process(path, doc_id=None, first_text="", backends=None,
            chain=None):
    """Classify → run the chain with fallback → quality gate.

    Falls through to the next engine when one is unavailable or errors,
    and escalates to the VLM when the chosen engine leaves too many
    low-confidence blocks (VLM output is merged for those blocks only,
    marked engine="vlm" so provenance survives).
    """
    backends = backends or default_backends()
    doc_class = classify(path, first_text)
    doc = Document(doc_id or hashlib.sha256(
        (path + doc_class).encode()).hexdigest()[:16], doc_class)
    order = chain or chain_for(doc_class)

    chosen = None
    for name in order:
        backend = backends.get(name)
        if backend is None or not backend.available():
            doc.fallbacks.append(name + ":unavailable")
            continue
        try:
            blocks, version = backend.extract(path, doc_class)
        except Error as e:
            doc.fallbacks.append("{}:{}".format(name, e))
            continue
        for b in blocks:
            b.engine_version = b.engine_version or version
        doc.blocks = blocks
        doc.engine_used = name
        chosen = name
        break

    if chosen is None:
        raise Error("no OCR backend available (tried {})".format(order))

    # Quality gate: if more than a third of blocks are under the floor
    # and a VLM is reachable, re-read the weak blocks with the VLM.
    weak = doc.needs_review()
    vlm = backends.get("vlm")
    if weak and len(weak) > max(1, len(doc.blocks) // 3) \
            and vlm is not None and vlm.available():
        try:
            vblocks, vver = vlm.extract(path, doc_class)
            by_page = {}
            for vb in vblocks:
                by_page.setdefault(vb.page, []).append(vb)
            for b in weak:
                cands = by_page.get(b.page, [])
                if cands:
                    best = max(cands, key=lambda c: c.confidence)
                    if best.confidence > b.confidence:
                        b.text, b.confidence = best.text, best.confidence
                        b.engine, b.engine_version = "vlm", vver
            doc.fallbacks.append("vlm:escalated")
        except Error as e:
            doc.fallbacks.append("vlm:{}".format(e))

    return doc


def handoff(doc):
    """Payloads for downstream packages: semsearch indexes text spans,
    docclean dedupes on normalised text, review queue gets weak blocks."""
    return {
        "semsearch_documents": [
            {"id": doc.doc_id, "text": doc.text, "meta": {
                "class": doc.doc_class, "engine": doc.engine_used}}],
        "docclean_text": doc.text,
        "review_queue": [b.to_dict() for b in doc.needs_review()],
    }
