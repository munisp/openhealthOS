/* Patient app — boot, sign-in (patient code + phone), feed rendering.
 * Read-mostly by design: the patient's data is their notification feed.
 * Last-known feed is cached in localStorage so the app still opens with
 * zero connectivity; it never pretends stale data is live.
 */

import { LANGUAGES, setLanguage, getLanguage, t } from "./i18n.js";
import {
  kindMeta, groupFeed, relTime, stateLabel, groupRecord, groupConsults,
  upcomingAppointments, coverageSummary, bookableSlots, nextDue, buildQuestionnaireResponse,
  claimRows, nairaText,
  wellnessKpis, planItems, riskBadge, logEntry,
} from "./logic.js";
import { nearest, facilityRow } from "./geo.js";
import { elevatedFetch } from "./stepup.js";

const LS_KEY = "openhealth-patient";
const REC_CACHE_KEY = "openhealth-patient-record";
const $ = (sel) => document.querySelector(sel);

const state = {
  patientId: null,
  phone: null,
  feed: null,        // { notifications: [...] } last good fetch
  updatedAt: null,
  loading: false,
  tab: "updates",    // "updates" | "record" | "care"
  record: null,      // { record: <FHIR bundle>, consults: [...], complete } last good fetch
  timeline: null,    // Patient 360: merged chronological feed (ADR-0041)
  timelineLoading: false,
  recordAt: null,
  recordLoading: false,
  coverage: null,    // last good eligibility payload
  appts: null,       // last good appointment list
  consents: null,    // last good consent list
  claims: null,      // last good claims-in-my-name list (ADR-0018)
  careLoading: false,
  wellness: null,        // last good wellness summary (ADR-0035)
  wellnessPlan: null,    // last good prevention plan
  wellnessLoading: false,
};

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "onclick") n.addEventListener("click", v);
    else n[k] = v;
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    n.append(c.nodeType ? c : document.createTextNode(c));
  }
  return n;
}

/* ---------- persistence (localStorage; throws are swallowed) -------- */
function saveLocal() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({
      patientId: state.patientId, phone: state.phone,
      feed: state.feed, updatedAt: state.updatedAt, language: getLanguage(),
    }));
  } catch { /* private mode — session still works */ }
}
function loadLocal() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "null");
  } catch { return null; }
}

/* ---------- API -------------------------------------------------------
 * Served by the gateway at /patient/* → the rail is /api/notify/*. */
function apiBase() {
  if (location.pathname.startsWith("/patient")) return `${location.origin}/api/notify`;
  return location.origin.startsWith("http") ? `${location.origin}/api/notify` : "http://127.0.0.1:18089";
}

/* Registry rail (ADR-0014): facilities near the patient. */
function registryBase() {
  if (location.pathname.startsWith("/patient")) return `${location.origin}/api/registry`;
  return location.origin.startsWith("http") ? `${location.origin}/api/registry` : "http://127.0.0.1:18082";
}

/* Wellness rail (ADR-0035): device + self-logged activity, nutrition,
 * prevention plan — its own service, same-origin via the gateway. */
function wellnessBase() {
  if (location.pathname.startsWith("/patient")) return `${location.origin}/api/wellness`;
  return location.origin.startsWith("http") ? `${location.origin}/api/wellness` : "http://127.0.0.1:18090";
}

const WQ_KEY = "oh-wellness-queue"; // offline outbox: entries sync when online

function wellnessQueue() {
  try { return JSON.parse(localStorage.getItem(WQ_KEY) || "[]"); } catch { return []; }
}
function setWellnessQueue(q) {
  try { localStorage.setItem(WQ_KEY, JSON.stringify(q)); } catch { /* private mode */ }
}

async function flushWellnessQueue() {
  if (!navigator.onLine || !state.patientId) return;
  const q = wellnessQueue();
  if (!q.length) return;
  const left = [];
  for (const entry of q) {
    try {
      const r = await fetch(`${wellnessBase()}/wellness/ingest`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          patient_id: state.patientId, source: entry.source || "manual",
          observations: [entry.obs],
        }),
      });
      if (!r.ok) left.push(entry);
    } catch { left.push(entry); }
  }
  setWellnessQueue(left);
  if (left.length < q.length) loadWellness(true);
}

/* Log a wellness entry: online it goes straight to the rail, offline it
 * waits in the outbox — either way the deterministic ref makes the
 * eventual sync exactly-once. */
async function logWellness(type, payload, source = "manual") {
  const obs = logEntry(state.patientId, type, payload);
  const entry = { source, obs };
  if (navigator.onLine) {
    try {
      const r = await fetch(`${wellnessBase()}/wellness/ingest`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ patient_id: state.patientId, source, observations: [obs] }),
      });
      if (r.ok) { toast(t("wl_logged")); loadWellness(true); return; }
    } catch { /* fall through to queue */ }
  }
  setWellnessQueue([...wellnessQueue(), entry]);
  toast(t("wl_queued"));
}

async function loadWellness(force = false) {
  if (state.wellnessLoading || (!force && state.wellness)) return;
  state.wellnessLoading = true;
  const pid = encodeURIComponent(state.patientId);
  // age/sex for the plan come from the record when we have it — the
  // wellness rail never asks the patient to re-enter clinical facts.
  let age = "", sex = "";
  const pat = (state.record?.record?.entry || [])
    .map((e) => e.resource).find((r) => r?.resourceType === "Patient");
  if (pat?.birthDate) {
    age = String(new Date().getFullYear() - parseInt(pat.birthDate.slice(0, 4), 10));
    sex = pat.gender || "";
  }
  const settle = (p) => p.then((v) => ({ ok: true, v })).catch(() => ({ ok: false }));
  const [sum, plan] = await Promise.all([
    settle(fetch(`${wellnessBase()}/wellness/${pid}/summary`, { cache: "no-store" }).then((r) => r.ok ? r.json() : Promise.reject(r.status))),
    settle(fetch(`${wellnessBase()}/wellness/${pid}/plan?age=${age}&sex=${sex}`, { cache: "no-store" }).then((r) => r.ok ? r.json() : Promise.reject(r.status))),
  ]);
  if (sum.ok) state.wellness = sum.v;
  if (plan.ok) state.wellnessPlan = plan.v;
  state.wellnessLoading = false;
  if (state.tab === "wellness") render();
}

