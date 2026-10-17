/* OpenHealth OS PWA — boot, router, state. Offline-first: the app shell,
 * data and all workflows run locally; sync is opportunistic.
 */

import { LocalDB } from "./db.js";
import { SyncClient, materialize } from "./sync.js";
import { LANGUAGES, setLanguage, getLanguage, t } from "./i18n.js";
import { el, TasksView, PatientsView, RegisterForm, EncounterView,
         SyncView, DashboardView, SettingsView, generateTasksFor } from "./views.js";

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
  closeSheet() { $("#sheet-root").innerHTML = ""; },
  openEncounter(pid) {
    state.activePatient = this._patients.find((p) => p.id === pid);
    location.hash = "#/encounter";
  },
  get activePatient() { return state.activePatient; },
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
    "#/encounter": () => (state.activePatient ? EncounterView(ctx) : TasksView(ctx)),
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
    ["#/sync", "sync", "↻"], ["#/dashboard", "dashboard", "▦"], ["#/more", "more", "⋯"],
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
