"""syncmesh.py — offline sync governance for the whole platform.

The platform's offline claim used to stop at "pages survive a tunnel."
This package is the layer that makes it "a clinic runs a full day with
no network and reconciles honestly afterward" — for every write path,
not a pilot: eMAR administrations, flowsheets, orders, prescriptions,
certificates, teleconsulta bundles, panel recalls.

The design mirrors the PowerSync contract (buckets, scoped sync rules,
client upload queue draining through the backend) while staying a
clean-room engine: PowerSync — Apache 2.0 since 2026-11-03 — can carry
the transport later; the governance here is the platform's own and does
not change with the transport.

Doctrine:
- SYNC RULES ARE RATIFIED ENTRIES: who may hold what data offline is
  clinical governance — proposed, ratified four-eyes, versioned. A
  rule edited in a console is a breach, not a config change.
- THE SERVER DECIDES THE BUCKET: scope is evaluated server-side from
  the principal; a client never asks for data it was not granted.
- EVERY UPLOAD PASSES ITS ENGINE: an offline write replays through the
  same fail-closed validator it would have hit online. Sync is a
  transport, never a bypass.
- CONFLICTS HAVE DECLARED POLICIES: append-only, amend-not-edit,
  refuse-and-reissue, or latest-wins-with-audit — per entity class,
  stated on the entry. Silent merges are how records become fiction.
- EVICTION IS STATED: offline retention is bounded and every eviction
  is recorded; a missing row on a device is a policy outcome, not a
  mystery.
- IDEMPOTENCY IS THE CONTRACT: a retried upload is recognised by its
  idempotency key and answered with the original verdict, never
  applied twice.
"""

import hashlib
import json
import sqlite3
import time

VERSION = "syncmesh@1.2.0"

# Scope axes a sync rule may declare. A rule is a conjunction of axes.
SCOPE_AXES = ("facility", "role", "panel", "patient-consent")

# Entity classes the platform syncs, with their declared conflict
# policy. Adding an entity is a registry entry, never a branch.
CONFLICT_POLICIES = ("append-only", "amend-not-edit",
                     "refuse-and-reissue", "latest-wins-with-audit")

DEFAULT_ENTITY_CLASSES = {
    # Observations accrete; two devices recording vitals never collide.
    "observation": "append-only",
    # A flowsheet row is corrected by a citing amendment, never edited.
    "flowsheet-row": "amend-not-edit",
    # An eMAR administration is a witnessed act; replays are idempotent,
    # a changed record for the same slot refuses and must be re-issued.
    "emar-administration": "refuse-and-reissue",
    "medication-order": "refuse-and-reissue",
    "prescription": "refuse-and-reissue",
    # Referrals and consults move forward; the newer state wins but the
    # loser is kept in the audit trail.
    "teleconsulta-status": "latest-wins-with-audit",
    "referral-status": "latest-wins-with-audit",
    # Certificates are signed documents — a changed signed document is
    # a new document.
    "certificate": "refuse-and-reissue",
    "panel-recall": "append-only",
    "triage-call": "append-only",
}

RETENTION_CLASSES = ("episode", "rolling-30d", "rolling-90d",
                     "pinned")


class Error(Exception):
    pass


