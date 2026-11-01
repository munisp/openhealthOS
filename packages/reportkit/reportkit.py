"""reportkit.py — structured reporting: templates as data, signatures
as hashes.

The gap this closes: specialists report against templates (BI-RADS,
CAP synoptics, KDIGO referrals, WHO semen analysis), not blank text
areas — and a verified report must leave the system as a signed,
tamper-evident document.

Doctrine:
- TEMPLATES ARE DATA, VERSIONED: every report binds to
  template@version. A report is only interpretable against the
  template version that shaped it (same rule as domain packs).
- FAIL-CLOSED VALIDATION: unknown template, missing required field,
  or a value outside a coded answer set = refused, never defaulted.
- THE HASH IS THE CONTRACT: signing = canonical-JSON content hash +
  HMAC with the deployment's signing key. Any post-signature edit
  invalidates verification; corrections are new signed versions
  (addenda), matching the reading workflow's citing-addenda rule.
- TERMINOLOGY IS BOUND AT DEPLOYMENT: templates ship with display
  text; SNOMED/LOINC binding happens through terminology-service so
  no code is asserted that this package cannot verify.
- ABSENCE IS NOT A VALUE: an unanswered optional field is omitted,
  never emitted as a guess.
"""

import hashlib
import hmac
import html
import json


class ReportKitError(Exception):
    pass


def _code(key, options, required=True, label=None):
    return {"key": key, "type": "code", "options": options,
            "required": required, "label": label or key.replace("_", " ")}


def _text(key, required=True, label=None):
    return {"key": key, "type": "text", "required": required,
            "label": label or key.replace("_", " ")}


def _num(key, required=True, label=None, minimum=None, maximum=None,
         unit=None):
    return {"key": key, "type": "number", "required": required,
            "label": label or key.replace("_", " "),
            "min": minimum, "max": maximum, "unit": unit}


TEMPLATES = {
    "birads-mammo@1.0.0": {
        "title": "BI-RADS mammography assessment",
        "domain": "radiology",
        "sections": [
            _text("comparison", required=False,
                  label="comparison with priors"),
            _code("breast_density", {"a": "almost entirely fatty",
                                     "b": "scattered fibroglandular",
                                     "c": "heterogeneously dense",
                                     "d": "extremely dense"}),
            _text("findings"),
            _code("birads_category", {
                "0": "incomplete — need additional imaging",
                "1": "negative",
                "2": "benign",
                "3": "probably benign",
                "4": "suspicious",
                "5": "highly suggestive of malignancy",
                "6": "known biopsy-proven malignancy"}),
            _text("recommendation"),
        ],
    },
    "ct-head-acute@1.0.0": {
        "title": "CT head — acute",
        "domain": "radiology",
        "sections": [
            _text("technique"),
            _text("comparison", required=False),
            _code("hemorrhage", {"no": "no hemorrhage",
                                 "yes": "hemorrhage present"}),
            _code("midline_shift", {"no": "none",
                                    "yes": "present — measure mm"}),
            _code("mass_effect", {"no": "none", "yes": "present"}),
            _code("acute_infarct", {"no": "none", "yes": "present"}),
            _text("impression"),
        ],
    },
    "ecg-12lead@1.0.0": {
        "title": "12-lead ECG interpretation",
        "domain": "ecg",
        "sections": [
            _num("rate_bpm", minimum=20, maximum=300, unit="bpm"),
            _text("rhythm"),
            _num("pr_ms", minimum=80, maximum=600, unit="ms"),
            _num("qrs_ms", minimum=40, maximum=300, unit="ms"),
            _num("qtc_ms", minimum=250, maximum=700, unit="ms"),
            _text("axis", required=False),
            _text("findings"),
            _text("interpretation"),
            _code("acuity", {"routine": "routine",
                             "urgent": "urgent review",
                             "stemi": "STEMI — activate pathway"}),
        ],
    },
    "kdigo-referral@1.0.0": {
        "title": "KDIGO nephrology referral",
        "domain": "nephrology",
        "sections": [
            _num("egfr", minimum=1, maximum=150, unit="mL/min/1.73m2"),
            _num("acr_mg_g", required=False, minimum=0, maximum=20000,
                 unit="mg/g"),
            _text("ckd_band"),
            _code("diabetes", {"yes": "yes", "no": "no"}),
            _text("blood_pressure"),
            _text("current_medications"),
            _code("urgency", {"routine": "routine",
                              "soon": "within weeks",
                              "urgent": "urgent — declining function"}),
            _text("reason_for_referral"),
        ],
    },
    "cap-colon-resection@1.0.0": {
        "title": "Colon resection for carcinoma (synoptic)",
        "domain": "pathology",
        "sections": [
            _text("specimen_type"),
            _text("tumor_site"),
            _text("histologic_type"),
            _code("grade", {"g1": "well differentiated",
                            "g2": "moderately differentiated",
                            "g3": "poorly differentiated",
                            "gx": "cannot be assessed"}),
            _text("tumor_extent_pt"),
            _code("margins", {"negative": "all margins negative",
                              "positive": "margin involved — specify"}),
            _num("lymph_nodes_examined", minimum=0, maximum=500),
            _num("lymph_nodes_positive", minimum=0, maximum=500),
            _code("lymphovascular_invasion",
                  {"present": "present", "absent": "absent"}),
            _code("perineural_invasion",
                  {"present": "present", "absent": "absent"}),
            _text("tnm_stage"),
        ],
    },
    "who-semen@2021": {
        "title": "Semen analysis (WHO 6th edition reference limits)",
        "domain": "fertility",
        "sections": [
            _num("volume_ml", minimum=0, maximum=20, unit="mL"),
            _num("concentration_m_per_ml", minimum=0, maximum=1000,
                 unit="10^6/mL"),
            _num("total_motility_pct", minimum=0, maximum=100,
                 unit="%"),
            _num("progressive_motility_pct", minimum=0, maximum=100,
                 unit="%"),
            _num("vitality_pct", required=False, minimum=0,
                 maximum=100, unit="%"),
            _num("normal_forms_pct", minimum=0, maximum=100, unit="%"),
            _text("interpretation_vs_reference_limits"),
        ],
    },
}


