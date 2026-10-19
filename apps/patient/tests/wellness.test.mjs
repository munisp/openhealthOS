/* Wellness tab transforms (ADR-0035) — run: node --test tests/ */
import { test } from "node:test";
import assert from "node:assert/strict";
import { wellnessKpis, planItems, riskBadge, logEntry, makeLogRef } from "../js/logic.js";

test("wellnessKpis shapes the summary", () => {
  const k = wellnessKpis({
    steps_today: 6200, avg_daily_steps_14d: 4100.4, workouts_7d: 3,
    nutrition_days_7d: 2, avg_sleep_hours_7d: 6.5,
    latest_weight: { value: 81.5, unit: "kg" },
    latest_heart_rate: { value: 72 },
  });
  const byKey = Object.fromEntries(k.map((x) => [x.key, x]));
  assert.equal(byKey.steps.value, "6200");
  assert.equal(byKey.steps.kind, "ok");
  assert.equal(byKey.steps14.value, "4100");
  assert.equal(byKey.steps14.kind, "warn");   // below 5000 target
  assert.equal(byKey.workouts.kind, "ok");     // >=3/wk
  assert.equal(byKey.sleep.value, "6.5h");
  assert.equal(byKey.sleep.kind, "warn");      // under 7h
  assert.equal(byKey.weight.value, "81.5 kg");
  assert.equal(byKey.hr.value, "72");
});

test("wellnessKpis tolerates empty summary", () => {
  assert.deepEqual(wellnessKpis(null), []);
  const k = wellnessKpis({ steps_today: 0 });
  assert.equal(k[0].value, "0");
});

test("planItems surfaces overdue gaps, recs and honest risk label", () => {
  const plan = planItems({
    overdue: [{ label: "HbA1c (diabetes control)", last_done: "2026-02-01", months_since: 8.4 }],
    recommendations: ["Try the nutrition log"],
    risk: { cvd_band: "high", diabetes_band: "moderate", model: "heuristic-1" },
    avg_daily_steps_14d: 2300,
  });
  assert.equal(plan.overdue[0].note, "last done 8 months ago");
  assert.equal(plan.risk.cvd, "high");
  assert.equal(plan.risk.model, "heuristic-1");
  assert.equal(plan.recs[0], "Try the nutrition log");
});

test("planItems handles never-done and null plan", () => {
  const plan = planItems({ overdue: [{ label: "Blood pressure check", last_done: null, months_since: null }] });
  assert.equal(plan.overdue[0].note, "never recorded");
  assert.equal(planItems(null), null);
});

test("riskBadge maps bands", () => {
  assert.equal(riskBadge("high"), "crit");
  assert.equal(riskBadge("moderate"), "warn");
  assert.equal(riskBadge("low"), "ok");
});

test("logEntry refs are deterministic — offline replays are exactly-once", () => {
  const a = logEntry("p1", "nutrition", { text: "rice", ts: "2026-10-19T12:00:00Z" });
  const b = logEntry("p1", "nutrition", { text: "rice", ts: "2026-10-19T12:00:00Z" });
  assert.equal(a.ref, b.ref);
  assert.equal(a.type, "nutrition");
  const c = logEntry("p1", "nutrition", { text: "beans", ts: "2026-10-19T12:00:00Z" });
  assert.notEqual(a.ref, c.ref);
  assert.equal(makeLogRef("p1", "x", "t", "y"), makeLogRef("p1", "x", "t", "y"));
});
