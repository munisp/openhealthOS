"""sidecar_server.py — OpenMed sidecar HTTP service (ADR-0065).

Serves de-identification and clinical entity extraction to the facility
edge over loopback. Stdlib-only HTTP layer; the NLP itself is OpenMed.

Honest-start doctrine: if the gated models cannot be loaded (manifest
pins unverified, cache empty, network unreachable), the sidecar exits 2
loudly instead of serving degraded output — the silver free-text gate
keeps protecting the pipeline with the deterministic pattern pack
meanwhile, so nothing silently passes raw narrative through.

Endpoints:
  GET  /healthz    -> {"ok": true, "models": [...]}
  POST /deid       {"text": ...} -> {"text": redacted, "spans": [...]}
  POST /entities   {"text": ...} -> {"entities": [...]}
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model_gate import vet, ModelGateError  # noqa: E402

PORT = int(os.environ.get("MEDNLP_PORT", "8765"))
MANIFEST = os.environ.get(
    "MEDNLP_MANIFEST",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "models-manifest.json"),
)


def load_pipeline():
    """Manifest-gated OpenMed load. Raises rather than degrading."""
    vet(MANIFEST)  # licence allowlist + 64-hex sha256 pins, or raise
    import openmed  # noqa: imported only after the gate passes
    return openmed


def main():
    try:
        openmed = load_pipeline()
    except (ModelGateError, ImportError, Exception) as e:  # noqa: BLE001
        sys.stderr.write(
            "mednlp sidecar: model pipeline unavailable (%s).\n"
            "Provision gated models (see infra/compose/mednlp.yaml) — "
            "refusing to serve degraded NLP. Silver gate remains on the "
            "deterministic pattern pack.\n" % e
        )
        sys.exit(2)

    deid_fn = openmed.deidentify
    extract_fn = openmed.extract_clinical_entities

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # quiet: PHI must not hit access logs
            pass

        def do_GET(self):
            if self.path == "/healthz":
                self._json(200, {"ok": True, "manifest": MANIFEST})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", "0"))
                req = json.loads(self.rfile.read(n) or b"{}")
                text = req.get("text", "")
                if not isinstance(text, str) or not text:
                    return self._json(400, {"error": "text required"})
                if self.path == "/deid":
                    out = deid_fn(text)
                    self._json(200, {"text": out.get("text", out)
                                     if isinstance(out, dict) else str(out)})
                elif self.path == "/entities":
                    ents = extract_fn(text)
                    self._json(200, {"entities": ents})
                else:
                    self._json(404, {"error": "not found"})
            except Exception as e:  # noqa: BLE001 — fail closed, no PHI echo
                self._json(500, {"error": type(e).__name__})

    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
