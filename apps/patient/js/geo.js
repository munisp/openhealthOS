/* Patient-side geospatial helpers (ADR-0014) — mirrors the dashboards'
 * map.js and the registry's geo.go: haversine distance, nearest sort,
 * navigation deep links. Pure functions, no DOM — node-testable.
 *
 * Turn-by-turn navigation deliberately belongs to the phone's maps app
 * (geo: link): it has offline maps, voice guidance and transport modes a
 * health app never will. We answer "which facility, how far, what does it
 * have"; the OS answers "how do I get there".
 */

export function haversineKm(lat1, lng1, lat2, lng2) {
  const rad = Math.PI / 180, R = 6371.0088;
  const dLat = (lat2 - lat1) * rad, dLng = (lng2 - lng1) * rad;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

export function nearest(facilities, lat, lng, limit = 8) {
  return (facilities || [])
    .filter((f) => f.latitude || f.longitude)
    .map((f) => ({ ...f, distance_km: Math.round(haversineKm(lat, lng, f.latitude, f.longitude) * 10) / 10 }))
    .sort((a, b) => a.distance_km - b.distance_km)
    .slice(0, limit);
}

export function navLink(f) {
  const label = encodeURIComponent(f.name || "facility");
  return `geo:${f.latitude},${f.longitude}?q=${f.latitude},${f.longitude}(${label})`;
}

/* Shape a near-me row for display: what a patient actually asks —
 * how far, what level, can they admit me, is there a pharmacy/lab. */
export function facilityRow(f) {
  return {
    id: f.id,
    name: f.name,
    level: f.level || f.kind || "",
    distance_km: f.distance_km ?? null,
    beds: f.beds || 0,
    doctors: f.doctors || 0,
    nurses: f.nurses || 0,
    amenities: f.amenities || [],
    hours: f.hours || "",
    nav: (f.latitude || f.longitude) ? navLink(f) : null,
  };
}