def _sha(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class SyncRuleRegistry:
    """Versioned, four-eyes ratified sync rules. The registry is the
    only source the bucket evaluator and the PowerSync generator read —
    a hand-edited YAML is not governance."""

    def __init__(self):
        self.rules = {}
        self._live_cache = None  # invalidated on every transition

    def propose(self, rule_id, definition, *, by, day):
        if rule_id in self.rules:
            raise Error(f"rule {rule_id} exists; version up, never "
                        "edit in place")
        required = {"entity_class", "bucket", "scopes", "retention",
                    "rule_version"}
        missing = required - set(definition)
        if missing:
            raise Error(f"sync rule missing {sorted(missing)}")
        if definition["entity_class"] not in DEFAULT_ENTITY_CLASSES:
            raise Error(f"unknown entity class "
                        f"{definition['entity_class']!r}; entity classes "
                        "are entries — register the class first")
        unknown_axes = set(definition["scopes"]) - set(SCOPE_AXES)
        if unknown_axes:
            raise Error(f"unknown scope axes {sorted(unknown_axes)}; "
                        f"axes are {SCOPE_AXES}")
        if not definition["scopes"]:
            raise Error("a rule with no scopes syncs everything to "
                        "everyone — refused")
        if definition["retention"] not in RETENTION_CLASSES:
            raise Error(f"unknown retention "
                        f"{definition['retention']!r}; classes are "
                        f"{RETENTION_CLASSES}")
        self.rules[rule_id] = {**definition, "status": "proposed",
                               "proposed_by": by, "proposed_day": day}
        self._live_cache = None

    def ratify(self, rule_id, *, by, day):
        r = self.rules.get(rule_id)
        if r is None or r["status"] != "proposed":
            raise Error(f"nothing to ratify for {rule_id!r}")
        if by == r["proposed_by"]:
            raise Error("the hand that proposes never approves")
        r["status"] = "live"
        r["ratified_by"] = by
        r["ratified_day"] = day
        self._live_cache = None

    def retire(self, rule_id, *, by, day, reason):
        r = self.rules.get(rule_id)
        if r is None or r["status"] != "live":
            raise Error(f"{rule_id!r} is not live")
        if not reason:
            raise Error("retirement carries a stated reason — clients "
                        "holding this bucket must be told why it ends")
        r["status"] = "retired"
        r["retired"] = {"by": by, "day": day, "reason": reason}
        self._live_cache = None

    def live(self, rule_id=None):
        if self._live_cache is None:
            self._live_cache = {k: v for k, v in self.rules.items()
                                if v["status"] == "live"}
        live = self._live_cache
        if rule_id is None:
            return live
        if rule_id not in live:
            raise Error(f"no live sync rule {rule_id!r}")
        return live[rule_id]


class Principal:
    """Who is asking: facility, roles, panel, consented patients. The
    server computes this from its own records — a client asserting a
    role is a claim, not a fact."""

    def __init__(self, subject, *, facility, roles=(), panel=None,
                 consented_patients=frozenset()):
        if not facility:
            raise Error("a principal has a facility")
        self.subject = subject
        self.facility = facility
        self.roles = frozenset(roles)
        self.panel = panel  # clinician id whose panel they hold
        self.consented_patients = frozenset(consented_patients)

    def satisfies(self, scopes, *, record):
        """Does this principal satisfy every axis the rule declares,
        for this record?"""
        for axis in scopes:
            if axis == "facility":
                if record.get("facility") != self.facility:
                    return False
            elif axis == "role":
                needed = record.get("role_required")
                if needed and needed not in self.roles:
                    return False
            elif axis == "panel":
                if record.get("panel") not in (None, self.panel):
                    return False
            elif axis == "patient-consent":
                if record.get("patient_ref") not in \
                        self.consented_patients:
                    return False
        return True


class BucketEvaluator:
    """Server-side scope evaluation: which buckets a principal holds,
    and whether one record belongs in their offline set. A record that
    fails scope never leaves the server."""

    def __init__(self, registry):
        self.registry = registry

    def buckets_for(self, principal):
        out = []
        for rule_id, rule in sorted(self.registry.live().items()):
            out.append({"bucket": rule["bucket"], "rule": rule_id,
                        "rule_version": rule["rule_version"],
                        "entity_class": rule["entity_class"],
                        "retention": rule["retention"]})
        return out

    def admits(self, rule_id, principal, record):
        rule = self.registry.live(rule_id)
        return principal.satisfies(rule["scopes"], record=record)


class MemoryStore:
    """Default verdict store — fast, volatile. Honest about it: a
    restart forgets every verdict. Deployments that promise
    exactly-once across restarts must inject SqliteStore."""

    durable = False

    def __init__(self):
        self.verdicts = {}  # key -> (verdict dict, day)

    def get(self, key):
        hit = self.verdicts.get(key)
        return dict(hit[0]) if hit else None

    def put(self, key, verdict, *, day):
        self.verdicts[key] = (dict(verdict), day)

    def prune(self, *, before_day):
        drop = [k for k, (_, d) in self.verdicts.items()
                if d < before_day]
        for k in drop:
            del self.verdicts[k]
        return len(drop)

    def __len__(self):
        return len(self.verdicts)


class SqliteStore:
    """Durable verdict store on stdlib sqlite3 — the idempotency
    contract survives restarts and deploys. One table, one row per
    judged key; WAL mode so readers never block the writer."""

    durable = True

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS verdicts ("
                        "key TEXT PRIMARY KEY, day INTEGER, "
                        "body TEXT NOT NULL)")
        self.db.commit()

    def get(self, key):
        row = self.db.execute("SELECT body FROM verdicts WHERE key=?",
                              (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, verdict, *, day):
        self.db.execute(
            "INSERT OR REPLACE INTO verdicts (key, day, body) "
            "VALUES (?,?,?)",
            (key, day, json.dumps(verdict, sort_keys=True,
                                  ensure_ascii=False)))
        self.db.commit()

    def prune(self, *, before_day):
        cur = self.db.execute("DELETE FROM verdicts WHERE day < ?",
                              (before_day,))
        self.db.commit()
        return cur.rowcount

    def __len__(self):
        return self.db.execute("SELECT COUNT(*) FROM verdicts"
                               ).fetchone()[0]


class DbapiStore:
    """Postgres-ready verdict store on any DBAPI connection.

    The same seam SqliteStore fills in the field, filled by the state
    plane in production: pass a psycopg connection and the idempotency
    contract lives where the rest of the platform's state lives —
    replicated, backed up, and shared across every gate process behind
    the load balancer. Paramstyle is declared ("qmark" for sqlite3 in
    tests, "format" for psycopg in production) so the SQL is honest in
    both worlds."""

    durable = True

    DDL = ("CREATE TABLE IF NOT EXISTS syncmesh_verdicts ("
           "key TEXT PRIMARY KEY, day INTEGER, body TEXT NOT NULL)")

    def __init__(self, conn, *, paramstyle="format"):
        if paramstyle not in ("qmark", "format"):
            raise Error(f"unknown paramstyle {paramstyle!r} — declared, "
                        "not guessed")
        self.conn = conn
        self.ph = "?" if paramstyle == "qmark" else "%s"
        self.conn.execute(self.DDL)
        self.conn.commit()

    def get(self, key):
        row = self.conn.execute(
            f"SELECT body FROM syncmesh_verdicts WHERE key={self.ph}",
            (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, verdict, *, day):
        # portable upsert: delete-then-insert inside one commit; the
        # PRIMARY KEY makes a second insert of the same key a stated
        # no-op, not a duplicate row
        ph = self.ph
        cur = self.conn.cursor()
        cur.execute(f"DELETE FROM syncmesh_verdicts WHERE key={ph}",
                    (key,))
        cur.execute(f"INSERT INTO syncmesh_verdicts (key, day, body) "
                    f"VALUES ({ph},{ph},{ph})",
                    (key, day, json.dumps(verdict, sort_keys=True,
                                          ensure_ascii=False)))
        self.conn.commit()

    def prune(self, *, before_day):
        cur = self.conn.execute(
            f"DELETE FROM syncmesh_verdicts WHERE day < {self.ph}",
            (before_day,))
        self.conn.commit()
        return cur.rowcount

    def __len__(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM syncmesh_verdicts").fetchone()[0]


class WalCoalescer:
    """Group-commit for the upload path.

    Postgres pays one WAL flush per commit; at sync-storm volume the
    fsync queue — not the SQL — is the ceiling. The coalescer buffers
    mutations and flushes them as ONE transaction when a declared
    threshold trips: count, buffered bytes, or the window closing.

    The doctrine that keeps this honest: THE FLUSH IS THE ACK. A
    mutation is acknowledged only after its buffer flushed — nothing in
    the buffer is durable, and the client knows because its verdict has
    not arrived. Window is measured on an injected clock; tests are
    deterministic."""

    def __init__(self, sink, *, max_count=500, max_bytes=1 << 20,
                 window_seconds=0.005, clock=None):
        """sink(buffered) persists one group commit and returns a list
        of per-mutation results in buffer order."""
        if max_count < 1 or max_bytes < 1 or window_seconds <= 0:
            raise Error("coalescing thresholds are declared positive "
                        "numbers")
        self.sink = sink
        self.max_count = max_count
        self.max_bytes = max_bytes
        self.window = window_seconds
        self.clock = clock or time.monotonic
        self.buffer = []
        self.buffered_bytes = 0
        self.opened_at = None
        self.flushes = 0

    def submit(self, mutation):
        """Buffer one mutation. Returns the per-mutation results if
        this submit tripped a flush, else None — the ack arrives with
        the flush, never before."""
        body = json.dumps(mutation, sort_keys=True).encode()
        if not self.buffer:
            self.opened_at = self.clock()
        self.buffer.append(mutation)
        self.buffered_bytes += len(body)
        if len(self.buffer) >= self.max_count or \
                self.buffered_bytes >= self.max_bytes:
            return self.flush()
        return None

    def tick(self):
        """The window check — call on a timer; closes a stale buffer
        so a trickle of writes is not held hostage by a quiet ward."""
        if self.buffer and \
                self.clock() - self.opened_at >= self.window:
            return self.flush()
        return None

    def flush(self):
        if not self.buffer:
            return []
        batch, self.buffer = self.buffer, []
        self.buffered_bytes = 0
        self.opened_at = None
        self.flushes += 1
        return self.sink(batch)

    def stats(self):
        return {"flushes": self.flushes,
                "buffered": len(self.buffer),
                "avg_batch": None}


class CheckpointCache:
    """Read-side fan-out for checkpoints.

    A checkpoint snapshot for a bucket changes only when that bucket
    gains an applied batch — yet thousands of devices poll it between
    writes. The cache memoizes per-bucket snapshots and invalidates on
    append; hit ratio is reported, never assumed. This is what lets
    read replicas carry the polling load while the primary carries
    only the WAL."""

    def __init__(self):
        self.snapshots = {}   # bucket -> (fingerprint, snapshot)
        self.hits = 0
        self.misses = 0

    def get(self, bucket, *, fingerprint):
        """Return the cached snapshot if the fingerprint matches, else
        None. The fingerprint is the caller's proof of freshness — the
        checkpoint head."""
        hit = self.snapshots.get(bucket)
        if hit and hit[0] == fingerprint:
            self.hits += 1
            return hit[1]
        self.misses += 1
        return None

    def put(self, bucket, *, fingerprint, snapshot):
        self.snapshots[bucket] = (fingerprint, snapshot)

    def invalidate(self, bucket):
        self.snapshots.pop(bucket, None)

    def stats(self):
        total = self.hits + self.misses
        return {"hits": self.hits, "misses": self.misses,
                "hit_ratio": round(self.hits / total, 4)
                if total else None}


class ReplicationSlotGuard:
    """Logical-replication slot discipline.

    A replication slot pins WAL: an abandoned or lagging slot fills the
    disk and takes the primary down — the classic PowerSync-on-Postgres
    outage. The guard samples lag (bytes behind the WAL head) through
    an injected reader and answers honestly: within budget, the slot
    feeds; beyond it, stated backpressure — catch-up is declared, and
    new sync sessions are refused until the slot drains."""

    def __init__(self, lag_budget_bytes, *, reader):
        """reader(slot) -> lag in bytes. Injected so tests are
        deterministic and production reads pg_stat_replication."""
        if lag_budget_bytes < 1:
            raise Error("a lag budget is a declared positive number")
        self.budget = lag_budget_bytes
        self.reader = reader
        self.refusals = []

    def admit(self, slot, *, day=None):
        """May a new sync session attach to this slot?"""
        lag = self.reader(slot)
        if lag > self.budget:
            self.refusals.append({
                "slot": slot, "lag_bytes": lag,
                "budget_bytes": self.budget, "day": day,
                "reason": "slot lag beyond budget — catch-up in "
                          "progress; new sessions wait, stated not "
                          "silent"})
            return {"admitted": False, "lag_bytes": lag,
                    "reason": self.refusals[-1]["reason"]}
        return {"admitted": True, "lag_bytes": lag}


class RateLimiter:
    """Per-principal token bucket. A syncing device has a declared
    budget; draining it is refused with a stated reason, never throttled
    silently into looking offline."""

    def __init__(self, capacity, refill_per_day):
        if capacity < 1 or refill_per_day <= 0:
            raise Error("rate budget is positive — capacity and refill "
                        "are stated numbers")
        self.capacity = capacity
        self.refill = refill_per_day
        self.buckets = {}  # subject -> [tokens, last_seen_day]

    def consume(self, subject, *, at):
        """at is a float day. Refill accrues linearly, capped at
        capacity. Returns remaining tokens."""
        tokens, last = self.buckets.get(subject,
                                        [float(self.capacity), at])
        tokens = min(float(self.capacity),
                     tokens + max(0.0, at - last) * self.refill)
        if tokens < 1.0:
            raise Error(f"rate limit — {subject!r} has drained its "
                        "sync budget; stated, not silent")
        self.buckets[subject] = [tokens - 1.0, at]
        return tokens - 1.0


class ShardRouter:
    """Deterministic facility -> shard assignment. Shard membership is
    epoch-bound: re-sharding is a stated event with a reason, never a
    silent rebalance that strands checkpoints mid-flight."""

    def __init__(self, shard_count, *, epoch=1):
        if shard_count < 1:
            raise Error("a shard map has at least one shard")
        self.shard_count = shard_count
        self.epoch = epoch
        self.epochs = [{"epoch": epoch, "shard_count": shard_count,
                        "reason": "initial map"}]

    def route(self, facility):
        digest = hashlib.sha256(facility.encode()).hexdigest()
        return int(digest[:16], 16) % self.shard_count

    def reshard(self, shard_count, *, by, day, reason):
        if shard_count < 1:
            raise Error("a shard map has at least one shard")
        if not (reason and reason.strip()):
            raise Error("re-sharding states its reason — every "
                        "in-flight checkpoint moves epoch")
        if not by:
            raise Error("re-sharding names its hand")
        self.epoch += 1
        self.shard_count = shard_count
        self.epochs.append({"epoch": self.epoch,
                            "shard_count": shard_count, "by": by,
                            "day": day, "reason": reason.strip()})
        return self.epoch


class UploadGate:
    """The write path. Every offline mutation arrives with an
    idempotency key, the base checksum it was written against, and the
    mutation itself. The gate routes it through the owning engine's
    validator — the same code that would have judged it online.

    Scale doctrine:
    - VERDICTS PERSIST: the store is injectable; SqliteStore keeps the
      idempotency contract across restarts.
    - SATURATION IS STATED: max_inflight backpressure refuses loudly
      and is NEVER stored as a verdict — a saturated retry must not
      poison the idempotency record.
    - REPORTS ARE MEMOIZED: conflict_report recomputes only after a
      state change, not on every read."""

    def __init__(self, registry, *, store=None, verdict_ttl_days=None,
                 max_inflight=None, limiter=None):
        self.registry = registry
        self.validators = {}   # entity_class -> callable(mutation) -> verdict dict
        # NB: never write `store or MemoryStore()` — stores define
        # __len__, and an empty store is falsy.
        self.store = store if store is not None else MemoryStore()
        self.seen = getattr(self.store, "verdicts", {})  # compat view
        self.verdict_ttl_days = verdict_ttl_days
        self.max_inflight = max_inflight
        self.inflight = 0
        self.limiter = limiter
        self.applied = []      # append-only applied log
        self.conflicts = []    # append-only conflict log
        self.store_evictions = []  # stated TTL evictions
        self._report_cache = None

    def register_validator(self, entity_class, fn):
        if entity_class not in DEFAULT_ENTITY_CLASSES:
            raise Error(f"unknown entity class {entity_class!r}")
        if entity_class in self.validators:
            raise Error(f"validator for {entity_class} already "
                        "registered")
        self.validators[entity_class] = fn

    def submit(self, mutation, *, principal, day):
        """One uploaded mutation. Required keys: idempotency_key,
        entity_class, rule_id, base_sha256, op, payload. The verdict is
        stored against the key — a retry gets the same answer."""
        if self.max_inflight is not None and \
                self.inflight >= self.max_inflight:
            # stated backpressure — and deliberately NOT stored: a
            # saturated retry must not poison the idempotency record
            raise Error("gate saturated — stated backpressure; the "
                        "client retries with its key intact")
        if self.limiter is not None:
            self.limiter.consume(principal.subject, at=float(day))
        key = mutation.get("idempotency_key")
        if not key:
            raise Error("an upload carries an idempotency key — a "
                        "retried write must be recognisable")
        hit = self.store.get(key)
        if hit is not None:
            verdict = hit
            verdict["replayed"] = True
            return verdict
        entity_class = mutation.get("entity_class")
        if entity_class not in DEFAULT_ENTITY_CLASSES:
            raise Error(f"unknown entity class {entity_class!r}")
        rule_id = mutation.get("rule_id")
        rule = self.registry.live(rule_id)  # refuses dead rules
        if rule["entity_class"] != entity_class:
            raise Error(f"rule {rule_id} governs "
                        f"{rule['entity_class']}, not {entity_class}")
        record = mutation.get("payload", {})
        if not principal.satisfies(rule["scopes"], record=record):
            verdict = {"verdict": "refused",
                       "reason": "scope — this record was never in "
                                 "your bucket; an upload cannot reach "
                                 "data the server never granted",
                       "idempotency_key": key}
            self.store.put(key, verdict, day=day)
            return verdict
        validator = self.validators.get(entity_class)
        if validator is None:
            raise Error(f"no validator registered for {entity_class} "
                        "— refusing to judge what we cannot judge")
        self.inflight += 1
        try:
            verdict = validator(mutation, day=day)
        finally:
            self.inflight -= 1
        verdict = {"idempotency_key": key, **verdict}
        if verdict.get("verdict") == "conflict":
            verdict.setdefault("policy",
                               DEFAULT_ENTITY_CLASSES[entity_class])
            self.conflicts.append({**verdict, "day": day,
                                   "entity_class": entity_class})
        elif verdict.get("verdict") == "applied":
            self.applied.append({**verdict, "day": day,
                                 "entity_class": entity_class})
        self.store.put(key, {k: v for k, v in verdict.items()
                             if k != "replayed"}, day=day)
        self._report_cache = None
        return verdict

    def submit_batch(self, mutations, *, principal, day):
        """A device's queued mutations in one call — one round trip,
        one verdict per mutation, in order. Contract violations (missing
        keys, dead rules) still raise; judgement outcomes come back as
        verdicts."""
        if not mutations:
            return []
        return [self.submit(m, principal=principal, day=day)
                for m in mutations]

    def prune_verdicts(self, *, day):
        """TTL pruning of the verdict store. Eviction is stated: an
        idempotency key older than the window may be re-judged, and the
        log says exactly how many verdicts aged out."""
        if self.verdict_ttl_days is None:
            raise Error("no verdict TTL declared — pruning is a "
                        "policy, declare it first")
        dropped = self.store.prune(before_day=day - self.verdict_ttl_days)
        self.store_evictions.append(
            {"day": day, "dropped": dropped,
             "reason": f"verdicts older than {self.verdict_ttl_days}d "
                       "aged out — a retried upload this old is "
                       "re-judged, and that is the policy"})
        return dropped

    def conflict_report(self):
        if self._report_cache is None:
            self._report_cache = {
                "conflicts": len(self.conflicts),
                "by_policy": {p: sum(1 for c in self.conflicts
                                     if c.get("policy") == p)
                              for p in CONFLICT_POLICIES},
                "applied": len(self.applied),
                "sha256": _sha({"conflicts": self.conflicts,
                                "applied": self.applied}),
                "version": VERSION}
        return dict(self._report_cache)


class ConflictResolver:
    """Applies the declared policy when two writes meet. The resolver
    never merges silently — every outcome names the policy that
    produced it."""

    def resolve(self, entity_class, *, existing, incoming):
        policy = DEFAULT_ENTITY_CLASSES.get(entity_class)
        if policy is None:
            raise Error(f"unknown entity class {entity_class!r}")
        if existing.get("sha256") == incoming.get("base_sha256"):
            return {"outcome": "clean", "policy": policy}
        if policy == "append-only":
            return {"outcome": "both-kept", "policy": policy,
                    "note": "observations accrete; nothing is lost"}
        if policy == "amend-not-edit":
            return {"outcome": "amendment-required", "policy": policy,
                    "note": "the correction must cite the row it "
                            "amends — an edit is not an amendment"}
        if policy == "refuse-and-reissue":
            return {"outcome": "refused", "policy": policy,
                    "note": "the record changed under you; re-issue "
                            "against current state"}
        return {"outcome": "incoming-wins", "policy": policy,
                "note": "newer state wins; the superseded state stays "
                        "in the audit trail"}


class RetentionPolicy:
    """Bounded offline depth with stated eviction. A device holds what
    its rules say, for as long as the retention class says; everything
    evicted is logged."""

    LIMITS = {"rolling-30d": 30, "rolling-90d": 90}

    def __init__(self):
        self.eviction_log = []

    def evaluate(self, records, *, day):
        """records: [{ref, retention, last_touch_day, pinned}]. Returns
        (kept, evicted); every eviction is recorded with its rule."""
        kept, evicted = [], []
        for r in records:
            retention = r.get("retention")
            if retention not in RETENTION_CLASSES:
                raise Error(f"unknown retention {retention!r}")
            if retention == "pinned":
                kept.append(r)
                continue
            if retention == "episode":
                if r.get("episode_open", True):
                    kept.append(r)
                else:
                    evicted.append(r)
                    self.eviction_log.append({
                        "ref": r["ref"], "retention": retention,
                        "evicted_day": day,
                        "reason": "episode closed — the record lives on "
                                  "the server, not the device"})
                continue
            limit = self.LIMITS[retention]
            if day - r["last_touch_day"] > limit:
                evicted.append(r)
                self.eviction_log.append({
                    "ref": r["ref"], "retention": retention,
                    "evicted_day": day,
                    "reason": f"untouched beyond {retention}"})
            else:
                kept.append(r)
        return kept, evicted


class CheckpointLog:
    """Server-authoritative checkpoints. Each accepted batch extends a
    hash chain; a client that diverged can prove where."""

    GENESIS = "0" * 64

    def __init__(self):
        self.entries = []

    def append(self, batch_id, *, applied_keys, day):
        prev = self.entries[-1]["checkpoint_sha256"] if self.entries \
            else self.GENESIS
        body = {"batch_id": batch_id, "applied": sorted(applied_keys),
                "day": day, "prev": prev, "version": VERSION}
        entry = {**body, "checkpoint_sha256": _sha(body)}
        self.entries.append(entry)
        return entry

    def head(self):
        return self.entries[-1]["checkpoint_sha256"] if self.entries \
            else self.GENESIS

    def verify_chain(self):
        prev = self.GENESIS
        for e in self.entries:
            body = {k: e[k] for k in ("batch_id", "applied", "day",
                                      "prev")}
            body["version"] = VERSION
            if e["prev"] != prev or e["checkpoint_sha256"] != _sha(body):
                return False
            prev = e["checkpoint_sha256"]
        return True


def emit_powersync_yaml(registry):
    """Render live rules as PowerSync sync-rules YAML. The YAML is a
    build artifact of the ratified registry — hand-editing it is
    forking governance."""
    lines = ["# GENERATED from syncmesh registry — do not hand-edit",
             f"# generator: {VERSION}", "bucket_definitions:"]
    for rule_id, rule in sorted(registry.live().items()):
        scopes = " AND ".join(
            f"{axis} = :principal_{axis}" for axis in rule["scopes"])
        lines += [
            f"  {rule['bucket']}:",
            f"    # rule {rule_id} v{rule['rule_version']} · "
            f"{rule['entity_class']} · retention {rule['retention']}",
            "    parameters: |",
            f"      SELECT {', '.join(rule['scopes'])} FROM principals "
            "WHERE subject = request.user_id()",
            "    data:",
            f"      - SELECT * FROM {rule['entity_class'].replace('-', '_')}"
            f" WHERE {scopes}",
        ]
    return "\n".join(lines) + "\n"
