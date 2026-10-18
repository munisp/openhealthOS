/* Patient geo helpers conformance (ADR-0014). Run: node --test apps/patient/tests/ */
import { test } from "node:test";
import assert from "node:assert/strict";
import { haversineKm, nearest, navLink, facilityRow } from "../js/geo.js";

const LAGOS = [6.524, 3.379];
const ABUJA = [9.057, 7.495];

test("haversine: Lagos-Abuja ≈ 540km, zero distance is zero", () => {
  const d = haversineKm(...LAGOS, ...ABUJA);
  assert.ok(d > 520 && d < 560, `got ${d}`);
  assert.equal(haversineKm(...LAGOS, ...LAGOS), 0);
});

test("nearest: sorted by distance, unplaced facilities excluded, limit honoured", () => {
  const facs = [
    { id: "FAR", latitude: 11.0, longitude: 8.0 },
    { id: "NEAR", latitude: 6.54, longitude: 3.36 },
    { id: "UNPLACED" },                       // no coords — never returned
    { id: "ZERO", latitude: 0, longitude: 0 }, // unset sentinel — never returned
    { id: "MID", latitude: 7.5, longitude: 3.9 },
  ];
  const out = nearest(facs, ...LAGOS, 2);
  assert.deepEqual(out.map((f) => f.id), ["NEAR", "MID"]);
  assert.ok(out[0].distance_km < out[1].distance_km);
});

test("navLink: geo: deep link carries label", () => {
  const u = navLink({ name: "Kawo PHC", latitude: 10.599, longitude: 7.434 });
  assert.ok(u.startsWith("geo:10.599,7.434?q=10.599,7.434("));
  assert.ok(u.includes("Kawo%20PHC"));
});

test("facilityRow: capacity + amenities surfaced, nav null when unplaced", () => {
  const r = facilityRow({ id: "X", name: "X Clinic", level: "clinic",
    latitude: 6.5, longitude: 3.4, beds: 8, doctors: 1, nurses: 4,
    amenities: ["pharmacy"], hours: "24/7", distance_km: 1.2 });
  assert.equal(r.beds, 8);
  assert.equal(r.distance_km, 1.2);
  assert.ok(r.nav.startsWith("geo:"));
  const unplaced = facilityRow({ id: "Y", name: "Y" });
  assert.equal(unplaced.nav, null);
  assert.deepEqual(unplaced.amenities, []);
});
