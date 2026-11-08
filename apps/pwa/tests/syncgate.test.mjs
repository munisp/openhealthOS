/* CI: sync gate client — idempotent envelopes, verdicts that travel
 * back, checkpoint chain, local retention. Run: node --test apps/pwa/tests/ */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildMutation, canonicalJson, sha256Hex,
  GateUploader, CheckpointFollower, LocalRetention,
  SYNC_GATE_VERSION,
} from "../js/syncgate.js";

function memDb(events = []) {
  const outbox = [...events];
  const meta = new Map();
  return {
    outbox: async () => [...outbox],
    clearOutbox: async (ids) => {
      const drop = new Set(ids);
      for (let i = outbox.length - 1; i >= 0; i--) {
        if (drop.has(outbox[i].id)) outbox.splice(i, 1);
      }
    },
    getMeta: async (k) => meta.get(k),
    setMeta: async (k, v) => meta.set(k, v),
    allMeta: async () => Object.fromEntries(meta),
    __outbox: outbox,
    __meta: meta,
  };
}

const event = (id, over = {}) => ({
  id, entityClass: "emar-administration", ruleId: "r-ward-emar",
  op: "record",
  payload: { slot_id: "mar-1-S1", state: "given", by: "nurse-ada" },
  ...over,
});

test("envelope: event id is the idempotency key", async () => {
  const m = await buildMutation(event("up-1"));
  assert.equal(m.idempotency_key, "up-1");
  assert.equal(m.entity_class, "emar-administration");
  assert.equal(m.rule_id, "r-ward-emar");
  assert.equal(m.op, "record");
  assert.deepEqual(m.payload.slot_id, "mar-1-S1");
  assert.match(m.base_sha256, /^[0-9a-f]{64}$/);
});

test("envelope: no base record -> GENESIS checksum", async () => {
  const m = await buildMutation(event("up-2"));
  assert.equal(m.base_sha256, "0".repeat(64));
});

test("envelope: base record checksums canonically", async () => {
  const m = await buildMutation(event("up-3", { base: { b: 1, a: 2 } }));
  const expect = await sha256Hex(canonicalJson({ a: 2, b: 1 }));
  assert.equal(m.base_sha256, expect);
});

test("envelope: missing id refuses — the key is not optional", async () => {
  await assert.rejects(buildMutation({ entityClass: "x", ruleId: "r",
    op: "record", payload: {} }), /idempotency/);
});

test("applied verdict clears the queue", async () => {
  const db = memDb([event("up-1"), event("up-2")]);
  const up = new GateUploader(db, "http://node/", {
    fetchImpl: async () => ({ ok: true, json: async () => ({ verdict: "applied" }) }),
  });
  const out = await up.drain();
  assert.deepEqual(out, { applied: 2, refused: 0, conflicts: 0, remaining: 0 });
  assert.equal(db.__outbox.length, 0);
});

test("refusal dead-letters WITH the stated reason, queue moves on", async () => {
  const db = memDb([event("bad"), event("good")]);
  const up = new GateUploader(db, "http://node/", {
    fetchImpl: async (_u, init) => {
      const key = JSON.parse(init.body).idempotency_key;
      return { ok: true, json: async () => key === "bad"
        ? { verdict: "refused", reason: "patient barcode mismatch — wrong wristband" }
        : { verdict: "applied" } };
    },
  });
  const out = await up.drain();
  assert.deepEqual(out, { applied: 1, refused: 1, conflicts: 0, remaining: 0 });
  const dead = await up.deadLetters();
  assert.equal(dead.length, 1);
  assert.equal(dead[0].id, "bad");
  assert.match(dead[0].reason, /wrong wristband/);
});

test("conflict stays pending — never silently overwritten", async () => {
  const db = memDb([event("c-1")]);
  const up = new GateUploader(db, "http://node/", {
    fetchImpl: async () => ({ ok: true, json: async () => ({
      verdict: "conflict", policy: "amend-not-edit",
      reason: "row changed under you" }) }),
  });
  const out = await up.drain();
  assert.deepEqual(out, { applied: 0, refused: 0, conflicts: 1, remaining: 0 });
  assert.equal(db.__outbox.length, 1); // still queued for the amendment flow
});

test("network failure stops the drain, order preserved", async () => {
  const db = memDb([event("a"), event("b"), event("c")]);
  let calls = 0;
  const up = new GateUploader(db, "http://node/", {
    fetchImpl: async () => (++calls === 2
      ? { ok: false, status: 502 }
      : { ok: true, json: async () => ({ verdict: "applied" }) }),
  });
  const out = await up.drain();
  assert.equal(out.applied, 1);
  assert.equal(out.remaining, 2);
  assert.match(out.error, /502/);
  assert.equal(db.__outbox[0].id, "b");
});

