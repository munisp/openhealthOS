/* Node tests for the ADR-0033 KPI-plane transforms — the economics and
 * ML-health boards read one nightly document; these pin its shaping.
 * Run: node --test tests/ */
import { test } from "node:test";
import assert from "node:assert/strict";
import { marginRows, marginTotals, panelRows, panelSummary, denialSummary,
         catchmentRows, catchmentSummary, mlHealthRows, driftSummary,
         demoData } from "../js/data.js";

const PLANE = {
  generated_at: "2026-10-18T23:00:00Z",
  sections: {
    facility_economics: {
      status: "ok",
      margins: {
        tariff_year: 2025, encounters: 2,
        lines: [
          { code: "CS-EMG", name: "CS", count: 1, tariff_kobo: 7500000,
            margin_kobo: -100000, margin_pct: -1.3, in_tariff_schedule: true },
          { code: "CONS-GEN", name: "Consult", count: 1, tariff_kobo: 80000,
            margin_kobo: 20000, margin_pct: 25.0, in_tariff_schedule: true },
        ],
        unknown_codes: ["X-1"],
        totals: { revenue_kobo: 7580000, cost_kobo: 7660000, margin_kobo: -80000 },
      },
      panel: {
        capitation_kobo: 145000, panel_size: 3, encounters: 2,
        bands: [
          { band: "adult", enrollees: 2, capitation_kobo: 290000,
            care_cost_kobo: 100000, margin_kobo: 190000,
            margin_per_enrollee_kobo: 95000, encounters: 1 },
          { band: "walk-in", enrollees: 0, capitation_kobo: 0,
            care_cost_kobo: 50000, margin_kobo: -50000,
            margin_per_enrollee_kobo: null, encounters: 1 },
        ],
        total_margin_kobo: 140000, walk_in_cost_kobo: 50000,
      },
      denials: {
        claims: 4, denied: 2, preventable_denied_kobo: 30000,
        preventable_share_pct: 75.0,
        by_hmo: { NHIA: { submitted: 4, denied: 2, denial_rate_pct: 50.0 } },
      },
    },
    panel_risk: { status: "no-data" },
    catchments: {
      status: "ok", members: 3, unassigned: 1, over_15km_total: 1,
      facilities: [
        { facility: "F1", name: "Kawo", assigned: 2, mean_km: 3.1,
          p90_km: 4.0, over_15km: 0 },
        { facility: "F2", name: "Far", assigned: 1, mean_km: 20.0,
          p90_km: 20.0, over_15km: 1 },
      ],
    },
    ml_health: {
      status: "ok",
      models: [
        { model: "fraud-mlp", champion: "v7",
          metrics: { auc: 0.91 }, champion_age_days: 40 },
        { model: "claim-gcn", champion: "v3",
          metrics: { auc: 0.93 }, champion_age_days: 5 },
      ],
      drift: { verdict: "moderate", worst_psi: 0.18 },
    },
  },
};

test("marginRows shapes kobo to naira, worst first", () => {
  const rows = marginRows(PLANE);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].code, "CS-EMG");
  assert.equal(rows[0].marginNgn, -1000);
  assert.equal(rows[1].marginPct, 25.0);
});

test("marginTotals exposes unknown-code count and naira totals", () => {
  const t = marginTotals(PLANE);
  assert.equal(t.encounters, 2);
  assert.equal(t.marginNgn, -800);
  assert.equal(t.unknownCodes, 1);
});

test("panelRows keeps walk-in null per-enrollee", () => {
  const rows = panelRows(PLANE);
  const walk = rows.find((r) => r.band === "walk-in");
  assert.equal(walk.perEnrolleeNgn, null);
  assert.equal(walk.marginNgn, -500);
});

test("panelSummary surfaces walk-in leakage", () => {
  const s = panelSummary(PLANE);
  assert.equal(s.panelSize, 3);
  assert.equal(s.capitationNgn, 1450);
  assert.equal(s.walkInLeakNgn, 500);
  assert.equal(s.totalMarginNgn, 1400);
});

test("denialSummary sorts HMOs by denial rate", () => {
  const d = denialSummary(PLANE);
  assert.equal(d.preventableSharePct, 75.0);
  assert.equal(d.preventableNgn, 300);
  assert.equal(d.byHmo[0].hmo, "NHIA");
});

test("catchment rows and summary", () => {
  const rows = catchmentRows(PLANE);
  assert.equal(rows.length, 2);
  const s = catchmentSummary(PLANE);
  assert.equal(s.members, 3);
  assert.equal(s.over15km, 1);
});

test("mlHealthRows flags stale champions (>30d)", () => {
  const rows = mlHealthRows(PLANE);
  assert.equal(rows.find((r) => r.model === "fraud-mlp").stale, true);
  assert.equal(rows.find((r) => r.model === "claim-gcn").stale, false);
  assert.equal(rows[0].auc, 0.91);
});

test("driftSummary carries verdict and PSI", () => {
  const d = driftSummary(PLANE);
  assert.equal(d.verdict, "moderate");
  assert.equal(d.worstPsi, 0.18);
});

test("transforms tolerate missing / no-data sections", () => {
  const empty = { sections: {
    facility_economics: { status: "no-data" },
    catchments: { status: "no-data" },
    ml_health: { status: "no-data", models: [], drift: { verdict: "no-data" } },
  } };
  assert.deepEqual(marginRows(empty), []);
  assert.equal(marginTotals(empty), null);
  assert.deepEqual(panelRows(empty), []);
  assert.equal(panelSummary(empty), null);
  assert.equal(denialSummary(empty), null);
  assert.deepEqual(catchmentRows(empty), []);
  assert.equal(catchmentSummary(empty), null);
  assert.deepEqual(mlHealthRows(empty), []);
  assert.deepEqual(marginRows(undefined), []);
});

test("demoData carries a full kpi plane", () => {
  const demo = demoData();
  assert.ok(demo.kpi?.sections?.facility_economics?.margins?.lines.length > 0);
  assert.ok(mlHealthRows(demo.kpi).length >= 2);
  assert.equal(driftSummary(demo.kpi).verdict, "moderate");
});
