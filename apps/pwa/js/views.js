/* Views — task-typed, protocol-guided screens for nurses and CHWs.
 * Large touch targets (min 48px), high contrast for sunlight, one primary
 * action per screen. Every mutation writes locally first and queues a
 * domain event — the UI never waits on a network.
 */

import { t } from "./i18n.js";
import { materialize } from "./crdt.js";

/* ---------- tiny DOM helper ---------- */
export function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "onclick") n.addEventListener("click", v);
    else if (k.startsWith("data-")) n.setAttribute(k, v);
    else n[k] = v;
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    n.append(c.nodeType ? c : document.createTextNode(c));
  }
  return n;
}

export function badge(text, kind) {
  return el("span", { class: `badge badge-${kind}` }, text);
}

/* ---------- task generation (mirrors packages/chw-tasks) ---------- */
const ANC_WEEKS = [12, 20, 26, 30, 34, 36, 38, 40];
const NPI_DAYS = [
  [0, "BCG + OPV0 + HepB"], [42, "Penta1 + OPV1 + PCV1"],
  [70, "Penta2 + OPV2 + PCV2"], [98, "Penta3 + OPV3 + PCV3"],
  [274, "Measles1 + Yellow fever"], [456, "Measles2"],
];

export function generateTasksFor(patient) {
  /* patient: materialized doc. Returns task field-sets to apply. */
  const tasks = [];
  const now = Date.now();
  if (patient.pregnant === true) {
    ANC_WEEKS.forEach((wk, i) => {
      tasks.push({
        kind: "anc", label: `${t("anc_visit")} ${i + 1}/8`,
        due: now + wk * 7 * 86400000, priority: wk >= 34 ? "high" : "med",
      });
    });
  }
  if (patient.age_days != null && patient.age_days < 548) {
    NPI_DAYS.filter(([d]) => d >= patient.age_days).forEach(([d, label]) => {
      tasks.push({
        kind: "immunization", label: `${t("immunization")}: ${label}`,
        due: now + (d - patient.age_days) * 86400000, priority: "med",
      });
    });
  }
  return tasks;
}

export const DANGER_SIGNS = [
  "vaginal_bleeding", "convulsions", "high_fever", "severe_headache",
  "not_breathing_well", "unable_to_drink", "lethargy", "swelling_face_hands",
];

/* ---------- views ---------- */

export function TasksView(ctx) {
  const wrap = el("div", { class: "view" });
  const tasks = ctx.tasks; // materialized + sorted
  const groups = [
    ["overdue", tasks.filter((x) => x.due < Date.now() - 86400000)],
    ["due_today", tasks.filter((x) => x.due >= Date.now() - 86400000 && x.due <= Date.now() + 86400000)],
    ["upcoming", tasks.filter((x) => x.due > Date.now() + 86400000)],
  ];
  let any = false;
  for (const [label, list] of groups) {
    if (!list.length) continue;
    any = true;
    wrap.append(el("h2", { class: "section-label" }, t(label)));
    for (const task of list) {
      any = true;
      wrap.append(el("div", { class: "card task-card" },
        el("div", { class: "task-main" },
          el("div", { class: "task-title" }, task.label),
          el("div", { class: "task-sub" },
            `${task.patient_name ?? ""} · ${task.village ?? ""} · ${new Date(task.due).toLocaleDateString()}`)),
        badge(t("priority_" + (task.priority === "high" ? "high" : task.priority === "med" ? "med" : "low")),
          task.priority === "high" ? "danger" : task.priority === "med" ? "warn" : "ok"),
        el("button", {
          class: "btn btn-primary btn-done",
          onclick: async () => { await ctx.completeTask(task.id); ctx.toast(t("task_done_toast")); ctx.rerender(); },
        }, t("done"))));
    }
  }
  if (!any) wrap.append(el("div", { class: "empty" }, t("no_tasks")));
  return wrap;
}

export function PatientsView(ctx) {
  const wrap = el("div", { class: "view" });
  const bar = el("input", {
    class: "input search", placeholder: t("search"),
    oninput: (e) => { ctx.query = e.target.value.toLowerCase(); ctx.rerender(); },
    value: ctx.query || "",
  });
  wrap.append(bar);
  const list = ctx.patients.filter((p) =>
    !ctx.query || (p.name || "").toLowerCase().includes(ctx.query) ||
    (p.village || "").toLowerCase().includes(ctx.query));
  for (const p of list) {
    wrap.append(el("div", { class: "card patient-card", onclick: () => ctx.openChart(p.id) },
      el("div", { class: "patient-main" },
        el("div", { class: "patient-name" }, p.name),
        el("div", { class: "task-sub" },
          `${p.village ?? ""} · ${t("age")} ${p.age_years ?? "—"}${p.pregnant ? " · " + t("pregnant") : ""}`)),
      ...(p.allergies || []).map((a) => badge("⚠", "danger")),
      p.pregnant ? badge(t("pregnant"), "warn") : null,
      (p.immunizations || []).length ? badge(`${(p.immunizations || []).length} 💉`, "ok") : null));
  }
  wrap.append(el("button", { class: "fab", onclick: () => ctx.openRegister() }, "+"));
  return wrap;
}

