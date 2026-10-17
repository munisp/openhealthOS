/* EMR views — patient chart, vitals with danger flags, CPOE-lite lab
 * orders, prescribing with the allergy gate, appointments and today's
 * queue. Everything writes to the local replica first and queues a domain
 * event; care never waits on connectivity.
 */

import { t } from "./i18n.js";
import { el, badge } from "./views.js";
import {
  VITAL_FIELDS, assessVitals, anyDanger,
  FORMULARY, allergyConflict, ALLERGY_CLASSES,
  LAB_CATALOG, COHORTS, cohortsFor,
} from "./clinical.js";

function field(label, input) {
  return el("label", { class: "field" }, el("span", { class: "field-label" }, label), input);
}

function chip(text, kind, onclick) {
  return el("button", { class: `chip chip-${kind}`, type: "button", onclick }, text);
}

/* ---------- patient header (shared by chart + clinical forms) -------- */
function patientHeader(ctx, p) {
  const cohorts = cohortsFor(p);
  const allergies = p.allergies || [];
  return el("div", { class: "card chart-head" },
    el("div", { class: "patient-name chart-name" }, p.name),
    el("div", { class: "task-sub" },
      `${p.village ?? ""} · ${t("age")} ${p.age_years ?? "—"}${p.age_days != null ? ` (${p.age_days} ${t("days")})` : ""}`),
    el("div", { class: "chip-row" },
      ...cohorts.map((c) => badge(c, "ok")),
      ...allergies.map((a) => badge(`⚠ ${t("allergy_" + a)}`, "danger")),
      !cohorts.length && !allergies.length ? el("span", { class: "task-sub" }, t("no_cohorts")) : null));
}

/* ---------- chart ---------------------------------------------------- */
export function ChartView(ctx) {
  const p = ctx.activePatient;
  if (!p) return el("div", { class: "view" }, el("div", { class: "empty" }, t("no_patient")));
  const wrap = el("div", { class: "view" },
    patientHeader(ctx, p),
    el("div", { class: "chart-actions" },
      actionBtn(t("new_encounter"), () => ctx.navigate("#/encounter")),
      actionBtn(t("vitals"), () => ctx.openSheet(VitalsForm(ctx, p))),
      actionBtn(t("lab_order"), () => ctx.openSheet(LabOrderForm(ctx, p))),
      actionBtn(t("prescribe"), () => ctx.openSheet(PrescribeForm(ctx, p))),
      actionBtn(t("appointment"), () => ctx.openSheet(AppointmentForm(ctx, p))),
      actionBtn(t("edit_cohorts"), () => ctx.openSheet(CohortForm(ctx, p)))),
    el("h2", { class: "section-label" }, t("timeline")));

  const tl = ctx.timeline || [];
  if (!tl.length) wrap.append(el("div", { class: "empty" }, t("no_history")));
  for (const row of tl) wrap.append(timelineRow(row));
  return wrap;
}

function actionBtn(label, onclick) {
  return el("button", { class: "btn btn-outline btn-action", type: "button", onclick }, label);
}

function timelineRow(row) {
  const d = row.ref;
  const when = new Date(row.ts).toLocaleString();
  let title = "", detail = "", kind = "ok";
  if (row.kind === "encounter") {
    title = t("encounter");
    detail = (d.danger_signs || []).join(", ") || t("routine");
    kind = d.disposition === "REFER-EMERGENCY" ? "danger" : "ok";
  } else if (row.kind === "vitals") {
    title = t("vitals");
    const bits = [];
    if (d.temp_c != null) bits.push(`${d.temp_c}°C`);
    if (d.sbp != null) bits.push(`BP ${d.sbp}/${d.dbp ?? "—"}`);
    if (d.spo2 != null) bits.push(`SpO2 ${d.spo2}%`);
    detail = bits.join(" · ");
    kind = (d.flags || []).some((f) => f.level === "danger") ? "danger"
      : (d.flags || []).length ? "warn" : "ok";
  } else if (row.kind === "lab") {
    title = `${t("lab_order")}: ${d.test_name || d.test_code}`;
    detail = d.result ? `${t("result")}: ${d.result}` : t("awaiting_result");
    kind = d.result ? "ok" : "warn";
  } else if (row.kind === "med") {
    title = `${t("prescribe")}: ${d.drug_name || d.drug_code} ×${d.quantity}`;
    detail = `${d.dose} · ${t("rx_status_" + (d.status || "sent"))}`;
    kind = d.status === "rejected" ? "danger" : "ok";
  } else if (row.kind === "appointment") {
    title = `${t("appointment")}: ${new Date(d.when).toLocaleDateString()}`;
    detail = d.reason || "";
    kind = d.status === "cancelled" ? "danger" : "ok";
  }
  return el("div", { class: "card tl-row" },
    el("div", { class: `tl-dot tl-${kind}` }),
    el("div", { class: "tl-main" },
      el("div", { class: "task-title" }, title),
      detail ? el("div", { class: "task-sub" }, detail) : null,
      el("div", { class: "tl-when" }, when)));
}

