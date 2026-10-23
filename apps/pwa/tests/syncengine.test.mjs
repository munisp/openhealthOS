/* CI: offline-first sync engine — chunking, per-chunk ack, honest backoff.
 * Run: node --test apps/pwa/tests/ */
import { test } from "node:test";
import assert from "node:assert/strict";
import { SyncEngine } from "../js/syncengine.js";

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
    __outbox: outbox,
  };
}

const evs = (n) =>
  Array.from({ length: n }, (_, i) => ({ id: `e${i}`, t: "obs", v: i }));

const okFetch = (bodies) => async (url, init) => {
  bodies.push(init.body.split("\n").length);
  return { ok: true };
};

test("drains 60 events in 25-event chunks, all acked", async () => {
  const db = memDb(evs(60));
  const bodies = [];
  const eng = new SyncEngine(db, "http://node/", {
    fetchImpl: okFetch(bodies), rand: () => 0.5,
  });
  const out = await eng.drainOutbox();
  assert.equal(out.ok, true);
  assert.equal(out.sent, 60);
  assert.deepEqual(bodies, [25, 25, 10]);
  assert.equal(db.__outbox.length, 0);
});

test("failed chunk stops the drain, unacked events survive", async () => {
  const db = memDb(evs(60));
  let calls = 0;
  const eng = new SyncEngine(db, "http://node/", {
    rand: () => 0.5,
    fetchImpl: async () => (++calls === 2 ? { ok: false, status: 502 } : { ok: true }),
  });
  const out = await eng.drainOutbox();
  assert.equal(out.ok, false);
  assert.equal(out.sent, 25);           // first chunk acked
  assert.equal(out.remaining, 35);      // rest intact, in order
  assert.equal(db.__outbox[0].id, "e25");
  assert.equal(calls, 2);               // stopped at the failure
});

test("unreachable tower: nothing lost, backoff armed", async () => {
  const db = memDb(evs(5));
  const eng = new SyncEngine(db, "http://node/", {
    rand: () => 0.5,
    fetchImpl: async () => { throw new Error("network down"); },
  });
  const out = await eng.drainOutbox();
  assert.equal(out.ok, false);
  assert.equal(out.sent, 0);
  assert.equal(db.__outbox.length, 5);
  assert.equal(out.failCount, 1);
  assert.equal(await eng.due(), false); // backoff says: not yet
});

test("backoff grows exponentially and caps", async () => {
  const db = memDb(evs(1));
  let clock = 1_000_000;
  const eng = new SyncEngine(db, "http://node/", {
    rand: () => 0.5, now: () => clock,
    fetchImpl: async () => { throw new Error("down"); },
  });
  const delays = [];
  for (let i = 0; i < 6; i++) {
    await eng.drainOutbox();
    delays.push((await db.getMeta("syncNextRetryAt")) - clock);
  }
  // jitter with rand=0.5 is deterministic: delay * 0.875
  assert.ok(delays[1] > delays[0]);
  assert.ok(delays[5] <= 5 * 60 * 1000); // capped
});

test("success resets backoff", async () => {
  const db = memDb(evs(1));
  let fail = true;
  const eng = new SyncEngine(db, "http://node/", {
    rand: () => 0.5,
    fetchImpl: async () => (fail ? (() => { throw new Error("down"); })() : { ok: true }),
  });
  await eng.drainOutbox();
  assert.equal(await db.getMeta("syncFailCount"), 1);
  fail = false;
  const out = await eng.drainOutbox();
  assert.equal(out.ok, true);
  assert.equal(await db.getMeta("syncFailCount"), 0);
  assert.equal(await eng.due(), true);
});

test("empty outbox is a clean no-op", async () => {
  const eng = new SyncEngine(memDb(), "http://node/", {
    fetchImpl: async () => { throw new Error("must not be called"); },
  });
  const out = await eng.drainOutbox();
  assert.deepEqual(out, { ok: true, sent: 0, remaining: 0, chunks: 0 });
});

test("chunk bodies preserve event order", async () => {
  const db = memDb(evs(30));
  const seen = [];
  const eng = new SyncEngine(db, "http://node/", {
    fetchImpl: async (url, init) => {
      seen.push(...init.body.split("\n").map((l) => JSON.parse(l).id));
      return { ok: true };
    },
  });
  await eng.drainOutbox();
  assert.deepEqual(seen, evs(30).map((e) => e.id));
});
