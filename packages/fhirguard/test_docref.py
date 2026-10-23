#!/usr/bin/env python3
import base64
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from docref import DocRefError, deid_document_reference  # noqa: E402

NOTE = ("Discharge summary: patient reachable on 08034567890, "
        "enrollee NHIA-1234567. Follow up in two weeks.")


def b64(text):
    return base64.b64encode(text.encode()).decode()


def doc(content_type="text/plain", data=NOTE, title=None, description=None):
    att = {"contentType": content_type, "data": b64(data)}
    if title:
        att["title"] = title
    res = {"resourceType": "DocumentReference", "id": "doc-1",
           "status": "current", "subject": {"reference": "Patient/NG-0001"},
           "date": "2026-10-20T12:00:00Z",
           "content": [{"attachment": att}]}
    if description:
        res["description"] = description
    return res


class TestDocRefDeid(unittest.TestCase):
    def test_text_attachment_scrubbed_roundtrip(self):
        r = deid_document_reference(doc())
        out = r["resource"]
        decoded = base64.b64decode(
            out["content"][0]["attachment"]["data"]).decode()
        self.assertNotIn("08034567890", decoded)
        self.assertNotIn("NHIA-1234567", decoded)
        self.assertIn("[PHONE_NG]", decoded)
        self.assertIn("Follow up in two weeks.", decoded)  # prose survives
        self.assertGreaterEqual(r["report"]["spans_redacted"], 2)

    def test_title_and_description_scrubbed(self):
        r = deid_document_reference(doc(
            title="Referral for 08034567890",
            description="Call 08034567890 on arrival"))
        att = r["resource"]["content"][0]["attachment"]
        self.assertNotIn("08034567890", att["title"])
        self.assertNotIn("08034567890", r["resource"]["description"])
        self.assertIn("description", r["report"]["fields_scrubbed"])

    def test_linkage_and_dates_pass_through_documented(self):
        r = deid_document_reference(doc())
        out = r["resource"]
        self.assertEqual(out["subject"]["reference"], "Patient/NG-0001")
        self.assertEqual(out["date"], "2026-10-20T12:00:00Z")

    def test_input_not_mutated(self):
        original = doc()
        snapshot = original["content"][0]["attachment"]["data"]
        deid_document_reference(original)
        self.assertEqual(original["content"][0]["attachment"]["data"], snapshot)

    def test_wrong_resource_type_refused(self):
        with self.assertRaises(DocRefError):
            deid_document_reference({"resourceType": "Patient"})

    def test_binary_attachment_refused_by_default(self):
        with self.assertRaises(DocRefError) as ctx:
            deid_document_reference(doc(content_type="application/pdf",
                                        data="%PDF-1.4 fake bytes"))
        self.assertIn("not text", str(ctx.exception))

    def test_drop_binary_drops_and_reports(self):
        res = doc(content_type="application/pdf", data="%PDF-1.4 fake")
        res["content"].append({"attachment": {
            "contentType": "text/plain", "data": b64(NOTE)}})
        r = deid_document_reference(res, drop_binary=True)
        self.assertEqual(len(r["resource"]["content"]), 1)
        self.assertIn("application/pdf", r["report"]["dropped_binary"])

    def test_all_binary_dropped_refuses_empty_document(self):
        with self.assertRaises(DocRefError) as ctx:
            deid_document_reference(doc(content_type="image/jpeg",
                                        data="/9j/4fake"), drop_binary=True)
        self.assertIn("nothing", str(ctx.exception))

    def test_invalid_base64_refused(self):
        res = doc()
        res["content"][0]["attachment"]["data"] = "!!!not-base64!!!"
        with self.assertRaises(DocRefError):
            deid_document_reference(res)

    def test_hash_method_linkable_tokens(self):
        r = deid_document_reference(doc(), method="hash", salt=b"research-1")
        decoded = base64.b64decode(
            r["resource"]["content"][0]["attachment"]["data"]).decode()
        self.assertIn("<phone_ng:", decoded)
        self.assertNotIn("08034567890", decoded)

    def test_json_content_type_is_textual(self):
        body = '{"phone": "08034567890", "note": "stable"}'
        r = deid_document_reference(doc(content_type="application/json",
                                        data=body))
        decoded = base64.b64decode(
            r["resource"]["content"][0]["attachment"]["data"]).decode()
        self.assertNotIn("08034567890", decoded)

    def test_no_data_attachment_passes_with_title_scrub(self):
        res = doc()
        att = res["content"][0]["attachment"]
        del att["data"]
        att["url"] = "https://files.example/scan1.pdf"
        att["title"] = "scan for 08034567890"
        r = deid_document_reference(res)
        self.assertEqual(r["resource"]["content"][0]["attachment"]["url"],
                         "https://files.example/scan1.pdf")
        self.assertNotIn("08034567890",
                         r["resource"]["content"][0]["attachment"]["title"])


if __name__ == "__main__":
    unittest.main()