async function fetchFeed() {
  const url = `${apiBase()}/portal/feed?patient_id=${encodeURIComponent(state.patientId)}&phone=${encodeURIComponent(state.phone)}`;
  const resp = await fetch(url, { cache: "no-store" });
  if (!resp.ok) throw new Error(String(resp.status));
  return resp.json();
}

async function refresh() {
  if (!state.patientId || state.loading) return;
  state.loading = true;
  render();
  try {
    const feed = await fetchFeed();
    state.feed = feed;
    state.updatedAt = Date.now();
    saveLocal();
  } catch { /* keep last-known; banner already shows offline */ }
  state.loading = false;
  render();
}

/* ---------- my record (ADR-0016) ---------------------------------------
 * The patient's own FHIR $everything bundle + telehealth thread, fetched
 * through the portal record proxy. Cached in localStorage exactly like the
 * feed: offline you see your saved copy, clearly stamped — never a
 * fabricated "live" one. */
async function fetchRecord() {
  // PHI boundary (ADR-0039): the record renders only through the step-up
  // gate — 428 opens the challenge sheet, elevation retries transparently.
  const path = `/portal/record?patient_id=${encodeURIComponent(state.patientId)}&phone=${encodeURIComponent(state.phone)}`;
  const resp = await elevatedFetch(apiBase(), path, { patientId: state.patientId, phone: state.phone });
  if (!resp.ok) throw new Error(String(resp.status));
  return resp.json();
}

/* Patient 360 timeline (innovation 1): one chronological feed of
 * appointments, results, prescriptions, imaging, consults. Elevated — the
 * same step-up gate as the record. */
async function fetchTimeline() {
  const path = `/portal/timeline?patient_id=${encodeURIComponent(state.patientId)}&phone=${encodeURIComponent(state.phone)}`;
  const resp = await elevatedFetch(apiBase(), path, { patientId: state.patientId, phone: state.phone });
  if (!resp.ok) throw new Error(String(resp.status));
  return resp.json();
}

function loadTimeline() {
  state.timelineLoading = true; render();
  fetchTimeline()
    .then((d) => { state.timeline = d; })
    .catch(() => { state.timeline = { complete: false, items: [] }; })
    .finally(() => { state.timelineLoading = false; render(); });
}

function TimelineView() {
  const wrap = el("div", { class: "view" }, Tabs(),
    el("div", { class: "feed-head" },
      el("h2", {}, t("tab_timeline")),
      el("button", { class: "btn btn-outline btn-refresh", onclick: loadTimeline }, t("refresh"))));
  if (state.timelineLoading) {
    wrap.append(el("div", { class: "empty" }, t("loading") || "…"));
    return wrap;
  }
  const items = state.timeline?.items || [];
  if (!items.length) {
    wrap.append(el("div", { class: "empty" }, t("timeline_empty")));
    return wrap;
  }
  if (state.timeline && !state.timeline.complete) {
    wrap.append(el("div", { class: "banner banner-show" }, t("offline")));
  }
  const list = el("ul", { class: "timeline" });
  for (const it of items) {
    list.append(el("li", { class: "timeline-item" },
      el("span", { class: `badge badge-${it.kind}` }, t("kind_" + it.kind) || it.kind),
      el("span", { class: "timeline-summary" }, it.summary),
      el("span", { class: "meta" }, it.ts ? new Date(it.ts).toLocaleString() : "")));
  }
  wrap.append(list);
  return wrap;
}

function saveRecordLocal() {
  try {
    localStorage.setItem(REC_CACHE_KEY, JSON.stringify({
      patientId: state.patientId, data: state.record, at: state.recordAt,
    }));
  } catch { /* full — older copy still there */ }
}

function loadRecordLocal() {
  try {
    const rc = JSON.parse(localStorage.getItem(REC_CACHE_KEY) || "null");
    if (rc && rc.patientId === state.patientId) {
      state.record = rc.data;
      state.recordAt = rc.at;
    }
  } catch { /* ignore */ }
}

async function loadRecord() {
  if (!state.patientId || state.recordLoading) return;
  state.recordLoading = true;
  if (state.tab === "record") render();
  try {
    const data = await fetchRecord();
    state.record = data;
    state.recordAt = Date.now();
    saveRecordLocal();
  } catch { /* keep last-known copy */ }
  state.recordLoading = false;
  if (state.tab === "record") render();
}

/* ---------- views ----------------------------------------------------- */

function SignInView() {
  const f = { code: "", phone: "" };
  const err = el("div", { class: "signin-error" });
  const wrap = el("div", { class: "view" },
    el("div", { class: "hero" },
      el("div", { class: "hero-mark" }, "+"),
      el("h2", {}, t("welcome")),
      el("p", { class: "muted" }, t("code_hint"))),
    el("div", { class: "card" },
      field(t("patient_code"), el("input", { class: "input", autocomplete: "off", oninput: (e) => (f.code = e.target.value.trim()) })),
      field(t("phone"), el("input", { class: "input", type: "tel", inputmode: "tel", oninput: (e) => (f.phone = e.target.value.trim()) })),
      err,
      el("button", {
        class: "btn btn-primary btn-block",
        onclick: async () => {
          if (!f.code || !f.phone) return;
          err.textContent = t("loading");
          state.patientId = f.code;
          state.phone = f.phone;
          try {
            const feed = await fetchFeed();
            state.feed = feed;
            state.updatedAt = Date.now();
            if (feed.language && feed.language !== getLanguage()) setLanguage(feed.language);
            saveLocal();
            err.textContent = "";
            render();
          } catch {
            state.patientId = state.phone = null;
            err.textContent = t("wrong_code");
          }
        },
      }, t("sign_in_btn")),
      PasskeyUnlock()),
    langRow());
  return wrap;
}

function field(label, input) {
  return el("label", { class: "field" }, el("span", { class: "field-label" }, label), input);
}