class ReportKit:
    """Validates structured drafts against template versions, signs
    them, exports them. State: reports dict (persisted by the CLI)."""

    def __init__(self, *, signing_key=None, reports=None):
        # signing_key: bytes; without it, signing refuses loudly
        self.key = signing_key
        self.reports = reports if reports is not None else {}

    # ---- templates ----
    def templates(self):
        return [{"id": tid, "title": t["title"], "domain": t["domain"],
                 "sections": len(t["sections"])}
                for tid, t in sorted(TEMPLATES.items())]

    def template(self, template_id):
        t = TEMPLATES.get(template_id)
        if not t:
            raise ReportKitError(f"unknown template: {template_id} — "
                                 "reports bind to a versioned template, "
                                 "never to free text")
        return {"id": template_id, **t}

    # ---- validation ----
    def validate(self, template_id, answers):
        t = self.template(template_id)
        if not isinstance(answers, dict):
            raise ReportKitError("answers must be an object")
        known = {s["key"] for s in t["sections"]}
        unknown = set(answers) - known
        if unknown:
            raise ReportKitError(
                f"fields not in template {template_id}: "
                f"{sorted(unknown)} — a report carries only what its "
                "template asks")
        errors = []
        for s in t["sections"]:
            k = s["key"]
            if k not in answers or answers[k] is None:
                if s["required"]:
                    errors.append(f"missing required: {k}")
                continue
            v = answers[k]
            if s["type"] == "code" and v not in s["options"]:
                errors.append(f"{k}: '{v}' not in coded set "
                              f"{sorted(s['options'])}")
            elif s["type"] == "number":
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    errors.append(f"{k}: must be a number")
                    continue
                if s["min"] is not None and v < s["min"]:
                    errors.append(f"{k}: {v} below plausible minimum "
                                  f"{s['min']}{s['unit'] or ''}")
                if s["max"] is not None and v > s["max"]:
                    errors.append(f"{k}: {v} above plausible maximum "
                                  f"{s['max']}{s['unit'] or ''}")
            elif s["type"] == "text" and not str(v).strip():
                errors.append(f"{k}: empty text is not an answer")
        if errors:
            raise ReportKitError("; ".join(errors))
        return {k: v for k, v in answers.items() if k in known}

    # ---- draft / sign ----
    def _canonical(self, report):
        return json.dumps(report, sort_keys=True,
                          separators=(",", ":")).encode()

    def content_hash(self, report):
        return hashlib.sha256(self._canonical(report)).hexdigest()

    def draft(self, report_id, *, template_id, answers, case_id,
              author_id, day):
        if report_id in self.reports:
            raise ReportKitError(f"duplicate report id: {report_id}")
        clean = self.validate(template_id, answers)
        if not case_id or not author_id:
            raise ReportKitError("a report needs a case and an author")
        rec = {"id": report_id, "template_id": template_id,
               "case_id": case_id, "author_id": author_id, "day": day,
               "answers": clean, "status": "draft",
               "versions": []}
        self.reports[report_id] = rec
        return dict(rec)

    def sign(self, report_id, *, signer_id, day):
        r = self._report(report_id)
        if not self.key:
            raise ReportKitError("no signing key configured — a report "
                                 "cannot be signed into a vacuum; "
                                 "configure the deployment key")
        if r["status"] == "signed":
            raise ReportKitError("already signed — corrections are "
                                 "addenda (new signed versions), never "
                                 "edits")
        if signer_id != r["author_id"]:
            raise ReportKitError("only the author signs their own "
                                 "report (co-sign flows are a separate, "
                                 "explicit artifact)")
        payload = {"id": r["id"], "template_id": r["template_id"],
                   "case_id": r["case_id"], "answers": r["answers"],
                   "signer_id": signer_id, "day": day}
        chash = self.content_hash(payload)
        sig = hmac.new(self.key, chash.encode(),
                       hashlib.sha256).hexdigest()
        r["status"] = "signed"
        r["signature"] = {"algorithm": "HMAC-SHA256",
                          "signer_id": signer_id, "day": day,
                          "content_hash": chash, "signature": sig}
        r["versions"].append({"day": day, "content_hash": chash})
        return dict(r)

    def verify(self, report_id):
        r = self._report(report_id)
        if r["status"] != "signed":
            raise ReportKitError("unsigned reports are not verifiable")
        sig = r["signature"]
        payload = {"id": r["id"], "template_id": r["template_id"],
                   "case_id": r["case_id"], "answers": r["answers"],
                   "signer_id": sig["signer_id"], "day": sig["day"]}
        chash = self.content_hash(payload)
        ok = (chash == sig["content_hash"] and self.key and
              hmac.compare_digest(
                  hmac.new(self.key, chash.encode(),
                           hashlib.sha256).hexdigest(),
                  sig["signature"]))
        return {"report_id": report_id, "valid": bool(ok),
                "content_hash": chash}

    # ---- export ----
    def export_fhir(self, report_id):
        """FHIR R4 DiagnosticReport shape. Validate at the edge with
        fhir-validate; terminology codes bound by terminology-service
        at deployment."""
        r = self._report(report_id)
        t = TEMPLATES[r["template_id"]]
        sig = r.get("signature", {})
        return {
            "resourceType": "DiagnosticReport",
            "id": r["id"],
            "status": "final" if r["status"] == "signed" else
                      "preliminary",
            "category": [{"text": t["domain"]}],
            "code": {"text": f"{t['title']} [{r['template_id']}]"},
            "subject": {"reference": f"Case/{r['case_id']}"},
            "performer": [{"reference": f"Practitioner/"
                                        f"{r['author_id']}"}],
            "conclusion": str(r["answers"].get(
                "impression") or r["answers"].get("interpretation") or ""),
            "extension": [{
                "url": "https://openhealth.ng/fhir/StructureDefinition/"
                       "report-content-hash",
                "valueString": sig.get("content_hash", "")}],
            "_structured": {"template_id": r["template_id"],
                            "answers": r["answers"]},
        }

    def export_html(self, report_id):
        """Printable signed document — the hash block is the tamper
        evidence; anyone can recompute it from the printed content."""
        r = self._report(report_id)
        t = TEMPLATES[r["template_id"]]
        rows = []
        for s in t["sections"]:
            k = s["key"]
            if k not in r["answers"]:
                continue  # absence is not a value
            v = r["answers"][k]
            if s["type"] == "code":
                v = s["options"][v]
            rows.append(f"<tr><th>{html.escape(s['label'])}</th>"
                        f"<td>{html.escape(str(v))}</td></tr>")
        sig = r.get("signature")
        sig_html = ("<p class='sig'>UNSIGNED DRAFT — not a medical "
                    "record</p>")
        if sig:
            sig_html = (f"<p class='sig'>Signed by "
                        f"{html.escape(sig['signer_id'])} (day "
                        f"{sig['day']}) — content hash "
                        f"<code>{sig['content_hash'][:16]}…</code>. "
                        "Recompute from content to verify; a changed "
                        "letter breaks the hash.</p>")
        return ("<!doctype html><meta charset='utf-8'>"
                f"<title>{html.escape(t['title'])}</title>"
                f"<h1>{html.escape(t['title'])}</h1>"
                f"<p>Report {html.escape(r['id'])} · case "
                f"{html.escape(r['case_id'])} · template "
                f"{html.escape(r['template_id'])}</p>"
                f"<table>{''.join(rows)}</table>" + sig_html)

    def _report(self, rid):
        if rid not in self.reports:
            raise ReportKitError(f"unknown report: {rid}")
        return self.reports[rid]