test("replayed verdict still clears — the server remembered", async () => {
  const db = memDb([event("up-9")]);
  const up = new GateUploader(db, "http://node/", {
    fetchImpl: async () => ({ ok: true, json: async () => ({
      verdict: "applied", replayed: true }) }),
  });
  const out = await up.drain();
  assert.equal(out.applied, 1);
  assert.equal(db.__outbox.length, 0);
});

test("checkpoint follower advances on a linked chain", async () => {
  const db = memDb();
  const f = new CheckpointFollower(db);
  assert.equal(await f.head(), "0".repeat(64));
  const r1 = await f.advance({ id: "cp1", prev: "0".repeat(64) });
  assert.equal(r1.advanced, true);
  const r2 = await f.advance({ id: "cp2", prev: "cp1" });
  assert.equal(r2.advanced, true);
  assert.equal(await f.head(), "cp2");
});

test("checkpoint follower refuses a broken chain — resync required", async () => {
  const db = memDb();
  const f = new CheckpointFollower(db);
  await f.advance({ id: "cp1", prev: "0".repeat(64) });
  const out = await f.advance({ id: "cpX", prev: "somebody-else" });
  assert.equal(out.advanced, false);
  assert.match(out.reason, /chain broken/);
  assert.equal(await f.head(), "cp1"); // head unmoved
});

test("local retention: episode close and rolling window evict with reasons", () => {
  const rt = new LocalRetention();
  const records = [
    { ref: "r1", retention: "pinned" },
    { ref: "r2", retention: "episode", episode_open: true },
    { ref: "r3", retention: "episode", episode_open: false },
    { ref: "r4", retention: "rolling-30d", last_touch_day: 10 },
    { ref: "r5", retention: "rolling-30d", last_touch_day: 95 },
  ];
  const [kept, evicted] = rt.evaluate(records, 100);
  assert.deepEqual(kept.map((r) => r.ref), ["r1", "r2", "r5"]);
  assert.deepEqual(evicted.map((r) => r.ref), ["r3", "r4"]);
  assert.equal(rt.evictionLog.length, 2);
  assert.match(rt.evictionLog[0].reason, /episode closed/);
  assert.match(rt.evictionLog[1].reason, /rolling-30d window passed/);
});

test("local retention: unknown class refuses", () => {
  const rt = new LocalRetention();
  assert.throws(() => rt.evaluate(
    [{ ref: "x", retention: "forever-ish", last_touch_day: 1 }], 2),
    /retention/);
});

test("version stamp", () => {
  assert.equal(SYNC_GATE_VERSION, "syncgate@1.0.0");
});

test("batch drain: one request, per-mutation verdicts applied", async () => {
  const db = memDb([event("a"), event("b"), event("c")]);
  const seen = [];
  const up = new GateUploader(db, "http://node/", {
    fetchImpl: async (_u, init) => {
      const body = JSON.parse(init.body);
      seen.push(body.mutations.length);
      return { ok: true, json: async () => ({ verdicts:
        body.mutations.map((m) => m.idempotency_key === "b"
          ? { verdict: "refused", reason: "engine says no" }
          : { verdict: "applied" }) }) };
    },
  });
  const out = await up.drainBatch();
  assert.deepEqual(seen, [3]);            // one round trip
  assert.deepEqual(out, { applied: 2, refused: 1, conflicts: 0, remaining: 0 });
  const dead = await up.deadLetters();
  assert.equal(dead[0].reason, "engine says no");
});

test("batch drain: verdict count mismatch holds the queue", async () => {
  const db = memDb([event("a"), event("b")]);
  const up = new GateUploader(db, "http://node/", {
    fetchImpl: async () => ({ ok: true, json: async () => ({
      verdicts: [{ verdict: "applied" }] }) }),  // one short — breach
  });
  const out = await up.drainBatch();
  assert.equal(out.remaining, 2);
  assert.match(out.error, /count mismatch/);
  assert.equal(db.__outbox.length, 2);
});

test("batch drain: transport failure loses nothing", async () => {
  const db = memDb([event("a"), event("b")]);
  const up = new GateUploader(db, "http://node/", {
    fetchImpl: async () => { throw new Error("tower down"); },
  });
  const out = await up.drainBatch();
  assert.equal(out.remaining, 2);
  assert.match(out.error, /tower down/);
});
