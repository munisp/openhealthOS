/* OpenHealth OS PWA — boot, router, state. Offline-first: the app shell,
 * data and all workflows run locally; sync is opportunistic.
 */

import { LocalDB } from "./db.js";
import { SyncClient, materialize } from "./sync.js";
import { LANGUAGES, setLanguage, getLanguage, t } from "./i18n.js";
import { el, TasksView, PatientsView, RegisterForm, EncounterView,
         SyncView, DashboardView, SettingsView, generateTasksFor } from "./views.js";
import { ChartView, QueueView } from "./emr.js";
import { FORMULARY, buildTimeline, appointmentsForDay } from "./clinical.js";

const state = {
  db: null,
  sync: null,
  route: "#/tasks",
  query: "",
  activePatient: null,
  language: "en",
};

const $ = (sel) => document.querySelector(sel);

async function boot() {
  state.db = await LocalDB.init();
  const base = await state.db.getMeta("syncBase");
  state.sync = new SyncClient(state.db, base || defaultSyncBase());
  state.language = (await state.db.getMeta("language")) || "en";
  setLanguage(state.language);
  if (!(await state.db.getMeta("seeded"))) await seedDemoData();
  window.addEventListener("hashchange", render);
  window.addEventListener("online", () => { render(); syncQuietly(); });
  window.addEventListener("offline", render);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  render();
  syncQuietly();
  setInterval(syncQuietly, 60000); // opportunistic background sync
}

function defaultSyncBase() {
  // Served by the api-gateway at /app/* → sync through its /api/sync route.
  // Served by the edge node directly → sync-ingest is co-located at origin.
  if (location.pathname.startsWith("/app")) return `${location.origin}/api/sync`;
  return location.origin.startsWith("http") ? location.origin : "http://127.0.0.1:8443";
}

/* ---------- demo seed (first run only) ---------- */
async function seedDemoData() {
  const db = state.db;
  const r = await db.replica();
  const seedPatients = [
    ["pt-ada", "Adaeze Nwosu", 27, "Umuahia", true, null],
    ["pt-musa", "Musa Danjuma", 34, "Kawo", false, null],
    ["pt-bola", "Bola Adeyemi", 22, "Ado-Ekiti", true, null],
    ["pt-chi", "Chiamaka Obi", 0, "Nsukka", false, 200], // infant: immunization tasks
  ];
  for (const [id, name, age, village, pregnant, ageDays] of seedPatients) {
    r.set(id, "Patient", "name", name);
    r.set(id, "Patient", "age_years", age);
    if (ageDays != null) r.set(id, "Patient", "age_days", ageDays);
    r.set(id, "Patient", "village", village);
    if (pregnant) r.set(id, "Patient", "pregnant", true);
    for (const task of generateTasksFor(materialize(r.docs.get(id)))) {
      const tid = `task-${id}-${task.kind}-${Math.round(task.due / 86400000)}`;
      r.set(tid, "Task", "label", task.label);
      r.set(tid, "Task", "kind", task.kind);
      r.set(tid, "Task", "due", task.due);
      r.set(tid, "Task", "priority", task.priority);
      r.set(tid, "Task", "patient_id", id);
      r.set(tid, "Task", "patient_name", name);
      r.set(tid, "Task", "village", village);
      r.set(tid, "Task", "status", "open");
    }
    await db.queueEvent("patient_registered", { patient_id: id, name });
  }
  await db.saveReplica(r);
  await db.setMeta("seeded", true);
}

