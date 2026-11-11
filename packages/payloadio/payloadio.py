"""payloadio@1.0.0 — atomic writes for stamped evidence payloads.

ADR-0088. The wave-19 flake (test_self_exclusion_is_declared_data: one
failure while the tree held a stale geointproof, unreproduced since) is
EXPLAINED: scan_stamped never raises — an unparseable file is skipped —
so an exporter rewriting proofindex.json CONCURRENTLY with the suite
handed the coverage scan a torn read, the exemption vanished from the
scan for that one run, and the assertion failed, then self-healed. The
stale geointproof was the tree state, not the cause. Measured on this
estate's harness (256KB payload, 4KB chunks, yield per chunk, 2.0s
window): a raw chunked writer under concurrent scanning is torn on
12,579 of 12,759 scans (98.6%); the same harness through write_text is
torn on ZERO of 6,953 — os.replace is atomic on a same-filesystem
rename (POSIX), so a reader sees the old payload or the new one, never
a torn one.

Contract:
  - write_text(path, text) is the ONLY way a registered payload lands.
    Bytes go to a tmp file in the SAME directory as the target (the
    rename guarantee does not cross filesystems), then os.replace
    publishes.
  - A crash mid-write leaves the last-good payload and a tmp residue
    whose name NEVER ends in .json — scan_stamped cannot mistake
    residue for a payload.
  - The write is LOUD: a missing parent directory, a read-only
    filesystem, a failed replace — all raise. Fail-closed beats
    fail-silent at the evidence boundary.
  - payloadio owns ATOMICITY, not serialization. Each exporter keeps
    its own json.dumps arguments; the two stamp contracts (compact /
    spaced-utf8) are the proofindex registry's data, not this seam's.

Stdlib-only (ADR-0005). No clocks, no randomness, no retries.
"""

from __future__ import annotations

import os
import tempfile

__version__ = "1.0.0"

#: Tmp residue suffix. Deliberately NOT ".json": scan_stamped walks for
#: *.json, and residue from a crashed writer must never parse as a
#: payload — it is litter to sweep, not evidence to read.
TMP_SUFFIX = ".tmp-payloadio"

#: Exporters that do not write their payload themselves but delegate to
#: a library function that does. Declared data, with the delegate named
#: — the adoption gate and the atomproof census both follow the
#: delegation and audit the delegate.
DELEGATES = {
    "packages/ips/demo_portal.py": "packages/ips/portal.py",
}

#: Raw write-opens that remain in writer modules ON PURPOSE, each with
#: its stated reason. An exception that is not declared here is not an
#: exception; it is an adoption gap, and the adoption gate fails on it.
DECLARED_RAW_EXCEPTIONS = [
    {"module": "packages/ledgeranchor/demo_ledgeranchor.py",
     "site": "local_anchors (inside a TemporaryDirectory)",
     "sites": 1,
     "reason": "drill-internal fixture state in a private tmpdir — "
               "never a registered payload, never scanned, never "
               "served; the drill is rehearsing an attacker rewriting "
               "the anchors file, so the raw write IS the drill"},
    {"module": "packages/payloadio/demo_payloadio.py",
     "site": "drill fixtures (torn-read window, crash residue, "
             "in-flight tmp)",
     "sites": 3,
     "reason": "the drills ARE the raw writer — the retired fate "
               "replayed on private tmpdirs; proving the window "
               "requires opening it, and proving the crash requires "
               "abandoning a half-written tmp"},
]


def write_text(path: str, text: str) -> str:
    """Atomically publish `text` as the new bytes of `path`.

    Writes the full text to a tmp sibling in the target's own
    directory, fsyncs, then os.replace()s onto the target. Until the
    replace, readers of `path` see the last-good bytes; after it, the
    new bytes. There is no instant in which `path` holds half a
    payload. Raises on any failure (and sweeps the tmp residue it can
    reach) — a payload that could not be published is an error, never
    a quiet half-file.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".payloadio-",
                               suffix=TMP_SUFFIX)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
