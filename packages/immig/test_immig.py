#!/usr/bin/env python3
import json
import os
import tempfile
import unittest

from immig import (
    EPI_SCHEDULE, build_aefi_bundle, defaulter_status, dhis2_aggregate,
    identify_antigen, main, validate_immunization,
)


def imm(antigen_text="Penta-1", occ="2026-10-20", status="completed", lot="LOT-9",
        patient="P1", loc="Facility/F1"):
    r = {
        "resourceType": "Immunization", "status": status,
        "vaccineCode": {"coding": [{"system": "http://hl7.org/fhir/sid/cvx", "code": "XX"}],
                        "text": antigen_text},
        "patient": {"reference": f"Patient/{patient}"},
        "occurrenceDateTime": occ,
    }
    if lot:
        r["lotNumber"] = lot
    if loc:
        r["location"] = {"reference": loc}
    return r


class TestValidate(unittest.TestCase):
    def test_conformant(self):
        self.assertEqual(validate_immunization(imm()), [])

    def test_lot_required_on_completed(self):
        issues = validate_immunization(imm(lot=""))
        self.assertTrue(any("lotNumber" in i for i in issues))

    def test_unmappable_vaccine_code(self):
        issues = validate_immunization(imm(antigen_text="Some Obscure Vaccine ZZ99"))
        self.assertTrue(any("not mapped" in i for i in issues))

    def test_identity_fields(self):
        r = imm()
        del r["patient"], r["occurrenceDateTime"]
        issues = validate_immunization(r)
        self.assertTrue(any("patient" in i for i in issues))
        self.assertTrue(any("occurrenceDateTime" in i for i in issues))

    def test_not_done_needs_no_lot(self):
        self.assertEqual(validate_immunization(imm(status="not-done", lot="")), [])


class TestAntigenMap(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(identify_antigen(imm("BCG")), "BCG")
        self.assertEqual(identify_antigen(imm("Pentavalent 3")), "Penta-3")
        self.assertEqual(identify_antigen(imm("yellow fever")), "YellowFever")
        self.assertIsNone(identify_antigen(imm("Vitamin A Drop Admin")))


class TestDefaulter(unittest.TestCase):
    def test_newborn_due_birth_doses(self):
        st = defaulter_status("2026-10-20", [], as_of="2026-10-20")
        due = {l["antigen"] for l in st["lines"] if l["status"] == "DUE"}
        self.assertIn("BCG", due)
        self.assertIn("HepB-birth", due)
        self.assertFalse(st["defaulter"])

    def test_overdue_flags_defaulter(self):
        st = defaulter_status("2026-06-01", [], as_of="2026-10-20")
        self.assertTrue(st["defaulter"])
        self.assertEqual(st["done"], 0)
        self.assertIsNotNone(st["next_action"])

    def test_completed_dose_clears_line(self):
        st = defaulter_status("2026-09-08", [imm("BCG", "2026-09-08")], as_of="2026-09-09")
        bcg = next(l for l in st["lines"] if l["antigen"] == "BCG")
        self.assertEqual(bcg["status"], "DONE")
        self.assertEqual(bcg["given"], "2026-09-08")

    def test_full_schedule_completes(self):
        shots = [imm(row["antigen"], "2026-01-01") for row in EPI_SCHEDULE]
        st = defaulter_status("2025-01-01", shots, as_of="2036-01-01")
        self.assertTrue(st["complete"])
        self.assertIsNone(st["next_action"])

    def test_plain_history_rows(self):
        st = defaulter_status("2026-06-01", [{"vaccine": "bcg", "date": "2026-06-01"}],
                              as_of="2026-06-02")
        bcg = next(l for l in st["lines"] if l["antigen"] == "BCG")
        self.assertEqual(bcg["status"], "DONE")


class TestAEFI(unittest.TestCase):
    def test_bundle_shape(self):
        b = build_aefi_bundle("P1", "Immunization/I9", "293110002", "fever >39C",
                              "moderate", "2026-10-20T14:00:00Z", "DR-1", "F1")
        self.assertEqual(b["type"], "transaction")
        obs = b["entry"][0]["resource"]
        self.assertEqual(obs["resourceType"], "Observation")
        self.assertEqual(obs["subject"]["reference"], "Patient/P1")
        comp = {c["code"]["text"]: c for c in obs["component"]}
        self.assertEqual(comp["implicated-vaccine"]["valueReference"]["reference"],
                         "Immunization/I9")
        self.assertEqual(b["entry"][1]["resource"]["resourceType"], "Flag")
        self.assertTrue(all("request" in e for e in b["entry"]))


class TestDHIS2(unittest.TestCase):
    def test_aggregate_counts_period(self):
        shots = [imm("BCG", "2026-10-01"), imm("BCG", "2026-10-15"),
                 imm("Penta-1", "2026-10-20"), imm("Penta-1", "2026-09-20"),
                 imm("BCG", "2026-10-02", status="not-done")]
        agg = dhis2_aggregate(shots, "202610")
        self.assertEqual(agg["counts"], {"BCG": 2, "Penta-1": 1})
        self.assertEqual(agg["total"], 3)
        self.assertEqual(agg["skipped_not_completed"], 1)


class TestCLI(unittest.TestCase):
    def test_validate_cli(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(imm(), fh)
            path = fh.name
        try:
            self.assertEqual(main(["validate", path]), 0)
        finally:
            os.unlink(path)

    def test_defaulter_cli(self):
        self.assertEqual(main(["defaulters", "--birth-date", "2026-06-01",
                               "--as-of", "2026-10-20"]), 0)


if __name__ == "__main__":
    unittest.main()
