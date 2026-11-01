"""viewlink.py — viewer launch seam: the worklist hands the reader pixels.

The gap this closes: a reading worklist without a viewer is a paper
exercise. Every visual specialty (radiology, pathology, derm, DR,
obstetric US, ECG) needs the worklist to open the artifact — DICOM in
OHIF, whole-slide images in OpenSeadragon, ECG tracings on canvas.

Doctrine:
- HONEST SPIKE: no configured viewer base URL, no launch. The reader
  gets a named refusal, never a broken iframe that looks like an
  empty study.
- VIEWER IS COMPOSED, NOT BUILT: OHIF (Apache-2.0) for DICOM,
  OpenSeadragon (BSD-3) for slides. We build launch URLs and audit,
  not a viewer.
- OPENING A VIEWER IS AN ACCESS EVENT: sealed-record doctrine — every
  launch is logged (who, what study/slide, when, from which reading
  session). A read that left no trace didn't happen.
- TOKENS NEVER IN URLS: OIDC tokens are not appended to launch URLs;
  the viewer deployment holds its own session. A URL with a token is
  a URL in a log file with a token.
"""

import hashlib
import json

VIEWER_KINDS = ("dicom-ohif", "wsi-openseadragon", "ecg-canvas")


class ViewLinkError(Exception):
    pass


class ViewerLauncher:
    """Builds launch URLs against a configured viewer deployment and
    logs every launch as an access event."""

    def __init__(self, *, ohif_base=None, slide_base=None, ecg_base=None,
                 audit=None):
        # ohif_base: e.g. https://viewer.hospital.example/viewer
        #            (OHIF deployed against the dcm4chee DICOMweb source)
        # slide_base: e.g. https://slides.hospital.example/openseadragon
        # ecg_base:   in-house waveform canvas page
        self.bases = {"dicom-ohif": ohif_base, "wsi-openseadragon":
                      slide_base, "ecg-canvas": ecg_base}
        self.audit = audit if audit is not None else []

    def configured(self, kind):
        if kind not in VIEWER_KINDS:
            raise ViewLinkError(f"unknown viewer kind: {kind}")
        return self.bases[kind] is not None

    def launch(self, kind, *, artifact_ref, reader_id, session_id,
               case_id, day):
        """artifact_ref: StudyInstanceUID (dicom), slide URL/identifier
        (wsi), or tracing id (ecg). Returns the launch descriptor or
        refuses loudly."""
        if kind not in VIEWER_KINDS:
            raise ViewLinkError(f"unknown viewer kind: {kind}")
        if not artifact_ref:
            raise ViewLinkError("artifact reference required — a viewer "
                                "launch without an artifact is a broken "
                                "window, not an image")
        base = self.bases[kind]
        if not base:
            raise ViewLinkError(
                f"viewer '{kind}' is not configured for this deployment — "
                "refusing loudly rather than opening an empty frame")
        if not reader_id or not session_id:
            raise ViewLinkError("viewer launches require an identified "
                                "reader in an identified reading session")
        base = base.rstrip("/")
        if kind == "dicom-ohif":
            url = f"{base}?StudyInstanceUIDs={artifact_ref}"
        elif kind == "wsi-openseadragon":
            url = f"{base}?slide={artifact_ref}"
        else:
            url = f"{base}?tracing={artifact_ref}"
        event = {"kind": kind, "artifact_ref": artifact_ref,
                 "reader_id": reader_id, "session_id": session_id,
                 "case_id": case_id, "day": day,
                 "event_hash": hashlib.sha256(json.dumps(
                     [kind, artifact_ref, reader_id, session_id, day],
                     sort_keys=True).encode()).hexdigest()}
        self.audit.append(event)
        return {"url": url, "kind": kind, "access_event": event,
                "note": "viewer session is authenticated by the viewer "
                        "deployment itself; no tokens in URLs"}

    def access_log(self, *, case_id=None, reader_id=None):
        out = self.audit
        if case_id is not None:
            out = [e for e in out if e["case_id"] == case_id]
        if reader_id is not None:
            out = [e for e in out if e["reader_id"] == reader_id]
        return [dict(e) for e in out]
