/* Patient app — boot, sign-in (patient code + phone), feed rendering.
 * Read-mostly by design: the patient's data is their notification feed.
 * Last-known feed is cached in localStorage so the app still opens with
 * zero connectivity; it never pretends stale data is live.
 */

import { LANGUAGES, setLanguage, getLanguage, t } from "./i18n.js";
import { kindMeta, groupFeed, relTime, stateLabel } from "./logic.js";
import { nearest, facilityRow } from "./geo.js";

const LS_KEY = "openhealth-patient";
const $ = (sel) => document.querySelector(sel);

const state = {
  patientId: null,
  phone: null,
  feed: null,        // { notifications: [...] } last good fetch
  updatedAt: null,
  loading: false,
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
      }, t("sign_in_btn"))),
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

function FeedView() {
  const wrap = el("div", { class: "view" },
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
          try { localStorage.removeItem(LS_KEY); } catch { /* ignore */ }
          render();
        },
      }, t("sign_out"))));
  return wrap;
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

/* ---------- boot ------------------------------------------------------ */

function render() {
  document.title = t("app_name");
  $("#app-title").textContent = t("app_name");
  const root = $("#view-root");
  root.innerHTML = "";
  root.append(state.patientId ? FeedView() : SignInView());
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
  }
  window.addEventListener("online", () => { render(); refresh(); });
  window.addEventListener("offline", render);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  render();
  refresh();                       // opportunistic freshness
  setInterval(refresh, 5 * 60000); // gentle 5-minute poll when online
}

boot();