function langRow() {
  const row = el("div", { class: "lang-row" });
  for (const l of LANGUAGES) {
    row.append(el("button", {
      class: "lang-chip" + (l.code === getLanguage() ? " lang-on" : ""),
      onclick: async () => { setLanguage(l.code); saveLocal(); render(); },
    }, l.name));
  }
  return row;
}

/* Tab switcher shared by the feed and record views. */
function Tabs() {
  const mk = (tab, key) => el("button", {
    class: "tab-btn" + (state.tab === tab ? " tab-on" : ""),
    role: "tab",
    onclick: () => {
      state.tab = tab;
      if (tab === "record" && !state.record && navigator.onLine) loadRecord();
      if (tab === "care" && state.appts === null && navigator.onLine) loadCare();
      if (tab === "wellness" && !state.wellness && navigator.onLine) loadWellness();
      if (tab === "timeline" && !state.timeline && navigator.onLine) loadTimeline();
      render();
    },
  }, t(key));
  return el("div", { class: "tabs", role: "tablist" },
    mk("updates", "tab_updates"), mk("record", "tab_record"), mk("care", "tab_care"),
    mk("timeline", "tab_timeline"), mk("wellness", "tab_wellness"));
}

function FeedView() {
  const wrap = el("div", { class: "view" },
    Tabs(),
    el("div", { class: "feed-head" },
      el("h2", {}, t("updates")),
      el("button", { class: "btn btn-outline btn-refresh", onclick: refresh }, t("refresh"))),
    state.updatedAt
      ? el("div", { class: "muted stamp" }, `${t("last_updated")}: ${new Date(state.updatedAt).toLocaleString()}`)
      : null);

  const { action, info } = groupFeed(state.feed?.notifications || []);
  if (!action.length && !info.length) {
    wrap.append(el("div", { class: "empty" }, t("no_updates")));
  }
  if (action.length) {
    wrap.append(el("h3", { class: "section-label" }, t("action_needed")));
    for (const n of action) wrap.append(Card(n));
  }
  if (info.length) {
    wrap.append(el("h3", { class: "section-label" }, t("earlier")));
    for (const n of info) wrap.append(Card(n));
  }
  wrap.append(NearMeCard());
  wrap.append(
    el("div", { class: "card settings-card" },
      langRow(),
      el("button", {
        class: "btn btn-outline btn-block",
        onclick: () => {
          state.patientId = state.phone = state.feed = state.updatedAt = null;
          state.record = state.recordAt = null;
          state.coverage = state.appts = state.consents = state.claims = state.timeline = null;
          state.tab = "updates";
          try { localStorage.removeItem(LS_KEY); } catch { /* ignore */ }
          try { localStorage.removeItem(REC_CACHE_KEY); } catch { /* ignore */ }
          render();
        },
      }, t("sign_out"))));
  return wrap;
}

/* ---------- my record view (ADR-0016) -----------------------------------
 * Sections mirror what a patient asks about: visits, test results,
 * medicines, immunisations, conditions, allergies, and phone/video
 * consults. Empty sections are hidden; an entirely empty record says so
 * plainly, and a partially-fetched record carries an honest banner. */

function RecordRow(glyph, r) {
  return el("div", { class: "card notif-card accent-info" },
    el("div", { class: "notif-glyph" }, glyph),
    el("div", { class: "notif-main" },
      el("div", { class: "notif-kind" }, r.title),
      r.value ? el("div", { class: "notif-text" }, r.value) : null,
      el("div", { class: "notif-meta" },
        [relTime(r.when), r.source].filter(Boolean).join(" · "))));
}

function RecordSection(title, glyph, rows) {
  const sec = el("div", {}, el("h3", { class: "section-label" }, title));
  for (const r of rows) sec.append(RecordRow(glyph, r));
  return sec;
}

function RecordView() {
  const wrap = el("div", { class: "view" },
    Tabs(),
    el("div", { class: "feed-head" },
      el("h2", {}, t("tab_record")),
      el("button", { class: "btn btn-outline btn-refresh", onclick: loadRecord }, t("refresh"))),
    state.recordAt
      ? el("div", { class: "muted stamp" }, `${t("last_updated")}: ${new Date(state.recordAt).toLocaleString()}`)
      : null);

  if (state.recordLoading && !state.record) {
    wrap.append(el("div", { class: "empty" }, t("rec_loading")));
    return wrap;
  }
  const rec = state.record;
  if (!rec) {
    wrap.append(el("div", { class: "empty" }, navigator.onLine ? t("rec_loading") : t("rec_failed")));
    return wrap;
  }
  if (rec.complete === false) {
    wrap.append(el("div", { class: "banner banner-show banner-inline" }, t("rec_incomplete")));
  }

  const { sections, total } = groupRecord(rec.record);
  const consults = groupConsults(rec.consults);
  if (!total && !consults.length) {
    wrap.append(el("div", { class: "empty" }, t("rec_empty")));
    return wrap;
  }

  const SECTIONS = [
    ["encounters", "rec_visits", "◷"],
    ["labs", "rec_labs", "⚗"],
    ["meds", "rec_meds", "✚"],
    ["immunizations", "rec_immunizations", "❋"],
    ["conditions", "rec_conditions", "◈"],
    ["allergies", "rec_allergies", "⚠"],
  ];
  for (const [key, labelKey, glyph] of SECTIONS) {
    const rows = sections[key];
    if (rows.length) wrap.append(RecordSection(t(labelKey), glyph, rows));
  }
  if (consults.length) wrap.append(RecordSection(t("rec_telehealth"), "☎", consults));
  wrap.append(ShareSummaryCard());
  return wrap;
}

/* Share my summary — SMART Health Link (ADR-0054). The SHR encrypts the
 * IPS with a key that exists only inside the link: the patient shows the
 * link/QR to any clinician; the server never sees plaintext after minting.
 * Offline-honest: no network, no link — the card says so, never fakes one. */
