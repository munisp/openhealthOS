/* IndexedDB local-first store — the edge database on every phone.
 *
 * Three object stores:
 *   docs    — CRDT documents (patients, encounters, tasks) — the replica
 *   outbox  — domain events awaiting upload (append-only, acked on 2xx)
 *   meta    — settings, sync cursors, seed flag
 *
 * Every write lands here FIRST. The UI never waits on a network; sync is
 * a background concern (ADR-0001: connectivity is an optimisation).
 */

import { Replica } from "./crdt.js";

const DB_NAME = "openhealth";
const DB_VERSION = 1;
const IDB_TIMEOUT_MS = 2500; // embedded WebViews can hang IDB — never block care

function withTimeout(promise, ms = IDB_TIMEOUT_MS) {
  return Promise.race([
    promise,
    new Promise((_, rej) => setTimeout(() => rej(new Error("idb-timeout")), ms)),
  ]);
}

function open() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("docs")) db.createObjectStore("docs", { keyPath: "id" });
      if (!db.objectStoreNames.contains("outbox")) db.createObjectStore("outbox", { keyPath: "id" });
      if (!db.objectStoreNames.contains("meta")) db.createObjectStore("meta", { keyPath: "k" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/* In-memory fallback with the same shape as an IDB database handle —
 * used when IndexedDB is unavailable or hangs (embedded WebViews, private
 * mode). The app stays fully usable for the session; persistence resumes
 * on the next launch on a healthy browser. */
function memoryHandle() {
  const stores = { docs: new Map(), outbox: new Map(), meta: new Map() };
  const store = (name) => ({
    get: (k) => immediate(stores[name].get(k)),
    getAll: () => immediate([...stores[name].values()]),
    put: (v) => { stores[name].set(v.id ?? v.k, v); return immediate(v); },
    delete: (k) => { stores[name].delete(k); return immediate(undefined); },
  });
  const immediate = (v) => {
    const r = { result: v };
    queueMicrotask(() => r.onsuccess?.());
    return r;
  };
  return {
    __memory: true,
    transaction(name) {
      const s = store(name);
      const t = { objectStore: () => s, oncomplete: null, onerror: null };
      s.transaction = t;
      queueMicrotask(() => t.oncomplete?.());
      return t;
    },
  };
}

function tx(db, store, mode, fn) {
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const s = t.objectStore(store);
    const out = fn(s);
    t.oncomplete = () => resolve(out && out.result !== undefined ? out.result : out);
    t.onerror = () => reject(t.error);
  });
}

function reqToPromise(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export class LocalDB {
  constructor(db, deviceId) {
    this.db = db;
    this.deviceId = deviceId;
  }

  static async init() {
    let db;
    try {
      db = await withTimeout(open());
      const probe = db.transaction("meta", "readonly").objectStore("meta").get("deviceId");
      let deviceId = (await withTimeout(reqToPromise(probe)))?.v;
      if (!deviceId) {
        deviceId = "dev-" + crypto.randomUUID().slice(0, 8);
        await withTimeout(reqToPromise(
          db.transaction("meta", "readwrite").objectStore("meta").put({ k: "deviceId", v: deviceId })));
      }
      return new LocalDB(db, deviceId);
    } catch {
      // IndexedDB hung or is unavailable (embedded WebView, private mode):
      // fall back to an in-memory store so care workflows still run.
      return new LocalDB(memoryHandle(), "dev-" + crypto.randomUUID().slice(0, 8));
    }
  }

  /* Load all docs into a live Replica for CRDT writes. */
  async replica() {
    const r = new Replica(this.deviceId);
    const all = await reqToPromise(this.db.transaction("docs", "readonly").objectStore("docs").getAll());
    for (const d of all) r.docs.set(d.id, d);
    return r;
  }

  async putDoc(doc) {
    await reqToPromise(this.db.transaction("docs", "readwrite").objectStore("docs").put(doc));
  }

  async docsByType(type) {
    const all = await reqToPromise(this.db.transaction("docs", "readonly").objectStore("docs").getAll());
    return all.filter((d) => d.type === type);
  }

  async getDoc(id) {
    return reqToPromise(this.db.transaction("docs", "readonly").objectStore("docs").get(id));
  }

  async queueEvent(type, payload) {
    const ev = {
      id: `${this.deviceId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      type,
      ts: new Date().toISOString(),
      payload,
    };
    await reqToPromise(this.db.transaction("outbox", "readwrite").objectStore("outbox").put(ev));
    return ev;
  }

  async outbox() {
    return reqToPromise(this.db.transaction("outbox", "readonly").objectStore("outbox").getAll());
  }

  async clearOutbox(ids) {
    const s = this.db.transaction("outbox", "readwrite").objectStore("outbox");
    for (const id of ids) await reqToPromise(s.delete(id));
  }

  async getMeta(k) {
    return (await reqToPromise(this.db.transaction("meta", "readonly").objectStore("meta").get(k)))?.v;
  }

  async setMeta(k, v) {
    await reqToPromise(this.db.transaction("meta", "readwrite").objectStore("meta").put({ k, v }));
  }

  /* Persist every doc of a replica (after local mutations or merges). */
  async saveReplica(replica) {
    const s = this.db.transaction("docs", "readwrite").objectStore("docs");
    for (const d of replica.docs.values()) s.put(d);
    await new Promise((res, rej) => {
      s.transaction.oncomplete = res;
      s.transaction.onerror = () => rej(s.transaction.error);
    });
  }

  /* Merge an inbound batch (server truth) into local docs. */
  async applyBatch(batch) {
    const r = await this.replica();
    r.applyBatch(batch);
    await this.saveReplica(r);
    return r;
  }
}
