/* Clinical logic conformance — vitals danger bands, allergy gate,
 * cohorts, timeline. Run: node --test apps/pwa/tests/ */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  assessVitals, anyDanger, VITAL_FIELDS,
  FORMULARY, allergyConflict, ALLERGY_CLASSES,
  LAB_CATALOG, cohortsFor, buildTimeline, appointmentsForDay,
} from "../js/clinical.js";

test("adult normal vitals produce no flags", () => {
  const flags = assessVitals({ temp_c: 36.8, pulse: 72, resp_rate: 16, spo2: 98, sbp: 120, dbp: 80 }, { age_years: 30 });
  assert.deepEqual(flags, []);
  assert.equal(anyDanger(flags), false);
});

test("danger bands: hypoxia, hypotension, crisis BP, extreme temp", () => {
  for (const [v, code] of [
    [{ spo2: 88 }, "hypoxia"],
    [{ sbp: 80 }, "hypotension"],
    [{ sbp: 170 }, "bp_crisis"],
    [{ temp_c: 39.5 }, "temp_extreme"],
  ]) {
    const flags = assessVitals(v, { age_years: 40 });
    assert.equal(flags[0].code, code);
    assert.equal(flags[0].level, "danger");
    assert.ok(anyDanger(flags));
  }
});

test("warn bands sit below danger bands", () => {
  assert.equal(assessVitals({ temp_c: 37.8 }, { age_years: 40 })[0].code, "fever");
  assert.equal(assessVitals({ spo2: 92 }, { age_years: 40 })[0].level, "warn");
  assert.equal(assessVitals({ sbp: 145 }, { age_years: 40 })[0].code, "bp_high");
  assert.ok(!anyDanger(assessVitals({ sbp: 145 }, { age_years: 40 })));
});

test("IMCI fast-breathing thresholds follow age", () => {
  // infant <2mo: 60; 2-12mo: 50; 1-5y: 40; adult: 30
  assert.equal(assessVitals({ resp_rate: 55 }, { age_days: 30 })[0].code, "rr_high"); // warn below 60
  assert.equal(assessVitals({ resp_rate: 45 }, { age_days: 30 })[0], undefined);
  assert.equal(assessVitals({ resp_rate: 61 }, { age_days: 30 })[0].code, "fast_breathing");
  assert.equal(assessVitals({ resp_rate: 51 }, { age_days: 200 })[0].code, "fast_breathing");
  assert.equal(assessVitals({ resp_rate: 41 }, { age_years: 3 })[0].code, "fast_breathing");
  assert.equal(assessVitals({ resp_rate: 31 }, { age_years: 30 })[0].code, "fast_breathing");
  assert.equal(assessVitals({ resp_rate: 25 }, { age_years: 30 })[0].code, "rr_high");
  assert.equal(assessVitals({ resp_rate: 16 }, { age_years: 30 })[0], undefined);
});

test("MUAC malnutrition bands", () => {
  assert.equal(assessVitals({ muac_cm: 11.0 }, { age_years: 2 })[0].code, "sam");
  assert.equal(assessVitals({ muac_cm: 12.0 }, { age_years: 2 })[0].code, "mam");
  assert.equal(assessVitals({ muac_cm: 13.0 }, { age_years: 2 })[0], undefined);
});

test("blank and non-numeric inputs never flag", () => {
  assert.deepEqual(assessVitals({ temp_c: "", pulse: null, spo2: "abc" }, {}), []);
});

test("formulary mirrors the pharmacy tracer list shape", () => {
  assert.equal(FORMULARY.length, 10);
  for (const d of FORMULARY) {
    assert.ok(d.code && d.name);
    assert.ok(ALLERGY_CLASSES.includes(d.allergy_class) || d.allergy_class === "");
  }
});

test("allergy gate blocks matching class only", () => {
  assert.equal(allergyConflict("AMOX500", ["penicillin"]).allergy, "penicillin");
  assert.equal(allergyConflict("IBU400", ["nsaid"]).allergy, "nsaid");
  assert.equal(allergyConflict("COTRIM480", ["sulfa"]).allergy, "sulfa");
  assert.equal(allergyConflict("AMOX500", ["sulfa"]), null);
  assert.equal(allergyConflict("PARA500", ["penicillin", "nsaid", "sulfa"]), null);
  assert.equal(allergyConflict("AMOX500", []), null);
});

test("lab catalog codes are unique with specimen + tat", () => {
  const codes = LAB_CATALOG.map((l) => l.code);
  assert.equal(new Set(codes).size, codes.length);
  for (const l of LAB_CATALOG) assert.ok(l.specimen && l.tat_h > 0);
});

test("cohorts derive from facts; clinician tags pass through", () => {
  assert.deepEqual(cohortsFor({ pregnant: true }), ["ANC"]);
  assert.deepEqual(cohortsFor({ age_days: 100 }), ["EPI"]);
  assert.deepEqual(cohortsFor({ pregnant: true, age_days: 100 }), ["ANC", "EPI"]);
  assert.deepEqual(cohortsFor({ cohorts: ["HIV", "NCD"] }), ["HIV", "NCD"]);
  assert.deepEqual(cohortsFor({}), []);
});

test("timeline merges types newest-first for one patient", () => {
  const docs = [
    { type: "Encounter", patient_id: "p1", ts: 100 },
    { type: "Vitals", patient_id: "p1", ts: 300, flags: [] },
    { type: "LabOrder", patient_id: "p1", ts: 200, test_code: "HB" },
    { type: "Prescription", patient_id: "p1", ts: 400, drug_code: "AL8020" },
    { type: "Encounter", patient_id: "p2", ts: 500 }, // other patient
  ];
  const tl = buildTimeline("p1", docs);
  assert.deepEqual(tl.map((r) => r.kind), ["med", "vitals", "lab", "encounter"]);
});

test("appointmentsForDay filters to the day and sorts", () => {
  const dayStart = new Date(2026, 9, 18).getTime();
  const docs = [
    { type: "Appointment", when: dayStart + 3600000, status: "scheduled" },
    { type: "Appointment", when: dayStart + 7200000, status: "scheduled" },
    { type: "Appointment", when: dayStart + 90000000, status: "scheduled" }, // next day
    { type: "Appointment", when: dayStart + 1000, status: "cancelled" },
  ];
  const q = appointmentsForDay(docs, dayStart, dayStart + 86400000);
  assert.equal(q.length, 2);
  assert.ok(q[0].when < q[1].when);
});

test("vital field definitions are input-safe", () => {
  for (const f of VITAL_FIELDS) assert.ok(f.min < f.max && f.unit);
});
