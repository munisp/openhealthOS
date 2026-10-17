/* Clinical logic — pure functions, no DOM, no IndexedDB. Runs in the
 * browser and under node --test (CI). These are the rules the EMR views
 * render and the guards the pharmacy side re-checks server-side; the edge
 * must never silently skip a danger flag or an allergy conflict.
 *
 * Sources for ranges: WHO IMCI fast-breathing cutoffs, WHO SAM/MAM MUAC
 * thresholds, standard adult hypotension/hypertension urgency bands.
 */

/* ---------- vitals danger assessment ---------- */
export const VITAL_FIELDS = [
  { key: "temp_c", unit: "°C", min: 30, max: 43, step: "0.1" },
  { key: "pulse", unit: "bpm", min: 20, max: 250, step: "1" },
  { key: "resp_rate", unit: "/min", min: 5, max: 80, step: "1" },
  { key: "spo2", unit: "%", min: 50, max: 100, step: "1" },
  { key: "sbp", unit: "mmHg", min: 40, max: 260, step: "1" },
  { key: "dbp", unit: "mmHg", min: 20, max: 160, step: "1" },
  { key: "weight_kg", unit: "kg", min: 0.5, max: 250, step: "0.1" },
  { key: "muac_cm", unit: "cm", min: 5, max: 40, step: "0.1" },
];

/* IMCI fast-breathing thresholds by age. */
function respDangerThreshold(patient) {
  const days = patient?.age_days;
  const years = patient?.age_years ?? 99;
  if (days != null && days < 60) return 60;      // <2 months
  if (days != null && days < 365) return 50;     // 2–12 months
  if (years <= 5) return 40;                     // 1–5 years
  return 30;                                     // older child / adult
}

export function assessVitals(v, patient) {
  /* v: {temp_c?, pulse?, resp_rate?, spo2?, sbp?, dbp?, muac_cm?}
   * Returns [{key, level: "danger"|"warn", code}] — empty means all normal. */
  const flags = [];
  const num = (x) => (x === "" || x == null || Number.isNaN(Number(x)) ? null : Number(x));
  const temp = num(v.temp_c), pulse = num(v.pulse), rr = num(v.resp_rate);
  const spo2 = num(v.spo2), sbp = num(v.sbp), dbp = num(v.dbp), muac = num(v.muac_cm);
  if (temp != null) {
    if (temp >= 39.0 || temp < 35.0) flags.push({ key: "temp_c", level: "danger", code: "temp_extreme" });
    else if (temp >= 37.5) flags.push({ key: "temp_c", level: "warn", code: "fever" });
  }
  if (pulse != null) {
    if (pulse < 50 || pulse >= 130) flags.push({ key: "pulse", level: "danger", code: "pulse_extreme" });
    else if (pulse > 100) flags.push({ key: "pulse", level: "warn", code: "tachycardia" });
  }
  if (rr != null) {
    const limit = respDangerThreshold(patient);
    if (rr >= limit) flags.push({ key: "resp_rate", level: "danger", code: "fast_breathing" });
    else if (rr >= limit - 10) flags.push({ key: "resp_rate", level: "warn", code: "rr_high" });
  }
  if (spo2 != null) {
    if (spo2 < 90) flags.push({ key: "spo2", level: "danger", code: "hypoxia" });
    else if (spo2 < 94) flags.push({ key: "spo2", level: "warn", code: "spo2_low" });
  }
  if (sbp != null) {
    if (sbp < 90) flags.push({ key: "sbp", level: "danger", code: "hypotension" });
    else if (sbp >= 160) flags.push({ key: "sbp", level: "danger", code: "bp_crisis" });
    else if (sbp >= 140) flags.push({ key: "sbp", level: "warn", code: "bp_high" });
  }
  if (dbp != null && dbp >= 110) flags.push({ key: "dbp", level: "danger", code: "dbp_crisis" });
  if (muac != null) {
    if (muac < 11.5) flags.push({ key: "muac_cm", level: "danger", code: "sam" });
    else if (muac < 12.5) flags.push({ key: "muac_cm", level: "warn", code: "mam" });
  }
  return flags;
}

export function anyDanger(flags) {
  return flags.some((f) => f.level === "danger");
}

/* ---------- formulary (mirrors services/pharmacy-service tracer list) ---
 * allergy_class is the dispensing-gate key: a matching entry in the
 * patient's allergies blocks the prescription at review AND at the
 * pharmacy counter. */
