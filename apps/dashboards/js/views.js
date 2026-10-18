/* Views — facility board (daylight default, sunlight-readable) and
 * state situation room (dark ops default). Both render from the same
 * DataSource snapshot and degrade to last-known data offline. */

import { el } from "./dom.js";
import { dial, sparkline, facilityMap, hbar } from "./charts.js";
import { eventsByType, encountersToday, perFacilityCounts, pilotGates,
         overviewRows, platformHealth, payerRows, payerSummary } from "./data.js";
import { nearest, navLink, renderMap, hitTest } from "./map.js";

/* ---------- shared bits ---------- */

function kpi(value, label, { kind = "", sub = "" } = {}) {
  return el("div", { class: "kpi" },
    el("div", { class: `kpi-value mono ${kind}` }, value),
    el("div", { class: "kpi-label" }, label),
    sub ? el("div", { class: "kpi-sub" }, sub) : null);
}

function fmtUptime(sec) {
  const d = Math.floor(sec / 86400);
  return d > 0 ? `${d}d` : `${Math.floor(sec / 3600)}h`;
}

function facilityStatus(f, counts, tickets) {
  const open = (tickets || []).filter((t) => t.facility === f.id && t.status !== "resolved");
  const c = counts[f.id];
  if (open.some((t) => /cold|fridge|solar|inverter/i.test(t.equipment || ""))) return "crit";
  if (open.length) return "warn";
  if (!c || c.total === 0) return "dark";
  return "ok";
}

function alertTicker(data) {
  const items = [];
  for (const t of data.tickets || []) {
    if (t.status === "resolved") continue;
    const sev = /cold|fridge/i.test(t.equipment || "") ? "crit" : "warn";
    items.push({ sev, text: `${t.facility} · ${t.equipment} — ${t.note || t.status}` });
  }
  const counts = perFacilityCounts(data.events);
  for (const f of data.facilities || []) {
    if (!counts[f.id]) items.push({ sev: "warn", text: `${f.name} · no events received — check sync` });
  }
  if (!items.length) items.push({ sev: "info", text: "All facilities reporting · no open critical tickets" });
  return items;
}

export function renderTicker(track, data) {
  track.innerHTML = "";
  for (const it of alertTicker(data)) {
    track.append(el("span", { class: `ticker-item ${it.sev}` },
      el("b", {}, it.sev.toUpperCase()), it.text));
  }
}

/* ---------- facility board ---------- */

