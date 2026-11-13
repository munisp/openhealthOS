"""storageport — the platform storage port (ADR-0089).

Apache OpenDAL is the MAIN storage engine for object storage on this
platform — documents, imaging, media attachments, archives. One port,
one Operator API, backends selected from the registry as DATA (the
country/company-agnostic doctrine: a deployment target is a registry
entry, never a code branch).

Main means main: when the opendal wheel is importable, every scheme —
including plain fs and memory — runs through opendal. The stdlib
fallback exists only as DECLARED degraded mode (fail-closed doctrine:
degraded service with the mode stamped on every receipt beats a dead
service); it serves fs and memory only, and refuses cloud schemes.
The suite pins ADAPTER == "opendal": if the main engine is absent the
gate goes red and names it.

What this port does NOT own: local payload atomicity. That remains
payloadio's contract (ADR-0088). Object stores are atomic on PUT;
payloadio owns tmp+rename for local JSON payloads. Two layers,
two contracts, stated in both directions.

Refusals are stated, never silent:
  - unknown registry entry            -> StorageRefusal (names the registry)
  - missing required config (creds)   -> StorageRefusal (names the keys,
                                         BEFORE any network is touched)
  - capability the scheme lacks       -> StorageRefusal (names the capability)
  - capability the binding lacks      -> StorageRefusal (presign: core has
                                         it, the Python binding does not
                                         expose presign_* yet — stated,
                                         never faked)
  - absent object                     -> AbsentKey (absence is not a value)
"""

from __future__ import annotations

import hashlib
import os

try:
    import opendal
    from opendal.layers import RetryLayer
    _IMPORT_ERROR = None
except Exception as exc:  # degraded mode — declared, never silent
    opendal = None
    RetryLayer = None
    _IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)

__version__ = "1.0.0"

ADAPTER_MAIN = "opendal"
ADAPTER_DEGRADED = "stdlib-fs"

#: Which engine the port is running on RIGHT NOW. Main means main —
#: the suite pins this to ADAPTER_MAIN.
ADAPTER = ADAPTER_MAIN if opendal is not None else ADAPTER_DEGRADED
DEGRADED = ADAPTER != ADAPTER_MAIN
DEGRADED_REASON = _IMPORT_ERROR

OPENDAL_VERSION = getattr(opendal, "__version__", None)

#: Retry is a layer, and the policy is data.
RETRY_TIMES = 3

#: Schemes the port knows how to serve in each adapter mode.
SCHEMES_OPENDAL = ("fs", "memory", "s3")
SCHEMES_DEGRADED = ("fs", "memory")

#: Capability policy as data. presign exists in the opendal core for
#: s3 but the Python binding exposes no presign_* methods — stated.
CAPABILITY_POLICY = {
    "presign": {"fs": False, "memory": False, "s3": True},
}

#: The shipped registry. Deployment targets are DATA. Credentials are
#: NEVER values in this registry — they arrive via `secrets=` at
#: connect time (environment, vault); a `requires` key without a
#: secret is a stated refusal, not a network attempt.
REGISTRY = {
    "ministry-docs": {
        "scheme": "fs",
        "config": {"root": "/var/openhealth/ministry-docs"},
        "purpose": "document vault — imaging, lab PDFs, discharge "
                   "summaries, insurance documents",
    },
    "clinic-cache": {
        "scheme": "memory",
        "config": {},
        "purpose": "ephemeral low-bandwidth cache — process-local, "
                   "lost on restart, never a store of record",
    },
    "cloud-archive": {
        "scheme": "s3",
        "config": {"bucket": "openhealth-archive",
                   "region": "eu-west-1"},
        "requires": ["access_key_id", "secret_access_key"],
        "purpose": "long-term archive / disaster recovery",
    },
}


class StorageError(Exception):
    """Base for every storageport failure."""


class StorageRefusal(StorageError):
    """A stated refusal: the port declines, with the reason named."""


class AbsentKey(StorageError):
    """The object is not there. Absence is not a value."""


def _check_key(key: str) -> None:
    if not key or key.startswith("/"):
        raise StorageRefusal(
            "object keys are relative and non-empty, got %r" % (key,))
    if ".." in key.split("/"):
        raise StorageRefusal(
            "object keys never climb out of the root: %r" % (key,))


