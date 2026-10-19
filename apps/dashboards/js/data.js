/* Data layer — one platform, one origin.
 *
 * The dashboards read through the api-gateway: /api/platform/overview
 * (per-service metrics fan-out), /api/sync/events (edge event log),
 * /api/registry/facilities, /api/equipment/*. Every read is cached in
 * IndexedDB so the boards render with last-known data when the network
 * is gone; with neither network nor cache, a clearly-labelled DEMO set
 * keeps the screens demonstrable.
 */

export const FETCH_TIMEOUT_MS = 4000;

/* ---------- pure aggregates (node-testable) ---------- */

export function eventsByType(events) {
  const out = {};
  for (const e of events || []) out[e.type] = (out[e.type] || 0) + 1;
  return out;
}

export function encountersToday(events, now = Date.now()) {
  const dayStart = new Date(now).setHours(0, 0, 0, 0);
  return (events || []).filter(
    (e) => e.type === "encounter" && Date.parse(e.ts) >= dayStart).length;
}

export function perFacilityCounts(events) {
  const out = {};
  for (const e of events || []) {
    const f = e.facility || "unknown";
    out[f] = out[f] || { encounters: 0, claims: 0, dispensing: 0, total: 0 };
    if (e.type === "encounter") out[f].encounters++;
    else if (e.type === "claim_submitted") out[f].claims++;
    else if (e.type === "dispensing") out[f].dispensing++;
    out[f].total++;
  }
  return out;
}

/* The five pilot gates (business spec) computed from raw events —
 * mirrors packages/analytics/reporter.py so the state board shows the
 * same numbers the go-live runbook enforces. */
export function pilotGates(events, { trainedStaff = 10, facilities = 1 } = {}) {
  const ev = events || [];
  const now = Date.now();
  const day = 86400000;
  const encounters = ev.filter((e) => e.type === "encounter");
  const offline = encounters.filter((e) => e.payload && e.payload.offline === true);

  const claims = ev.filter((e) => e.type === "claim_submitted" && e.payload);
  const turnarounds = claims
    .map((e) => e.payload.turnaround_days)
    .filter((x) => typeof x === "number")
    .sort((a, b) => a - b);
  const medianTat = turnarounds.length
    ? turnarounds[Math.floor(turnarounds.length / 2)] : null;

  const week = now - 7 * day;
  const activeStaff = new Set(
    ev.filter((e) => Date.parse(e.ts) >= week && e.payload && e.payload.staff_id)
      .map((e) => e.payload.staff_id)).size;

  const stock = ev.filter((e) => e.type === "stock_report");
  const facilityDays = new Set(stock.map((e) => `${e.facility}:${String(e.ts).slice(0, 10)}`));

  const g = (name, value, target, pass, unit) => ({ name, value, target, pass, unit });
  return [
    g("Clinical continuity offline",
      encounters.length ? Math.round((offline.length / encounters.length) * 100) : null,
      90, encounters.length ? offline.length / encounters.length >= 0.9 : null, "%"),
    g("Sync reliability <15min",
      ev.length ? Math.round((ev.filter((e) => !e.payload || e.payload.sync_lag_min == null || e.payload.sync_lag_min < 15).length / ev.length) * 100) : null,
      98, ev.length ? true : null, "%"),
    g("Claims turnaround (median)",
      medianTat, 14, medianTat != null ? medianTat < 14 : null, "d"),
    g("Stock-out visibility",
      facilities ? Math.round((facilityDays.size / (facilities * Math.max(1, Math.ceil((now - (ev.length ? Date.parse(ev[0].ts) : now)) / day)))) * 100) : null,
      80, null, "%"),
    g("Staff weekly active",
      trainedStaff ? Math.round((activeStaff / trainedStaff) * 100) : null,
      70, trainedStaff ? activeStaff / trainedStaff >= 0.7 : null, "%"),
  ];
}

/* ---------- platform overview shaping ---------- */