export function FacilityView(data, facilityId) {
  const counts = perFacilityCounts(data.events);
  const facilities = data.facilities || [];
  const f = facilities.find((x) => x.id === facilityId) || facilities[0]
    || { id: "—", name: "No facility", lga: "" };
  const fc = counts[f.id] || { encounters: 0, claims: 0, dispensing: 0, total: 0 };
  const byType = eventsByType((data.events || []).filter((e) => e.facility === f.id));
  const eq = (data.equipment || []).filter((e) => e.facility === f.id);
  const tickets = (data.tickets || []).filter((t) => t.facility === f.id && t.status !== "resolved");

  const page = el("div", { class: "page" });

  // facility picker
  const picker = el("select", { class: "themebtn", onchange: (e) => { location.hash = `#/facility/${e.target.value}`; } });
  for (const x of facilities) {
    const o = el("option", { value: x.id }, `${x.id} — ${x.name}`);
    if (x.id === f.id) o.selected = true;
    picker.append(o);
  }

  page.append(
    el("div", { style: "display:flex;align-items:center;gap:12px;flex-wrap:wrap" },
      el("h1", { style: "font-size:20px;font-weight:700" }, f.name),
      el("span", { class: "kpi-sub mono" }, `${f.id} · ${f.lga || ""}`),
      picker),
  );

  page.append(el("div", { class: "section-label" }, "Today"));
  page.append(el("div", { class: "kpi-strip" },
    kpi(String(encountersToday(data.events.filter((e) => e.facility === f.id))), "encounters today"),
    kpi(String(fc.claims), "claims submitted"),
    kpi(String(fc.dispensing), "dispensings"),
    kpi(String(tickets.length), "open tickets", { kind: tickets.length ? "warn" : "ok" })));

  // infrastructure dials
  const tele = (kind) => eq.find((e) => e.kind === kind)?.telemetry || {};
  const fridge = tele("cold-chain fridge"), solar = tele("solar array"), tank = tele("water tank");
  const infra = el("div", { class: "panel" },
    el("div", { class: "panel-title" }, "Infrastructure — the platform knows before a vaccine dies"),
    el("div", { class: "dial-grid" },
      el("div", { class: "dial" },
        dial({ value: fridge.cold_chain_c ?? 0, max: 15, band: [2, 8], unit: "°C" }),
        el("div", { class: "dial-label" }, "Cold chain (2–8°C)")),
      el("div", { class: "dial" },
        dial({ value: solar.battery_soc_pct ?? 0 }),
        el("div", { class: "dial-label" }, "Battery SOC")),
      el("div", { class: "dial" },
        dial({ value: tank.water_tank_pct ?? 0 }),
        el("div", { class: "dial-label" }, "Water tank"))));

  // activity sparkline: encounters per day, last 7 days
  const days = [...Array(7)].map((_, i) => {
    const d0 = new Date(); d0.setHours(0, 0, 0, 0); d0.setDate(d0.getDate() - (6 - i));
    const d1 = d0.getTime() + 86400000;
    return (data.events || []).filter((e) =>
      e.facility === f.id && e.type === "encounter" &&
      Date.parse(e.ts) >= d0.getTime() && Date.parse(e.ts) < d1).length;
  });
  const activity = el("div", { class: "panel" },
    el("div", { class: "panel-title" }, "Encounter trend — 7 days"),
    sparkline(days),
    el("div", { class: "kpi-sub mono" }, `${days.reduce((a, b) => a + b, 0)} encounters · ${byType.claim_submitted || 0} claims · ${byType.stock_report || 0} stock reports`));

  // open tickets
  const tk = el("div", { class: "panel" },
    el("div", { class: "panel-title" }, `Equipment tickets (${tickets.length} open)`));
  if (!tickets.length) tk.append(el("div", { class: "empty" }, "No open tickets — equipment is holding."));
  for (const t of tickets) {
    tk.append(el("div", { class: `alert-card ${/cold|fridge/i.test(t.equipment || "") ? "crit" : "warn"}` },
      el("div", { class: "alert-head" },
        el("span", { class: `sev-${/cold|fridge/i.test(t.equipment || "") ? "crit" : "warn"}` }, t.status),
        el("span", { class: "mono" }, t.id)),
      el("div", { class: "alert-msg" }, `${t.equipment} — ${t.note || ""}`),
      el("div", { class: "alert-ts" }, `opened ${new Date(t.opened).toLocaleString()}`)));
  }

  page.append(el("div", { class: "grid-2" }, infra, activity), tk);
  return page;
}

/* ---------- state situation room ---------- */