export const FORMULARY = [
  { code: "AMOX500", name: "Amoxicillin 500mg cap", allergy_class: "penicillin", therapy_class: "antibiotic" },
  { code: "AL8020", name: "Artemether/Lumefantrine 80/20 tab", allergy_class: "", therapy_class: "antimalarial" },
  { code: "PARA500", name: "Paracetamol 500mg tab", allergy_class: "", therapy_class: "analgesic" },
  { code: "IBU400", name: "Ibuprofen 400mg tab", allergy_class: "nsaid", therapy_class: "analgesic" },
  { code: "ORS", name: "ORS sachet", allergy_class: "", therapy_class: "rehydration" },
  { code: "ZINC20", name: "Zinc 20mg tab", allergy_class: "", therapy_class: "supplement" },
  { code: "AMLO5", name: "Amlodipine 5mg tab", allergy_class: "", therapy_class: "antihypertensive" },
  { code: "MET500", name: "Metformin 500mg tab", allergy_class: "", therapy_class: "antidiabetic" },
  { code: "COTRIM480", name: "Co-trimoxazole 480mg tab", allergy_class: "sulfa", therapy_class: "antibiotic" },
  { code: "OXYTOCIN10", name: "Oxytocin 10IU inj", allergy_class: "", therapy_class: "uterotonic" },
];

export function allergyConflict(drugCode, allergies) {
  const drug = FORMULARY.find((d) => d.code === drugCode);
  if (!drug || !drug.allergy_class) return null;
  const hit = (allergies || []).find((a) => a === drug.allergy_class);
  return hit ? { drug: drug.name, allergy: hit } : null;
}

export const ALLERGY_CLASSES = ["penicillin", "nsaid", "sulfa"];

/* ---------- lab order catalog (CPOE-lite) -------------------------- */
export const LAB_CATALOG = [
  { code: "MAL-RDT", name: "Malaria RDT", specimen: "blood", tat_h: 0.5 },
  { code: "MAL-MICRO", name: "Malaria microscopy", specimen: "blood", tat_h: 4 },
  { code: "HB", name: "Haemoglobin", specimen: "blood", tat_h: 2 },
  { code: "HIV-RDT", name: "HIV rapid test", specimen: "blood", tat_h: 0.5 },
  { code: "GLUCOSE", name: "Blood glucose", specimen: "blood", tat_h: 1 },
  { code: "URINALYSIS", name: "Urinalysis", specimen: "urine", tat_h: 2 },
  { code: "HCG", name: "Pregnancy test", specimen: "urine", tat_h: 0.5 },
  { code: "SPUTUM-AFB", name: "Sputum AFB (TB)", specimen: "sputum", tat_h: 24 },
  { code: "CD4", name: "CD4 count", specimen: "blood", tat_h: 48 },
  { code: "VL", name: "HIV viral load", specimen: "blood", tat_h: 168 },
];

/* ---------- cohorts -------------------------------------------------
 * Derived from patient facts; ANC/EPI derive automatically, HIV/TB/NCD
 * are clinician-set flags on the chart. Cohorts drive task generation
 * (ANC schedule, NPI immunizations) and state program dashboards. */
export const COHORTS = ["ANC", "EPI", "HIV", "TB", "NCD"];

export function cohortsFor(patient) {
  const out = [];
  if (patient.pregnant === true) out.push("ANC");
  if (patient.age_days != null && patient.age_days < 548) out.push("EPI");
  for (const c of ["HIV", "TB", "NCD"]) {
    if ((patient.cohorts || []).includes(c)) out.push(c);
  }
  return out;
}

/* ---------- chart timeline ------------------------------------------ */
/* Merge encounters, vitals, lab orders/results, prescriptions and
 * appointments for one patient into a single newest-first timeline. */
export function buildTimeline(patientId, docs) {
  const rows = [];
  for (const d of docs) {
    if (d.type === "Encounter" && d.patient_id === patientId)
      rows.push({ kind: "encounter", ts: d.ts || 0, ref: d });
    if (d.type === "Vitals" && d.patient_id === patientId)
      rows.push({ kind: "vitals", ts: d.ts || 0, ref: d });
    if (d.type === "LabOrder" && d.patient_id === patientId)
      rows.push({ kind: "lab", ts: d.ts || 0, ref: d });
    if (d.type === "Prescription" && d.patient_id === patientId)
      rows.push({ kind: "med", ts: d.ts || 0, ref: d });
    if (d.type === "Appointment" && d.patient_id === patientId)
      rows.push({ kind: "appointment", ts: d.when || d.ts || 0, ref: d });
  }
  rows.sort((a, b) => b.ts - a.ts);
  return rows;
}

export function appointmentsForDay(docs, dayStart, dayEnd) {
  return docs
    .filter((d) => d.type === "Appointment" && d.when >= dayStart && d.when < dayEnd && d.status !== "cancelled")
    .sort((a, b) => a.when - b.when);
}
