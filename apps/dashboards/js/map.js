/* Offline geospatial map (ADR-0014) — a self-contained canvas render over
 * the bundled Nigeria boundary. No tiles, no CDN, no WebGL: the whole map
 * works with zero connectivity on the same phones the rest of the platform
 * targets. Sedona/Spark-scale batch geospatial analytics belong to the
 * state plane (see ADR-0014); the read path is haversine in the registry.
 */

/* ---------- pure helpers (node-testable) ---------- */

// haversineKm mirrors the registry's geo.go exactly.
export function haversineKm(lat1, lng1, lat2, lng2) {
  const rad = Math.PI / 180, R = 6371.0088;
  const dLat = (lat2 - lat1) * rad, dLng = (lng2 - lng1) * rad;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

// project fits (lat,lng) into a w×h canvas with padding, y-axis flipped.
// Returns a function lngLat→[x,y].
export function projector(boundary, w, h, pad = 24) {
  let minLng = 999, maxLng = -999, minLat = 999, maxLat = -999;
  for (const [lng, lat] of boundary) {
    if (lng < minLng) minLng = lng;
    if (lng > maxLng) maxLng = lng;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }
  const spanLng = maxLng - minLng || 1, spanLat = maxLat - minLat || 1;
  const scale = Math.min((w - 2 * pad) / spanLng, (h - 2 * pad) / spanLat);
  const offX = pad + ((w - 2 * pad) - spanLng * scale) / 2;
  const offY = pad + ((h - 2 * pad) - spanLat * scale) / 2;
  return ([lng, lat]) => [offX + (lng - minLng) * scale, offY + (maxLat - lat) * scale];
}

// levelStyle: marker colour/radius per facility level; size grows with beds.
export function levelStyle(f) {
  const base = { hospital: "#e2554f", clinic: "#e2a44f", PHC: "#3f9d63" }[f.level] || "#5b8bd4";
  return { color: base, radius: 4 + Math.min(6, Math.sqrt(f.beds || 0) / 2) };
}

// nearest sorts facilities by distance from a point (client-side fallback
// when the registry is unreachable and we only have the cached list).
export function nearest(facilities, lat, lng, limit = 10) {
  return (facilities || [])
    .filter((f) => f.latitude || f.longitude)
    .map((f) => ({ ...f, distance_km: Math.round(haversineKm(lat, lng, f.latitude, f.longitude) * 100) / 100 }))
    .sort((a, b) => a.distance_km - b.distance_km)
    .slice(0, limit);
}

// navLink builds a navigation deep link: geo: on Android, maps.apple.com
// elsewhere — the phone's own navigator does turn-by-turn (offline nav
// belongs to the OS maps app, not to a health record).
export function navLink(f) {
  const label = encodeURIComponent(f.name || "facility");
  return `geo:${f.latitude},${f.longitude}?q=${f.latitude},${f.longitude}(${label})`;
}

/* ---------- canvas renderer (browser only) ---------- */

export function renderMap(canvas, boundary, facilities, selectedId) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const w = canvas.width, h = canvas.height;
  const proj = projector(boundary, w, h);
  ctx.clearRect(0, 0, w, h);
  // boundary
  ctx.beginPath();
  boundary.forEach((pt, i) => {
    const [x, y] = proj(pt);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.closePath();
  ctx.fillStyle = "rgba(63,157,99,0.07)";
  ctx.fill();
  ctx.strokeStyle = "rgba(120,140,130,0.8)";
  ctx.lineWidth = 1.5;
  ctx.stroke();
  // facilities
  const hits = [];
  for (const f of facilities || []) {
    if (!(f.latitude || f.longitude)) continue;
    const [x, y] = proj([f.longitude, f.latitude]);
    const st = levelStyle(f);
    ctx.beginPath();
    ctx.arc(x, y, st.radius, 0, 2 * Math.PI);
    ctx.fillStyle = st.color;
    ctx.globalAlpha = f.id === selectedId ? 1 : 0.75;
    ctx.fill();
    ctx.globalAlpha = 1;
    if (f.id === selectedId) {
      ctx.beginPath();
      ctx.arc(x, y, st.radius + 4, 0, 2 * Math.PI);
      ctx.strokeStyle = st.color;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    hits.push({ id: f.id, x, y, r: st.radius + 6 });
  }
  return { hits, proj };
}

// hitTest finds which marker (if any) was tapped.
export function hitTest(hits, x, y) {
  for (const h of hits || []) {
    if ((h.x - x) ** 2 + (h.y - y) ** 2 <= h.r * h.r) return h.id;
  }
  return null;
}
