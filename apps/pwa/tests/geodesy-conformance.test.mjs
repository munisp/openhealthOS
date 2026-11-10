/* Cross-runtime geodesy conformance (ADR-0078) — the web tier proves
 * itself against the stamped vectors, not against memory.
 *
 * The vectors live in apps/pwa/georuntimeproof.json, computed by
 * geolock.distance_km (CPython/glibc). This suite asserts the canonical
 * JS module is BIT-IDENTICAL to those bytes (===), that both retired
 * sites now delegate and stay bit-identical, that the gate fails closed
 * in the same words as Python, and that the antipodal clamp holds.
 * Tamper with a vector in the payload and this suite goes red AND
 * proofindex goes red — two independent gates.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { distanceKm, GEODESY_CONTRACT } from "../js/geodesy.js";
import { haversineKm as mapHaversine } from "../../dashboards/js/map.js";
import { haversineKm as patientHaversine } from "../../patient/js/geo.js";

const payload = JSON.parse(readFileSync(
  new URL("../georuntimeproof.json", import.meta.url), "utf8"));
const vectors = payload.conformance_vectors;

test("contract is the estate contract", () => {
  assert.equal(GEODESY_CONTRACT.radius_km, 6371.0);
  assert.equal(GEODESY_CONTRACT.model, "spherical-haversine");
});

test("canonical module is bit-identical to the stamped Python vectors", () => {
  for (const v of vectors) {
    const got = distanceKm(v.a[0], v.a[1], v.b[0], v.b[1]);
    assert.equal(got, v.km, `${v.name}: ${got} !== ${v.km}`);
  }
});

test("both retired sites delegate and stay bit-identical", () => {
  for (const v of vectors) {
    const want = distanceKm(v.a[0], v.a[1], v.b[0], v.b[1]);
    assert.equal(mapHaversine(v.a[0], v.a[1], v.b[0], v.b[1]), want,
                 `map.js diverged on ${v.name}`);
    assert.equal(patientHaversine(v.a[0], v.a[1], v.b[0], v.b[1]), want,
                 `geo.js diverged on ${v.name}`);
  }
});

test("the gate fails closed in the same words as Python", () => {
  assert.throws(() => distanceKm(6.52, 376170.8, 6.52, 3.37),
                (e) => e instanceof RangeError &&
                       e.message.includes("376170.8") &&
                       e.message.includes("lng1"));
  assert.throws(() => distanceKm(91, 7, 6.52, 3.37), RangeError);
});

test("the antipodal clamp holds — no NaN where the retired copies NaN'd", () => {
  assert.equal(distanceKm(0, 0, 0, 180), 20015.086796020572);
});

test("census guard: no private haversine survives in the web tier", () => {
  // The retired radius may appear in COMMENTS as stated data (the
  // supersession is disclosed, not scrubbed); what must not survive is
  // private ARITHMETIC. The guard pins: no trig, and a delegation import.
  for (const rel of ["../../dashboards/js/map.js",
                     "../../patient/js/geo.js"]) {
    const src = readFileSync(new URL(rel, import.meta.url), "utf8");
    assert.ok(!src.includes("Math.asin("),
              `${rel} still computes its own haversine`);
    assert.ok(!src.includes("Math.cos("),
              `${rel} still computes its own haversine`);
    assert.ok(src.includes('from "../../pwa/js/geodesy.js"'),
              `${rel} does not delegate to the canonical module`);
  }
});