export function StateView(data) {
  const counts = perFacilityCounts(data.events);
  const rows = overviewRows(data.overview);
  const gates = pilotGates(data.events, { trainedStaff: 48, facilities: (data.facilities || []).length || 1 });
  const facilities = data.facilities || [];

  const page = el("div", { class: "page" });

  const live = facilities.filter((f) => facilityStatus(f, counts, data.tickets) === "ok").length;
  const claims = (data.events || []).filter((e) => e.type === "claim_submitted");
  const tatGate = gates.find((g) => g.name.startsWith("Claims"));

  page.append(el("div", { class: "section-label" }, "State overview"));
  page.append(el("div", { class: "kpi-strip" },
    kpi(`${live}/${facilities.length}`, "facilities live", { kind: live === facilities.length ? "ok" : "warn" }),
    kpi(String((data.events || []).filter((e) => e.type === "encounter").length), "encounters (7d)"),
    kpi(tatGate && tatGate.value != null ? `${tatGate.value}d` : "—", "claims turnaround",
      { kind: tatGate && tatGate.pass ? "ok" : "warn", sub: "median, target <14d" }),
    kpi(String((data.tickets || []).filter((t) => t.status !== "resolved").length), "open equipment tickets",
      { kind: (data.tickets || []).some((t) => t.status !== "resolved") ? "warn" : "ok" }),
    kpi(platformHealth(rows) === "ok" ? "ALL UP" : platformHealth(rows) === "warn" ? "DEGRADED" : "DOWN",
      "platform services", { kind: platformHealth(rows) })));

  // watchlist
  const watch = el("div", { class: "panel" },
    el("div", { class: "panel-title" }, "Facility watchlist"));
  for (const f of facilities) {
    const st = facilityStatus(f, counts, data.tickets);
    const c = counts[f.id] || { encounters: 0 };
    watch.append(el("div", { class: "watch-row", onclick: () => { location.hash = `#/facility/${f.id}`; } },
      el("span", { class: `status-dot ${st}` }),
      el("div", {},
        el("div", { class: "watch-name" }, f.name),
        el("div", { class: "watch-sub" }, `${f.lga || ""} · ${f.kind}`)),
      el("div", { class: "watch-metric" }, String(c.encounters), el("small", {}, "encounters"))));
  }

  // map + gates
  const mapPanel = el("div", { class: "panel mapwrap" },
    el("div", { class: "panel-title" }, "State plane — live facility status"),
    facilityMap(facilities, (f) => facilityStatus(f, counts, data.tickets)),
    el("div", { class: "map-legend" },
      el("span", {}, el("span", { class: "status-dot ok" }), " reporting"),
      el("span", {}, el("span", { class: "status-dot warn" }), " ticket open"),
      el("span", {}, el("span", { class: "status-dot crit" }), " critical"),
      el("span", {}, el("span", { class: "status-dot dark" }), " silent")));

  const gatePanel = el("div", { class: "panel" },
    el("div", { class: "panel-title" }, "Pilot gates — measured, not asserted"));
  for (const g of gates) {
    const pct = g.value == null ? 0 : Math.min(100, (g.value / g.target) * 100);
    gatePanel.append(el("div", { class: "gate-row" },
      el("span", { class: "gate-name" }, g.name),
      el("span", { class: "gate-bar" },
        el("span", { class: `gate-fill ${g.pass ? "pass" : "fail"}`, style: `width:${g.pass ? 100 : pct}%` })),
      el("span", { class: `gate-val mono ${g.pass ? "pass" : "fail"}` },
        g.value == null ? "no data" : `${g.value}${g.unit} / ${g.target}${g.unit}`)));
  }

  // alerts column
  const alerts = el("div", { class: "panel" },
    el("div", { class: "panel-title" }, "Alerts & actions"));
  const openTickets = (data.tickets || []).filter((t) => t.status !== "resolved");
  if (!openTickets.length) alerts.append(el("div", { class: "empty" }, "Nothing needs attention."));
  for (const t of openTickets) {
    const sev = /cold|fridge/i.test(t.equipment || "") ? "crit" : "warn";
    alerts.append(el("div", { class: `alert-card ${sev}` },
      el("div", { class: "alert-head" },
        el("span", { class: `sev-${sev}` }, sev === "crit" ? "critical" : "warning"),
        el("span", { class: "mono" }, t.id)),
      el("div", { class: "alert-msg" }, `${t.facility} · ${t.equipment} — ${t.note || ""}`),
      el("div", { class: "alert-ts" }, `${t.status} · opened ${new Date(t.opened).toLocaleString()}`)));
  }

  // service health matrix
  const matrix = el("div", { class: "panel" },
    el("div", { class: "panel-title" }, "Platform services"));
  const table = el("table", { class: "matrix" },
    el("tr", {},
      el("th", {}, "service"), el("th", {}, "state"), el("th", {}, "req"),
      el("th", {}, "5xx%"), el("th", {}, "ms"), el("th", {}, "uptime")));
  for (const r of rows) {
    table.append(el("tr", {},
      el("td", {}, r.service),
      el("td", {}, el("span", { class: `status-dot ${r.up ? "ok" : "crit"}`, style: "display:inline-block" })),
      el("td", { class: "mono" }, String(r.requests)),
      el("td", { class: "mono" }, r.errRate.toFixed(2)),
      el("td", { class: "mono" }, r.latency.toFixed(1)),
      el("td", { class: "mono" }, fmtUptime(r.uptime))));
  }
  matrix.append(table);

  page.append(
    el("div", { class: "grid-3" }, watch, mapPanel, alerts),
    el("div", { class: "grid-2" }, gatePanel, matrix));
  return page;
}

