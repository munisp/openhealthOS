"""ml/docai — the document-AI sidecar (ADR-0028).

A stdlib http.server wrapper around the heavy engines that live inside
the ml/ fence:

  * PaddleOCR (Apache-2.0)   — POST /ocr/paddle
  * Docling (MIT)            — POST /parse/docling
  * VLM (transformers/torch) — POST /vqa

All imports are LAZY: the server starts and answers /healthz even when
none of the engines is installed, reporting which engines loaded. The
docintel orchestrator treats a missing engine as "unavailable" and falls
through its chain, so this sidecar can be scaled or upgraded without
touching the platform core.

Doctrine: engine outputs are advisory extraction aids with per-block
confidence. The VLM never returns a diagnosis; it answers bounded visual
questions and re-reads illegible regions. Low-confidence output routes
to human review (docintel quality gate).
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN = int(os.environ.get("DOCAI_PORT", "8100"))

_ENGINES = {}
_ERRORS = {}


def _load_engines():
    """Lazy, best-effort engine loading; records load errors for healthz."""
    try:
        from paddleocr import PaddleOCR  # noqa: F401
        _ENGINES["paddleocr"] = {"version": _mod_version("paddleocr")}
    except Exception as e:                                   # noqa: BLE001
        _ERRORS["paddleocr"] = str(e)[:200]
    try:
        import docling  # noqa: F401
        _ENGINES["docling"] = {"version": _mod_version("docling")}
    except Exception as e:                                   # noqa: BLE001
        _ERRORS["docling"] = str(e)[:200]
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        _ENGINES["vlm"] = {"version": _mod_version("transformers")}
    except Exception as e:                                   # noqa: BLE001
        _ERRORS["vlm"] = str(e)[:200]


def _mod_version(mod):
    try:
        import importlib.metadata as md
        return md.version(mod)
    except Exception:                                        # noqa: BLE001
        return "unknown"


def _paddle(path, doc_class):
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    blocks = []
    for page_i, page in enumerate(ocr.ocr(path, cls=True) or [], start=1):
        for line in page or []:
            bbox_pts, (text, conf) = line
            xs = [p[0] for p in bbox_pts]
            ys = [p[1] for p in bbox_pts]
            blocks.append({
                "text": text, "page": page_i,
                "bbox": [int(min(xs)), int(min(ys)),
                         int(max(xs)), int(max(ys))],
                "confidence": float(conf), "kind": "text"})
    return blocks


def _docling(path, doc_class):
    from docling.document_converter import DocumentConverter
    conv = DocumentConverter().convert(path)
    doc = conv.document
    blocks = []
    for item in doc.iterate_items():
        node = item[0] if isinstance(item, tuple) else item
        text = getattr(node, "text", "") or ""
        if not text.strip():
            continue
        kind = "table" if node.__class__.__name__.lower().startswith("table") else "text"
        prov = getattr(node, "prov", [])
        page = prov[0].page_no if prov else 1
        blocks.append({"text": text.strip(), "page": page,
                       "confidence": 0.9, "kind": kind})
    return blocks


def _vlm(path, doc_class, question=None):
    """Bounded visual QA / region re-read. Model id via DOCAI_VLM_MODEL;
    default is a small CPU-tolerable VLM. Never prompted for diagnosis —
    the caller (docintel) only escalates illegible regions."""
    import torch
    from transformers import AutoModelForVision2Seq, AutoProcessor
    model_id = os.environ.get("DOCAI_VLM_MODEL", "HuggingFaceTB/SmolVLM-256M-Instruct")
    processor = AutoProcessor.from_pretrained(model_id)
    model = AutoModelForVision2Seq.from_pretrained(
        model_id, torch_dtype=torch.float32)
    from PIL import Image
    image = Image.open(path).convert("RGB")
    q = question or "Transcribe every legible word in this document image."
    inputs = processor(images=image, text=q, return_tensors="pt")
    out = model.generate(**inputs, max_new_tokens=512)
    text = processor.batch_decode(out, skip_special_tokens=True)[0]
    return [{"text": text, "page": 1, "confidence": 0.8, "kind": "text"}]


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # quiet
        pass

    def do_GET(self):
        if self.path == "/healthz":
            self._json(200, {"ok": True, "engines": sorted(_ENGINES),
                             "versions": {k: v["version"] for k, v in _ENGINES.items()},
                             "errors": _ERRORS})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length) or b"{}")
            path = req["path"]
            doc_class = req.get("doc_class", "unknown")
        except Exception as e:                                 # noqa: BLE001
            return self._json(400, {"error": str(e)})

        routes = {"/ocr/paddle": ("paddleocr", _paddle),
                  "/parse/docling": ("docling", _docling),
                  "/vqa": ("vlm", _vlm)}
        route = routes.get(self.path)
        if route is None:
            return self._json(404, {"error": "not found"})
        engine, fn = route
        if engine not in _ENGINES:
            return self._json(503, {"error": "engine not loaded",
                                    "detail": _ERRORS.get(engine, "")})
        try:
            if engine == "vlm":
                blocks = fn(path, doc_class, req.get("question"))
            else:
                blocks = fn(path, doc_class)
        except Exception as e:                                 # noqa: BLE001
            return self._json(500, {"error": str(e)[:300]})
        self._json(200, {"blocks": blocks,
                         "version": _ENGINES[engine]["version"]})


def main():
    _load_engines()
    print("docai sidecar on :{} engines={} errors={}".format(
        LISTEN, sorted(_ENGINES), sorted(_ERRORS)))
    ThreadingHTTPServer(("0.0.0.0", LISTEN), Handler).serve_forever()


if __name__ == "__main__":
    main()