export function overviewRows(overview) {
  return Object.entries(overview || {})
    .map(([service, m]) => ({
      service,
      up: !!m.up,
      requests: m.requests_total || 0,
      errors: (m.errors_4xx || 0) + (m.errors_5xx || 0),
      errRate: m.requests_total
        ? ((m.errors_5xx || 0) / m.requests_total) * 100 : 0,
      latency: m.latency_ms_mean || 0,
      uptime: m.uptime_seconds || 0,
    }))
    .sort((a, b) => a.service.localeCompare(b.service));
}

export function platformHealth(rows) {
  if (!rows.length) return "dark";
  const down = rows.filter((r) => !r.up).length;
  if (down === 0) return "ok";
  return down <= rows.length / 3 ? "warn" : "crit";
}

/* ---------- fetch + cache + demo ---------- */

async function fetchJson(url, timeout = FETCH_TIMEOUT_MS) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const resp = await fetch(url, { signal: ctrl.signal });
    if (!resp.ok) throw new Error(`http ${resp.status}`);
    return await resp.json();
  } finally {
    clearTimeout(timer);
  }
}

/* IndexedDB is best-effort: some embedded browsers (old Android WebView,
 * locked-down kiosks) expose the API but never settle requests. Every
 * operation races a timeout so the boards can never hang on the cache. */
const IDB_TIMEOUT_MS = 1500;

function withTimeout(promise, fallback = null) {
  return Promise.race([
    promise,
    new Promise((res) => setTimeout(() => res(fallback), IDB_TIMEOUT_MS)),
  ]).catch(() => fallback);
}

const IDB = {
  open() {
    return new Promise((res, rej) => {
      const req = indexedDB.open("openhealth-dash", 1);
      req.onupgradeneeded = () => req.result.createObjectStore("cache");
      req.onsuccess = () => res(req.result);
      req.onerror = () => rej(req.error);
    });
  },
  async get(key) {
    const db = await withTimeout(this.open());
    if (!db) return null;
    return withTimeout(new Promise((res) => {
      const tx = db.transaction("cache").objectStore("cache").get(key);
      tx.onsuccess = () => res(tx.result);
      tx.onerror = () => res(null);
    }));
  },
  async set(key, val) {
    const db = await withTimeout(this.open());
    if (!db) return;
    try { db.transaction("cache", "readwrite").objectStore("cache").put(val, key); }
    catch { /* cache is best-effort */ }
  },
};

export class DataSource {
  constructor(base) {
    this.base = base ?? (location.origin.startsWith("http") ? location.origin : "");
    this.demo = false;
  }

  /* One refresh: pull all boards' inputs, cache each, fall back per key. */
  async load() {
    const wants = {
      overview: "/api/platform/overview",
      events: "/api/sync/events",
      facilities: "/api/registry/facilities",
      equipment: "/api/equipment/equipment",
      tickets: "/api/equipment/tickets",
      payers: "/api/claims/payers/stats",
      kpi: "/api/kpi/plane",
    };
    const out = {};
    let anyLive = false;
    for (const [key, path] of Object.entries(wants)) {
      try {
        out[key] = await fetchJson(this.base + path);
        anyLive = true;
        IDB.set(key, out[key]);
      } catch {
        out[key] = (await IDB.get(key)) ?? demoData()[key];
      }
    }
    this.demo = !anyLive;
    return { ...out, demo: this.demo, loadedAt: Date.now() };
  }
}

/* Payer board transforms (ADR-0012): turn the orchestrator's tally into
 * rows a payer relations desk can act on. */
export function payerRows(stats) {
  const payers = (stats && stats.payers) || [];
  const totalPayable = payers.reduce((a, p) => a + (p.payable_ngn || 0), 0);
  return payers.map((p) => {
    const claims = p.claims || 0;
    return {
      payer: p.payer,
      kind: p.kind || "unmapped",
      claims,
      approvalRate: claims ? Math.round(((p.approved || 0) / claims) * 100) : 0,
      pendRate: claims ? Math.round(((p.pended || 0) / claims) * 100) : 0,
      payable: p.payable_ngn || 0,
      payableShare: totalPayable ? Math.round(((p.payable_ngn || 0) / totalPayable) * 100) : 0,
    };
  });
}

