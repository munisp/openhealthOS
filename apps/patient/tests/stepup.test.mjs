// stepup.test.mjs — client gate logic: token storage (session-only),
// 428 -> challenge -> code -> retry, wrong-code retry, random-bounce
// re-elevation, cancel honesty.
import { test } from "node:test";
import assert from "node:assert/strict";

const sess = new Map();
globalThis.sessionStorage = {
  getItem: (k) => (sess.has(k) ? sess.get(k) : null),
  setItem: (k, v) => sess.set(k, String(v)),
  removeItem: (k) => sess.delete(k),
};

const { elevatedFetch, setSheetFactory } = await import("../js/stepup.js");
const ID = { patientId: "PT-1", phone: "0803" };
const jsonResp = (status, obj) => ({ status, ok: status < 300, json: async () => obj });

test("clean 200 passes through untouched, no sheet", async () => {
  setSheetFactory(() => { throw new Error("sheet must not open"); });
  globalThis.fetch = async () => jsonResp(200, { record: 1 });
  const r = await elevatedFetch("http://api", "/portal/record?x", ID);
  assert.equal(r.status, 200);
});

test("428 runs challenge -> code -> verify -> retry with token", async () => {
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    calls.push(url);
    if (url.endsWith("/portal/stepup/challenge")) return jsonResp(200, {});
    if (url.endsWith("/portal/stepup/verify")) {
      const body = JSON.parse(opts.body);
      return body.code === "123456"
        ? jsonResp(200, { stepup_token: "tok-1", expires_in: "600" })
        : jsonResp(404, {});
    }
    return (opts.headers || {})["X-StepUp-Token"] === "tok-1"
      ? jsonResp(200, { record: 1 }) : jsonResp(428, { error: "step_up_required" });
  };
  let sheets = 0;
  setSheetFactory(async () => { sheets++; return "123456"; });
  const r = await elevatedFetch("http://api", "/portal/record?x", ID);
  assert.equal(r.status, 200);
  assert.equal(sheets, 1);
  assert.ok(calls.some((u) => u.endsWith("/portal/stepup/challenge")), "challenge sent");
  assert.ok(sess.has("oh-stepup-PT-1"), "token stored for the session");
});

test("wrong code re-opens the sheet with an error flag", async () => {
  globalThis.fetch = async (url, opts = {}) => {
    if (url.endsWith("/portal/stepup/challenge")) return jsonResp(200, {});
    if (url.endsWith("/portal/stepup/verify")) {
      return JSON.parse(opts.body).code === "999999"
        ? jsonResp(200, { stepup_token: "tok-9", expires_in: "600" }) : jsonResp(404, {});
    }
    return (opts.headers || {})["X-StepUp-Token"] === "tok-9" ? jsonResp(200, {}) : jsonResp(428, {});
  };
  const seen = [];
  setSheetFactory(async (_pid, { hadError }) => { seen.push(hadError); return seen.length === 1 ? "000000" : "999999"; });
  const r = await elevatedFetch("http://api", "/portal/record?x", ID);
  assert.equal(r.status, 200);
  assert.deepEqual(seen, [false, true]); // second sheet carries the error state
});

test("random bounce on a held token re-elevates transparently", async () => {
  let reads = 0, tokens = 0;
  globalThis.fetch = async (url, opts = {}) => {
    if (url.endsWith("/portal/stepup/challenge")) return jsonResp(200, {});
    if (url.endsWith("/portal/stepup/verify")) return jsonResp(200, { stepup_token: "tok-" + (++tokens), expires_in: "600" });
    reads++;
    // every read bounces until the FRESH token arrives (simulated re-auth roll)
    return reads === 1 ? jsonResp(428, {}) : jsonResp(200, { record: 1 });
  };
  setSheetFactory(async () => "123456");
  const r = await elevatedFetch("http://api", "/portal/record?x", ID);
  assert.equal(r.status, 200);
});

test("user cancel returns the honest 428 — no fabricated data", async () => {
  sess.clear();
  globalThis.fetch = async (url) => {
    if (url.endsWith("/portal/stepup/challenge")) return jsonResp(200, {});
    return jsonResp(428, { error: "step_up_required" });
  };
  setSheetFactory(async () => null);
  const r = await elevatedFetch("http://api", "/portal/record?x", ID);
  assert.equal(r.status, 428);
});

test("challenge undeliverable (offline) returns honest 428, no sheet loop", async () => {
  sess.clear();
  globalThis.fetch = async (url) =>
    url.endsWith("/portal/stepup/challenge") ? jsonResp(503, {}) : jsonResp(428, {});
  setSheetFactory(async () => { throw new Error("sheet must not open when no code was sent"); });
  const r = await elevatedFetch("http://api", "/portal/record?x", ID);
  assert.equal(r.status, 428);
});