/* ---------- facility map (ADR-0014) — offline geographic view ----------
 * Canvas render over the bundled Nigeria boundary; capacity profile
 * (beds, doctors, nurses, amenities) on every marker; near-me uses the
 * client-side haversine over the cached facility list so it works with
 * zero connectivity. */

let boundaryPromise = null;
function loadBoundary() {
  if (!boundaryPromise) {
    boundaryPromise = fetch("assets/nigeria-boundary.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`http ${r.status}`))))
      .then((g) => g.geometry.coordinates[0])
      .catch(() => []);
  }
  return boundaryPromise;
}

const mapFilters = { level: "", amenity: "" };
const AMENITIES = ["pharmacy", "lab", "maternity", "xray", "ambulance", "oxygen", "cold-chain"];

function mapFacilities(data) {
  return (data.facilities || []).filter((f) =>
    (!mapFilters.level || f.level === mapFilters.level) &&
    (!mapFilters.amenity || (f.amenities || []).includes(mapFilters.amenity)));
}

function capacityLine(f) {
  const bits = [];
  if (f.beds) bits.push(`${f.beds} beds`);
  if (f.doctors) bits.push(`${f.doctors} doctors`);
  if (f.nurses) bits.push(`${f.nurses} nurses`);
  return bits.join(" · ") || "capacity not reported";
}

function facilityDetailCard(f, data) {
  const card = el("div", { class: "panel" },
    el("div", { class: "panel-title" }, f.name),
    el("div", { class: "kpi-sub mono" }, `${f.id} · ${f.level || f.kind || ""} · ${f.lga || ""}${f.state ? ", " + f.state : ""}`),
    el("div", { class: "kpi-strip" },
      kpi(String(f.beds || 0), "beds"),
      kpi(String(f.doctors || 0), "doctors"),
      kpi(String(f.nurses || 0), "nurses")));
  const chips = el("div", { class: "chip-row" });
  for (const a of f.amenities || []) chips.append(el("span", { class: "chip" }, a));
  if (!(f.amenities || []).length) chips.append(el("span", { class: "kpi-sub" }, "no amenities reported"));
  card.append(chips);
  const meta = [];
  if (f.phone) meta.push(`☎ ${f.phone}`);
  if (f.hours) meta.push(`Hours: ${f.hours}`);
  if (meta.length) card.append(el("div", { class: "kpi-sub mono" }, meta.join(" · ")));
  if (f.latitude || f.longitude) {
    card.append(el("a", { class: "themebtn", href: navLink(f), style: "display:inline-block;margin-top:8px;text-decoration:none" },
      "⟶ Navigate (opens maps app)"));
    const near = nearest((data.facilities || []).filter((x) => x.id !== f.id), f.latitude, f.longitude, 4);
    if (near.length) {
      card.append(el("div", { class: "section-label" }, "Nearest alternatives"));
      for (const n of near) {
        card.append(el("div", { class: "watch-row", onclick: () => { location.hash = `#/map/${n.id}`; } },
          el("div", {},
            el("div", { class: "watch-name" }, n.name),
            el("div", { class: "watch-sub" }, `${n.level || n.kind || ""} · ${capacityLine(n)}`)),
          el("div", { class: "watch-metric" }, `${n.distance_km}`, el("small", {}, "km"))));
      }
    }
  }
  return card;
}

export function MapView(data, selectedId) {
  const all = data.facilities || [];
  const facilities = mapFacilities(data);
  const selected = all.find((f) => f.id === selectedId) || null;
  const page = el("div", { class: "page" });

  const nearBox = el("div", {});
  const nearBtn = el("button", { class: "themebtn", onclick: () => {
    if (!navigator.geolocation) { nearBox.textContent = "Geolocation is not available on this device."; return; }
    nearBox.textContent = "Locating…";
    navigator.geolocation.getCurrentPosition((pos) => {
      const near = nearest(facilities, pos.coords.latitude, pos.coords.longitude, 5);
      nearBox.innerHTML = "";
      if (!near.length) { nearBox.append(el("div", { class: "empty" }, "No placed facilities in view.")); return; }
      nearBox.append(el("div", { class: "section-label" }, `Closest to you (${pos.coords.latitude.toFixed(3)}, ${pos.coords.longitude.toFixed(3)})`));
      for (const n of near) {
        const row = el("div", { class: "watch-row", onclick: () => { location.hash = `#/map/${n.id}`; } },
          el("div", {},
            el("div", { class: "watch-name" }, n.name),
            el("div", { class: "watch-sub" }, capacityLine(n))));
        row.append(el("div", { class: "watch-metric" }, `${n.distance_km}`, el("small", {}, "km")));
        nearBox.append(row);
      }
    }, () => { nearBox.textContent = "Location unavailable — allow location access and try again."; },
       { timeout: 8000, maximumAge: 300000 });
  } }, "◎ Facilities near me");

  const rerender = () => window.dispatchEvent(new Event("hashchange"));
  const levelSel = el("select", { class: "themebtn", onchange: (e) => { mapFilters.level = e.target.value; rerender(); } },
    el("option", { value: "" }, "all levels"),
    ...["PHC", "clinic", "hospital"].map((l) => {
      const o = el("option", { value: l }, l);
      if (mapFilters.level === l) o.selected = true;
      return o;
    }));
  const amenitySel = el("select", { class: "themebtn", onchange: (e) => { mapFilters.amenity = e.target.value; rerender(); } },
    el("option", { value: "" }, "any amenity"),
    ...AMENITIES.map((a) => {
      const o = el("option", { value: a }, a);
      if (mapFilters.amenity === a) o.selected = true;
      return o;
    }));

  page.append(
    el("div", { style: "display:flex;align-items:center;gap:12px;flex-wrap:wrap" },
      el("h1", { style: "font-size:20px;font-weight:700" }, "Facility map"),
      el("span", { class: "kpi-sub mono" },
        `${facilities.length}/${all.length} shown · ${all.filter((f) => f.latitude || f.longitude).length} geo-placed`),
      nearBtn, levelSel, amenitySel));

  const canvas = el("canvas", { width: 900, height: 620, class: "geomap" });
  const placed = facilities.filter((f) => f.latitude || f.longitude);
  loadBoundary().then((boundary) => {
    const res = boundary.length ? renderMap(canvas, boundary, placed, selectedId) : null;
    if (res) {
      canvas.addEventListener("click", (ev) => {
        const rect = canvas.getBoundingClientRect();
        const x = (ev.clientX - rect.left) * (canvas.width / rect.width);
        const y = (ev.clientY - rect.top) * (canvas.height / rect.height);
        const id = hitTest(res.hits, x, y);
        if (id) location.hash = `#/map/${id}`;
      });
    }
  });

  const mapPanel = el("div", { class: "panel mapwrap" },
    el("div", { class: "panel-title" }, "Nigeria — tap a marker for beds, staffing, amenities, navigation"),
    canvas,
    el("div", { class: "map-legend" },
      el("span", {}, el("span", { class: "status-dot", style: "background:#3f9d63;display:inline-block" }), " PHC"),
      el("span", {}, el("span", { class: "status-dot", style: "background:#e2a44f;display:inline-block" }), " clinic"),
      el("span", {}, el("span", { class: "status-dot", style: "background:#e2554f;display:inline-block" }), " hospital"),
      el("span", { class: "kpi-sub" }, "marker size = beds")));

  const side = el("div", {});
  if (selected) side.append(facilityDetailCard(selected, data));
  else {
    const summary = el("div", { class: "panel" },
      el("div", { class: "panel-title" }, "Capacity in view"));
    const beds = placed.reduce((a, f) => a + (f.beds || 0), 0);
    const docs = placed.reduce((a, f) => a + (f.doctors || 0), 0);
    const nurses = placed.reduce((a, f) => a + (f.nurses || 0), 0);
    summary.append(el("div", { class: "kpi-strip" },
      kpi(String(beds), "beds"), kpi(String(docs), "doctors"), kpi(String(nurses), "nurses")));
    summary.append(el("div", { class: "empty" },
      placed.length ? "Select a marker on the map for its full profile." : "No facilities with coordinates yet — registry entries need latitude/longitude."));
    side.append(summary);
  }
  side.append(nearBox);

  page.append(el("div", { class: "grid-2" }, mapPanel, side));
  return page;
}

/* ---------- payer board (NHIA + HMO view, ADR-0012) ---------- */

function fmtNGN(n) {
  return "₦" + (n || 0).toLocaleString("en-NG");
}

export function PayerView(data) {
  const rows = payerRows(data.payers);
  const sum = payerSummary(rows);
  const page = el("div", { class: "page" },
    el("div", { class: "kpis" },
      kpi(sum.totalClaims.toLocaleString(), "claims adjudicated", { sub: `${sum.payers} payers` }),
      kpi(fmtNGN(sum.totalPayable), "payable volume", { kind: "ok" }),
      kpi(sum.unmapped ? String(sum.unmapped) : "0", "unmapped schemes",
        { kind: sum.unmapped ? "warn" : "ok", sub: "claims with no payer route" })),
    el("h2", {}, "Payable by payer"));
  const bars = el("div", {});
  for (const r of rows) {
    bars.append(el("div", { class: "hbar-row" },
      el("span", {}, `${r.payer}`),
      hbar(r.payableShare / 100),
      el("span", { class: "mono" }, fmtNGN(r.payable))));
  }
  page.append(bars);
  const table = el("table", { class: "matrix" },
    el("tr", {},
      el("th", {}, "Payer"), el("th", {}, "Type"), el("th", {}, "Claims"),
      el("th", {}, "Approval"), el("th", {}, "Pending"), el("th", {}, "Payable"), el("th", {}, "Share")));
  for (const r of rows) {
    table.append(el("tr", { class: r.kind === "unmapped" ? "warn" : "" },
      el("td", {}, r.payer),
      el("td", {}, r.kind),
      el("td", { class: "mono" }, String(r.claims)),
      el("td", { class: `mono ${r.approvalRate >= 85 ? "ok" : "warn"}` }, `${r.approvalRate}%`),
      el("td", { class: `mono ${r.pendRate > 10 ? "warn" : ""}` }, `${r.pendRate}%`),
      el("td", { class: "mono" }, fmtNGN(r.payable)),
      el("td", { class: "mono" }, `${r.payableShare}%`)));
  }
  page.append(el("h2", {}, "Decision mix"), table);
  return page;
}