/* ---------- vitals --------------------------------------------------- */
export function VitalsForm(ctx, p) {
  const v = {};
  const flagsBox = el("div", { class: "vitals-flags" });
  const inputs = {};
  function reassess() {
    const flags = assessVitals(v, p);
    flagsBox.innerHTML = "";
    for (const f of flags) {
      flagsBox.append(el("div", { class: `vflag vflag-${f.level}` },
        `${t("vital_" + f.key)} — ${t("flag_" + f.code)}`));
    }
    return flags;
  }
  const grid = el("div", { class: "vitals-grid" });
  for (const vf of VITAL_FIELDS) {
    const input = el("input", {
      class: "input", type: "number", inputmode: "decimal",
      min: String(vf.min), max: String(vf.max), step: vf.step,
      oninput: (e) => { v[vf.key] = e.target.value === "" ? null : Number(e.target.value); reassess(); },
    });
    inputs[vf.key] = input;
    grid.append(field(`${t("vital_" + vf.key)} (${vf.unit})`, input));
  }
  return el("div", { class: "sheet" },
    el("div", { class: "sheet-card" },
      el("h2", {}, `${t("vitals")} — ${p.name}`),
      grid, flagsBox,
      el("div", { class: "sheet-actions" },
        el("button", { class: "btn btn-outline", onclick: () => ctx.closeSheet() }, t("cancel")),
        el("button", {
          class: "btn btn-primary",
          onclick: async () => {
            const flags = assessVitals(v, p);
            await ctx.recordVitals(p.id, v, flags);
            ctx.closeSheet();
            ctx.toast(anyDanger(flags) ? t("vitals_saved_danger") : t("vitals_saved"));
            ctx.rerender();
          },
        }, t("save")))));
}

/* ---------- lab orders (CPOE-lite) ----------------------------------- */
export function LabOrderForm(ctx, p) {
  const picked = new Set();
  return el("div", { class: "sheet" },
    el("div", { class: "sheet-card" },
      el("h2", {}, `${t("lab_order")} — ${p.name}`),
      el("div", { class: "chip-col" },
        ...LAB_CATALOG.map((test) => chip(
          `${test.name} (${test.specimen})`, "toggle",
          (e) => {
            if (picked.has(test.code)) { picked.delete(test.code); e.target.classList.remove("chip-on"); }
            else { picked.add(test.code); e.target.classList.add("chip-on"); }
          }))),
      el("div", { class: "sheet-actions" },
        el("button", { class: "btn btn-outline", onclick: () => ctx.closeSheet() }, t("cancel")),
        el("button", {
          class: "btn btn-primary",
          onclick: async () => {
            if (!picked.size) return;
            await ctx.orderLabs(p.id, [...picked]);
            ctx.closeSheet();
            ctx.toast(t("lab_ordered"));
            ctx.rerender();
          },
        }, t("order")))));
}

/* ---------- prescribe (allergy-gated) -------------------------------- */
export function PrescribeForm(ctx, p) {
  const f = { drug_code: FORMULARY[0].code, dose: "", quantity: "" };
  const warn = el("div", { class: "vitals-flags" });
  const allergies = p.allergies || [];
  function recheck() {
    warn.innerHTML = "";
    const conflict = allergyConflict(f.drug_code, allergies);
    if (conflict) {
      warn.append(el("div", { class: "vflag vflag-danger" },
        `${t("allergy_blocked")}: ${conflict.drug} — ${t("allergy_" + conflict.allergy)}`));
    }
    return conflict;
  }
  const sel = el("select", {
    class: "input",
    onchange: (e) => { f.drug_code = e.target.value; recheck(); },
  });
  for (const d of FORMULARY) sel.append(el("option", { value: d.code }, d.name));
  const form = el("div", { class: "sheet" },
    el("div", { class: "sheet-card" },
      el("h2", {}, `${t("prescribe")} — ${p.name}`),
      allergies.length
        ? el("div", { class: "chip-row" }, ...allergies.map((a) => badge(`⚠ ${t("allergy_" + a)}`, "danger")))
        : null,
      field(t("drug"), sel),
      field(t("dose"), el("input", { class: "input", placeholder: "500mg bd × 5d", oninput: (e) => (f.dose = e.target.value) })),
      field(t("quantity"), el("input", { class: "input", type: "number", min: "1", inputmode: "numeric", oninput: (e) => (f.quantity = e.target.value) })),
      warn,
      el("div", { class: "sheet-actions" },
        el("button", { class: "btn btn-outline", onclick: () => ctx.closeSheet() }, t("cancel")),
        el("button", {
          class: "btn btn-primary",
          onclick: async (e) => {
            if (recheck()) { ctx.toast(t("allergy_blocked_toast")); return; }
            const qty = Number(f.quantity);
            if (!f.dose.trim() || !qty || qty < 1) return;
            e.target.disabled = true;
            await ctx.prescribe(p.id, f.drug_code, f.dose.trim(), qty);
            ctx.closeSheet();
            ctx.toast(t("rx_sent"));
            ctx.rerender();
          },
        }, t("send_to_pharmacy")))));
  queueMicrotask(recheck);
  return form;
}