/* ---------- actions ---------- */
const ctx = {
  get tasks() { return this._tasks || []; },
  get patients() { return this._patients || []; },
  languages: LANGUAGES,
  get language() { return getLanguage(); },
  get facility() { return state.facility || "Kawo PHC · Kaduna"; },
  toast, navigate: (h) => (location.hash = h),
  async rerender() { await render(); },
  openRegister() {
    $("#sheet-root").innerHTML = "";
    $("#sheet-root").append(RegisterForm(ctx));
  },
  openSheet(node) {
    $("#sheet-root").innerHTML = "";
    $("#sheet-root").append(node);
  },
  closeSheet() { $("#sheet-root").innerHTML = ""; },
  openChart(pid) {
    state.activePatient = this._patients.find((p) => p.id === pid);
    location.hash = "#/chart";
  },
  startVisit(pid) {
    state.activePatient = this._patients.find((p) => p.id === pid);
    location.hash = "#/encounter";
  },
  get activePatient() { return state.activePatient; },
  get timeline() { return state._timeline || []; },
  get queue() { return state._queue || []; },
  async registerPatient(f) {
    const r = await state.db.replica();
    const id = "pt-" + crypto.randomUUID().slice(0, 8);
    r.set(id, "Patient", "name", f.name.trim());
    r.set(id, "Patient", "age_years", Number(f.age_years) || 0);
    if (f.village) r.set(id, "Patient", "village", f.village.trim());
    if (f.phone) r.set(id, "Patient", "phone", f.phone.trim());
    if (f.pregnant) r.set(id, "Patient", "pregnant", true);
    if (Number(f.age_years) === 0) r.set(id, "Patient", "age_days", 30);
    for (const task of generateTasksFor(materialize(r.docs.get(id)))) {
      const tid = `task-${id}-${task.kind}-${Math.round(task.due / 86400000)}`;
      r.set(tid, "Task", "label", task.label);
      r.set(tid, "Task", "kind", task.kind);
      r.set(tid, "Task", "due", task.due);
      r.set(tid, "Task", "priority", task.priority);
      r.set(tid, "Task", "patient_id", id);
      r.set(tid, "Task", "patient_name", f.name.trim());
      r.set(tid, "Task", "village", f.village.trim());
      r.set(tid, "Task", "status", "open");
    }
    await state.db.saveReplica(r);
    await state.db.queueEvent("patient_registered", { patient_id: id, name: f.name.trim(), village: f.village });
  },
  async completeTask(id) {
    const r = await state.db.replica();
    r.set(id, "Task", "status", "done");
    r.set(id, "Task", "completed_at", Date.now());
    await state.db.saveReplica(r);
    await state.db.queueEvent("task_completed", { task_id: id });
  },
  async recordEncounter(patientId, signs, emergency) {
    const r = await state.db.replica();
    const id = "enc-" + crypto.randomUUID().slice(0, 8);
    r.set(id, "Encounter", "patient_id", patientId);
    r.set(id, "Encounter", "ts", Date.now());
    r.set(id, "Encounter", "disposition", emergency ? "REFER-EMERGENCY" : "ROUTINE");
    if (signs.length) r.add(id, "Encounter", "danger_signs", signs);
    await state.db.saveReplica(r);
    await state.db.queueEvent("encounter", { encounter_id: id, patient_id: patientId, signs, disposition: emergency ? "REFER-EMERGENCY" : "ROUTINE" });
  },
  async recordVitals(patientId, v, flags) {
    const r = await state.db.replica();
    const id = "vit-" + crypto.randomUUID().slice(0, 8);
    r.set(id, "Vitals", "patient_id", patientId);
    r.set(id, "Vitals", "ts", Date.now());
    for (const [k, val] of Object.entries(v)) {
      if (val != null && val !== "") r.set(id, "Vitals", k, Number(val));
    }
    r.set(id, "Vitals", "flags", flags);
    await state.db.saveReplica(r);
    await state.db.queueEvent("vitals", { vitals_id: id, patient_id: patientId, ...v, danger: flags.some((f) => f.level === "danger") });
  },
  async orderLabs(patientId, codes) {
    const r = await state.db.replica();
    for (const code of codes) {
      const id = "lab-" + crypto.randomUUID().slice(0, 8);
      r.set(id, "LabOrder", "patient_id", patientId);
      r.set(id, "LabOrder", "ts", Date.now());
      r.set(id, "LabOrder", "test_code", code);
      await state.db.queueEvent("lab_order", { lab_order_id: id, patient_id: patientId, test_code: code });
    }
    await state.db.saveReplica(r);
  },
  async prescribe(patientId, drugCode, dose, quantity) {
    const p = state.activePatient;
    const drug = FORMULARY.find((d) => d.code === drugCode);
    const r = await state.db.replica();
    const id = "rx-" + crypto.randomUUID().slice(0, 8);
    r.set(id, "Prescription", "patient_id", patientId);
    r.set(id, "Prescription", "ts", Date.now());
    r.set(id, "Prescription", "drug_code", drugCode);
    r.set(id, "Prescription", "drug_name", drug?.name || drugCode);
    r.set(id, "Prescription", "dose", dose);
    r.set(id, "Prescription", "quantity", quantity);
    r.set(id, "Prescription", "status", "queued");
    await state.db.saveReplica(r);
    await state.db.queueEvent("prescription", {
      prescription_id: id, patient_id: patientId, drug_code: drugCode,
      dose, quantity, allergies: p?.allergies || [],
    });
    // Opportunistic push to the pharmacy counter when the gateway is
    // reachable; the queued event remains the durable record offline.
    if (navigator.onLine) {
      try {
        const base = location.pathname.startsWith("/app") ? location.origin : "";
        if (base) {
          const resp = await fetch(`${base}/api/pharmacy/dispense-requests`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              id, prescription_id: id, patient_id: patientId,
              facility_id: "edge", drug_code: drugCode, dose, quantity,
              prescriber: "pwa", allergies: p?.allergies || [],
            }),
          });
          if (resp.ok) {
            const r2 = await state.db.replica();
            r2.set(id, "Prescription", "status", "sent");
            await state.db.saveReplica(r2);
          }
        }
      } catch { /* stays queued — pharmacy pull/eventbus reconciles */ }
    }
  },
  async scheduleAppointment(patientId, when, reason) {
    const p = state.activePatient;
    const r = await state.db.replica();
    const id = "apt-" + crypto.randomUUID().slice(0, 8);
    r.set(id, "Appointment", "patient_id", patientId);
    r.set(id, "Appointment", "patient_name", p?.name || "");
    r.set(id, "Appointment", "when", when);
    r.set(id, "Appointment", "reason", reason);
    r.set(id, "Appointment", "status", "scheduled");
    r.set(id, "Appointment", "ts", Date.now());
    await state.db.saveReplica(r);
    await state.db.queueEvent("appointment", { appointment_id: id, patient_id: patientId, when, reason });
  },
  async setCohorts(patientId, cohorts, allergies) {
    const r = await state.db.replica();
    r.set(patientId, "Patient", "cohorts", cohorts);
    r.set(patientId, "Patient", "allergies", allergies);
    await state.db.saveReplica(r);
    await state.db.queueEvent("patient_updated", { patient_id: patientId, cohorts, allergies });
  },
  async syncNow() { return state.sync.syncNow(); },
  get syncStatus() { return state._syncStatus || { pendingEvents: 0, totalDocs: 0, online: navigator.onLine }; },
  async setLanguage(code) {
    setLanguage(code);
    await state.db.setMeta("language", code);
  },
  get dash() { return state._dash || { patients: 0, encountersToday: 0, tasksDue: 0, pendingEvents: 0 }; },
};

