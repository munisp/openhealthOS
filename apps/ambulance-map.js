/* OpenHealth OS — live ambulance map (ADR-0021).
 *
 * One shared tracker for the clinician PWA and the patient app (and so
 * the Android shell): an offline canvas map — no tiles, no CDN, no
 * WebGL, same doctrine as the facility map — that draws the patient,
 * the facility, the responding unit and its recent trail, and polls
 * the dispatch /incidents/{id}/track endpoint for fresh fixes.
 *
 * Pure parts (projection, ETA text) are exported for node tests; the
 * DOM parts guard on `document` so the file loads anywhere.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.AmbulanceMap = api;
})(typeof self !== "undefined" ? self : globalThis, function () {
  "use strict";

  /* Equirectangular projection around the view's bounding box — fine
   * at city scale, which is the only scale an ambulance map needs. */
  function projector(points, width, height, pad) {
    pad = pad || 24;
    const lats = points.map((p) => p.lat), lngs = points.map((p) => p.lng);
    let minLat = Math.min(...lats), maxLat = Math.max(...lats);
    let minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
    if (maxLat - minLat < 0.005) { minLat -= 0.0025; maxLat += 0.0025; }
    if (maxLng - minLng < 0.005) { minLng -= 0.0025; maxLng += 0.0025; }
    const cosMid = Math.cos(((minLat + maxLat) / 2) * Math.PI / 180);
    return function (lat, lng) {
      const x = pad + ((lng - minLng) * cosMid) /
        ((maxLng - minLng) * cosMid) * (width - 2 * pad);
      const y = height - pad - (lat - minLat) / (maxLat - minLat) * (height - 2 * pad);
      return [x, y];
    };
  }

  function etaText(eta) {
    if (!eta) return "";
    const m = eta.minutes;
    return m <= 1 ? "~1 min away" : "~" + m + " min away";
  }

  function staleSeconds(unit) {
    if (!unit || !unit.updated_at) return Infinity;
    return (Date.now() - Date.parse(unit.updated_at)) / 1000;
  }

  const COLORS = {
    patient: "#b3261e",      // red — where help is needed
    unit: "#2b5c9e",         // blue — help coming
    facility: "#2e6b4f",     // green — where care happens
    trail: "rgba(43, 92, 158, 0.45)",
    grid: "rgba(16, 24, 40, 0.08)",
    stale: "#8b98a8",
  };

  function draw(canvas, track) {
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    // grid — an honest "not a real street map" texture
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    for (let x = 0; x < W; x += 40) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
    for (let y = 0; y < H; y += 40) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }

    const pts = [];
    if (track.patient && track.patient.lat) pts.push(track.patient);
    if (track.unit && track.unit.lat) pts.push(track.unit);
    if (track.facility && track.facility.lat) pts.push(track.facility);
    (track.unit && track.unit.trail || []).forEach((t) => pts.push(t));
    if (!pts.length) return;
    const proj = projector(pts, W, H);

    function dot(lat, lng, color, r, ring) {
      const [x, y] = proj(lat, lng);
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = color; ctx.fill();
      if (ring) { ctx.lineWidth = 2; ctx.strokeStyle = color; ctx.globalAlpha = 0.35;
        ctx.beginPath(); ctx.arc(x, y, r + 6, 0, Math.PI * 2); ctx.stroke(); ctx.globalAlpha = 1; }
      return [x, y];
    }
    function label(text, x, y, color) {
      ctx.font = "600 11px system-ui, sans-serif";
      const w = ctx.measureText(text).width + 10;
      ctx.fillStyle = "rgba(255,255,255,0.92)";
      ctx.strokeStyle = color; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.roundRect(x - w / 2, y - 26, w, 16, 8); ctx.fill(); ctx.stroke();
      ctx.fillStyle = color; ctx.textAlign = "center";
      ctx.fillText(text, x, y - 14);
    }

    // trail first (under the dots)
    const trail = (track.unit && track.unit.trail) || [];
    if (trail.length > 1) {
      ctx.strokeStyle = COLORS.trail; ctx.lineWidth = 2; ctx.beginPath();
      trail.forEach((t, i) => {
        const [x, y] = proj(t.lat, t.lng);
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.stroke();
    }

    if (track.patient && track.patient.lat) {
      const [x, y] = dot(track.patient.lat, track.patient.lng, COLORS.patient, 7, true);
      label("patient", x, y, COLORS.patient);
    }
    if (track.facility && track.facility.lat) {
      const [x, y] = dot(track.facility.lat, track.facility.lng, COLORS.facility, 7, false);
      label(track.facility.name || "facility", x, y, COLORS.facility);
    }
    if (track.unit && track.unit.lat) {
      const stale = staleSeconds(track.unit) > 120;
      const color = stale ? COLORS.stale : COLORS.unit;
      const [x, y] = dot(track.unit.lat, track.unit.lng, color, 8, !stale);
      label((track.unit.callsign || "unit") + (stale ? " (signal lost)" : ""), x, y, color);
    }
  }

  /* Live controller: polls the track endpoint and repaints. Returns a
   * stop function. `fetchFn` injectable for tests. */
  function follow(canvas, baseUrl, incidentId, opts) {
    opts = opts || {};
    const intervalMs = opts.intervalMs || 10000;
    const fetchFn = opts.fetchFn || fetch;
    let timer = null, stopped = false;

    async function tick() {
      try {
        const res = await fetchFn(baseUrl + "/incidents/" + incidentId + "/track");
        if (res.ok) {
          const track = await res.json();
          draw(canvas, track);
          if (opts.onUpdate) opts.onUpdate(track);
        }
      } catch (e) { /* offline: the last frame stays up, retries continue */ }
      if (!stopped) timer = setTimeout(tick, intervalMs);
    }
    tick();
    return function stop() { stopped = true; if (timer) clearTimeout(timer); };
  }

  /* Paramedic side: the unit device's GPS heartbeat. Watches the
   * device's geolocation and pings the fleet; returns a stop function.
   * Falls back quietly when geolocation is unavailable (old Android). */
  function startPinging(baseUrl, unitId, opts) {
    opts = opts || {};
    const fetchFn = opts.fetchFn || fetch;
    const geo = opts.geolocation || (typeof navigator !== "undefined" && navigator.geolocation);
    if (!geo) return function () {};
    let last = 0;
    const minInterval = opts.minIntervalMs || 8000;
    const id = geo.watchPosition(
      function (pos) {
        const now = Date.now();
        if (now - last < minInterval) return;
        last = now;
        fetchFn(baseUrl + "/units/" + unitId + "/position", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
        }).catch(function () { /* offline edge: next fix retries */ });
      },
      function () { /* denied or unavailable — dispatcher still sees last fix */ },
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 }
    );
    return function stop() { geo.clearWatch(id); };
  }

  return { projector, draw, follow, startPinging, etaText, staleSeconds, COLORS };
});