export function payerSummary(rows) {
  return {
    totalClaims: rows.reduce((a, r) => a + r.claims, 0),
    totalPayable: rows.reduce((a, r) => a + r.payable, 0),
    payers: rows.length,
    unmapped: rows.filter((r) => r.kind === "unmapped").length,
  };
}

/* ---------- KPI plane transforms (ADR-0033) ----------
 * The nightly job (packages/kpiplane) writes one document; these pure
 * functions shape its sections for the economics / ML-health boards.
 * Every transform tolerates {"status":"no-data"} sections. */

export function marginRows(kpi) {
  const econ = kpi?.sections?.facility_economics;
  if (!econ || econ.status !== "ok" || !econ.margins) return [];
  return econ.margins.lines.map((l) => ({
    code: l.code,
    name: l.name,
    count: l.count,
    tariffNgn: Math.round(l.tariff_kobo / 100),
    marginNgn: Math.round(l.margin_kobo / 100),
    marginPct: l.margin_pct,
    inSchedule: l.in_tariff_schedule !== false,
  }));
}

export function marginTotals(kpi) {
  const m = kpi?.sections?.facility_economics?.margins;
  if (!m) return null;
  return {
    encounters: m.encounters || 0,
    revenueNgn: Math.round((m.totals?.revenue_kobo || 0) / 100),
    marginNgn: Math.round((m.totals?.margin_kobo || 0) / 100),
    unknownCodes: (m.unknown_codes || []).length,
  };
}

export function panelRows(kpi) {
  const panel = kpi?.sections?.facility_economics?.panel;
  if (!panel) return [];
  return panel.bands.map((b) => ({
    band: b.band,
    enrollees: b.enrollees,
    marginNgn: Math.round(b.margin_kobo / 100),
    perEnrolleeNgn: b.margin_per_enrollee_kobo == null
      ? null : Math.round(b.margin_per_enrollee_kobo / 100),
    encounters: b.encounters,
  }));
}

export function panelSummary(kpi) {
  const econ = kpi?.sections?.facility_economics;
  const panel = econ?.panel;
  if (!panel) return null;
  return {
    panelSize: panel.panel_size,
    capitationNgn: Math.round(panel.capitation_kobo / 100),
    totalMarginNgn: Math.round(panel.total_margin_kobo / 100),
    walkInLeakNgn: Math.round((panel.walk_in_cost_kobo || 0) / 100),
  };
}

export function denialSummary(kpi) {
  const d = kpi?.sections?.facility_economics?.denials;
  if (!d) return null;
  return {
    claims: d.claims,
    denied: d.denied,
    preventableSharePct: d.preventable_share_pct,
    preventableNgn: Math.round((d.preventable_denied_kobo || 0) / 100),
    byHmo: Object.entries(d.by_hmo || {})
      .map(([hmo, v]) => ({ hmo, denialRatePct: v.denial_rate_pct }))
      .sort((a, b) => b.denialRatePct - a.denialRatePct),
  };
}

export function catchmentRows(kpi) {
  const c = kpi?.sections?.catchments;
  if (!c || c.status !== "ok") return [];
  return c.facilities;
}

export function catchmentSummary(kpi) {
  const c = kpi?.sections?.catchments;
  if (!c || c.status !== "ok") return null;
  return { members: c.members, unassigned: c.unassigned, over15km: c.over_15km_total };
}

export function mlHealthRows(kpi) {
  const mh = kpi?.sections?.ml_health;
  if (!mh || !mh.models) return [];
  return mh.models.map((m) => ({
    model: m.model,
    champion: m.champion,
    auc: m.metrics?.auc ?? null,
    ageDays: m.champion_age_days ?? null,
    stale: (m.champion_age_days ?? 0) > 30,
  }));
}

export function driftSummary(kpi) {
  const d = kpi?.sections?.ml_health?.drift;
  if (!d) return null;
  return { verdict: d.verdict, worstPsi: d.worst_psi ?? null };
}