function ShareSummaryCard() {
  const card = el("div", { class: "card notif-card accent-info" },
    el("div", { class: "notif-glyph" }, "⛓"),
    el("div", { class: "notif-main" },
      el("div", { class: "notif-kind" }, "Share my summary (SMART Health Link)"),
      el("div", { class: "notif-text" },
        "A time-limited encrypted link to your International Patient Summary — show it to any clinician, anywhere. It expires in 30 days.")));
  const out = el("div", { class: "notif-meta" });
  const btn = el("button", { class: "btn btn-outline", onclick: async () => {
    if (!navigator.onLine) { out.textContent = "Offline — a share link needs the network once."; return; }
    btn.disabled = true;
    out.textContent = "Minting secure link…";
    try {
      const resp = await fetch(`${apiBase()}/shr/ips/${encodeURIComponent(state.patientId)}/shl`, {
        headers: { "X-Patient-Self": state.patientId }, cache: "no-store",
      });
      if (!resp.ok) throw new Error(String(resp.status));
      const { link } = await resp.json();
      out.textContent = "";
      const input = el("input", { class: "btn btn-outline", style: "width:100%;font-size:11px", readonly: true, value: link });
      const copy = el("button", { class: "btn", onclick: () => {
        navigator.clipboard?.writeText(link).then(() => { copy.textContent = "Copied"; });
      } }, "Copy link");
      out.append(input, copy);
    } catch {
      out.textContent = "Share service unavailable right now — your record stays on this phone regardless.";
    } finally {
      btn.disabled = false;
    }
  } }, "Create share link");
  card.querySelector(".notif-main").append(btn, out);
  return card;
}

/* ---------- facilities near me (ADR-0014) -------------------------------
 * Server-first (registry haversine), offline-honest: when the network is
 * gone we compute distance ourselves over the last cached facility list —
 * the answer may be stale but it is never fabricated. Navigation hands off
 * to the phone's maps app (geo: link), which does turn-by-turn better than
 * any in-app map could on these devices. */

const FAC_CACHE_KEY = "openhealth-patient-facilities";

function FacilityCard(f) {
  const cap = [
    f.beds ? `${f.beds} ${t("near_beds")}` : null,
    f.doctors ? `${f.doctors} ${t("near_doctors")}` : null,
    f.nurses ? `${f.nurses} ${t("near_nurses")}` : null,
  ].filter(Boolean).join(" · ");
  return el("div", { class: "card near-fac" },
    el("div", { class: "notif-main" },
      el("div", { class: "notif-kind" }, f.name),
      el("div", { class: "notif-meta" },
        [f.level, f.distance_km != null ? `${f.distance_km} km` : null, f.hours || null]
          .filter(Boolean).join(" · ")),
      cap ? el("div", { class: "notif-text" }, cap) : null,
      f.amenities.length
        ? el("div", { class: "notif-meta" }, f.amenities.join(" · ")) : null,
      f.nav ? el("a", { class: "btn btn-outline btn-block near-nav", href: f.nav }, t("near_navigate")) : null));
}

function NearMeCard() {
  const box = el("div", { class: "near-results" });

  async function serverNear(lat, lng) {
    const r = await fetch(`${registryBase()}/facilities/near?lat=${lat}&lng=${lng}&radius=25&limit=8`, { cache: "no-store" });
    if (!r.ok) throw new Error(String(r.status));
    const d = await r.json();
    return (d.results || []).map((x) => facilityRow(x));
  }

  async function cachedListNear(lat, lng) {
    let all = null;
    try { all = JSON.parse(localStorage.getItem(FAC_CACHE_KEY) || "null"); } catch { /* ignore */ }
    if (!all) {
      const r = await fetch(`${registryBase()}/facilities`, { cache: "no-store" });
      if (r.ok) {
        all = await r.json();
        try { localStorage.setItem(FAC_CACHE_KEY, JSON.stringify(all)); } catch { /* full — fine */ }
      }
    }
    return all ? nearest(all, lat, lng, 8).map(facilityRow) : null;
  }

  function locate() {
    if (!navigator.geolocation) { box.textContent = t("near_fail"); return; }
    box.textContent = t("near_locating");
    navigator.geolocation.getCurrentPosition(async (pos) => {
      const { latitude, longitude } = pos.coords;
      let rows = null;
      try { rows = await serverNear(latitude, longitude); } catch { /* offline */ }
      if (!rows) {
        try { rows = await cachedListNear(latitude, longitude); } catch { /* offline */ }
      }
      box.innerHTML = "";
      if (!rows || !rows.length) {
        box.append(el("div", { class: "empty" }, t("near_none")));
        return;
      }
      for (const f of rows) box.append(FacilityCard(f));
    }, () => { box.textContent = t("near_fail"); }, { timeout: 10000, maximumAge: 300000 });
  }

  return el("div", { class: "card near-card" },
    el("div", { class: "notif-main" },
      el("div", { class: "notif-kind" }, t("near_title")),
      el("div", { class: "notif-meta" }, t("near_sub"))),
    el("button", { class: "btn btn-primary btn-block", onclick: locate }, t("near_btn")),
    box);
}

function Card(n) {
  const meta = kindMeta(n.kind);
  const st = stateLabel(n.state);
  return el("div", { class: `card notif-card accent-${meta.accent}` },
    el("div", { class: "notif-glyph" }, meta.glyph),
    el("div", { class: "notif-main" },
      el("div", { class: "notif-kind" }, t(meta.titleKey)),
      el("div", { class: "notif-text" }, (n.text || "").replace(/^OpenHealth:\s*/, "")),
      el("div", { class: "notif-meta" },
        `${relTime(n.created_at)} · ${t("state_" + st)}`)));
}


/* ---------- self-service care tab (ADR-0017) ------------------------------
 * Book a visit, see your coverage, ask the clinic, manage who can see
 * your record — the Medplum-style interactive tier, on the same gate
 * (patient code + phone) as everything else. Each card fetches on first
 * view, keeps its last good answer, and says when it's stale. */

function portalFetch(path, opts = {}) {
  const sep = path.includes("?") ? "&" : "?";
  const url = `${apiBase()}${path}${sep}patient_id=${encodeURIComponent(state.patientId)}&phone=${encodeURIComponent(state.phone)}`;
  return fetch(url, { cache: "no-store", ...opts }).then((r) => {
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  });
}

function portalPost(path, body) {
  return portalFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, patient_id: state.patientId, phone: state.phone }),
  });
}

