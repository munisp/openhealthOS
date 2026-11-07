"""PSP settlement-file adapters — every PSP is an entry, never a branch.

Each PSP's file format is a versioned adapter in PSP_ADAPTERS. The
adapter contract: bytes in -> list of {"payer", "amount_minor",
"currency", "ref"} out. An unregistered format refuses — a file we
cannot parse is not a settlement, it is a story.

Ships with two reference adapters (csv-v1, json-v1) and the
conformance harness; a production PSP (Paystack, Flutterwave, Stripe,
CMI, Unitel Money…) is added as an entry + a sample-file conformance
test, never as an if-branch in the reconciliation path.
"""

import csv
import io
import json

PSP_VERSION = "vhpsp@1.0.0"

REQUIRED_KEYS = ("payer", "amount_minor", "currency", "ref")


class PSPError(Exception):
    pass


def _refuse(msg):
    raise PSPError(msg)


def _validated(rows, *, format_id):
    out = []
    for i, r in enumerate(rows):
        missing = [k for k in REQUIRED_KEYS if k not in r or r[k] in (None, "")]
        if missing:
            _refuse(f"{format_id} row {i}: missing {missing} — "
                    "a settlement line without a payer, amount, currency "
                    "and ref is not a settlement line")
        if not isinstance(r["amount_minor"], int) or r["amount_minor"] < 0:
            _refuse(f"{format_id} row {i}: amount_minor must be a "
                    "non-negative integer of minor units")
        out.append({k: r[k] for k in REQUIRED_KEYS})
    return out


def _parse_csv_v1(raw, *, format_id):
    """Header: payer,amount_minor,currency,ref — amounts already minor."""
    rows = []
    for r in csv.DictReader(io.StringIO(raw.decode())):
        rows.append({"payer": r.get("payer", "").strip(),
                     "amount_minor": int(r.get("amount_minor") or -1),
                     "currency": (r.get("currency") or "").strip().upper(),
                     "ref": (r.get("ref") or "").strip()})
    return _validated(rows, format_id=format_id)


def _parse_json_v1(raw, *, format_id):
    """{"lines": [{"payer","amount_minor","currency","ref"}, ...]}"""
    doc = json.loads(raw.decode())
    lines = doc.get("lines")
    if not isinstance(lines, list):
        _refuse(f"{format_id}: no 'lines' array — the file is not the contract")
    return _validated(lines, format_id=format_id)


PSP_ADAPTERS = {
    "csv-v1": _parse_csv_v1,
    "json-v1": _parse_json_v1,
}


def register_adapter(format_id, parser):
    """An adapter is registered code with a conformance-tested contract —
    never a name on a slide."""
    if not format_id or not callable(parser):
        _refuse("an adapter is an id and a parser")
    PSP_ADAPTERS[format_id] = parser
    return format_id


def parse_settlement_file(raw, *, format_id):
    adapter = PSP_ADAPTERS.get(format_id)
    if adapter is None:
        _refuse(f"no adapter for {format_id!r} — a file we cannot parse "
                "is not a settlement")
    return adapter(raw, format_id=format_id)


def conformance(format_id, sample_raw, expected):
    """Run an adapter against its pinned sample and demand exact output."""
    got = parse_settlement_file(sample_raw, format_id=format_id)
    if got != expected:
        _refuse(f"{format_id} conformance FAILED — the adapter drifted "
                "from its pinned sample")
    return True


def report():
    return {"version": PSP_VERSION,
            "adapters": sorted(PSP_ADAPTERS)}
