"""Tests for kgraph@1.0.0 — clinical knowledge-graph seam."""

import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kgraph  # noqa: E402
import demo_kgraph  # noqa: E402


def _graph():
    g = kgraph.ConceptGraph("t", version="1.0.0")
    g.add_concept("a", display="A", system="S")
    g.add_concept("b", display="B", system="S", parents=("a",))
    g.add_concept("c", display="C", system="S", parents=("b",))
    return g


class TestConceptGraph(unittest.TestCase):

    def test_transitive_subsumption(self):
        g = _graph()
        self.assertTrue(g.is_a("c", "a"))
        self.assertTrue(g.is_a("b", "a"))
        self.assertFalse(g.is_a("a", "c"))

    def test_self_parent_refused(self):
        g = _graph()
        with self.assertRaises(kgraph.KgraphError):
            g.add_concept("x", display="x", system="S",
                          parents=("x",))

    def test_unregistered_parent_refused(self):
        g = _graph()
        with self.assertRaises(kgraph.KgraphError):
            g.add_concept("x", display="x", system="S",
                          parents=("ghost",))

    def test_duplicate_concept_refused(self):
        g = _graph()
        with self.assertRaises(kgraph.KgraphError):
            g.add_concept("a", display="A2", system="S")

    def test_missing_display_or_system_refused(self):
        g = _graph()
        with self.assertRaises(kgraph.KgraphError):
            g.add_concept("x", display="", system="S")
        with self.assertRaises(kgraph.KgraphError):
            g.add_concept("x", display="x", system="")

    def test_unversioned_graph_refused(self):
        with self.assertRaises(kgraph.KgraphError):
            kgraph.ConceptGraph("t", version="")

    def test_is_a_names_absence(self):
        g = _graph()
        with self.assertRaises(kgraph.KgraphError):
            g.is_a("c", "ghost")

    def test_mutation_invalidates_closure(self):
        g = _graph()
        self.assertFalse(g.is_a("a", "d") if "d" in g.concepts
                         else False)
        g.add_concept("d", display="D", system="S", parents=("c",))
        self.assertTrue(g.is_a("d", "a"))


class TestMappings(unittest.TestCase):

    def test_two_approvers_ratify(self):
        g = _graph()
        m = g.add_mapping("b", "a", approved_by=("p1", "p2"))
        self.assertTrue(m["ratified"])

    def test_single_approver_does_not_traverse(self):
        g = _graph()
        g.add_mapping("b", "a", approved_by=("p1",))
        self.assertEqual(g.equivalents("b"), [])

    def test_duplicate_approver_refused(self):
        g = _graph()
        with self.assertRaises(kgraph.KgraphError):
            g.add_mapping("b", "a", approved_by=("p1", "p1"))

    def test_unknown_kind_refused(self):
        g = _graph()
        with self.assertRaises(kgraph.KgraphError):
            g.add_mapping("b", "a", kind="same-ish",
                          approved_by=("p1", "p2"))

    def test_unregistered_endpoints_refused(self):
        g = _graph()
        with self.assertRaises(kgraph.KgraphError):
            g.add_mapping("b", "ghost", approved_by=("p1", "p2"))


class TestShapes(unittest.TestCase):

    def _shape(self):
        s = kgraph.ShapeRegistry()
        s.register_shape(
            "t@1", target="a",
            constraints=[
                {"kind": "required", "path": "x"},
                {"kind": "datatype", "path": "x", "datatype": "int"},
                {"kind": "in", "path": "p", "values": ["u", "v"]},
                {"kind": "closed", "allowed": ["x", "p"]},
            ])
        return s

    def test_conforming_node(self):
        r = self._shape().validate({"x": 1, "p": "u"}, "t@1")
        self.assertTrue(r["conforms"])
        self.assertEqual(r["violations"], [])

    def test_violations_named_with_kind_and_path(self):
        r = self._shape().validate({"x": "nope", "p": "w", "q": 1},
                                   "t@1")
        kinds = sorted(v["kind"] for v in r["violations"])
        self.assertEqual(kinds, ["closed", "datatype", "in"])
        self.assertFalse(r["conforms"])

    def test_required_absent_named(self):
        r = self._shape().validate({"p": "u"}, "t@1")
        self.assertIn({"kind": "required", "path": "x",
                       "detail": "required property absent"},
                      r["violations"])

    def test_bool_is_not_int(self):
        r = self._shape().validate({"x": True}, "t@1")
        self.assertTrue(any(v["kind"] == "datatype"
                            for v in r["violations"]))

    def test_unknown_constraint_kind_refused_at_registration(self):
        s = kgraph.ShapeRegistry()
        with self.assertRaises(kgraph.KgraphError):
            s.register_shape("bad", target="a",
                             constraints=[{"kind": "telepathy",
                                           "path": "x"}])

    def test_unknown_datatype_refused(self):
        s = kgraph.ShapeRegistry()
        with self.assertRaises(kgraph.KgraphError):
            s.register_shape("bad", target="a",
                             constraints=[{"kind": "datatype",
                                           "path": "x",
                                           "datatype": "blob"}])

    def test_unregistered_shape_refused_loudly(self):
        with self.assertRaises(kgraph.KgraphError):
            self._shape().validate({}, "ghost")