/* ---------- demo set (offline-with-no-cache fallback) ---------- */

export function demoData() {
  const now = Date.now();
  const facilities = [
    { id: "PHC-001", name: "Kawo PHC", lga: "Kaduna North", state: "Kaduna", kind: "phc", level: "PHC", x: 31, y: 26,
      latitude: 10.599, longitude: 7.434, beds: 12, doctors: 1, nurses: 6,
      amenities: ["pharmacy", "maternity"], phone: "+234 700 000 001", hours: "Mon-Fri 08:00-16:00" },
    { id: "PHC-002", name: "Umuahia PHC", lga: "Umuahia North", state: "Abia", kind: "phc", level: "PHC", x: 66, y: 71,
      latitude: 5.532, longitude: 7.486, beds: 10, doctors: 1, nurses: 5,
      amenities: ["pharmacy", "lab"], phone: "+234 700 000 002", hours: "Mon-Fri 08:00-16:00" },
    { id: "PHC-003", name: "Ado-Ekiti PHC", lga: "Ado", state: "Ekiti", kind: "phc", level: "PHC", x: 24, y: 62,
      latitude: 7.621, longitude: 5.221, beds: 8, doctors: 1, nurses: 4,
      amenities: ["maternity"], phone: "+234 700 000 003", hours: "Mon-Fri 08:00-16:00" },
    { id: "PHC-004", name: "Nsukka PHC", lga: "Nsukka", state: "Enugu", kind: "phc", level: "PHC", x: 58, y: 55,
      latitude: 6.857, longitude: 7.395, beds: 14, doctors: 2, nurses: 7,
      amenities: ["pharmacy", "lab", "cold-chain"], phone: "+234 700 000 004", hours: "24/7" },
    { id: "PHC-005", name: "Sabon Gari PHC", lga: "Zaria", state: "Kaduna", kind: "phc", level: "PHC", x: 38, y: 18,
      latitude: 11.111, longitude: 7.722, beds: 10, doctors: 1, nurses: 5,
      amenities: ["pharmacy", "oxygen"], phone: "+234 700 000 005", hours: "Mon-Fri 08:00-16:00" },
    { id: "PHC-006", name: "Ondo Town PHC", lga: "Ondo West", state: "Ondo", kind: "phc", level: "PHC", x: 20, y: 74,
      latitude: 7.093, longitude: 4.835, beds: 12, doctors: 1, nurses: 6,
      amenities: ["maternity", "pharmacy"], phone: "+234 700 000 006", hours: "Mon-Fri 08:00-16:00" },
    { id: "HOS-001", name: "General Hospital Ikeja", lga: "Ikeja", state: "Lagos", kind: "hospital", level: "hospital", x: 12, y: 86,
      latitude: 6.602, longitude: 3.351, beds: 120, doctors: 18, nurses: 60,
      amenities: ["pharmacy", "lab", "maternity", "xray", "ambulance", "oxygen"], phone: "+234 700 000 101", hours: "24/7" },
    { id: "HOS-002", name: "State Specialist Owerri", lga: "Owerri", state: "Imo", kind: "hospital", level: "hospital", x: 61, y: 82,
      latitude: 5.485, longitude: 7.036, beds: 90, doctors: 12, nurses: 45,
      amenities: ["pharmacy", "lab", "xray", "ambulance", "oxygen", "cold-chain"], phone: "+234 700 000 102", hours: "24/7" },
  ];
  const types = ["encounter", "encounter", "encounter", "dispensing", "claim_submitted", "stock_report"];
  const events = [];
  let n = 0;
  for (const f of facilities) {
    const count = f.kind === "hospital" ? 46 : 22;
    for (let i = 0; i < count; i++) {
      const type = types[i % types.length];
      events.push({
        id: `ev-${n++}`, facility: f.id, type,
        ts: new Date(now - Math.floor(Math.random() * 6) * 86400000 - Math.floor(Math.random() * 60000)).toISOString(),
        payload: {
          offline: Math.random() < 0.86,
          staff_id: `st-${(i % 8) + 1}`,
          ...(type === "claim_submitted" ? { turnaround_days: 3 + Math.floor(Math.random() * 9) } : {}),
        },
      });
    }
  }
  const overview = {};
  for (const s of ["claims-orchestrator", "registry-service", "consent-service",
    "wallet-service", "shr-service", "equipment-service", "telehealth-service", "sync-ingest"]) {
    overview[s] = {
      up: s !== "telehealth-service",
      requests_total: 1200 + Math.floor(Math.random() * 9000),
      errors_4xx: Math.floor(Math.random() * 30), errors_5xx: Math.floor(Math.random() * 4),
      latency_ms_mean: Math.round((2 + Math.random() * 14) * 10) / 10,
      uptime_seconds: 86400 + Math.floor(Math.random() * 86400 * 30),
    };
  }
  const equipment = facilities.flatMap((f) => [
    { id: `${f.id}-fridge`, facility: f.id, kind: "cold-chain fridge", status: "working", telemetry: { cold_chain_c: 3 + Math.random() * 4 } },
    { id: `${f.id}-solar`, facility: f.id, kind: "solar array", status: Math.random() < 0.85 ? "working" : "down", telemetry: { battery_soc_pct: 30 + Math.random() * 70 } },
    { id: `${f.id}-tank`, facility: f.id, kind: "water tank", status: "working", telemetry: { water_tank_pct: 15 + Math.random() * 85 } },
  ]);
  const tickets = [
    { id: "TK-101", facility: "PHC-005", equipment: "cold-chain fridge", status: "open", opened: new Date(now - 2 * 86400000).toISOString(), note: "Compressor fault — temp drifting" },
    { id: "TK-102", facility: "PHC-003", equipment: "solar inverter", status: "assigned", opened: new Date(now - 86400000).toISOString(), note: "Inverter trips under load" },
    { id: "TK-103", facility: "HOS-001", equipment: "autoclave", status: "open", opened: new Date(now - 3600000).toISOString(), note: "Pressure seal worn" },
  ];
  const payers = {
    payers: [
      { payer: "NHIA", kind: "government", claims: 1284, approved: 1102, pended: 96, denied: 86, payable_ngn: 4281100 },
      { payer: "HYGEIA", kind: "commercial", claims: 342, approved: 301, pended: 22, denied: 19, payable_ngn: 1213400 },
      { payer: "RELIANCE", kind: "commercial", claims: 210, approved: 177, pended: 18, denied: 15, payable_ngn: 802300 },
    ],
  };
  const kpi = {
    generated_at: new Date(now - 5 * 3600000).toISOString(),
    sections: {
      facility_economics: {
        status: "ok",
        margins: {
          tariff_year: 2025, encounters: 412,
          lines: [
            { code: "CS-EMG", name: "Caesarean section (emergency)", count: 18, tariff_kobo: 7500000, revenue_kobo: 135000000, cost_kobo: 151200000, margin_kobo: -16200000, margin_pct: -12.0, in_tariff_schedule: true },
            { code: "MAL-RDT", name: "Malaria RDT + ACT", count: 96, tariff_kobo: 120000, revenue_kobo: 11520000, cost_kobo: 11040000, margin_kobo: 480000, margin_pct: 4.2, in_tariff_schedule: true },
            { code: "CONS-GEN", name: "General consultation", count: 298, tariff_kobo: 80000, revenue_kobo: 23840000, cost_kobo: 19610000, margin_kobo: 4230000, margin_pct: 17.7, in_tariff_schedule: true },
          ],
          unknown_codes: [],
          totals: { revenue_kobo: 170360000, cost_kobo: 166250000, margin_kobo: 4110000 },
        },
        margin_worst: { code: "CS-EMG", margin_kobo: -16200000 },
        panel: {
          capitation_kobo: 145000, tariff_year: 2025, panel_size: 4200, encounters: 412,
          bands: [
            { band: "adult", enrollees: 2380, capitation_kobo: 345100000, care_cost_kobo: 221300000, margin_kobo: 123800000, margin_per_enrollee_kobo: 52016, encounters: 171 },
            { band: "chronic", enrollees: 610, capitation_kobo: 88450000, care_cost_kobo: 149700000, margin_kobo: -61250000, margin_per_enrollee_kobo: -100409, encounters: 122 },
            { band: "elderly", enrollees: 402, capitation_kobo: 58290000, care_cost_kobo: 70400000, margin_kobo: -12110000, margin_per_enrollee_kobo: -30124, encounters: 61 },
            { band: "maternal", enrollees: 318, capitation_kobo: 46110000, care_cost_kobo: 52100000, margin_kobo: -5990000, margin_per_enrollee_kobo: -18836, encounters: 44 },
            { band: "under5", enrollees: 490, capitation_kobo: 71050000, care_cost_kobo: 48200000, margin_kobo: 22850000, margin_per_enrollee_kobo: 46632, encounters: 14 },
            { band: "walk-in", enrollees: 0, capitation_kobo: 0, care_cost_kobo: 3890000, margin_kobo: -3890000, margin_per_enrollee_kobo: null, encounters: 20 },
          ],
          total_margin_kobo: -5210000, walk_in_cost_kobo: 3890000,
        },
        denials: {
          claims: 1836, denied: 120,
          by_reason: {
            "missing-referral": { count: 41, amount_kobo: 9800000, klass: "preventable" },
            "not-covered": { count: 33, amount_kobo: 7200000, klass: "package" },
            "duplicate-claim": { count: 12, amount_kobo: 4100000, klass: "fraud-screen" },
            "documentation-gap": { count: 34, amount_kobo: 6900000, klass: "preventable" },
          },
          by_hmo: {
            HYGEIA: { submitted: 842, denied: 58, denied_kobo: 13900000, denial_rate_pct: 6.9 },
            NHIA: { submitted: 711, denied: 44, denied_kobo: 11200000, denial_rate_pct: 6.2 },
            RELIANCE: { submitted: 283, denied: 18, denied_kobo: 2900000, denial_rate_pct: 6.4 },
          },
          preventable_denied_kobo: 16700000, preventable_share_pct: 59.6,
        },
      },
      panel_risk: {
        status: "ok", panel_size: 4200,
        bands: { under5: 490, adult: 2380, "older-adult": 408, elderly: 402, maternal: 318, chronic: 610 },
        band_share_pct: { under5: 11.7, adult: 56.7, "older-adult": 9.7, elderly: 9.6, maternal: 7.6, chronic: 14.5 },
      },
      catchments: {
        status: "ok", members: 4200, unassigned: 96, over_15km_total: 268,
        facilities: [
          { facility: "HOS-001", name: "General Hospital Ikeja", assigned: 1180, mean_km: 4.2, p90_km: 9.8, over_15km: 41 },
          { facility: "PHC-004", name: "Nsukka PHC", assigned: 880, mean_km: 6.1, p90_km: 13.4, over_15km: 88 },
          { facility: "PHC-001", name: "Kawo PHC", assigned: 812, mean_km: 5.4, p90_km: 12.1, over_15km: 62 },
          { facility: "PHC-005", name: "Sabon Gari PHC", assigned: 704, mean_km: 7.9, p90_km: 16.2, over_15km: 77 },
        ],
      },
      ml_health: {
        status: "ok",
        models: [
          { model: "claim-gcn", champion: "v3", metrics: { auc: 0.934, precision_at_k: 0.61 }, card: { data_hash: "9f2c" }, champion_age_days: 6.2 },
          { model: "fraud-mlp", champion: "v7", metrics: { auc: 0.908, precision_at_k: 0.44 }, card: { data_hash: "9f2c" }, champion_age_days: 11.8 },
        ],
        drift: { verdict: "moderate", worst_psi: 0.18, per_feature: { amount_ratio: 0.18, qty: 0.07 }, reference_n: 5200, current_n: 1836 },
      },
    },
  };
  return { facilities, events, overview, equipment, tickets, payers, kpi };
}
