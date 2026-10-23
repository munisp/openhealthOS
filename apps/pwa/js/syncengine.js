/* Sync engine — the hardened half of offline-first (complements sync.js,
 * which owns the CRDT merge; this owns the event outbox drain).
 *
 * What hardens here:
 *
 * 1. CHUNKED DRAIN — a clinic on 2G with 600 queued events does not POST
 *    one 600-line body that dies at 90%. Events go up in chunks
 *    (default 25); each 2xx acks exactly those ids; a failed chunk stops
 *    the drain — order is preserved and nothing is skipped.
 *
 * 2. HONEST BACKOFF — consecutive failures grow a delay (exponential,
 *    capped, jittered) persisted in meta. The engine answers due()
 *    truthfully: "not yet" is a state, not a silent spin that burns the
 *    patient's battery on a dead tower.
 *
 * 3. NEVER THROWS INTO THE UI — same doctrine as sync.js: unreachable
 *    means {ok:false, queued:N}, the ward keeps working.
 *
 * All I/O is injectable (fetchImpl, now) so the engine is fully testable
 * in node without a browser.
 */

const DEFAULTS = {
  chunkSize: 25,
  baseDelayMs: 1000,
  maxDelayMs: 5 * 60 * 1000,
  jitter: 0.25,
};

export class SyncEngine {
  constructor(db, baseUrl, opts = {}) {
    this.db = db;
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.o = { ...DEFAULTS, ...opts };
    this.fetchImpl = opts.fetchImpl || fetch;
    this.now = opts.now || (() => Date.now());
    this.rand = opts.rand || Math.random;
  }

  async due() {
    const until = (await this.db.getMeta("syncNextRetryAt")) || 0;
    return this.now() >= until;
  }

  async _recordFailure() {
    const fails = ((await this.db.getMeta("syncFailCount")) || 0) + 1;
    const delay = Math.min(
      this.o.baseDelayMs * 2 ** (fails - 1), this.o.maxDelayMs);
    const jittered = delay * (1 - this.o.jitter + this.rand() * this.o.jitter);
    await this.db.setMeta("syncFailCount", fails);
    await this.db.setMeta("syncNextRetryAt", this.now() + Math.round(jittered));
    return fails;
  }

  async _recordSuccess() {
    await this.db.setMeta("syncFailCount", 0);
    await this.db.setMeta("syncNextRetryAt", 0);
    await this.db.setMeta("lastSync", new Date(this.now()).toISOString());
  }

  /* Drain the outbox in chunks. Returns
   * {ok, sent, remaining, chunks, error?} — remaining > 0 with ok:false
   * means the queue is intact and backoff is armed. */
  async drainOutbox() {
    const events = await this.db.outbox();
    if (!events.length) return { ok: true, sent: 0, remaining: 0, chunks: 0 };

    let sent = 0, chunks = 0;
    for (let i = 0; i < events.length; i += this.o.chunkSize) {
      const chunk = events.slice(i, i + this.o.chunkSize);
      const body = chunk.map((e) => JSON.stringify(e)).join("\n");
      try {
        const resp = await this.fetchImpl(`${this.baseUrl}/ingest/raw`, {
          method: "POST",
          headers: { "Content-Type": "application/jsonlines" },
          body,
        });
        if (!resp.ok) throw new Error(`ingest ${resp.status}`);
      } catch (e) {
        const fails = await this._recordFailure();
        return {
          ok: false, sent, remaining: events.length - sent, chunks,
          error: String(e), failCount: fails,
        };
      }
      // ack exactly what the server took — no more, no less
      await this.db.clearOutbox(chunk.map((e) => e.id));
      sent += chunk.length;
      chunks++;
    }
    await this._recordSuccess();
    return { ok: true, sent, remaining: 0, chunks };
  }

  async status() {
    const events = await this.db.outbox();
    return {
      queued: events.length,
      failCount: (await this.db.getMeta("syncFailCount")) || 0,
      nextRetryAt: (await this.db.getMeta("syncNextRetryAt")) || 0,
      due: await this.due(),
      lastSync: (await this.db.getMeta("lastSync")) || null,
    };
  }
}