async function loadCare() {
  if (!state.patientId || state.careLoading || !navigator.onLine) return;
  state.careLoading = true;
  if (state.tab === "care") render();
  const settle = (p) => p.catch(() => null);
  const [coverage, appts, consents, claims] = await Promise.all([
    settle(portalFetch("/portal/coverage")),
    settle(portalFetch("/portal/appointments")),
    settle(portalFetch("/portal/consents")),
    settle(portalFetch("/portal/claims")),
  ]);
  if (coverage) state.coverage = coverage;
  if (appts) state.appts = appts.appointments || appts || [];
  if (consents) state.consents = consents.consents || [];
  if (claims) state.claims = claims.claims || [];
  state.careLoading = false;
  if (state.tab === "care") render();
}

/* --- coverage card --- */
function CoverageCard() {
  const sum = coverageSummary(state.coverage);
  const badge = sum.status === "active"
    ? el("span", { class: "pill pill-ok" }, t("cov_active"))
    : sum.status === "inactive"
      ? el("span", { class: "pill pill-warn" }, t("cov_inactive"))
      : el("span", { class: "pill" }, state.careLoading && !state.coverage ? t("loading") : t("cov_unknown"));
  return el("div", { class: "card care-card" },
    el("div", { class: "care-head" },
      el("div", { class: "notif-kind" }, t("cov_title")), badge),
    sum.detail ? el("div", { class: "notif-text" }, sum.detail) : null,
    !state.coverage && !state.careLoading
      ? el("div", { class: "notif-meta" }, navigator.onLine ? t("cov_none") : t("offline"))
      : null);
}

/* --- book-a-visit stepper: facility → service → date → slot --- */
function BookVisitCard() {
  const st = { facilities: null, facility: null, service: null, date: null, slots: null, busy: false, done: null };
  const box = el("div", { class: "book-box" });

  function dates() {
    const out = [];
    const d = new Date();
    for (let i = 0; i < 7; i++) {
      const x = new Date(d.getTime() + i * 86400000);
      out.push(x.toISOString().slice(0, 10));
    }
    return out;
  }

  async function pickFacilities() {
    box.innerHTML = "";
    box.append(el("div", { class: "muted" }, t("loading")));
    let facs = st.facilities;
    if (!facs) {
      try { facs = await portalFetch("/portal/facilities"); } catch { facs = null; }
      st.facilities = Array.isArray(facs) ? facs : [];
    }
    box.innerHTML = "";
    if (!st.facilities.length) { box.append(el("div", { class: "empty" }, t("bk_none"))); return; }
    for (const f of st.facilities) {
      box.append(el("button", {
        class: "btn btn-outline btn-block book-choice",
        onclick: () => { st.facility = f; st.service = null; pickService(); },
      }, `${f.name} — ${f.level || ""}`));
    }
  }

  function pickService() {
    box.innerHTML = "";
    box.append(el("div", { class: "muted book-crumb" }, st.facility.name));
    const services = (st.facility.services || []).length ? st.facility.services : ["general"];
    for (const sv of services) {
      box.append(el("button", {
        class: "btn btn-outline btn-block book-choice",
        onclick: () => { st.service = sv; pickDate(); },
      }, sv));
    }
    box.append(backBtn(pickFacilities));
  }

  function pickDate() {
    box.innerHTML = "";
    box.append(el("div", { class: "muted book-crumb" }, `${st.facility.name} · ${st.service}`));
    const row = el("div", { class: "chip-row" });
    for (const d of dates()) {
      row.append(el("button", {
        class: "chip" + (st.date === d ? " chip-on" : ""),
        onclick: () => { st.date = d; pickSlots(); },
      }, d.slice(5)));
    }
    box.append(row, backBtn(pickService));
  }

  async function pickSlots() {
    box.innerHTML = "";
    box.append(el("div", { class: "muted book-crumb" }, `${st.facility.name} · ${st.service} · ${st.date}`));
    box.append(el("div", { class: "muted" }, t("loading")));
    let slots = null;
    try {
      const d = await portalFetch(`/portal/slots?facility_id=${encodeURIComponent(st.facility.id)}&service=${encodeURIComponent(st.service)}&date=${st.date}`);
      slots = bookableSlots(d.slots || d);
    } catch { slots = null; }
    box.innerHTML = "";
    box.append(el("div", { class: "muted book-crumb" }, `${st.facility.name} · ${st.service} · ${st.date}`));
    if (!slots || !slots.length) {
      box.append(el("div", { class: "empty" }, t("bk_no_slots")), backBtn(pickDate));
      return;
    }
    const grid = el("div", { class: "slot-grid" });
    for (const sl of slots) {
      const hhmm = (sl.start || "").slice(11, 16);
      grid.append(el("button", { class: "slot-btn", onclick: () => book(sl) }, hhmm));
    }
    box.append(grid, backBtn(pickDate));
  }

  async function book(slot) {
    if (st.busy) return;
    st.busy = true;
    try {
      await portalPost("/portal/appointments", { slot_id: slot.id });
      st.done = slot;
      state.appts = null; // force refresh
      loadCare();
      box.innerHTML = "";
      box.append(el("div", { class: "banner banner-show banner-inline" },
        `${t("bk_done")} — ${(slot.start || "").slice(0, 10)} ${(slot.start || "").slice(11, 16)}`));
    } catch {
      box.append(el("div", { class: "banner banner-show banner-inline" }, t("bk_failed")));
    }
    st.busy = false;
  }

  function backBtn(fn) {
    return el("button", { class: "btn btn-outline book-back", onclick: fn }, `← ${t("bk_back")}`);
  }

  return el("div", { class: "card care-card" },
    el("div", { class: "care-head" }, el("div", { class: "notif-kind" }, t("bk_title"))),
    el("div", { class: "notif-meta" }, t("bk_sub")),
    box,
    el("button", {
      class: "btn btn-primary btn-block", onclick: () => { st.done = null; pickFacilities(); },
    }, t("bk_start")));
}

