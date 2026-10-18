/* Map view tests (ADR-0014): projection, distance, styles, hit-testing. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { haversineKm, projector, levelStyle, nearest, navLink, hitTest } from "../js/map.js";

const boundary = [[2.7, 4.3], [14.6, 4.3], [14.6, 13.9], [2.7, 13.9], [2.7, 4.3]];

test("haversine matches known distances", () => {
  const d = haversineKm(6.5244, 3.3792, 9.0765, 7.3986); // Lagos → Abuja
  assert.ok(d > 500 && d < 580, `lagos-abuja ${d}`);
  assert.equal(haversineKm(10, 7, 10, 7), 0);
});

test("projector fits boundary and flips y", () => {
  const proj = projector(boundary, 800, 600);
  const [x1, y1] = proj([2.7, 13.9]);   // NW corner → top-left
  const [x2, y2] = proj([14.6, 4.3]);   // SE corner → bottom-right
  assert.ok(x1 < x2 && y1 < y2);
  assert.ok(x1 >= 0 && y1 >= 0 && x2 <= 800 && y2 <= 600);
});

test("levelStyle colors by level, grows with beds", () => {
  assert.equal(levelStyle({ level: "hospital", beds: 0 }).color, "#e2554f");
  assert.equal(levelStyle({ level: "PHC", beds: 0 }).color, "#3f9d63");
  assert.ok(levelStyle({ level: "hospital", beds: 400 }).radius >
            levelStyle({ level: "hospital", beds: 0 }).radius);
});

test("nearest sorts and skips unplaced", () => {
  const facilities = [
    { id: "A", latitude: 10.53, longitude: 7.44 },
    { id: "B", latitude: 12.0, longitude: 8.9 },
    { id: "C" }, // no coordinates
  ];
  const out = nearest(facilities, 10.52, 7.43, 10);
  assert.deepEqual(out.map((f) => f.id), ["A", "B"]);
  assert.ok(out[0].distance_km < 5);
});

test("navLink builds a geo: deep link", () => {
  const url = navLink({ name: "Kawo PHC", latitude: 10.545, longitude: 7.435 });
  assert.ok(url.startsWith("geo:10.545,7.435?q="));
  assert.ok(url.includes("Kawo%20PHC"));
});

test("hitTest finds tapped marker, misses empty space", () => {
  const hits = [{ id: "A", x: 100, y: 100, r: 10 }];
  assert.equal(hitTest(hits, 105, 103), "A");
  assert.equal(hitTest(hits, 300, 300), null);
});
