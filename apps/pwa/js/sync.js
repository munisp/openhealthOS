/* Sync client — drains the local replica and event outbox to the state
 * plane. Never throws into the UI: unreachable = "queued, will retry".
 *
 * Endpoints (via the api-gateway when deployed, direct on the edge):
 *   POST {base}/sync/merge   — CRDT replica batch (entity state)
 *   POST {base}/ingest/raw   — plain JSON-lines events (audit trail)
 * Server-side merge is idempotent, so retries are always safe.
 */

import { materialize } from "./crdt.js";

export class SyncClient {
  constructor(db, baseUrl) {
    this.db = db;
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async status() {
    const docs = await this.db.outbox();
    const lastSync = await this.db.getMeta("lastSync");
    const lastResult = await this.db.getMeta("lastSyncResult");
    const all = await this.db.docsByType("");
    const anyDocs = await this.db.replica();
    return {
      pendingEvents: docs.length,
      totalDocs: anyDocs.docs.size,
      lastSync: lastSync || null,
      lastResult: lastResult || null,
      online: navigator.onLine,
    };
  }

  /* One sync pass. Returns {ok, merged, events, error?}. */
  async syncNow() {
    const replica = await this.db.replica();
    const batch = replica.exportBatch();
    const events = await this.db.outbox();
    let merged = 0;

    try {
      const resp = await fetch(`${this.baseUrl}/sync/merge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(batch),
      });
      if (!resp.ok) throw new Error(`merge ${resp.status}`);
      merged = (await resp.json()).merged ?? batch.docs.length;
    } catch (e) {
      await this.db.setMeta("lastSyncResult", "failed");
      return { ok: false, merged: 0, events: 0, error: String(e) };
    }

    // events outbox: plain JSON-lines to /ingest/raw (no gzip — works on
    // ancient Android browsers; the facility node's agent handles gzip)
    let sent = 0;
    if (events.length) {
      try {
        const body = events.map((e) => JSON.stringify(e)).join("\n");
        const resp = await fetch(`${this.baseUrl}/ingest/raw`, {
          method: "POST",
          headers: { "Content-Type": "application/jsonlines" },
          body,
        });
        if (!resp.ok) throw new Error(`ingest ${resp.status}`);
        await this.db.clearOutbox(events.map((e) => e.id));
        sent = events.length;
      } catch (e) {
        await this.db.setMeta("lastSync", new Date().toISOString());
        await this.db.setMeta("lastSyncResult", "partial");
        return { ok: false, merged, events: 0, error: String(e) };
      }
    }

    await this.db.setMeta("lastSync", new Date().toISOString());
    await this.db.setMeta("lastSyncResult", "ok");
    return { ok: true, merged, events: sent };
  }
}

export { materialize };