/* --- my appointments --- */
function MyAppointmentsCard() {
  const card = el("div", { class: "card care-card" },
    el("div", { class: "care-head" }, el("div", { class: "notif-kind" }, t("ap_title"))));
  const up = upcomingAppointments(state.appts || []);
  if (!up.length) {
    card.append(el("div", { class: "notif-meta" }, t("ap_none")));
    return card;
  }
  for (const a of up) {
    const row = el("div", { class: "appt-row" },
      el("div", { class: "notif-main" },
        el("div", { class: "notif-kind" }, a.service || "visit"),
        el("div", { class: "notif-meta" },
          `${(a.start || "").slice(0, 10)} ${(a.start || "").slice(11, 16)} · ${a.facility_id || ""}`)),
      el("button", {
        class: "btn btn-outline appt-cancel",
        onclick: async (ev) => {
          ev.target.disabled = true;
          try {
            await portalPost("/portal/appointments/cancel", { appointment_id: a.id });
            state.appts = null;
            loadCare();
          } catch { ev.target.disabled = false; toast(t("ap_cancel_failed")); }
        },
      }, t("ap_cancel")));
    card.append(row);
  }
  return card;
}

/* --- ask the clinic --- */
function AskClinicCard() {
  const ta = el("textarea", { class: "ask-input", rows: 3, placeholder: t("ask_placeholder") });
  const status = el("div", { class: "notif-meta" });
  return el("div", { class: "card care-card" },
    el("div", { class: "care-head" }, el("div", { class: "notif-kind" }, t("ask_title"))),
    el("div", { class: "notif-meta" }, t("ask_sub")),
    ta, status,
    el("button", {
      class: "btn btn-primary btn-block",
      onclick: async (ev) => {
        const complaint = ta.value.trim();
        if (!complaint) { status.textContent = t("ask_empty"); return; }
        ev.target.disabled = true;
        try {
          await portalPost("/portal/messages", { complaint, facility_id: "" });
          ta.value = "";
          status.textContent = t("ask_sent");
        } catch { status.textContent = t("ask_failed"); }
        ev.target.disabled = false;
      },
    }, t("ask_send")));
}

/* --- sharing: who can see my record --- */
function SharingCard() {
  const card = el("div", { class: "card care-card" },
    el("div", { class: "care-head" }, el("div", { class: "notif-kind" }, t("sh_title"))));
  const list = state.consents;
  if (!list || !list.length) {
    card.append(el("div", { class: "notif-meta" }, t("sh_none")));
    return card;
  }
  for (const c of list) {
    const revoked = c.status === "revoked";
    card.append(el("div", { class: "appt-row" },
      el("div", { class: "notif-main" },
        el("div", { class: "notif-kind" }, c.grantee || ""),
        el("div", { class: "notif-meta" }, `${c.scope || ""} · ${revoked ? t("sh_revoked") : c.status || ""}`)),
      revoked ? null : el("button", {
        class: "btn btn-outline appt-cancel",
        onclick: async (ev) => {
          ev.target.disabled = true;
          try {
            await portalPost("/portal/consents/revoke", { consent_id: c.id });
            state.consents = null;
            loadCare();
          } catch { ev.target.disabled = false; toast(t("sh_failed")); }
        },
      }, t("sh_revoke"))));
  }
  return card;
}

/* --- claims in my name (ADR-0018): the patient as auditor --------------
 * Every claim a facility submits against the patient's coverage is
 * listed here, with amount and date — and a dispute button that asks
 * one question: "did this care happen?" A disputed claim is flagged on
 * the facility's integrity scorecard and lands in the audit chain. */
function ClaimsCard() {
  const card = el("div", { class: "card care-card" },
    el("div", { class: "care-head" }, el("div", { class: "notif-kind" }, t("cl_title"))),
    el("div", { class: "notif-meta" }, t("cl_sub")));
  const rows = claimRows(state.claims || []);
  if (!rows.length) {
    card.append(el("div", { class: "notif-meta" },
      state.claims === null && state.careLoading ? t("loading") : t("cl_none")));
    return card;
  }
  for (const r of rows) {
    const main = el("div", { class: "notif-main" },
      el("div", { class: "notif-kind" }, `${nairaText(r.amount) || t("cl_amount_unknown")} · ${r.facility}`),
      el("div", { class: "notif-meta" },
        `${r.date} · ${r.decision}${r.disputed ? " · " + t("cl_disputed") : ""}`));
    if (r.disputed) {
      card.append(el("div", { class: "appt-row" }, main, null));
      continue;
    }
    const box = el("div", { class: "cl-dispute-box" });
    const btn = el("button", {
      class: "btn btn-outline appt-cancel",
      onclick: () => {
        box.innerHTML = "";
        const ta = el("textarea", { class: "ask-input", rows: 2, placeholder: t("cl_reason_placeholder") });
        box.append(ta, el("button", {
          class: "btn btn-primary btn-block",
          onclick: async (ev) => {
            const reason = ta.value.trim();
            if (!reason) { ta.focus(); return; }
            ev.target.disabled = true;
            try {
              await portalPost("/portal/claims/dispute", { claim_id: r.id, reason });
              state.claims = null;
              loadCare();
              toast(t("cl_disputed"));
            } catch { ev.target.disabled = false; toast(t("cl_failed")); }
          },
        }, t("cl_send")));
      },
    }, t("cl_dispute"));
    card.append(el("div", { class: "appt-row" }, main, btn), box);
  }
  return card;
}

/* --- passkey unlock (ADR-0017): device-bound, server-verified --- */
const PASSKEY_KEY = "openhealth-passkey";

