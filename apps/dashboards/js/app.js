/* OpenHealth OS dashboards — boot, router, refresh loop.
 * Boards render from cache first (instant, offline), then live data
 * replaces it; a DEMO badge is shown whenever nothing live was reached.
 */

import { el } from "./dom.js";
import { DataSource } from "./data.js";
import { FacilityView, StateView, PayerView, MapView, EconomicsView, MLHealthView, ReadinessView, ImmunizationView, renderTicker } from "./views.js";

const REFRESH_MS = 30000;
const ds = new DataSource();
let last = null;

const $ = (s) => document.querySelector(s);

function currentView() {
  const h = location.hash || "#/state";
  if (h.startsWith("#/facility")) {
    return { name: "facility", id: h.split("/")[2] || null };
  }
  if (h.startsWith("#/payers")) {
    return { name: "payers" };
  }
  if (h.startsWith("#/map")) {
    return { name: "map", id: h.split("/")[2] || null };
  }
  if (h.startsWith("#/economics")) {
    return { name: "economics" };
  }
  if (h.startsWith("#/mlhealth")) {
    return { name: "mlhealth" };
  }
  if (h.startsWith("#/readiness")) {
    return { name: "readiness" };
  }
  if (h.startsWith("#/immig")) {
    return { name: "immig" };
  }
  return { name: "state" };
}

function applyThemeFor(view) {
  const saved = localStorage.getItem("oh-dash-theme");
  const theme = saved || (view === "facility" ? "daylight" : "ops");
  document.documentElement.setAttribute("data-theme", theme);
  $("#theme").textContent = theme === "ops" ? "◐ daylight" : "◑ ops";
}

async function render() {
  const view = currentView();
  applyThemeFor(view.name);
  for (const a of document.querySelectorAll(".viewswitch a")) {
    a.classList.toggle("active",
      (view.name === "state" && a.dataset.view === "state") ||
      (view.name === "facility" && a.dataset.view === "facility") ||
      (view.name === "payers" && a.dataset.view === "payers") ||
      (view.name === "map" && a.dataset.view === "map") ||
      (view.name === "economics" && a.dataset.view === "economics") ||
      (view.name === "mlhealth" && a.dataset.view === "mlhealth") ||
      (view.name === "immig" && a.dataset.view === "immig"));
  }
  const root = $("#board");
  root.innerHTML = "";
  if (!last) { // skeleton while the first snapshot lands
    root.append(el("div", { class: "page" },
      el("div", { class: "skel", style: "width:40%" }),
      el("div", { class: "skel" }), el("div", { class: "skel" })));
    try {
      last = await ds.load();
    } catch (err) {
      console.error("load failed, rendering demo set", err);
      const { demoData } = await import("./data.js");
      last = { ...demoData(), demo: true, loadedAt: Date.now() };
    }
  }
  root.innerHTML = "";
  root.append(view.name === "facility" ? FacilityView(last, view.id)
    : view.name === "payers" ? PayerView(last)
    : view.name === "map" ? MapView(last, view.id)
    : view.name === "economics" ? EconomicsView(last)
    : view.name === "mlhealth" ? MLHealthView(last)
    : view.name === "readiness" ? ReadinessView(last)
    : view.name === "immig" ? ImmunizationView(last)
    : StateView(last));
  renderTicker($("#ticker-track"), last);

  const badge = $("#live");
  badge.className = `live-badge ${last.demo ? "demo" : "on"}`;
  badge.lastChild.textContent = last.demo ? " demo data" : " live";
}

async function refreshLoop() {
  try { last = await ds.load(); } catch { /* keep last-known */ }
  render();
}

function boot() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  window.addEventListener("hashchange", render);
  $("#theme").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "ops" ? "daylight" : "ops";
    localStorage.setItem("oh-dash-theme", next);
    render();
  });
  $("#syncnow").addEventListener("click", refreshLoop);
  window.addEventListener("online", refreshLoop);
  render();
  setInterval(refreshLoop, REFRESH_MS);
}

document.addEventListener("DOMContentLoaded", boot);
