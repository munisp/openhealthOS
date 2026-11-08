/* Sync gate client — the device half of the syncmesh doctrine.
 *
 * syncengine.js drains bulk events to /ingest/raw. This module is the
 * CLINICAL path: every mutation that must be judged (eMAR
 * administrations, flowsheet amendments, prescriptions) travels one
 * envelope at a time to /sync/upload, where the server-side UploadGate
 * replays it through the real clinical engine and returns a verdict.
 *
 * Doctrine carried onto the device:
 *
 * 1. IDEMPOTENCY ENVELOPES — the outbox event id IS the idempotency
 *    key. A judged mutation is never re-sent; a network retry that
 *    arrives twice is answered with the stored verdict ("replayed").
 *
 * 2. VERDICTS TRAVEL BACK — applied clears the queue; refused moves to
 *    the dead-letter list WITH its stated reason (the nurse sees "wrong
 *    wristband", not a spinner); conflict stays pending — the amendment
 *    flow resolves it, never a silent overwrite.
 *
 * 3. CHECKPOINT CHAIN — the follower stores the last checkpoint and
 *    verifies the prev-link on every advance. A broken chain refuses to
 *    trust the stream and demands a resync. The checksum is the
 *    contract, on the device too.
 *
 * 4. LOCAL RETENTION MIRRORS THE SERVER — episode / rolling-30d /
 *    rolling-90d / pinned, evicted with a stated reason. The record
 *    lives on the server, not the device.
 *
 * All I/O is injectable (fetchImpl, now, sha) — fully testable in node.
 */

const GENESIS = "0".repeat(64);

/* Canonical JSON: sorted keys, UTF-8 — same contract as the server's
 * json.dumps(sort_keys=True), so a base checksum computed on the device
 * matches the server's. */
export function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const keys = Object.keys(value).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k])}`).join(",")}}`;
}

export async function sha256Hex(text) {
  const subtle = globalThis.crypto && globalThis.crypto.subtle;
  if (!subtle) throw new Error("no subtle crypto — refuse to checksum weakly");
  const digest = await subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/* Wrap one outbox event in the UploadGate envelope. The event carries
 * entityClass, ruleId, op and payload; base (the record the mutation
 * was written against) is checksummed canonically — GENESIS when the
 * mutation creates rather than amends. */
export async function buildMutation(event, { sha = sha256Hex } = {}) {
  if (!event || !event.id) throw new Error("an outbox event carries an id — it is the idempotency key");
  for (const k of ["entityClass", "ruleId", "op", "payload"]) {
    if (!event[k]) throw new Error(`mutation envelope missing ${k}`);
  }
  const baseSha = event.base ? await sha(canonicalJson(event.base)) : GENESIS;
  return {
    idempotency_key: event.id,
    entity_class: event.entityClass,
    rule_id: event.ruleId,
    base_sha256: baseSha,
    op: event.op,
    payload: event.payload,
  };
}

/* Drains the clinical outbox through the gate. Verdict handling:
 *   applied  -> cleared from the queue
 *   refused  -> dead-lettered with the stated reason, never retried
 *   conflict -> left pending; the amendment flow owns it
 *   replayed -> treated as its original verdict (the server remembered)
 * A network failure stops the drain with backoff — order preserved. */
export class GateUploader {
  constructor(db, baseUrl, opts = {}) {
    this.db = db;
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.fetchImpl = opts.fetchImpl || fetch;
    this.sha = opts.sha || sha256Hex;
  }

  async uploadOne(event) {
    const mutation = await buildMutation(event, { sha: this.sha });
    const resp = await this.fetchImpl(`${this.baseUrl}/sync/upload`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(mutation),
    });
    if (!resp.ok) throw new Error(`gate ${resp.status}`);
    return resp.json();
  }

  /* Returns {applied, refused, conflicts, remaining, error?}. */
  async drain() {
    const events = await this.db.outbox();
    let applied = 0, refused = 0, conflicts = 0, done = 0;
    for (const event of events) {
      let verdict;
      try {
        verdict = await this.uploadOne(event);
      } catch (e) {
        return { applied, refused, conflicts,
                 remaining: events.length - done, error: String(e) };
      }
      done++;
      if (verdict.verdict === "applied") {
        await this.db.clearOutbox([event.id]);
        applied++;
      } else if (verdict.verdict === "refused") {
        await this.db.clearOutbox([event.id]);
        await this.db.setMeta(`dead:${event.id}`, {
          id: event.id, entityClass: event.entityClass,
          reason: verdict.reason || "refused — no reason stated",
        });
        refused++;
      } else if (verdict.verdict === "conflict") {
        conflicts++; // stays queued — amendment flow owns it
      }
    }
    return { applied, refused, conflicts, remaining: events.length - done };
  }

  async deadLetters() {
    const all = await this.db.allMeta();
    return Object.entries(all)
      .filter(([k]) => k.startsWith("dead:"))
      .map(([, v]) => v);
  }
}

/* Follows the server's checkpoint chain. Each checkpoint carries
 * {id, prev, sha256}; a checkpoint whose prev does not match the stored
 * head is a broken chain — refuse to advance and demand a resync. */
export class CheckpointFollower {
  constructor(db) { this.db = db; }

  async head() {
    return (await this.db.getMeta("checkpointHead")) || GENESIS;
  }

  /* Returns {advanced: true, head} or {advanced: false, reason}. */
  async advance(checkpoint) {
    if (!checkpoint || !checkpoint.id || !checkpoint.prev) {
      return { advanced: false, reason: "checkpoint malformed" };
    }
    const head = await this.head();
    if (checkpoint.prev !== head) {
      return { advanced: false,
               reason: "checkpoint chain broken — resync required" };
    }
    await this.db.setMeta("checkpointHead", checkpoint.id);
    return { advanced: true, head: checkpoint.id };
  }
}

/* Local retention — the same four classes as the server. Records carry
 * {ref, retention, episode_open?, last_touch_day}; evaluate(day) splits
 * kept from evicted, every eviction stating its reason. */
export const RETENTION_LIMITS = { "rolling-30d": 30, "rolling-90d": 90 };

export class LocalRetention {
  constructor() { this.evictionLog = []; }

  evaluate(records, day) {
    const kept = [], evicted = [];
    for (const r of records) {
      if (!r.retention) throw new Error(`record ${r.ref} declares no retention class`);
      if (r.retention === "pinned") { kept.push(r); continue; }
      if (r.retention === "episode") {
        if (r.episode_open) { kept.push(r); continue; }
        evicted.push(r);
        this.evictionLog.push({ ref: r.ref, day,
          reason: "episode closed — the record lives on the server, not the device" });
        continue;
      }
      const limit = RETENTION_LIMITS[r.retention];
      if (limit === undefined) throw new Error(`unknown retention class: ${r.retention}`);
      if (day - (r.last_touch_day || 0) > limit) {
        evicted.push(r);
        this.evictionLog.push({ ref: r.ref, day,
          reason: `${r.retention} window passed — the record lives on the server, not the device` });
      } else {
        kept.push(r);
      }
    }
    return [kept, evicted];
  }
}

export const SYNC_GATE_VERSION = "syncgate@1.0.0";
