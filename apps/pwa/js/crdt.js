/* OpenHealth OS — CRDT core (JS port of shared/crdt, byte-compatible).
 *
 * Merge law parity with the Go implementation is a correctness requirement:
 * divergence between edge (JS) and state-plane (Go) merge = lost clinical
 * facts. The shapes are identical:
 *   doc  = { id, type, fields: { name: field } }
 *   field= { k: "lww"|"set", v: <json value>, s: {w,l,n}, t?: true }
 *   stamp order: wall, then logic, then node id (lexicographic)
 *
 * Pure module: no DOM, no IndexedDB — runs in the browser and under
 * node --test (CI conformance).
 */

export function hlcAfter(a, b) {
  if (a.w !== b.w) return a.w > b.w;
  if (a.l !== b.l) return a.l > b.l;
  return a.n > b.n;
}

export class Clock {
  constructor(node) {
    this.node = node;
    this.last = { w: 0, l: 0, n: node };
  }
  tick(now = Date.now()) {
    if (now > this.last.w) this.last = { w: now, l: 0, n: this.node };
    else this.last = { w: this.last.w, l: this.last.l + 1, n: this.node };
    return { ...this.last };
  }
  observe(remote, now = Date.now()) {
    const maxW = Math.max(now, this.last.w, remote.w);
    if (maxW === this.last.w) {
      const l = remote.w === this.last.w ? Math.max(this.last.l, remote.l) : this.last.l;
      this.last = { w: maxW, l: l + 1, n: this.node };
    } else if (maxW === remote.w) {
      this.last = { w: maxW, l: remote.l + 1, n: this.node };
    } else {
      this.last = { w: maxW, l: 0, n: this.node };
    }
    return { ...this.last };
  }
}

export class Replica {
  constructor(node) {
    this.clock = new Clock(node);
    this.docs = new Map();
  }
  _doc(id, type) {
    let d = this.docs.get(id);
    if (!d) {
      d = { id, type, fields: {} };
      this.docs.set(id, d);
    }
    return d;
  }
  set(id, type, field, value, now) {
    this._doc(id, type).fields[field] = { k: "lww", v: value, s: this.clock.tick(now) };
  }
  add(id, type, field, members, now) {
    const d = this._doc(id, type);
    let f = d.fields[field];
    if (!f) f = d.fields[field] = { k: "set", v: [], s: this.clock.tick(now) };
    const set = new Set(Array.isArray(f.v) ? f.v : []);
    for (const m of members) set.add(m);
    f.v = [...set].sort();
    f.s = this.clock.tick(now);
  }
  del(id, type, field, now) {
    const d = this._doc(id, type);
    const f = d.fields[field];
    if (f && f.k === "set") return; // add-only sets are never erased
    d.fields[field] = { k: "lww", v: null, s: this.clock.tick(now), t: true };
  }

  /* Merge remote doc into local. Idempotent + commutative. */
  mergeDoc(remote) {
    const local = this._doc(remote.id, remote.type);
    if (!local.type) local.type = remote.type;
    for (const [name, rf] of Object.entries(remote.fields)) {
      this.clock.observe(rf.s);
      const lf = local.fields[name];
      if (!lf || (rf.k !== "set" && hlcAfter(rf.s, lf.s))) {
        local.fields[name] = structuredClone(rf);
        continue;
      }
      if (lf.k === "set" || rf.k === "set") {
        // union regardless of stamp: a fact recorded anywhere counts
        const set = new Set([...(Array.isArray(lf.v) ? lf.v : []), ...(Array.isArray(rf.v) ? rf.v : [])]);
        lf.k = "set";
        lf.v = [...set].sort();
        if (hlcAfter(rf.s, lf.s)) lf.s = rf.s;
      }
    }
    return local;
  }

  exportBatch() {
    return { from: this.clock.node, docs: [...this.docs.values()].sort((a, b) => (a.id < b.id ? -1 : 1)) };
  }

  applyBatch(batch) {
    let n = 0;
    for (const d of batch.docs || []) {
      this.mergeDoc(d);
      n++;
    }
    return n;
  }
}

export function materialize(doc) {
  const out = { id: doc.id, type: doc.type };
  for (const [name, f] of Object.entries(doc.fields)) {
    if (f.t) continue;
    out[name] = f.v;
  }
  return out;
}
