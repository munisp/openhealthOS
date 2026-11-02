"""terminology.py — binding reports to SNOMED/LOINC/ICD codes,
honestly.

Reports without codes block analytics, FHIR export, and cross-
facility comparability. But the honest constraint cuts the other
way: NO CODE IS ASSERTED THAT THIS DEPLOYMENT CANNOT VERIFY. So
binding is a versioned, deployment-loaded MAP from (template, field,
value) to a code — and the rules do the work:

  - binding is TOTAL for coded fields present in a report: a coded
    answer with no map entry REFUSES the coded export — text-only
    export would smuggle the semantics past the audit
  - maps are versioned data, loaded at deployment; a report's codes
    are only interpretable against the map version that bound them
  - LOINC and ICD-10 seed entries ship (both freely licensed);
    SNOMED CT SHIPS EMPTY — it is affiliate-licensed, so a deployment
    attests its licence and loads its own map. We will not ship
    someone else's licensed content
  - coverage is reported with denominators: 'bound rate' without n
    is marketing
"""

TERMINOLOGY_VERSION = "terminology@1.0.0"
SYSTEMS = ("LOINC", "ICD-10", "SNOMED-CT")
# which systems ship with seed content vs deployment-loaded only
SHIPS_SEEDED = ("LOINC", "ICD-10")
LICENSE_GATED = ("SNOMED-CT",)


class TerminologyError(Exception):
    pass


# --- seed map: only codes that are widely published and stable ---
# key: (template_id, field, value) -> (system, code, display)
SEED_MAP = {
    # LOINC — vital signs / observations (freely licensed)
    ("observation@1.0.0", "kind", "systolic-bp"):
        ("LOINC", "8480-6", "Systolic blood pressure"),
    ("observation@1.0.0", "kind", "diastolic-bp"):
        ("LOINC", "8462-4", "Diastolic blood pressure"),
    ("observation@1.0.0", "kind", "heart-rate"):
        ("LOINC", "8867-4", "Heart rate"),
    ("observation@1.0.0", "kind", "respiratory-rate"):
        ("LOINC", "9279-1", "Respiratory rate"),
    ("observation@1.0.0", "kind", "temperature"):
        ("LOINC", "8310-5", "Body temperature"),
    ("observation@1.0.0", "kind", "spo2"):
        ("LOINC", "59408-5",
         "Oxygen saturation in Arterial blood by Pulse oximetry"),
    ("observation@1.0.0", "kind", "gcs"):
        ("LOINC", "9269-2", "Glasgow coma score total"),
    # ICD-10 — WHO, freely licensed
    ("diagnosis@1.0.0", "code", "t2dm-uncomplicated"):
        ("ICD-10", "E11.9",
         "Type 2 diabetes mellitus without complications"),
    ("diagnosis@1.0.0", "code", "essential-hypertension"):
        ("ICD-10", "I10", "Essential (primary) hypertension"),
}


class TerminologyMap:
    """A versioned binding map. Construct from the seed plus any
    deployment entries; SNOMED entries require a licence attestation
    at load time."""

    def __init__(self, name, *, deployment, extra_entries=None,
                 snomed_license_attested=False):
        if not deployment:
            raise TerminologyError("a map belongs to a named "
                                   "deployment — codes without a "
                                   "deployment context are folklore")
        self.name = name
        self.deployment = deployment
        self.entries = dict(SEED_MAP)
        self.map_version = TERMINOLOGY_VERSION
        self.attestations = []
        if snomed_license_attested:
            self.attest("SNOMED-CT", by=deployment)
        for key, val in (extra_entries or {}).items():
            self._add(key, val)

    def attest(self, system, *, by):
        """Loading license-gated terminology requires the deployment
        to attest its licence — on the record, attributed."""
        if system not in LICENSE_GATED:
            raise TerminologyError(f"{system} is not licence-gated")
        self.attestations.append({"system": system, "by": by})
        return dict(self.attestations[-1])

    def _attested(self, system):
        return any(a["system"] == system for a in self.attestations)

    def _add(self, key, val):
        system, code, display = val
        if system not in SYSTEMS:
            raise TerminologyError(f"unknown terminology system: "
                                   f"{system}")
        if system in LICENSE_GATED and not self._attested(system):
            raise TerminologyError(
                f"{system} entries require a licence attestation "
                "first — we do not ship or silently load someone "
                "else's licensed content")
        if not code or not display:
            raise TerminologyError("a code needs its code and its "
                                   "display text")
        self.entries[key] = val

    # ---- binding ----
    def bind_field(self, template_id, field, value):
        """One coded answer → its coding, or a refusal. Unbound is
        not a code."""
        hit = self.entries.get((template_id, field, value))
        if not hit:
            raise TerminologyError(
                f"no binding for ({template_id}, {field}, {value}) — "
                "unbound is not a code; extend the deployment map "
                "rather than let the report guess")
        system, code, display = hit
        return {"system": system, "code": code, "display": display,
                "map_version": self.map_version}

    def bind_report(self, template, answers):
        """Walk a reportkit template: every coded field PRESENT in the
        answers must bind. Returns coded answers + explicit list of
        what could not bind (for coverage accounting). Strict: any
        unbound coded field present → refuse the coded export."""
        coded_fields = {s["key"]: s for s in template["sections"]
                        if s.get("type") == "code"}
        codings, unbound = [], []
        for key, spec in coded_fields.items():
            if key not in answers:
                continue          # absent optional field: omitted,
                                  # never guessed (reportkit doctrine)
            value = answers[key]
            if value not in spec["options"]:
                raise TerminologyError(
                    f"answer {value!r} for {key} is outside the "
                    "template's coded set — bind the template, not "
                    "the typo")
            try:
                codings.append({"field": key, "value": value,
                                **self.bind_field(template["id"], key,
                                                  value)})
            except TerminologyError:
                unbound.append({"field": key, "value": value})
        if unbound:
            raise TerminologyError(
                f"unbound coded fields: {unbound} — the coded export "
                "refuses rather than smuggle semantics past the "
                "audit; extend the deployment map")
        return {"template": template["id"], "codings": codings,
                "map_version": self.map_version}

    def coverage(self, template):
        """How much of a template's coded vocabulary this map binds —
        with the denominator, so 'coverage' is not marketing."""
        coded = [(s["key"], v) for s in template["sections"]
                 if s.get("type") == "code"
                 for v in s["options"]]
        bound = [c for c in coded
                 if (template["id"], c[0], c[1]) in self.entries]
        return {"template": template["id"], "coded_values": len(coded),
                "bound": len(bound),
                "coverage_rate": (round(len(bound) / len(coded), 3)
                                  if coded else None),
                "map_version": self.map_version,
                "note": "unbound values REFUSE at export; coverage "
                        "here is a worklist for the deployment map, "
                        "not a fudge factor"}