class _OpendalBackend:
    """The main engine: every scheme through opendal, retry layered."""

    def __init__(self, scheme: str, config: dict):
        try:
            op = opendal.Operator(scheme, **config)
        except Exception as exc:
            raise StorageRefusal(
                "opendal refused scheme %r with this config: %s: %s"
                % (scheme, type(exc).__name__, exc))
        self.op = op.layer(RetryLayer(max_times=RETRY_TIMES))
        self.scheme = scheme

    def write(self, key: str, data: bytes) -> None:
        self.op.write(key, data)

    def read(self, key: str, offset=None, size=None) -> bytes:
        kwargs = {}
        if offset is not None:
            kwargs["offset"] = offset
        if size is not None:
            kwargs["size"] = size
        try:
            return bytes(self.op.read(key, **kwargs))
        except opendal.exceptions.NotFound:
            raise AbsentKey("no object at %r — absence is not a value"
                            % (key,))

    def stat(self, key: str) -> dict:
        try:
            meta = self.op.stat(key)
        except opendal.exceptions.NotFound:
            raise AbsentKey("no object at %r — absence is not a value"
                            % (key,))
        return {"content_length": meta.content_length,
                "etag": meta.etag,
                "last_modified": str(meta.last_modified)
                if meta.last_modified else None}

    def exists(self, key: str) -> bool:
        return bool(self.op.exists(key))

    def list(self, prefix: str = "") -> list:
        return sorted(e.path for e in self.op.list(prefix or "/")
                      if not e.path.endswith("/"))

    def delete(self, key: str) -> None:
        self.op.delete(key)  # idempotent by contract

    def capability(self) -> dict:
        cap = self.op.capability()
        return {"read": bool(cap.read), "write": bool(cap.write),
                "stat": bool(cap.stat), "delete": bool(cap.delete),
                "list": bool(cap.list), "presign": bool(cap.presign)}


class _StdlibFSBackend:
    """Degraded fs service — declared degraded mode only."""

    def __init__(self, scheme: str, config: dict):
        self.scheme = scheme
        self.root = config.get("root") or ""
        if not self.root:
            raise StorageRefusal(
                "degraded fs service needs a root in config")
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.root, key)

    def write(self, key: str, data: bytes) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)

    def read(self, key: str, offset=None, size=None) -> bytes:
        path = self._path(key)
        if not os.path.isfile(path):
            raise AbsentKey("no object at %r — absence is not a value"
                            % (key,))
        with open(path, "rb") as fh:
            if offset is not None:
                fh.seek(offset)
            return fh.read() if size is None else fh.read(size)

    def stat(self, key: str) -> dict:
        path = self._path(key)
        if not os.path.isfile(path):
            raise AbsentKey("no object at %r — absence is not a value"
                            % (key,))
        return {"content_length": os.path.getsize(path),
                "etag": None, "last_modified": None}

    def exists(self, key: str) -> bool:
        return os.path.isfile(self._path(key))

    def list(self, prefix: str = "") -> list:
        found = []
        base = self._path(prefix) if prefix else self.root
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                found.append(os.path.relpath(
                    os.path.join(dirpath, name), self.root))
        return sorted(found)

    def delete(self, key: str) -> None:
        try:
            os.unlink(self._path(key))
        except FileNotFoundError:
            pass  # idempotent by contract

    def capability(self) -> dict:
        return {"read": True, "write": True, "stat": True,
                "delete": True, "list": True, "presign": False}


class _StdlibMemBackend:
    """Degraded memory service — declared degraded mode only."""

    def __init__(self, scheme: str, config: dict):
        self.scheme = scheme
        self._store = {}

    def write(self, key: str, data: bytes) -> None:
        self._store[key] = bytes(data)

    def read(self, key: str, offset=None, size=None) -> bytes:
        if key not in self._store:
            raise AbsentKey("no object at %r — absence is not a value"
                            % (key,))
        data = self._store[key]
        start = offset or 0
        return data[start:] if size is None else data[start:start + size]

    def stat(self, key: str) -> dict:
        if key not in self._store:
            raise AbsentKey("no object at %r — absence is not a value"
                            % (key,))
        return {"content_length": len(self._store[key]),
                "etag": None, "last_modified": None}

    def exists(self, key: str) -> bool:
        return key in self._store

    def list(self, prefix: str = "") -> list:
        return sorted(k for k in self._store if k.startswith(prefix))

    def delete(self, key: str) -> None:
        self._store.pop(key, None)  # idempotent by contract

    def capability(self) -> dict:
        return {"read": True, "write": True, "stat": True,
                "delete": True, "list": True, "presign": False}


