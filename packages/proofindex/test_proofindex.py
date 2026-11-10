"""test_proofindex.py — the proof-index contract.

Doctrines under test:
- Every stamped payload in the tree verifies against its recorded
  contract, right now, in the real working tree.
- The gate fails closed and NAMED: drift, absence, malformed JSON,
  unstamped, non-ASCII or non-canonical stamps are verdicts, never
  exceptions — compare_digest is guarded before it can TypeError.
- The demo surface is deterministic and self-stamped.
- META: every demo exporter in the estate is byte-deterministic across
  fresh processes (random hash seeds), proven by running each one
  twice in a subprocess and comparing output bytes.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

import proofindex
from proofindex import (CONTRACTS, PAYLOADS, SELF_EXCLUDED, coverage,
                        scan_stamped, verify_all, verify_dict,
                        verify_payload)
import demo_proofindex

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__),
                                          "..", ".."))


def _compact(body):
    return CONTRACTS["compact"](body)


class TestVerifyDict(unittest.TestCase):
    """In-memory gate behaviour — attacker-shaped input is a verdict."""

    def setUp(self):
        self.entry = dict(PAYLOADS[0])   # tamperproof.json, compact
        with open(os.path.join(REPO_ROOT, self.entry["path"]),
                  encoding="utf-8") as fh:
            self.genuine = json.load(fh)

    def test_genuine_verifies(self):
        v = verify_dict(self.genuine, self.entry)
        self.assertEqual(v["status"], "verified")
        self.assertEqual(v["severity"], "green")
        self.assertEqual(v["presented"], self.genuine["sha256"])
        self.assertEqual(v["payload_version"],
                         self.genuine["version"])

    def test_drift_is_red(self):
        tampered = dict(self.genuine)
        tampered["gate"] = dict(self.genuine["gate"])
        tampered["gate"]["sweep_ok"] = True
        v = verify_dict(tampered, self.entry)
        self.assertEqual(v["status"], "drift")
        self.assertEqual(v["severity"], "red")

    def test_uppercase_stamp_is_non_canonical(self):
        twin = dict(self.genuine)
        twin["sha256"] = self.genuine["sha256"].upper()
        v = verify_dict(twin, self.entry)
        self.assertEqual(v["status"], "non-canonical-stamp")
        self.assertEqual(v["severity"], "red")

    def test_non_ascii_stamp_is_a_verdict_not_a_typeerror(self):
        # hmac.compare_digest(str, str) raises TypeError on non-ASCII;
        # the gate must refuse before that can happen (row 202 lesson).
        bad = dict(self.genuine)
        bad["sha256"] = "¡£§" * 21 + "x"
        v = verify_dict(bad, self.entry)   # must not raise
        self.assertEqual(v["status"], "malformed-stamp")
        self.assertEqual(v["severity"], "red")

    def test_non_string_stamp_is_a_verdict_not_a_typeerror(self):
        bad = dict(self.genuine)
        bad["sha256"] = 12345
        v = verify_dict(bad, self.entry)   # must not raise
        self.assertEqual(v["status"], "malformed-stamp")

    def test_unstamped_is_stated(self):
        bare = {k: v for k, v in self.genuine.items() if k != "sha256"}
        v = verify_dict(bare, self.entry)
        self.assertEqual(v["status"], "unstamped")
        self.assertEqual(v["severity"], "red")

    def test_non_object_payload_is_malformed(self):
        v = verify_dict([1, 2, 3], self.entry)
        self.assertEqual(v["status"], "malformed")

    def test_contract_mismatch_is_amber(self):
        body = {k: v for k, v in self.genuine.items() if k != "sha256"}
        restamped = dict(body)
        restamped["sha256"] = CONTRACTS["spaced-utf8"](body)
        v = verify_dict(restamped, self.entry)
        self.assertEqual(v["status"], "contract-mismatch")
        self.assertEqual(v["severity"], "amber")
        self.assertEqual(v["verifies_under"], "spaced-utf8")


class TestVerifyAllRealTree(unittest.TestCase):
    """The standing state: every registered payload verifies."""

    def test_every_payload_verified(self):
        report = verify_all(root=REPO_ROOT, generated_at="T")
        self.assertEqual(report["summary"]["total"], 21)
        self.assertEqual(report["summary"]["verified"], 21)
        self.assertTrue(report["summary"]["all_verified"])
        for v in report["payloads"]:
            self.assertEqual(v["status"], "verified", v["path"])

    def test_contract_census(self):
        report = verify_all(root=REPO_ROOT, generated_at="T")
        self.assertEqual(report["contracts"],
                         {"compact": 13, "spaced-utf8": 8})

    def test_absent_is_stated_not_raised(self):
        ghost = {"path": "apps/pwa/definitely-not-here.json",
                 "contract": "compact", "exporter": "test",
                 "title": "ghost"}
        v = verify_payload(ghost, root=REPO_ROOT)
        self.assertEqual(v["status"], "absent")
        self.assertEqual(v["severity"], "red")

    def test_malformed_json_file_is_a_verdict(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "broken.json")
            with open(p, "w") as fh:
                fh.write("{not json")
            entry = {"path": "broken.json", "contract": "compact",
                     "exporter": "test", "title": "broken"}
            v = verify_payload(entry, root=td)
        self.assertEqual(v["status"], "malformed")
        self.assertEqual(v["severity"], "red")


class TestDemoProofindex(unittest.TestCase):
    """The demo surface itself: deterministic, self-stamped, shaped."""

    def test_deterministic_byte_identical(self):
        a = demo_proofindex.run_demo()
        b = demo_proofindex.run_demo()
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))

    def test_stamp_contract(self):
        p = demo_proofindex.run_demo()
        body = {k: v for k, v in p.items() if k != "sha256"}
        self.assertEqual(p["sha256"], _compact(body))
        self.assertRegex(p["sha256"], r"^[0-9a-f]{64}$")

    def test_payload_shape(self):
        p = demo_proofindex.run_demo()
        self.assertTrue(p["flags"]["all_verified"])
        self.assertTrue(p["flags"]["coverage_complete"])
        self.assertTrue(p["flags"]["drills_named"])
        self.assertEqual(len(p["registry"]["payloads"]), 21)
        self.assertEqual({d["verdict"] for d in p["drills"]},
                         {"drift", "non-canonical-stamp",
                          "contract-mismatch", "absent",
                          "unregistered"})
        self.assertEqual(p["self_integrity"]["contract"], "compact")

    def test_file_emission(self):
        demo_proofindex.main()
        out = os.path.normpath(demo_proofindex.OUT)
        with open(out) as fh:
            back = json.load(fh)
        self.assertEqual(back["version"], demo_proofindex.DEMO_VERSION)
        body = {k: v for k, v in back.items() if k != "sha256"}
        self.assertEqual(back["sha256"], _compact(body))


class TestCoverage(unittest.TestCase):
    """Registry-vs-disk accounting — the wave-6 lesson as a gate.

    anchorproof.json spent one wave stamped but unindexed; nothing
    noticed. scan_stamped + coverage make a falling-behind registry
    loud: every stamped file on disk is registered or declared
    self-excluded, and anything else fails the gate.
    """

    def test_real_tree_coverage_complete(self):
        cov = coverage(root=REPO_ROOT)
        self.assertTrue(cov["complete"],
                        "unregistered stamped payloads: %s"
                        % cov["unregistered"])
        self.assertEqual(cov["unregistered"], [])
        self.assertEqual(cov["stamped_on_disk"],
                         cov["registered"]
                         + len(cov["self_excluded"]))

    def test_self_exclusion_is_declared_data(self):
        self.assertEqual([e["path"] for e in SELF_EXCLUDED],
                         ["apps/pwa/proofindex.json"])
        self.assertTrue(SELF_EXCLUDED[0]["reason"])
        cov = coverage(root=REPO_ROOT)
        self.assertEqual([e["path"] for e in cov["self_excluded"]],
                         ["apps/pwa/proofindex.json"])
        # The exemption is real: the file IS stamped, IS on disk.
        self.assertIn("apps/pwa/proofindex.json",
                      scan_stamped(root=REPO_ROOT))

    def test_unregistered_stamped_payload_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "apps", "pwa"))
            rogue = {"claim": "stamped but never registered"}
            rogue["sha256"] = _compact(rogue)
            with open(os.path.join(td, "apps", "pwa", "rogue.json"),
                      "w") as fh:
                json.dump(rogue, fh)
            cov = coverage(root=td)
        self.assertFalse(cov["complete"])
        self.assertEqual(cov["unregistered"],
                         [os.path.join("apps", "pwa", "rogue.json")])

    def test_unstamped_files_are_not_the_scans_business(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "apps", "pwa"))
            with open(os.path.join(td, "apps", "pwa", "plain.json"),
                      "w") as fh:
                json.dump({"no": "stamp here"}, fh)
            # Malformed JSON in a scan dir is skipped, never raised —
            # if it were registered, verify_payload would name it.
            with open(os.path.join(td, "apps", "pwa", "broken.json"),
                      "w") as fh:
                fh.write("{not json")
            cov = coverage(root=td)
        self.assertTrue(cov["complete"])
        self.assertEqual(cov["stamped_on_disk"], 0)

    def test_report_carries_coverage(self):
        report = verify_all(root=REPO_ROOT, generated_at="T")
        self.assertIn("coverage", report)
        self.assertTrue(report["coverage"]["complete"])


# ---------------------------------------------------------------------------
# The meta gate: every demo exporter is byte-deterministic across fresh
# processes. Recipes mirror how each package's own demo tests invoke its
# exporter. In-memory producers print canonical JSON; file producers
# export into a tmpdir and print per-file digests (paths never enter
# payloads, so digests are comparable across runs).
# ---------------------------------------------------------------------------

_MEM = "import json; print(json.dumps(%s, sort_keys=True))"

EXPORTERS = [
    ("agentwrite", "packages/agentwrite",
     _MEM % "__import__('demo_agentwrite').run_demo()"),
    ("appmarket", "packages/appmarket",
     _MEM % ("(lambda d: d.appmarket_payload(d.seed_appmarket()))"
             "(__import__('demo_appmarket'))")),
    ("charting", "packages/charting",
     _MEM % ("(lambda d: d.charting_payload(d.seed_charting()))"
             "(__import__('demo_charting'))")),
    ("deployproof", "packages/travelpass",
     _MEM % "__import__('demo_deploy').run_demo()"),
    ("tamperproof", "packages/travelpass",
     _MEM % "__import__('demo_tamper').run_demo()"),
    ("anchorproof", "packages/ledgeranchor",
     _MEM % "__import__('demo_ledgeranchor').run_demo()"),
    ("shipproof", "packages/shipgate",
     _MEM % "__import__('demo_shipgate').run_demo()"),
    ("inferproof", "packages/inferlock",
     _MEM % "__import__('demo_inferlock').run_demo()"),
    ("portal", "packages/ips",
     _MEM % "__import__('demo_portal').seed_record_payload()"),
    ("dashboards", "packages/travelpass",
     "import tempfile, glob, hashlib, os, sys; "
     "_h = os.getcwd(); "
     "[sys.path.insert(0, os.path.abspath(os.path.join(_h, '..', p))) "
     "for p in ('crosslink', 'drugkb', 'surveillance', 'poe_screen', "
     "'steward', 'medrec', 'inpatient', 'terminology', 'mammoprog', "
     "'offlinespec', 'teachfile', 'formulary', 'earlywarn', "
     "'cervixscreen', 'usguard', 'callrecall', 'sepsis', 'teleicu', "
     "'acuity', 'telepath', 'teleread', 'reportkit', 'pacs', "
     "'fertility', 'rota', 'triggers', 'vitals', 'mdt')]; "
     "from vhpilot import seed_pilot; from vhdash import export_dashboards; "
     "td = tempfile.mkdtemp(); "
     "export_dashboards(seed_pilot(seed=42)['registries'], td); "
     "print('\\n'.join(hashlib.sha256(open(f, 'rb').read()).hexdigest() "
     "for f in sorted(glob.glob(td + '/*.json'))))"),
    ("growth", "packages/travelpass",
     "import tempfile, glob, hashlib; "
     "from vhdashx import seed_growth, export_growth; "
     "td = tempfile.mkdtemp(); export_growth(seed_growth(), td); "
     "print('\\n'.join(hashlib.sha256(open(f, 'rb').read()).hexdigest() "
     "for f in sorted(glob.glob(td + '/*'))) )"),
    ("emr", "packages/emrdemo",
     "import tempfile, glob, hashlib; "
     "td = tempfile.mkdtemp(); "
     "__import__('demo_emr').export_all(td, seed=42); "
     "print('\\n'.join(hashlib.sha256(open(f, 'rb').read()).hexdigest() "
     "for f in sorted(glob.glob(td + '/*.json'))))"),
    ("national", "packages/natdemo",
     "import tempfile, glob, hashlib; "
     "td = tempfile.mkdtemp(); "
     "__import__('demo_national').export_all(td); "
     "print('\\n'.join(hashlib.sha256(open(f, 'rb').read()).hexdigest() "
     "for f in sorted(glob.glob(td + '/*.json'))))"),
    ("sync", "packages/syncmesh",
     "import tempfile, glob, hashlib; "
     "td = tempfile.mkdtemp(); "
     "__import__('demo_sync').export_all(td); "
     "print('\\n'.join(hashlib.sha256(open(f, 'rb').read()).hexdigest() "
     "for f in sorted(glob.glob(td + '/*.json'))))"),
]


class TestExporterDeterminism(unittest.TestCase):
    """One subprocess pair per exporter: fresh interpreters, random
    hash seeds, identical bytes — a demo you cannot reproduce is a
    claim, not evidence."""

    def _run(self, pkg_dir, snippet):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(REPO_ROOT, pkg_dir)
        r = subprocess.run(
            [sys.executable, "-c", snippet],
            cwd=os.path.join(REPO_ROOT, pkg_dir),
            env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0,
                         "%s exited %s: %s" % (pkg_dir, r.returncode,
                                               r.stderr[-500:]))
        return r.stdout

    def test_all_exporters_byte_deterministic(self):
        for name, pkg, snippet in EXPORTERS:
            with self.subTest(exporter=name):
                self.assertEqual(self._run(pkg, snippet),
                                 self._run(pkg, snippet),
                                 "%s is not byte-deterministic" % name)


if __name__ == "__main__":
    unittest.main()
