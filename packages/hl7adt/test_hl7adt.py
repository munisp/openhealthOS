"""Tests for packages/hl7adt — HL7 v2 ADT/ORM/ORU parser."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hl7adt import ADTFeed, ParsedMessage, Error  # noqa: E402


def adt_a01():
    return (
        "MSH|^~\\&|LegacyHIS|FAC-A|OpenHealth|FAC-A|20261108120000||"
        "ADT^A01|MSG001|P|2.5\r"
        "EVN|A01|20261108120000\r"
        "PID|1||12345^^^FAC-A||OKAFOR^ADAEZE^CHIOMA||19850315|F\r"
        "PV1|1|I|WARD-A^BED-12||DR-EZE\r"
    )


def oru_r01():
    return (
        "MSH|^~\\&|LabAnalyzer|FAC-A|OpenHealth|FAC-A|20261108130000||"
        "ORU^R01|MSG002|P|2.5\r"
        "PID|1||12345^^^FAC-A||OKAFOR^ADAEZE||19850315|F\r"
        "OBR|1||LAB-001|FBC^Full Blood Count\r"
        "OBX|1|NM|WBC^White Cell Count||7.5|10^9/L|4.0-11.0|N\r"
        "OBX|2|NM|HGB^Haemoglobin||11.2|g/dL|12.0-16.0|L\r"
    )


class TestParser(unittest.TestCase):
    def test_adt_a01_parse(self):
        msg = ParsedMessage(adt_a01())
        self.assertEqual(msg.msg_type, "ADT^A01")
        self.assertEqual(msg.control_id, "MSG001")
        self.assertEqual(msg.sending_app, "LegacyHIS")
        p = msg.patient()
        self.assertEqual(p["patient_id"], "12345")
        self.assertEqual(p["family_name"], "OKAFOR")
        self.assertEqual(p["given_name"], "ADAEZE")
        self.assertEqual(p["dob"], "19850315")
        self.assertEqual(p["sex"], "F")

    def test_visit_info(self):
        msg = ParsedMessage(adt_a01())
        v = msg.visit()
        self.assertEqual(v["patient_class"], "I")
        self.assertEqual(v["ward"], "WARD-A")

    def test_oru_results(self):
        msg = ParsedMessage(oru_r01())
        r = msg.results()
        self.assertEqual(r["test"], "Full Blood Count")
        self.assertEqual(len(r["observations"]), 2)
        self.assertEqual(r["observations"][0]["observation_id"], "WBC")
        self.assertEqual(r["observations"][0]["value"], "7.5")
        self.assertEqual(r["observations"][1]["abnormal_flag"], "L")

    def test_missing_msh_refused(self):
        with self.assertRaises(Error):
            ParsedMessage("PID|1||12345")

    def test_unknown_type_refused(self):
        bad = ("MSH|^~\\&|X|X|X|X|20261108||ZZZ^Z99|M1|P|2.5\r"
               "PID|1||12345")
        with self.assertRaises(Error):
            ParsedMessage(bad)

    def test_missing_pid_refused_for_adt(self):
        no_pid = ("MSH|^~\\&|X|X|X|X|20261108||ADT^A01|M1|P|2.5\r"
                  "PV1|1|I|WARD-A\r")
        msg = ParsedMessage(no_pid)
        with self.assertRaises(Error):
            msg.patient()


class TestFeed(unittest.TestCase):
    def setUp(self):
        self.feed = ADTFeed("feed-1")

    def test_receive_and_dedupe(self):
        ev = self.feed.receive(adt_a01())
        self.assertEqual(ev["msg_type"], "ADT^A01")
        with self.assertRaises(Error):
            self.feed.receive(adt_a01())  # same control ID

    def test_rejected_tracked(self):
        with self.assertRaises(Error):
            self.feed.receive("garbage")
        s = self.feed.stats()
        self.assertEqual(s["rejected"], 1)
        self.assertEqual(s["received"], 0)

    def test_mixed_feed(self):
        self.feed.receive(adt_a01())
        self.feed.receive(oru_r01())
        s = self.feed.stats()
        self.assertEqual(s["received"], 2)


if __name__ == "__main__":
    unittest.main()