/* ---------- appointments --------------------------------------------- */
export function AppointmentForm(ctx, p) {
  const f = { when: "", reason: "" };
  return el("div", { class: "sheet" },
    el("div", { class: "sheet-card" },
      el("h2", {}, `${t("appointment")} — ${p.name}`),
      field(t("date_time"), el("input", { class: "input", type: "datetime-local", oninput: (e) => (f.when = e.target.value) })),
      field(t("reason"), el("input", { class: "input", oninput: (e) => (f.reason = e.target.value) })),
      el("div", { class: "sheet-actions" },
        el("button", { class: "btn btn-outline", onclick: () => ctx.closeSheet() }, t("cancel")),
        el("button", {
          class: "btn btn-primary",
          onclick: async () => {
            const when = new Date(f.when).getTime();
            if (!when) return;
            await ctx.scheduleAppointment(p.id, when, f.reason.trim());
            ctx.closeSheet();
            ctx.toast(t("appointment_saved"));
            ctx.rerender();
          },
        }, t("save")))));
}

/* ---------- cohorts + allergies -------------------------------------- */
export function CohortForm(ctx, p) {
  const set = new Set(p.cohorts || []);
  const allergies = new Set(p.allergies || []);
  return el("div", { class: "sheet" },
    el("div", { class: "sheet-card" },
      el("h2", {}, `${t("edit_cohorts")} — ${p.name}`),
      el("div", { class: "field-label" }, t("cohorts")),
      el("div", { class: "chip-col" },
        ...["HIV", "TB", "NCD"].map((c) => {
          const on = set.has(c);
          const b = chip(c, "toggle" + (on ? " chip-on" : ""), (e) => {
            if (set.has(c)) { set.delete(c); e.target.classList.remove("chip-on"); }
            else { set.add(c); e.target.classList.add("chip-on"); }
          });
          if (on) b.classList.add("chip-on");
          return b;
        })),
      el("div", { class: "field-label" }, t("allergies")),
      el("div", { class: "chip-col" },
        ...ALLERGY_CLASSES.map((a) => {
          const on = allergies.has(a);
          const b = chip(t("allergy_" + a), "toggle", (e) => {
            if (allergies.has(a)) { allergies.delete(a); e.target.classList.remove("chip-on"); }
            else { allergies.add(a); e.target.classList.add("chip-on"); }
          });
          if (on) b.classList.add("chip-on");
          return b;
        })),
      el("div", { class: "sheet-actions" },
        el("button", { class: "btn btn-outline", onclick: () => ctx.closeSheet() }, t("cancel")),
        el("button", {
          class: "btn btn-primary",
          onclick: async () => {
            await ctx.setCohorts(p.id, [...set], [...allergies]);
            ctx.closeSheet();
            ctx.toast(t("saved"));
            ctx.rerender();
          },
        }, t("save")))));
}

/* ---------- today's queue -------------------------------------------- */
export function QueueView(ctx) {
  const appts = ctx.queue || [];
  const wrap = el("div", { class: "view" },
    el("h2", { class: "section-label" }, t("todays_queue")));
  if (!appts.length) wrap.append(el("div", { class: "empty" }, t("queue_empty")));
  for (const a of appts) {
    wrap.append(el("div", { class: "card task-card" },
      el("div", { class: "task-main" },
        el("div", { class: "task-title" }, a.patient_name || a.patient_id),
        el("div", { class: "task-sub" },
          `${new Date(a.when).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · ${a.reason || t("follow_up")}`)),
      el("button", {
        class: "btn btn-primary btn-done",
        onclick: () => ctx.startVisit(a.patient_id),
      }, t("start_visit"))));
  }
  return wrap;
}
