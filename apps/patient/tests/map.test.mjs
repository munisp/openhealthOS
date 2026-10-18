// Live ambulance map — pure parts (ADR-0021): projection stays inside
// the canvas, ETA text is honest, staleness is detected. DOM parts are
// guarded and untestable here by design.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const M = require("../../ambulance-map.js");

test("projector keeps every point inside the padded canvas", () => {
  const pts = [
    { lat: 6.5244, lng: 3.3792 }, // patient
    { lat: 6.5300, lng: 3.3850 }, // unit
    { lat: 6.5244, lng: 3.3600 }, // facility
  ];
  const proj = M.projector(pts, 520, 360, 24);
  for (const p of pts) {
    const [x, y] = proj(p.lat, p.lng);
    assert.ok(x >= 24 && x <= 520 - 24, `x=${x} out of bounds`);
    assert.ok(y >= 24 && y <= 360 - 24, `y=${y} out of bounds`);
  }
});

test("projector handles a single point (degenerate bbox)", () => {
  const proj = M.projector([{ lat: 6.5, lng: 3.4 }], 400, 300);
  const [x, y] = proj(6.5, 3.4);
  assert.ok(Number.isFinite(x) && Number.isFinite(y));
});

test("eta text is plain and honest", () => {
  assert.equal(M.etaText({ minutes: 7 }), "~7 min away");
  assert.equal(M.etaText({ minutes: 1 }), "~1 min away");
  assert.equal(M.etaText(null), "");
});

test("stale fix detection — a unit silent for 2+ minutes greys out", () => {
  const fresh = { updated_at: new Date().toISOString() };
  const old = { updated_at: new Date(Date.now() - 300_000).toISOString() };
  assert.ok(M.staleSeconds(fresh) < 5);
  assert.ok(M.staleSeconds(old) > 120);
  assert.equal(M.staleSeconds(null), Infinity);
});

test("follow polls and repaints via injected fetch", async () => {
  let calls = 0;
  const fn = () => ctx; const ctx = new Proxy({}, { get: (t, k) => (k === "measureText" ? () => ({ width: 10 }) : fn), set: () => true });
  const canvas = { getContext: () => ctx, width: 100, height: 100 };
  const fetchFn = async () => ({
    ok: true,
    json: async () => ({ status: "dispatched", patient: { lat: 6.5, lng: 3.4 } }),
  });
  const stop = M.follow(canvas, "http://x", "INC-1", { intervalMs: 10, fetchFn,
    onUpdate: () => calls++ });
  await new Promise((r) => setTimeout(r, 55));
  stop();
  assert.ok(calls >= 2, `expected >=2 polls, got ${calls}`);
});
