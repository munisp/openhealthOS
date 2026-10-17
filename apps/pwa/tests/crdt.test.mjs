/* CI conformance: the JS CRDT port must obey the same laws as shared/crdt
 * (Go). Run: node --test apps/pwa/tests/ */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Replica, hlcAfter, materialize } from "../js/crdt.js";

test("hlc total order", () => {
  assert.ok(hlcAfter({ w: 100, l: 1, n: "a" }, { w: 100, l: 0, n: "a" }));
  assert.ok(hlcAfter({ w: 100, l: 1, n: "b" }, { w: 100, l: 1, n: "a" }));
  assert.ok(hlcAfter({ w: 101, l: 0, n: "a" }, { w: 100, l: 9, n: "z" }));
  assert.ok(!hlcAfter({ w: 100, l: 0, n: "a" }, { w: 100, l: 0, n: "a" }));
});

test("lww latest wins, stale merge cannot regress", () => {
  const edge = new Replica("e1");
  edge.set("p1", "Patient", "name", "Aisha", 100);
  const state = new Replica("s1");
  state.set("p1", "Patient", "name", "Aisha Bello", 200);
  edge.applyBatch(state.exportBatch());
  assert.equal(materialize(edge.docs.get("p1")).name, "Aisha Bello");
  state.applyBatch(edge.exportBatch());
  assert.equal(materialize(state.docs.get("p1")).name, "Aisha Bello");
});

test("concurrent same-wall writes converge deterministically", () => {
  const a = new Replica("a-node");
  const b = new Replica("b-node");
  a.set("p1", "Patient", "phone", "from-a", 100);
  b.set("p1", "Patient", "phone", "from-b", 100);
  a.applyBatch(b.exportBatch());
  b.applyBatch(a.exportBatch());
  assert.equal(materialize(a.docs.get("p1")).phone, materialize(b.docs.get("p1")).phone);
});

test("add-only sets union across partition and heal both ways", () => {
  const e1 = new Replica("e1");
  e1.add("p1", "Patient", "immunizations", ["BCG", "OPV0"], 1);
  const e2 = new Replica("e2");
  e2.add("p1", "Patient", "immunizations", ["Penta1"], 2);
  e1.applyBatch(e2.exportBatch());
  e2.applyBatch(e1.exportBatch());
  for (const r of [e1, e2]) {
    assert.deepEqual(materialize(r.docs.get("p1")).immunizations, ["BCG", "OPV0", "Penta1"]);
  }
});

test("tombstones hide scalars; sets refuse deletion", () => {
  const a = new Replica("a");
  a.set("p1", "Patient", "nick", "Ada", 1);
  a.del("p1", "Patient", "nick", 2);
  assert.equal(materialize(a.docs.get("p1")).nick, undefined);
  a.add("p1", "Patient", "allergies", ["penicillin"], 3);
  a.del("p1", "Patient", "allergies", 4);
  assert.deepEqual(materialize(a.docs.get("p1")).allergies, ["penicillin"]);
});

test("merge is idempotent and commutative", () => {
  const a = new Replica("a");
  const b = new Replica("b");
  a.set("d", "Encounter", "note", "edge-note", 10);
  a.add("d", "Encounter", "diagnoses", ["malaria"], 11);
  b.set("d", "Encounter", "status", "closed", 20);
  b.add("d", "Encounter", "diagnoses", ["anaemia"], 21);
  const ba = a.exportBatch();
  const bb = b.exportBatch();
  const x = new Replica("x");
  const y = new Replica("y");
  x.applyBatch(ba); x.applyBatch(bb); x.applyBatch(ba); // duplicate delivery
  y.applyBatch(bb); y.applyBatch(ba);
  assert.deepEqual(materialize(x.docs.get("d")), materialize(y.docs.get("d")));
});

test("wire shape matches the Go batch contract", () => {
  const r = new Replica("edge-kd-04");
  r.set("p1", "Patient", "name", "Aisha", 1000);
  const batch = JSON.parse(JSON.stringify(r.exportBatch()));
  assert.equal(batch.from, "edge-kd-04");
  const field = batch.docs[0].fields.name;
  assert.equal(field.k, "lww");
  assert.deepEqual(field.s, { w: 1000, l: 0, n: "edge-kd-04" });
});