async function refreshData() {
  const db = state.db;
  const r = await db.replica();
  const docs = [...r.docs.values()].map(materialize);
  ctx._patients = docs.filter((d) => d.type === "Patient").sort((a, b) => (a.name > b.name ? 1 : -1));
  ctx._tasks = docs.filter((d) => d.type === "Task" && d.status === "open")
    .sort((a, b) => a.due - b.due);
  state._syncStatus = await state.sync.status();
  if (state.activePatient) {
    state._timeline = buildTimeline(state.activePatient.id, docs);
  }
  const now = new Date();
  const dayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  state._queue = appointmentsForDay(docs, dayStart, dayStart + 86400000);
  const today = new Date().toDateString();
  state._dash = {
    patients: ctx._patients.length,
    encountersToday: docs.filter((d) => d.type === "Encounter" && new Date(d.ts).toDateString() === today).length,
    tasksDue: ctx._tasks.filter((x) => x.due <= Date.now() + 86400000).length,
    pendingEvents: state._syncStatus.pendingEvents,
  };
}

/* ---------- router ---------- */
async function render() {
  await refreshData();
  state.route = location.hash || "#/tasks";
  const root = $("#view-root");
  root.innerHTML = "";
  const routes = {
    "#/tasks": () => TasksView(ctx),
    "#/patients": () => PatientsView(ctx),
    "#/chart": () => (state.activePatient ? ChartView(ctx) : PatientsView(ctx)),
    "#/encounter": () => (state.activePatient ? EncounterView(ctx) : TasksView(ctx)),
    "#/queue": () => QueueView(ctx),
    "#/sync": () => SyncView(ctx),
    "#/dashboard": () => DashboardView(ctx),
    "#/more": () => SettingsView(ctx),
  };
  root.append((routes[state.route] || routes["#/tasks"])());
  renderNav();
  renderBanner();
}

function renderNav() {
  const tabs = [
    ["#/tasks", "tasks", "☑"], ["#/patients", "patients", "◉"],
    ["#/queue", "queue", "☷"], ["#/sync", "sync", "↻"], ["#/more", "more", "⋯"],
  ];
  const nav = $("#tabbar");
  nav.innerHTML = "";
  for (const [hash, key, glyph] of tabs) {
    nav.append(el("a", {
      href: hash,
      class: "tab" + (state.route === hash ? " tab-active" : ""),
    }, el("span", { class: "tab-glyph" }, glyph), el("span", { class: "tab-label" }, t(key))));
  }
}

function renderBanner() {
  const b = $("#offline-banner");
  if (!navigator.onLine) {
    b.textContent = t("offline");
    b.className = "banner banner-show";
  } else {
    b.className = "banner";
  }
}

let toastTimer;
function toast(msg) {
  const n = $("#toast");
  n.textContent = msg;
  n.className = "toast toast-show";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (n.className = "toast"), 2400);
}

async function syncQuietly() {
  if (!navigator.onLine) return;
  try {
    await state.sync.syncNow();
  } catch { /* queued — the next pass retries */ }
}

boot();