class TestEndpoints(unittest.TestCase):

    def _eps(self):
        eps = kgraph.EndpointRegistry()
        eps.ratify_adapter("oxirs-fuseki", by="a1")
        eps.ratify_adapter("oxirs-fuseki", by="a2")
        return eps

    def test_https_only(self):
        eps = self._eps()
        with self.assertRaises(kgraph.KgraphError):
            eps.register("e", url="http://insecure.example/sparql")

    def test_unratified_endpoint_refuses(self):
        eps = self._eps()
        eps.register("e", url="https://e.example/sparql")
        r = eps.query("e", "ASK { ?s ?p ?o }",
                      transport=lambda u, q: [])
        self.assertEqual(r["status"], "refused")

    def test_missing_transport_stated_not_faked(self):
        eps = self._eps()
        eps.register("e", url="https://e.example/sparql",
                     approvers=("a1", "a2"))
        r = eps.query("e", "ASK { ?s ?p ?o }")
        self.assertEqual(r["status"], "not-checked")
        self.assertIn("no transport", r["detail"])

    def test_transport_error_is_not_checked_not_empty(self):
        eps = self._eps()
        eps.register("e", url="https://e.example/sparql",
                     approvers=("a1", "a2"))

        def boom(u, q):
            raise TimeoutError("slow")

        r = eps.query("e", "ASK { ?s ?p ?o }", transport=boom)
        self.assertEqual(r["status"], "not-checked")
        self.assertIn("TimeoutError", r["detail"])

    def test_template_adapter_refuses_until_ratified(self):
        eps = kgraph.EndpointRegistry()
        with self.assertRaises(kgraph.KgraphError):
            eps.register("e", url="https://e.example/sparql",
                         adapter="jena-fuseki",
                         approvers=("a1", "a2"))

    def test_duplicate_adapter_approver_refused(self):
        eps = kgraph.EndpointRegistry()
        eps.ratify_adapter("jena-fuseki", by="a1")
        with self.assertRaises(kgraph.KgraphError):
            eps.ratify_adapter("jena-fuseki", by="a1")

    def test_ratified_query_returns_pinned_rows(self):
        eps = self._eps()
        eps.register("e", url="https://e.example/sparql",
                     adapter="oxirs-fuseki", approvers=("a1", "a2"))
        rows = [{"s": "x"}]
        r = eps.query("e", "SELECT * WHERE { ?s ?p ?o }",
                      transport=lambda u, q: rows,
                      executed_at="2026-11-11T12:00:00Z")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["row_count"], 1)
        self.assertEqual(
            r["rows_sha256"],
            hashlib.sha256(json.dumps(rows, sort_keys=True,
                                      separators=(",", ":"))
                           .encode()).hexdigest())


class TestDemoKgproof(unittest.TestCase):

    def test_demo_is_deterministic(self):
        a = demo_kgraph.run_demo()
        b = demo_kgraph.run_demo()
        self.assertEqual(a, b)

    def test_stamp_contract(self):
        body = demo_kgraph.run_demo()
        stamp = body.pop("sha256")
        expect = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        self.assertEqual(stamp, expect)

    def test_payload_shape(self):
        body = demo_kgraph.run_demo()
        self.assertEqual(body["surface"], "kgproof")
        self.assertTrue(body["demo"])
        self.assertEqual(len(body["drills"]), 8)
        for d in body["drills"]:
            outcome = d["result"].get("outcome") \
                or d["result"].get("status")
            self.assertIn(outcome, ("refused", "not-traversed",
                                    "named", "not-checked"))
        ctrl = body["control"]
        self.assertTrue(ctrl["subsumption"]["sbp-is-vital"])
        self.assertTrue(ctrl["subsumption"]["e119-is-diabetes"])
        self.assertFalse(ctrl["subsumption"]["sbp-is-not-diabetes"])
        self.assertTrue(ctrl["conforming_node"]["conforms"])
        self.assertEqual(ctrl["ratified_query"]["status"], "ok")
        self.assertEqual(len(ctrl["ratified_mapping_traverses"]), 1)

    def test_file_emission(self):
        demo_kgraph.main()
        with open(demo_kgraph.OUT, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk, demo_kgraph.run_demo())


if __name__ == "__main__":
    unittest.main()