class Store:
    """A connected storage entry. Every receipt carries the adapter —
    degraded mode is stamped, never silent."""

    def __init__(self, name: str, entry: dict, backend, adapter: str):
        self.name = name
        self.scheme = entry["scheme"]
        self.adapter = adapter
        self.degraded = adapter != ADAPTER_MAIN
        self._backend = backend

    def put(self, key: str, data: bytes) -> dict:
        """Store bytes; the receipt carries the checksum — the
        checksum is the contract."""
        _check_key(key)
        self._backend.write(key, data)
        return {"key": key, "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "adapter": self.adapter, "scheme": self.scheme}

    def get(self, key: str) -> bytes:
        _check_key(key)
        return self._backend.read(key)

    def get_range(self, key: str, offset: int, size: int) -> bytes:
        """Range read: an interrupted transfer resumes at the offset,
        it does not restart from byte zero — the low-bandwidth
        contract."""
        _check_key(key)
        if offset < 0 or size <= 0:
            raise StorageRefusal(
                "range reads need offset >= 0 and size > 0")
        return self._backend.read(key, offset=offset, size=size)

    def stat(self, key: str) -> dict:
        _check_key(key)
        return self._backend.stat(key)

    def exists(self, key: str) -> bool:
        _check_key(key)
        return self._backend.exists(key)

    def list(self, prefix: str = "") -> list:
        return self._backend.list(prefix)

    def delete(self, key: str) -> None:
        _check_key(key)
        self._backend.delete(key)

    def checksum(self, key: str) -> str:
        return hashlib.sha256(self.get(key)).hexdigest()

    def capability(self) -> dict:
        return self._backend.capability()

    def presign_read(self, key: str, expire_seconds: int) -> str:
        """Presigned URLs: stated, never faked. The scheme policy is
        data; the binding limitation is stated by name."""
        _check_key(key)
        if not CAPABILITY_POLICY["presign"].get(self.scheme, False):
            raise StorageRefusal(
                "scheme %r declares no presign capability — the "
                "capability policy is data, not a hope" % (self.scheme,))
        raise StorageRefusal(
            "opendal core supports presign on %r but the Python "
            "binding (%s) exposes no presign_* methods — stated "
            "limitation, tracked, never faked"
            % (self.scheme, OPENDAL_VERSION or "unknown"))


def connect(name: str, registry: dict = None, secrets: dict = None,
            force_degraded: bool = False) -> Store:
    """Connect a registry entry. Fail-closed: unknown entries, missing
    required secrets, and unservable schemes are stated refusals —
    the cloud is never touched on a hope."""
    registry = REGISTRY if registry is None else registry
    secrets = {} if secrets is None else secrets
    if name not in registry:
        raise StorageRefusal(
            "unknown storage entry %r — the registry holds: %s"
            % (name, ", ".join(sorted(registry))))
    entry = registry[name]
    scheme = entry.get("scheme")
    config = dict(entry.get("config", {}))
    config.update({k: v for k, v in secrets.items()})
    missing = [k for k in entry.get("requires", []) if not config.get(k)]
    if missing:
        raise StorageRefusal(
            "entry %r requires config keys %s — absent, so this is a "
            "stated refusal, not a network attempt"
            % (name, ", ".join(missing)))

    use_degraded = force_degraded or DEGRADED
    if not use_degraded:
        if scheme not in SCHEMES_OPENDAL:
            raise StorageRefusal(
                "scheme %r is not served by this port — served: %s"
                % (scheme, ", ".join(SCHEMES_OPENDAL)))
        return Store(name, entry, _OpendalBackend(scheme, config),
                     ADAPTER_MAIN)

    # Declared degraded mode: fs and memory only, and the receipts say so.
    if scheme not in SCHEMES_DEGRADED:
        raise StorageRefusal(
            "degraded mode (opendal not active%s) serves %s only — "
            "entry %r is scheme %r: stated refusal, not a silent "
            "fallback to the cloud"
            % ("" if force_degraded else ": " + str(DEGRADED_REASON),
               ", ".join(SCHEMES_DEGRADED), name, scheme))
    backend = (_StdlibFSBackend if scheme == "fs"
               else _StdlibMemBackend)(scheme, config)
    return Store(name, entry, backend, ADAPTER_DEGRADED)


def census() -> dict:
    """The live state of the port — read, never assumed."""
    return {
        "adapter_main": ADAPTER_MAIN,
        "adapter_active": ADAPTER,
        "degraded": DEGRADED,
        "degraded_reason": DEGRADED_REASON,
        "opendal_version": OPENDAL_VERSION,
        "retry_times": RETRY_TIMES,
        "registry_entries": len(REGISTRY),
        "schemes": sorted({e["scheme"] for e in REGISTRY.values()}),
        "entries_requiring_secrets": sorted(
            n for n, e in REGISTRY.items() if e.get("requires")),
    }
