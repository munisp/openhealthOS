"""docref.py — de-identification for FHIR R4 DocumentReference resources.

A DocumentReference is how scanned letters, discharge notes, and referral
attachments enter the platform. Before any such document crosses into the
analytical plane (lakehouse silver, research extracts, agent context), its
free text must be de-identified — the same doctrine the lakehouse silver
gate enforces for event free_text (ADR-0065), applied at the document edge.

What this module scrubs (per content element, plus resource level):
  - content[].attachment.data   (base64 text: decoded, scrubbed, re-encoded)
  - content[].attachment.title
  - description

What it deliberately does NOT touch, and why:
  - subject / author / custodian references — linkage is the point of a
    document; patient-level privacy belongs to the row-level gate that
    releases the containing extract, not to field-scrubbing here.
  - dates (indexed, context.period) — downstream gold release passes the
    k-anonymity/cell-size gates; stripping dates here would destroy the
    temporality analytics exists for. Documented, not silent.

Fail-closed doctrine:
  - resourceType != DocumentReference  -> DocRefError (wrong lane).
  - attachment contentType that is not text/* or application/json
    (PDF, JPEG, DICOM...) CANNOT be de-identified by a text scrubber.
    Default: refuse the whole resource — a document is released whole or
    not at all. With drop_binary=True the binary parts are dropped and the
    report names every dropped part; nothing is ever passed through raw.
  - base64 that does not decode, or decodes to non-UTF-8 -> refuse.
"""
import base64
import binascii
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mednlp"))
from deid import deidentify_note  # noqa: E402

TEXTUAL_PREFIXES = ("text/",)
TEXTUAL_TYPES = {"application/json", "application/xml", "application/fhir+json"}


class DocRefError(ValueError):
    pass


def _is_textual(content_type: str) -> bool:
    ct = (content_type or "").split(";")[0].strip().lower()
    return ct.startswith(TEXTUAL_PREFIXES) or ct in TEXTUAL_TYPES


def _scrub_text(text, method, salt, min_score):
    return deidentify_note(text, method=method, salt=salt,
                           min_score=min_score)


def deid_document_reference(resource: dict, method: str = "mask",
                            salt: bytes = b"", min_score: float = 0.5,
                            drop_binary: bool = False) -> dict:
    """De-identify a DocumentReference. Returns
    {resource, report} — the input is not mutated.

    report = {fields_scrubbed: [...], spans_redacted: n,
              dropped_binary: [contentType...], gate: "refuse-or-drop"}
    """
    if not isinstance(resource, dict) or \
            resource.get("resourceType") != "DocumentReference":
        raise DocRefError("resourceType must be DocumentReference — "
                          "this scrubber has one lane")

    import copy
    out = copy.deepcopy(resource)
    report = {"fields_scrubbed": [], "spans_redacted": 0,
              "dropped_binary": [], "gate": "refuse-or-drop"}

    def scrub_field(holder, key, label):
        val = holder.get(key)
        if isinstance(val, str) and val.strip():
            r = _scrub_text(val, method, salt, min_score)
            holder[key] = r["text"]
            report["fields_scrubbed"].append(label)
            report["spans_redacted"] += r["spans_redacted"]

    scrub_field(out, "description", "description")

    kept_content = []
    for i, content in enumerate(out.get("content", [])):
        att = content.get("attachment", {})
        ct = att.get("contentType", "")
        if att.get("data") is not None:
            if not _is_textual(ct):
                if not drop_binary:
                    raise DocRefError(
                        f"content[{i}] contentType {ct or '?'} is not text — "
                        "a text scrubber cannot de-identify it; refusing to "
                        "release (or pass drop_binary=True to drop it)")
                report["dropped_binary"].append(ct or "?")
                continue
            try:
                raw = base64.b64decode(att["data"], validate=True)
                text = raw.decode("utf-8")
            except (binascii.Error, UnicodeDecodeError) as exc:
                raise DocRefError(
                    f"content[{i}] attachment data is not valid base64 UTF-8 "
                    "text — refusing to guess at its content") from exc
            r = _scrub_text(text, method, salt, min_score)
            att["data"] = base64.b64encode(r["text"].encode()).decode()
            report["fields_scrubbed"].append(f"content[{i}].attachment.data")
            report["spans_redacted"] += r["spans_redacted"]
        if "title" in att:
            before = report["spans_redacted"]
            scrub_field(att, "title", f"content[{i}].attachment.title")
            _ = before  # counted inside scrub_field
        kept_content.append(content)

    if len(kept_content) != len(out.get("content", [])):
        if kept_content:
            out["content"] = kept_content
        else:
            raise DocRefError(
                "every content element was binary and dropped — nothing "
                "releasable remains; refusing to emit an empty document")
    return {"resource": out, "report": report}