export function RegisterForm(ctx) {
  const f = { name: "", age_years: "", village: "", phone: "", pregnant: false, newborn: false };
  const form = el("div", { class: "sheet" },
    el("div", { class: "sheet-card" },
      el("h2", {}, t("register_patient")),
      field(t("name"), el("input", { class: "input", required: true, oninput: (e) => (f.name = e.target.value) })),
      field(t("age"), el("input", { class: "input", type: "number", min: "0", max: "120", oninput: (e) => (f.age_years = e.target.value) })),
      field(t("village"), el("input", { class: "input", oninput: (e) => (f.village = e.target.value) })),
      field(t("phone"), el("input", { class: "input", type: "tel", inputmode: "tel", oninput: (e) => (f.phone = e.target.value) })),
      el("label", { class: "checkrow" },
        el("input", { type: "checkbox", onchange: (e) => (f.pregnant = e.target.checked) }),
        el("span", {}, t("pregnant"))),
      el("div", { class: "sheet-actions" },
        el("button", { class: "btn btn-outline", onclick: () => ctx.closeSheet() }, t("cancel")),
        el("button", {
          class: "btn btn-primary",
          onclick: async () => {
            if (!f.name.trim()) return;
            await ctx.registerPatient(f);
            ctx.closeSheet();
            ctx.toast(t("patient_saved"));
            ctx.rerender();
          },
        }, t("save")))));
  return form;
}

function field(label, input) {
  return el("label", { class: "field" }, el("span", { class: "field-label" }, label), input);
}

export function EncounterView(ctx) {
  const p = ctx.activePatient;
  const checked = new Set();
  const wrap = el("div", { class: "view" },
    el("h2", { class: "section-label" }, `${t("new_encounter")} — ${p.name}`),
    el("div", { class: "card" },
      el("div", { class: "field-label" }, t("danger_signs")),
      ...DANGER_SIGNS.map((s) =>
        el("label", { class: "checkrow" },
          el("input", { type: "checkbox", onchange: (e) => (e.target.checked ? checked.add(s) : checked.delete(s)) }),
          el("span", {}, s.replaceAll("_", " "))))),
    el("div", { id: "triage-result" }),
    el("button", {
      class: "btn btn-primary btn-block",
      onclick: async () => {
        const emergency = checked.has("vaginal_bleeding") || checked.has("convulsions") || checked.has("not_breathing_well");
        await ctx.recordEncounter(p.id, [...checked], emergency);
        const slot = wrap.querySelector("#triage-result");
        slot.innerHTML = "";
        slot.append(el("div", { class: `triage ${emergency ? "triage-emergency" : "triage-ok"}` },
          emergency ? t("refer_emergency") : t("routine")));
        ctx.toast(t("encounter_saved"));
        setTimeout(() => ctx.navigate("#/tasks"), 1400);
      },
    }, t("submit")),
    el("button", { class: "btn btn-outline btn-block", onclick: () => ctx.navigate("#/chart") }, t("cancel")));
  return wrap;
}

export function SyncView(ctx) {
  const s = ctx.syncStatus;
  const wrap = el("div", { class: "view" },
    el("div", { class: `sync-hero ${s.online ? "sync-online" : "sync-offline"}` },
      el("div", { class: "sync-dot" }),
      el("div", { class: "sync-state" }, s.online ? t("online") : t("offline"))),
    el("div", { class: "card stat-row" },
      stat(t("pending_docs"), s.totalDocs), stat(t("pending_events"), s.pendingEvents)),
    el("div", { class: "card stat-row" },
      stat(t("last_sync"), s.lastSync ? new Date(s.lastSync).toLocaleString() : t("never"))),
    el("button", {
      class: "btn btn-primary btn-block",
      onclick: async () => {
        const r = await ctx.syncNow();
        ctx.toast(r.ok ? t("synced") : t("sync_failed"));
        ctx.rerender();
      },
    }, t("sync_now")));
  return wrap;
}

function stat(label, value) {
  return el("div", { class: "stat" },
    el("div", { class: "stat-value" }, String(value)), el("div", { class: "stat-label" }, label));
}

export function DashboardView(ctx) {
  const d = ctx.dash;
  return el("div", { class: "view" },
    el("h2", { class: "section-label" }, ctx.facility),
    el("div", { class: "card stat-row" },
      stat(t("patients_registered"), d.patients), stat(t("encounters_today"), d.encountersToday)),
    el("div", { class: "card stat-row" },
      stat(t("tasks_due"), d.tasksDue), stat(t("pending_events"), d.pendingEvents)));
}

export function SettingsView(ctx) {
  const sel = el("select", {
    class: "input",
    onchange: async (e) => { await ctx.setLanguage(e.target.value); ctx.rerender(); },
  });
  for (const lang of ctx.languages) {
    const opt = el("option", { value: lang.code }, lang.name);
    if (lang.code === ctx.language) opt.selected = true;
    sel.append(opt);
  }
  return el("div", { class: "view" },
    el("h2", { class: "section-label" }, t("settings")),
    el("div", { class: "card" }, field(t("language"), sel)),
    el("div", { class: "card" },
      el("div", { class: "field-label" }, t("facility")),
      el("div", {}, ctx.facility)),
    el("button", { class: "btn btn-outline btn-block", onclick: () => ctx.navigate("#/dashboard") }, t("dashboard")),
    el("div", { class: "card about" },
      el("div", { class: "field-label" }, t("about")),
      el("div", {}, `OpenHealth OS · AGPL-3.0 · ${t("version")} 1.0.0`),
      el("div", { class: "task-sub" }, "Offline-first · FHIR R4 · NDPA 2023")));
}

export { materialize };