function passkeySupported() {
  return !!(window.PublicKeyCredential && navigator.credentials);
}
function b64ToBuf(b64) {
  const s = atob(b64.replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out.buffer;
}
function bufToB64(buf) {
  const b = new Uint8Array(buf);
  let s = "";
  for (const x of b) s += String.fromCharCode(x);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function PasskeyCard() {
  if (!passkeySupported()) return null;
  const bound = (() => { try { return JSON.parse(localStorage.getItem(PASSKEY_KEY) || "null"); } catch { return null; } })();
  const status = el("div", { class: "notif-meta" },
    bound && bound.patientId === state.patientId ? t("pk_on") : t("pk_off"));
  return el("div", { class: "card care-card" },
    el("div", { class: "care-head" }, el("div", { class: "notif-kind" }, t("pk_title"))),
    el("div", { class: "notif-meta" }, t("pk_sub")), status,
    el("button", {
      class: "btn btn-outline btn-block",
      onclick: async (ev) => {
        ev.target.disabled = true;
        try {
          const begin = await portalPost("/portal/passkey/register/begin", {});
          const cred = await navigator.credentials.create({
            publicKey: {
              challenge: b64ToBuf(begin.challenge),
              rp: { id: begin.rp_id, name: "OpenHealth" },
              user: { id: b64ToBuf(begin.user_id), name: state.patientId, displayName: state.patientId },
              pubKeyCredParams: [{ type: "public-key", alg: -7 }],
              authenticatorSelection: { authenticatorAttachment: "platform", userVerification: "required" },
            },
          });
          await portalPost("/portal/passkey/register/finish", {
            attestation_object: bufToB64(cred.response.attestationObject),
            client_data_json: bufToB64(cred.response.clientDataJSON),
          });
          try { localStorage.setItem(PASSKEY_KEY, JSON.stringify({ patientId: state.patientId, phone: state.phone })); } catch { /* full */ }
          status.textContent = t("pk_on");
        } catch { status.textContent = t("pk_failed"); }
        ev.target.disabled = false;
      },
    }, t("pk_setup")));
}

/* Sign-in unlock button — shown when a passkey was bound on this device. */
function PasskeyUnlock() {
  if (!passkeySupported()) return null;
  let bound = null;
  try { bound = JSON.parse(localStorage.getItem(PASSKEY_KEY) || "null"); } catch { /* none */ }
  if (!bound || !bound.patientId || !bound.phone) return null;
  return el("button", {
    class: "btn btn-outline btn-block",
    onclick: async (ev) => {
      ev.target.disabled = true;
      try {
        const base = apiBase();
        const begin = await fetch(`${base}/portal/passkey/login/begin`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ patient_id: bound.patientId }),
        }).then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); });
        const cred = await navigator.credentials.get({
          publicKey: {
            challenge: b64ToBuf(begin.challenge),
            rpId: begin.rp_id,
            allowCredentials: (begin.allow_credentials || []).map((id) => ({ type: "public-key", id: b64ToBuf(id) })),
            userVerification: "required",
          },
        });
        await fetch(`${base}/portal/passkey/login/finish`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            patient_id: bound.patientId,
            cred_id: bufToB64(cred.rawId),
            authenticator_data: bufToB64(cred.response.authenticatorData),
            client_data_json: bufToB64(cred.response.clientDataJSON),
            signature: bufToB64(cred.response.signature),
          }),
        }).then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); });
        state.patientId = bound.patientId;
        state.phone = bound.phone;
        saveLocal();
        render();
        refresh();
      } catch { toast(t("pk_failed")); }
      ev.target.disabled = false;
    },
  }, `☝ ${t("pk_unlock")}`);
}


/* --- health check (screening questionnaire, ADR-0017): bundled FHIR
 * Questionnaire rendered on-device, answers become a
 * QuestionnaireResponse in the shared record. Danger signs say
 * "come in today" plainly. */
function HealthCheckCard() {
  const st = { q: null, answers: {}, started: false };
  const box = el("div", { class: "book-box" });

  async function start() {
    box.innerHTML = "";
    box.append(el("div", { class: "muted" }, t("loading")));
    if (!st.q) {
      try {
        st.q = await fetch("questionnaires/anc-screening.json", { cache: "no-store" }).then((r) => r.json());
      } catch {
        box.innerHTML = "";
        box.append(el("div", { class: "empty" }, t("hc_failed")));
        return;
      }
    }
    box.innerHTML = "";
    for (const item of st.q.item || []) {
      if (item.type === "boolean") {
        const row = el("div", { class: "hc-row" },
          el("div", { class: "hc-q" }, item.text || item.linkId),
          el("div", { class: "chip-row" },
            el("button", {
              class: "chip" + (st.answers[item.linkId] === true ? " chip-on" : ""),
              onclick: (e) => { st.answers[item.linkId] = true; mark(e.target); },
            }, t("hc_yes")),
            el("button", {
              class: "chip" + (st.answers[item.linkId] === false ? " chip-on" : ""),
              onclick: (e) => { st.answers[item.linkId] = false; mark(e.target); },
            }, t("hc_no"))));
        box.append(row);
      } else {
        box.append(el("label", { class: "field" },
          el("span", { class: "field-label" }, item.text || item.linkId),
          el("input", {
            class: "input", type: item.type === "text" ? "text" : "number",
            oninput: (e) => { st.answers[item.linkId] = e.target.value; },
          })));
      }
    }
    const status = el("div", { class: "notif-meta" });
    box.append(status, el("button", {
      class: "btn btn-primary btn-block",
      onclick: async (ev) => {
        const { response, missing, danger } = buildQuestionnaireResponse(st.q, st.answers);
        if (missing.length) { status.textContent = t("hc_missing"); return; }
        ev.target.disabled = true;
        try {
          await portalPost("/portal/questionnaire", { response });
          box.innerHTML = "";
          if (danger) {
            box.append(el("div", { class: "banner banner-show banner-inline" }, t("hc_danger")));
          } else {
            box.append(el("div", { class: "banner banner-show banner-inline" }, t("hc_done")));
          }
        } catch { status.textContent = t("hc_failed"); }
        ev.target.disabled = false;
      },
    }, t("hc_submit")));
  }

  function mark(btn) {
    btn.parentElement.querySelectorAll(".chip").forEach((c) => c.classList.remove("chip-on"));
    btn.classList.add("chip-on");
  }

  return el("div", { class: "card care-card" },
    el("div", { class: "care-head" }, el("div", { class: "notif-kind" }, t("hc_title"))),
    el("div", { class: "notif-meta" }, t("hc_sub")),
    box,
    el("button", { class: "btn btn-outline btn-block", onclick: start }, t("hc_start")));
}

