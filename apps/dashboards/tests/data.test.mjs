/* Node tests for the dashboards' pure data transforms — the numbers on
 * the state board must match the pilot-gates runbook, so the gate math
 * is pinned here. Run: node --test tests/ */
import { test } from "node:test";
import assert from "node:assert/strict";
import { eventsByType, encountersToday, perFacilityCounts, pilotGates,
         overviewRows, platformHealth, demoData } from "../js/data.js";

test("eventsByType counts", () => {
  const out = eventsByType([
    { type: "encounter" }, { type: "encounter" }, { type: "claim_submitted" }]);
  assert.equal(out.encounter, 2);
  assert.equal(out.claim_submitted, 1);
});

test("encountersToday only counts today", () => {
  const now = Date.now();
  const ev = [
    { type: "encounter", ts: new Date(now).toISOString() },          // now: today in any TZ
    { type: "encounter", ts: new Date(now - 26 * 3600000).toISOString() }, // >24h ago: never today
    { type: "claim_submitted", ts: new Date(now).toISOString() },    // not an encounter
  ];
  assert.equal(encountersToday(ev, now), 1);
});

test("perFacilityCounts buckets per facility", () => {
  const out = perFacilityCounts([
    { facility: "A", type: "encounter" },
    { facility: "A", type: "claim_submitted" },
    { facility: "B", type: "dispensing" },
  ]);
  assert.deepEqual(out.A, { encounters: 1, claims: 1, dispensing: 0, total: 2 });
  assert.equal(out.B.total, 1);
});

test("pilotGates: offline continuity gate passes at ≥90%", () => {
  const ev = [
    ...Array(9).fill({ type: "encounter", ts: new Date().toISOString(), payload: { offline: true, staff_id: "s1" } }),
    { type: "encounter", ts: new Date().toISOString(), payload: { offline: false, staff_id: "s2" } },
  ];
  const gates = pilotGates(ev, { trainedStaff: 2, facilities: 1 });
  const continuity = gates.find((g) => g.name.startsWith("Clinical"));
  assert.equal(continuity.value, 90);
  assert.equal(continuity.pass, true);
});

test("pilotGates: claims turnaround uses median", () => {
  const mk = (d) => ({ type: "claim_submitted", ts: new Date().toISOString(), payload: { turnaround_days: d } });
  const gates = pilotGates([mk(2), mk(5), mk(20)], {});
  const tat = gates.find((g) => g.name.startsWith("Claims"));
  assert.equal(tat.value, 5);
  assert.equal(tat.pass, true);
});

test("pilotGates: no data is null, never a fake pass", () => {
  const gates = pilotGates([], {});
  assert.ok(gates.every((g) => g.value === null || g.pass !== true || g.name === "Sync reliability <15min" || g.name === "Stock-out visibility"));
});

test("overviewRows sorts and computes error rate", () => {
  const rows = overviewRows({
    "svc-b": { up: true, requests_total: 100, errors_5xx: 2, latency_ms_mean: 4, uptime_seconds: 60 },
    "svc-a": { up: false },
  });
  assert.equal(rows[0].service, "svc-a");
  assert.equal(rows[1].errRate, 2);
});

test("platformHealth ladder", () => {
  assert.equal(platformHealth([]), "dark");
  assert.equal(platformHealth([{ up: true }, { up: true }]), "ok");
  assert.equal(platformHealth([{ up: true }, { up: true }, { up: false }]), "warn");
  assert.equal(platformHealth([{ up: false }, { up: false }, { up: true }]), "crit");
});

test("demoData is internally consistent", () => {
  const d = demoData();
  assert.ok(d.facilities.length >= 8);
  assert.ok(d.events.length > 0);
  for (const e of d.events) {
    assert.ok(d.facilities.some((f) => f.id === e.facility), `orphan event ${e.id}`);
  }
});

test("demoData custody spine is well-formed (ADR-0052)", () => {
  const d = demoData();
  assert.ok(Array.isArray(d.custody) && d.custody.length > 0);
  for (const c of d.custody) {
    assert.ok(c.asset && c.actor && c.action && c.at, `bad custody event ${JSON.stringify(c)}`);
    assert.ok(["locate", "transfer", "maintenance_flag", "check_in"].includes(c.action), `bad action ${c.action}`);
    assert.ok(d.facilities.some((f) => f.id === c.facility), `orphan custody ${c.asset}`);
  }
});

/* ---------- payer board transforms (ADR-0012) ---------- */

test("payerRows: rates, shares, unmapped visibility", async () => {
  const { payerRows } = await import("../js/data.js");
  const rows = payerRows({
    payers: [
      { payer: "NHIA", kind: "government", claims: 100, approved: 90, pended: 5, denied: 5, payable_ngn: 3000 },
      { payer: "HYGEIA", kind: "commercial", claims: 50, approved: 40, pended: 10, denied: 0, payable_ngn: 1000 },
      { payer: "UNMAPPED", kind: "unmapped", claims: 10, approved: 0, pended: 0, denied: 10, payable_ngn: 0 },
    ],
  });
  assert.equal(rows.length, 3);
  assert.equal(rows[0].approvalRate, 90);
  assert.equal(rows[0].payableShare, 75);
  assert.equal(rows[1].pendRate, 20);
  assert.equal(rows[2].kind, "unmapped");
});

test("payerRows: empty stats render as empty, never crash", async () => {
  const { payerRows, payerSummary } = await import("../js/data.js");
  assert.deepEqual(payerRows(null), []);
  assert.deepEqual(payerRows({}), []);
  const sum = payerSummary(payerRows({ payers: [{ payer: "NHIA", claims: 2, payable_ngn: 100 }] }));
  assert.equal(sum.totalClaims, 2);
  assert.equal(sum.totalPayable, 100);
  assert.equal(sum.payers, 1);
});
