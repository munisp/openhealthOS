import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from devicehub import (DeviceHub, DeviceHubError, DEVICE_ADAPTERS,  # noqa: E402
                       catalog, parse_oru, build_ack, build_oru,
                       DEVICE_ADAPTER_VERSION)

ORU_SAMPLE = (
    "MSH|^~\\&|COBAS6000|ROCHE|OPENHEALTH|LAB|20261103083000||"
    "ORU^R01|MSG-001|P|2.5\r"
    "PID|1||P-12345^^^HOSP\r"
    "OBR|1|||CHEM7^Basic metabolic\r"
    "OBX|1|NM|K^Potassium||5.2|mmol/L|3.5-5.1|H||||||20261103082500\r"
    "OBX|2|NM|NA^Sodium||138|mmol/L|136-145|N||||||20261103082500\r"
    "NTE|1||fasting sample\r"
    "ZZZ|custom vendor segment\r")


class TestCatalogue(unittest.TestCase):
    def test_every_domain_has_three_vendors_plus_open(self):
        for domain, rows in DEVICE_ADAPTERS.items():
            self.assertEqual(len(rows), 4, domain)
            self.assertEqual(sum(1 for a in rows
                                 if a["posture"] == "composed"), 3)
            self.assertEqual(sum(1 for a in rows
                                 if a["posture"] == "open"), 1)
            for a in rows:
                self.assertTrue(a["vendor"])
                self.assertTrue(a["protocol"])

    def test_expected_domains_covered(self):
        self.assertEqual(set(DEVICE_ADAPTERS),
                         {"ecg", "patient-monitor", "dialysis",
                          "ultrasound", "pathology-scanner",
                          "dermoscopy", "lab-analyzer",
                          "semen-analyzer"})

    def test_unknown_domain_raises_not_empty(self):
        with self.assertRaises(DeviceHubError):
            catalog("astrology")


class TestHL7Codec(unittest.TestCase):
    def test_parse_oru_normalizes_observations(self):
        out = parse_oru(ORU_SAMPLE)
        self.assertEqual(out["message_id"], "MSG-001")
        self.assertEqual(out["patient_id"], "P-12345")
        self.assertEqual(len(out["observations"]), 2)
        k = out["observations"][0]
        self.assertEqual((k["code"], k["value"], k["units"],
                          k["abnormal_flag"], k["panel"]),
                         ("K", "5.2", "mmol/L", "H", "CHEM7"))
        self.assertEqual(out["unmapped_segments"], ["ZZZ"])

    def test_garbage_refuses_loudly(self):
        for bad in ("", "hello world",
                    "MSH|only|three\r",
                    "MSH|^~\\&|A|B|C|D|2026||ORU^R01|X|P|2.5\r"
                    "PID|1||P1\r"):
            with self.assertRaises(DeviceHubError):
                parse_oru(bad)

    def test_ack_honest_about_acceptance(self):
        self.assertIn("MSA|AA|MSG-001", build_ack("MSG-001"))
        nack = build_ack("MSG-002", error="bad segment")
        self.assertIn("MSA|AE|MSG-002", nack)
        self.assertIn("bad segment", nack)

    def test_build_oru_round_trip_and_gates(self):
        msg = build_oru(message_id="M-9", patient_id="P-7",
                        sender="openhealth",
                        observations=[{"code": "K", "value": "4.1",
                                       "units": "mmol/L",
                                       "flag": "N"}],
                        panel="CHEM7")
        back = parse_oru(msg)
        self.assertEqual(back["observations"][0]["code"], "K")
        with self.assertRaises(DeviceHubError):
            build_oru(message_id="M-1", patient_id="P",
                      sender="x", observations=[])
        with self.assertRaises(DeviceHubError):
            build_oru(message_id="M-1", patient_id="P", sender="x",
                      observations=[{"code": "", "value": "4"}])
        with self.assertRaises(DeviceHubError):
            build_oru(message_id="M-1", patient_id="P", sender="x",
                      observations=[{"code": "K", "value": ""}])


class TestHubSessions(unittest.TestCase):
    def test_open_lane_connects_and_ingests(self):
        hub = DeviceHub()
        s = hub.connect("open-lis", endpoint="tcp://lis.local:2575")
        out = hub.ingest(s["session_id"], ORU_SAMPLE)
        self.assertEqual(out["adapter"], "open-lis")
        self.assertEqual(out["observations"][0]["source_adapter"],
                         "open-lis")
        self.assertIn("clean-room", out["protocol_note"])

    def test_no_endpoint_refuses(self):
        hub = DeviceHub()
        with self.assertRaises(DeviceHubError) as ctx:
            hub.connect("open-lis", endpoint="")
        self.assertIn("not an interface", str(ctx.exception))

    def test_composed_lane_needs_transport(self):
        hub = DeviceHub()
        with self.assertRaises(DeviceHubError) as ctx:
            hub.connect("ge-muse", endpoint="https://muse.hospital")
        self.assertIn("transport", str(ctx.exception))

    def test_composed_lane_delegates_with_provenance(self):
        hub = DeviceHub()
        calls = []

        def bridge(op, payload):
            calls.append(op)
            return {"observations": [{"code": "HR", "value": "72"}],
                    "message_id": "V-1"}

        s = hub.connect("mindray-benelink", endpoint="hl7://bl:6661",
                        transport=bridge)
        out = hub.ingest(s["session_id"], "whatever-the-vendor-sends")
        self.assertEqual(calls, ["ingest"])
        self.assertEqual(out["observations"][0]["source_adapter"],
                         "mindray-benelink")
        self.assertEqual(out["protocol_note"],
                         "parsed by vendor transport")

    def test_push_open_lane_serializes(self):
        hub = DeviceHub()
        s = hub.connect("open-hd", endpoint="tcp://hd.local:9000")
        msg = hub.push_result(s["session_id"], message_id="HD-1",
                              patient_id="p-ckd",
                              observations=[{"code": "SPKTV",
                                             "value": "1.45"}],
                              panel="HD-ADEQUACY")
        self.assertIn("SPKTV", msg)
        self.assertIn("MSH", msg)

    def test_unknown_adapter_and_session_refuse(self):
        hub = DeviceHub()
        with self.assertRaises(DeviceHubError):
            hub.connect("acme-tricorder", endpoint="x")
        with self.assertRaises(DeviceHubError):
            hub.ingest("DH-999", ORU_SAMPLE)


if __name__ == "__main__":
    unittest.main()
