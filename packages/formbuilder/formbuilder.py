"""formbuilder.py — dynamic forms as ratified registry entries.

OpenMRS has HTML Form Entry; OpenEMR has layout-based forms. The pattern
that works in both: the form is data, reviewed and ratified like an
order set, and every submission validates against the ratified schema.
A form nobody ratified is a draft; a submission that fails validation
is refused, not approximated.

Doctrine:
- FORMS ARE SCHEMAS: fields with types, labels, and constraints.
  Field types: text, number, date, choice, boolean, vitals-ref.
- RATIFICATION IS FOUR-EYES: propose → ratify (≠ proposer) → live.
  Only live forms accept submissions.
- SUBMISSIONS VALIDATE AGAINST THE RATIFIED VERSION: a submission
  against a draft or retired form refuses.
- EVERY SUBMISSION CARRIES THE SCHEMA FINGERPRINT: the data is
  interpretable only against the version that defined it.
"""

import hashlib
import json

VERSION = "formbuilder@1.0.0"

FIELD_TYPES = ("text", "number", "date", "choice", "boolean",
               "vitals-ref")

FORM_STATES = ("draft", "proposed", "live", "retired")


class Error(Exception):
    pass


class FormSchema:
    """A form definition. Immutable once live."""

    def __init__(self, form_id, title, *, fields, version_str="1.0.0"):
        if not (title and title.strip()):
            raise Error("a form names itself")
        if not fields:
            raise Error("a form has fields")
        self.form_id = form_id
        self.title = title
        self.version_str = version_str
        self.fields = []
        seen = set()
        for f in fields:
            self._validate_field(f)
            if f["name"] in seen:
                raise Error(f"duplicate field: {f['name']}")
            seen.add(f["name"])
            self.fields.append(dict(f))
        self.state = "draft"
        self.proposed_by = None
        self.ratified_by = None

    def _validate_field(self, f):
        for k in ("name", "type", "label"):
            if k not in f:
                raise Error(f"field missing {k}")
        if f["type"] not in FIELD_TYPES:
            raise Error(f"unknown field type: {f['type']}")
        if f["type"] == "choice" and not f.get("options"):
            raise Error(f"choice field {f['name']} has no options")

    def fingerprint(self):
        body = {"form_id": self.form_id, "title": self.title,
                "version_str": self.version_str, "fields": self.fields}
        return hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()

    def propose(self, *, by):
        if self.state != "draft":
            raise Error(f"cannot propose from state {self.state}")
        if not by:
            raise Error("the proposer names herself")
        self.state = "proposed"
        self.proposed_by = by

    def ratify(self, *, by):
        if self.state != "proposed":
            raise Error("only proposed forms ratify")
        if not by:
            raise Error("the ratifier names herself")
        if by == self.proposed_by:
            raise Error("the hand that proposes never approves")
        self.state = "live"
        self.ratified_by = by

    def retire(self, *, by, reason):
        if self.state != "live":
            raise Error("only live forms retire")
        if not (reason and reason.strip()):
            raise Error("retirement states its reason")
        self.state = "retired"
        self.retired_by = by

    def validate_submission(self, data):
        """Every field checked against the schema. Extra keys refuse."""
        if self.state != "live":
            raise Error(
                f"form is {self.state} — only live forms accept data")
        schema_names = {f["name"] for f in self.fields}
        data_names = set(data.keys())
        extra = data_names - schema_names
        if extra:
            raise Error(f"unknown fields in submission: {sorted(extra)}")
        errors = []
        for f in self.fields:
            name = f["name"]
            required = f.get("required", False)
            if name not in data:
                if required:
                    errors.append(f"{name}: required, absent")
                continue
            v = data[name]
            t = f["type"]
            if t == "number":
                if not isinstance(v, (int, float)):
                    errors.append(f"{name}: expected number, got "
                                  f"{type(v).__name__}")
                elif "min" in f and v < f["min"]:
                    errors.append(f"{name}: {v} below min {f['min']}")
                elif "max" in f and v > f["max"]:
                    errors.append(f"{name}: {v} above max {f['max']}")
            elif t == "choice":
                if v not in f["options"]:
                    errors.append(f"{name}: '{v}' not in options")
            elif t == "boolean":
                if not isinstance(v, bool):
                    errors.append(f"{name}: expected boolean")
            elif t == "date":
                if not isinstance(v, int) or v < 0:
                    errors.append(f"{name}: expected day-number (int)")
            elif t == "text":
                if not isinstance(v, str):
                    errors.append(f"{name}: expected text")
            elif t == "vitals-ref":
                if not isinstance(v, str):
                    errors.append(f"{name}: expected observation ref")
        return errors


class FormRegistry:
    def __init__(self, facility_id):
        self.facility_id = facility_id
        self.forms = {}
        self.submissions = []

    def register(self, schema):
        if schema.form_id in self.forms:
            raise Error(f"form {schema.form_id} already registered")
        self.forms[schema.form_id] = schema
        return schema

    def submit(self, form_id, patient_ref, data, *, by, day):
        schema = self.forms.get(form_id)
        if schema is None:
            raise Error(f"unknown form: {form_id}")
        errors = schema.validate_submission(data)
        if errors:
            raise Error(f"submission refused: {errors}")
        sub = {"form_id": form_id,
               "schema_sha256": schema.fingerprint(),
               "patient_ref": patient_ref, "data": data,
               "by": by, "day": day}
        sub["sha256"] = hashlib.sha256(
            json.dumps(sub, sort_keys=True).encode()).hexdigest()
        self.submissions.append(sub)
        return sub

    def submissions_for(self, patient_ref):
        return [s for s in self.submissions
                if s["patient_ref"] == patient_ref]