function CareView() {
  const wrap = el("div", { class: "view" },
    Tabs(),
    el("div", { class: "feed-head" },
      el("h2", {}, t("tab_care")),
      el("button", { class: "btn btn-outline btn-refresh", onclick: loadCare }, t("refresh"))));
  const due = nextDue(state.appts || []);
  if (due) {
    wrap.append(el("div", { class: "card care-card care-due" },
      el("div", { class: "notif-kind" }, t("due_title")),
      el("div", { class: "notif-text" },
        `${(due.when || "").slice(0, 10)} ${(due.when || "").slice(11, 16)} — ${due.service} · ${due.where}`)));
  }
  wrap.append(
    CoverageCard(),
    BookVisitCard(),
    MyAppointmentsCard(),
    AskClinicCard(),
    HealthCheckCard(),
    ClaimsCard(),
    SharingCard(),
    PasskeyCard());
  return wrap;
}

/* ---------- wellness tab (ADR-0035): prevention plan + activity + nutrition + devices ---------- */

function WellnessView() {
  const wrap = el("div", { class: "view" });
  const kpis = wellnessKpis(state.wellness);
  const plan = planItems(state.wellnessPlan);
  const queued = wellnessQueue().length;

  // prevention plan — the reason the tab exists
  const planCard = el("div", { class: "card care-card" },
    el("div", { class: "care-head" },
      el("div", { class: "notif-kind" }, t("wl_plan")),
      plan?.risk ? el("span", { class: `pill pill-${riskBadge(plan.risk.cvd)}` },
        `${t("wl_risk")}: ${plan.risk.cvd}`) : null));
  if (plan) {
    if (plan.risk?.model) {
      planCard.append(el("div", { class: "notif-meta" },
        t("wl_risk_note").replace("{model}", plan.risk.model)));
    }
    for (const g of plan.overdue) {
      planCard.append(el("div", { class: "notif-text" }, `⏰ ${g.label} — ${g.note}`));
    }
    for (const r of plan.recs) {
      planCard.append(el("div", { class: "notif-text" }, `• ${r}`));
    }
    if (!plan.recs.length && !plan.overdue.length) {
      planCard.append(el("div", { class: "notif-text" }, t("wl_plan_ok")));
    }
  } else {
    planCard.append(el("div", { class: "notif-meta" },
      state.wellnessLoading ? t("loading") : t(navigator.onLine ? "wl_plan_none" : "offline")));
  }
  wrap.append(planCard);

  // today's numbers
  const strip = el("div", { class: "card" },
    el("div", { class: "care-head" }, el("div", { class: "notif-kind" }, t("wl_today"))));
  if (kpis.length) {
    const grid = el("div", { class: "wl-grid" });
    for (const k of kpis) {
      grid.append(el("div", { class: "wl-kpi" },
        el("div", { class: `wl-kpi-v ${k.kind === "ok" ? "wl-ok" : k.kind === "warn" ? "wl-warn" : ""}` }, k.value),
        el("div", { class: "notif-meta" }, k.sub)));
    }
    strip.append(grid);
  } else {
    strip.append(el("div", { class: "notif-meta" },
      state.wellnessLoading ? t("loading") : t("wl_no_data")));
  }
  if (queued) strip.append(el("div", { class: "notif-meta" },
    t("wl_queued_n").replace("{n}", queued)));
  wrap.append(strip);

  // quick logs — manual entry is the zero-device baseline
  wrap.append(QuickLogCard());

  // device connections — honest about how each source arrives
  const dev = el("div", { class: "card" },
    el("div", { class: "care-head" }, el("div", { class: "notif-kind" }, t("wl_devices"))));
  const active = new Set(state.wellness?.sources || []);
  const SOURCES_INFO = [
    ["health-connect", "wl_dev_hc"], ["apple-health", "wl_dev_ah"],
    ["fitbit", "wl_dev_fitbit"], ["generic-iot", "wl_dev_iot"],
  ];
  for (const [id, labelKey] of SOURCES_INFO) {
    dev.append(el("div", { class: "notif-text" },
      `${active.has(id) ? "🟢" : "⚪"} ${t(labelKey)}`));
  }
  dev.append(el("div", { class: "notif-meta" }, t("wl_devices_note")));
  wrap.append(dev);
  return wrap;
}

function QuickLogCard() {
  const card = el("div", { class: "card" },
    el("div", { class: "care-head" }, el("div", { class: "notif-kind" }, t("wl_log"))));
  const row = (labelKey, fn) => el("button", {
    class: "btn btn-outline btn-block",
    onclick: async () => { await fn(); },
  }, t(labelKey));
  card.append(
    row("wl_log_workout", () => {
      const mins = prompt(t("wl_log_workout_prompt"), "30");
      if (mins) return logWellness("workout", { text: `${mins} min workout`, value: +mins, unit: "min" });
    }),
    row("wl_log_nutrition", () => {
      const meal = prompt(t("wl_log_nutrition_prompt"), "");
      if (meal) return logWellness("nutrition", { text: meal });
    }),
    row("wl_log_weight", () => {
      const kg = prompt(t("wl_log_weight_prompt"), "");
      if (kg) return logWellness("weight", { value: +kg, unit: "kg" });
    }),
    row("wl_log_steps", () => {
      const n = prompt(t("wl_log_steps_prompt"), "");
      if (n) return logWellness("steps", { value: +n, unit: "steps" });
    }),
  );
  return card;
}

/* ---------- boot ------------------------------------------------------ */

function render() {
  document.title = t("app_name");
  $("#app-title").textContent = t("app_name");
  const root = $("#view-root");
  root.innerHTML = "";
  root.append(state.patientId
    ? (state.tab === "record" ? RecordView()
      : state.tab === "care" ? CareView()
      : state.tab === "wellness" ? WellnessView()
      : state.tab === "timeline" ? TimelineView()
      : FeedView())
    : SignInView());
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

function boot() {
  const saved = loadLocal();
  if (saved?.language) setLanguage(saved.language);
  if (saved?.patientId) {
    state.patientId = saved.patientId;
    state.phone = saved.phone;
    state.feed = saved.feed;
    state.updatedAt = saved.updatedAt;
    loadRecordLocal();
  }
  window.addEventListener("online", () => { render(); refresh(); flushWellnessQueue(); });
  window.addEventListener("offline", render);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  render();
  refresh();                       // opportunistic freshness
  flushWellnessQueue();            // drain any offline wellness logs
  setInterval(refresh, 5 * 60000); // gentle 5-minute poll when online
}

boot();
