"""Tests for patientlink.py, units.py, reference adapter, astm.py,
and the codec upgrades in devicehub.py."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import astm  # noqa: E402
from devicehub import (DeviceHubError, build_ack, build_oru,  # noqa: E402
                       escape, parse_oru, unescape)
from patientlink import IdentityMap, parse_adt  # noqa: E402
from reference_adapter_dermengine import dermengine_transport  # noqa: E402
from units import normalize_observation  # noqa: E402

ADT_A01 = ("MSH|^~\\&|HIS|HOSP|OH||2026||ADT^A01|A1|P|2.5\r"
           "PID|1||MRN-100^^^HOSP||Doe^Jane\r")
ADT_A40 = ("MSH|^~\\&|HIS|HOSP|OH||2026||ADT^A40|A2|P|2.5\r"
           "PID|1||MRN-100^^^HOSP||Doe^Jane\r"
           "MRG|MRN-099^^^HOSP\r")
ORU = ("MSH|^~\\&|A|B|C|D|2026||ORU^R01|MSG-1|P|2.5\r"
       "PID|1||MRN-100\r"
       "OBX|1|NM|K^Potassium||5.2|mmol/L||N|||F|||20261103082500\r")


class TestPatientLink(unittest.TestCase):
    def test_register_resolve(self):
        im = IdentityMap()
        im.register("MRN-100", "plat-1", by="registrar")
        self.assertEqual(im.resolve("MRN-100"), "plat-1")
        self.assertIsNone(im.resolve("MRN-999"))

    def test_adt_register_and_merge_chain(self):
        im = IdentityMap()
        im.register("MRN-099", "plat-1", by="registrar")
        im.apply_adt(ADT_A40, by="his")
        self.assertEqual(im.resolve("MRN-099"), "plat-1")
        self.assertEqual(im.resolve("MRN-100"), "plat-1")

    def test_merge_unknown_source_refuses(self):
        im = IdentityMap()
        with self.assertRaises(DeviceHubError):
            im.merge("MRN-100", "MRN-999", by="his")

    def test_unhandled_adt_event_refuses(self):
        with self.assertRaises(DeviceHubError):
            parse_adt(ADT_A01.replace("ADT^A01", "ADT^A03"))

    def test_route_result_quarantines_unknown_patient(self):
        im = IdentityMap()
        out = im.route_result(parse_oru(ORU))
        self.assertFalse(out["routed"])
        self.assertEqual(im.quarantine_report()["count"], 1)
        im.register("MRN-100", "plat-1", by="registrar")
        out = im.route_result(parse_oru(ORU))
        self.assertTrue(out["routed"])
        self.assertEqual(out["platform_patient_id"], "plat-1")


class TestUnits(unittest.TestCase):
    def test_loinc_binding_stamped(self):
        obs = {"code": "GLU", "value": "180", "value_numeric": 180.0,
               "units": "mg/dL"}
        out = normalize_observation(obs, target_unit="mmol/L")
        self.assertEqual(out["loinc"], "2345-7")
        self.assertAlmostEqual(out["value_numeric"], 9.993, places=2)
        self.assertEqual(out["converted"]["from"], "mg/dL")

    def test_unbound_code_passes_raw_stated(self):
        obs = {"code": "VENDORX", "value": "42", "units": "u"}
        out = normalize_observation(obs)
        self.assertIsNone(out["binding"])
        self.assertIn("unbound", out["binding_note"])

    def test_missing_conversion_row_refuses(self):
        obs = {"code": "K", "value": "5.2", "value_numeric": 5.2,
               "units": "mmol/L"}
        with self.assertRaises(DeviceHubError) as cm:
            normalize_observation(obs, target_unit="mg/dL")
        self.assertIn("data gap", str(cm.exception))

    def test_conversion_of_non_numeric_refuses(self):
        obs = {"code": "GLU", "value": "see note",
               "value_numeric": None, "units": "mg/dL"}
        with self.assertRaises(DeviceHubError):
            normalize_observation(obs, target_unit="mmol/L")


class TestCodecUpgrades(unittest.TestCase):
    def test_escape_round_trip(self):
        raw = "dose 5|10 ^careful~"
        self.assertEqual(unescape(escape(raw)), raw)

    def test_obx_status_and_numeric_typing(self):
        out = parse_oru(ORU.replace("|||F|||", "|||C|||"))
        obs = out["observations"][0]
        self.assertEqual(obs["status"], "C")
        self.assertEqual(obs["value_type"], "NM")
        self.assertEqual(obs["value_numeric"], 5.2)
        self.assertEqual(obs["value"], "5.2")  # raw preserved

    def test_ack_timestamp_is_real(self):
        ack = build_ack("MSG-1")
        self.assertNotIn("20260101000000", ack)
        self.assertIn("MSA|AA|MSG-1", ack)


class TestReferenceAdapter(unittest.TestCase):
    def test_no_transport_refuses(self):
        with self.assertRaises(DeviceHubError):
            dermengine_transport(None)

    def test_ingest_maps_vendor_json(self):
        def fake_http(method, path, json=None):
            return {"message_id": "DE-1", "patient_ref": "MRN-100",
                    "results": [{"parameter": "asymmetry_score",
                                 "value": 0.82, "units": "",
                                 "captured_at": "2026-11-03"}]}
        t = dermengine_transport(fake_http)
        out = t("ingest", {"case_ref": "C-9"})
        self.assertEqual(out["observations"][0]["code"],
                         "asymmetry_score")
        self.assertEqual(out["patient_id"], "MRN-100")

    def test_push_unconfirmed_refuses(self):
        def fake_http(method, path, json=None):
            return {"status": "error"}
        t = dermengine_transport(fake_http)
        with self.assertRaises(DeviceHubError):
            t("push", {"message_id": "M", "patient_id": "P",
                       "observations": [{"code": "x", "value": "1"}],
                       "panel": None})


class TestASTM(unittest.TestCase):
    def _session(self, *texts):
        frames = "".join(astm.build_frame((i + 1) % 8, t)
                         for i, t in enumerate(texts))
        return f"{astm.ENQ}{frames}{astm.EOT}"

    def test_session_round_trip(self):
        s = self._session("H|\\^&|||HOST", "P|1||MRN-100",
                          "R|1|^^^GLU|95|mg/dL||N||||F", "L|1|N")
        out = astm.parse_session(s)
        self.assertEqual(out["patient_id"], "MRN-100")
        self.assertEqual(out["observations"][0]["code"], "GLU")
        self.assertEqual(out["observations"][0]["value"], "95")

    def test_checksum_tampering_refuses(self):
        s = self._session("R|1|^^^GLU|95|mg/dL||N||||F")
        bad = s.replace("95|mg", "96|mg")
        with self.assertRaises(astm.ASTMError) as cm:
            astm.parse_session(bad)
        self.assertIn("checksum", str(cm.exception))

    def test_sequence_gap_refuses(self):
        frames = astm.build_frame(1, "P|1||P1") + astm.build_frame(
            5, "R|1|^^^K|5.0|mmol/L||N||||F")
        with self.assertRaises(astm.ASTMError) as cm:
            astm.parse_session(f"{astm.ENQ}{frames}{astm.EOT}")
        self.assertIn("sequence gap", str(cm.exception))

    def test_session_without_enq_eot_refuses(self):
        with self.assertRaises(astm.ASTMError):
            astm.parse_session(astm.build_frame(1, "R|1|^^^K|5|mmol/L"))


if __name__ == "__main__":
    unittest.main()
