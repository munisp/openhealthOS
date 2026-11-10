/* Canonical geodesy for every web runtime (ADR-0078) — the JS mirror of
 * geolock.distance_km. One contract, one formula, one clamp, estate-wide.
 *
 * Before this wave the web tier measured distance with TWO private
 * unstated copies of the retired IUGG radius (6371.0088) and no
 * antipodal clamp (apps/dashboards/js/map.js, apps/patient/js/geo.js —
 * the wave-14 census swept Python only; that miss is stated in the
 * georuntimeproof payload). Both now delegate here under their historic
 * names.
 *
 * Conformance is pinned, not hoped: apps/pwa/tests/geodesy-conformance
 * .test.mjs runs the five reference legs through this module and
 * asserts BIT-IDENTITY with the Python-computed vectors stamped into
 * georuntimeproof.json (CPython/glibc and V8 agree bit-for-bit on the
 * vectors; Go converges to the same formula with a stated ~1-ulp libm
 * envelope — the envelope is data on the payload, not a hope).
 *
 * Serving note: dashboards and patient import this module as
 * ../pwa/js/geodesy.js — the three apps are served from one origin
 * root; if that ever changes, the import path changes loudly (a 404),
 * never silently.
 */

export const GEODESY_CONTRACT = {
  model: "spherical-haversine",
  radius_km: 6371.0,
  honest_error: "<=0.3% vs WGS-84 geodesic",
  never: "survey-grade",
  crs_meaning: "governed by geolock — degrees are WGS-84 lng/lat",
  clamp: "sqrt argument capped at 1.0 — antipodal rounding stated",
  supersedes: [
    "IUGG mean 6371.0088 (apps/dashboards/js/map.js, apps/patient/js/geo.js)",
  ],
};

function checkArg(name, v, lo, hi) {
  if (!(lo <= v && v <= hi)) {
    throw new RangeError(
      `${name} ${v} out of range — degrees are WGS-84 lng/lat; ` +
      `metre-space coordinates are not distances waiting to happen, ` +
      `reproject first (geolock.transform)`);
  }
}

export function distanceKm(lat1, lng1, lat2, lng2) {
  checkArg("lat1", lat1, -90, 90);
  checkArg("lat2", lat2, -90, 90);
  checkArg("lng1", lng1, -180, 180);
  checkArg("lng2", lng2, -180, 180);
  const rad = Math.PI / 180, R = GEODESY_CONTRACT.radius_km;
  const dLat = (lat2 - lat1) * rad, dLng = (lng2 - lng1) * rad;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) ** 2;
  // The clamp is stated: float rounding at exactly antipodal points can
  // push the argument a hair past 1.0; the retired copies returned NaN
  // there. Value-identical everywhere they did not crash.
  return 2 * R * Math.asin(Math.min(1.0, Math.sqrt(a)));
}
