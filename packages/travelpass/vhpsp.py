"""PSP settlement-file adapters — every PSP is an entry, never a branch.

Each PSP's file format is a versioned adapter in PSP_ADAPTERS. The
adapter contract: bytes in -> list of {"payer", "amount_minor",
"currency", "ref"} out. An unregistered format refuses — a file we
cannot parse is not a settlement, it is a story.

1.1.0 — format governance + real provider formats:

  - Every adapter carries status "template" or "ratified". A template
    format REFUSES to parse: the column layout of a provider's export
    is pinned to that provider's published report shape, but until two
    named approvers have ratified the adapter against a REAL export
    from the provider, it is a placeholder, not a settlement format.
    Ratification is four-eyes: two distinct named approvers.
  - Real provider entries ship as TEMPLATES with their source named:
    paystack-settlement-csv-v1, flutterwave-settlement-csv-v1,
    stripe-balance-csv-v1, mpesa-statement-csv-v1. When a provider
    changes its export, a NEW versioned entry is added — a format is
    never edited under a settlement desk's feet.
  - Amounts arrive in whatever unit the provider publishes; adapters
    convert to minor units via vhmarkets' ISO-4217 scale table. An
    unknown currency refuses — a guessed exponent is a wrong amount.
  - csv-v1 / json-v1 remain the platform's own ratified reference
    contract.
"""

import csv
import io
import json

import vhmarkets

PSP_VERSION = "vhpsp@1.1.0"

REQUIRED_KEYS = ("payer", "amount_minor", "currency", "ref")

STATUSES = ("template", "ratified")


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


def _to_minor(amount_text, currency, *, format_id, row):
    """Provider amounts are major units with decimals; the contract is
    minor units. The scale comes from the ISO table, never from a
    guess."""
    cur = (currency or "").strip().upper()
    entry = vhmarkets.CURRENCIES.get(cur)
    if entry is None:
        _refuse(f"{format_id} row {row}: currency {cur!r} is not in the "
                "ISO table — a guessed exponent is a wrong amount")
    text = str(amount_text).replace(",", "").strip()
    try:
        major = float(text)
    except ValueError:
        _refuse(f"{format_id} row {row}: amount {text!r} is not a number")
    if major < 0:
        _refuse(f"{format_id} row {row}: negative settlement line "
                "— reversals are a separate report, not a minus sign")
    return int(round(major * entry["scale"]))


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


# ------------------------------------------------ real provider formats
# TEMPLATE entries: column layouts pinned to the provider's published
# export shape (source named per entry). Each refuses to parse until
# four-eyes ratified against a real export at deployment.

def _parse_paystack_csv_v1(raw, *, format_id):
    """Paystack settlement/transaction export (paystack.com dashboard
    export shape): Reference, Amount (kobo — already minor), Currency,
    Customer Email, Status, Paid At."""
    rows = []
    for i, r in enumerate(csv.DictReader(io.StringIO(raw.decode()))):
        if (r.get("Status") or "").strip().lower() != "success":
            continue  # only successful transactions settle
        rows.append({
            "payer": (r.get("Customer Email") or "").strip(),
            "amount_minor": int(float((r.get("Amount") or "-1")
                                      .replace(",", ""))),
            "currency": (r.get("Currency") or "").strip().upper(),
            "ref": (r.get("Reference") or "").strip()})
    return _validated(rows, format_id=format_id)


def _parse_flutterwave_csv_v1(raw, *, format_id):
    """Flutterwave settlement export (dashboard shape): Transaction
    Reference, Amount (major units), Currency, Customer Email, Status."""
    rows = []
    for i, r in enumerate(csv.DictReader(io.StringIO(raw.decode()))):
        if (r.get("Status") or "").strip().lower() != "successful":
            continue
        cur = (r.get("Currency") or "").strip().upper()
        rows.append({
            "payer": (r.get("Customer Email") or "").strip(),
            "amount_minor": _to_minor(r.get("Amount"), cur,
                                      format_id=format_id, row=i),
            "currency": cur,
            "ref": (r.get("Transaction Reference") or "").strip()})
    return _validated(rows, format_id=format_id)


def _parse_stripe_balance_csv_v1(raw, *, format_id):
    """Stripe balance-transaction export: id, Type, Amount (major,
    signed), Currency, Source, Description. Only 'charge' rows settle
    to a payer; fees/payouts are not settlement lines."""
    rows = []
    for i, r in enumerate(csv.DictReader(io.StringIO(raw.decode()))):
        if (r.get("Type") or "").strip().lower() != "charge":
            continue
        cur = (r.get("Currency") or "").strip().upper()
        rows.append({
            "payer": (r.get("Source") or "").strip(),
            "amount_minor": _to_minor(r.get("Amount"), cur,
                                      format_id=format_id, row=i),
            "currency": cur,
            "ref": (r.get("id") or "").strip()})
    return _validated(rows, format_id=format_id)


def _parse_mpesa_statement_csv_v1(raw, *, format_id):
    """M-Pesa (Safaricom) statement export: Receipt No, Details,
    Transaction Status, Paid In (KES major). Currency is KES by the
    rail's own definition — stated, not assumed silently."""
    rows = []
    for i, r in enumerate(csv.DictReader(io.StringIO(raw.decode()))):
        status = (r.get("Transaction Status") or "").strip().lower()
        if status and status != "completed":
            continue
        paid_in = (r.get("Paid In") or "").strip()
        if not paid_in:  # a withdrawal row is not a settlement line
            continue
        rows.append({
            "payer": (r.get("Details") or "").strip(),
            "amount_minor": _to_minor(paid_in, "KES",
                                      format_id=format_id, row=i),
            "currency": "KES",
            "ref": (r.get("Receipt No") or "").strip()})
    return _validated(rows, format_id=format_id)


# ------------------------------------------------------- the registry

class _Adapter:
    def __init__(self, parser, *, status, source):
        if status not in STATUSES:
            _refuse(f"adapter status must be one of {STATUSES}")
        self.parser = parser
        self.status = status
        self.source = source
        self.ratified_by = []


PSP_ADAPTERS = {}


def _install(format_id, parser, *, status, source):
    PSP_ADAPTERS[format_id] = _Adapter(parser, status=status,
                                       source=source)


_install("csv-v1", _parse_csv_v1, status="ratified",
         source="platform reference contract (amounts already minor)")
_install("json-v1", _parse_json_v1, status="ratified",
         source="platform reference contract")
_install("paystack-settlement-csv-v1", _parse_paystack_csv_v1,
         status="template",
         source="Paystack dashboard settlement/transaction export shape")
_install("flutterwave-settlement-csv-v1", _parse_flutterwave_csv_v1,
         status="template",
         source="Flutterwave dashboard settlement export shape")
_install("stripe-balance-csv-v1", _parse_stripe_balance_csv_v1,
         status="template",
         source="Stripe balance-transaction report export shape")
_install("mpesa-statement-csv-v1", _parse_mpesa_statement_csv_v1,
         status="template",
         source="Safaricom M-Pesa statement export shape")


def register_adapter(format_id, parser, *, source="", status="template"):
    """An adapter is registered code with a conformance-tested contract —
    never a name on a slide. New formats enter as templates."""
    if not format_id or not callable(parser):
        _refuse("an adapter is an id and a parser")
    if status == "ratified":
        _refuse("adapters enter as templates — ratification is a "
                "separate, four-eyes act")
    if format_id in PSP_ADAPTERS:
        _refuse(f"{format_id} is already registered — a format is never "
                "edited under a settlement desk's feet; add a new "
                "versioned entry")
    if not source:
        _refuse("an adapter without a named source is a rumor, not a "
                "format")
    _install(format_id, parser, status=status, source=source)
    return format_id


def ratify_adapter(format_id, *, approver, second):
    """Four-eyes: two distinct named approvers who have compared the
    adapter against a REAL provider export. A template settlement
    format is a placeholder, not a settlement."""
    entry = PSP_ADAPTERS.get(format_id)
    if entry is None:
        _refuse(f"no adapter {format_id!r} to ratify")
    if not approver or not second or approver == second:
        _refuse("ratification needs two distinct named approvers — "
                "the hand that proposes never approves")
    entry.ratified_by = [approver, second]
    entry.status = "ratified"
    return format_id


def parse_settlement_file(raw, *, format_id):
    entry = PSP_ADAPTERS.get(format_id)
    if entry is None:
        _refuse(f"no adapter for {format_id!r} — a file we cannot parse "
                "is not a settlement")
    if entry.status != "ratified":
        _refuse(f"{format_id} is a template — pinned to "
                f"{entry.source or 'an unnamed source'} but not yet "
                "ratified against a real provider export. Ratify with "
                "two named approvers first.")
    return entry.parser(raw, format_id=format_id)


def conformance(format_id, sample_raw, expected):
    """Run an adapter against its pinned sample and demand exact output.
    Conformance parses through the same gate — a template fails
    conformance by refusing, which is itself the honest result."""
    got = parse_settlement_file(sample_raw, format_id=format_id)
    if got != expected:
        _refuse(f"{format_id} conformance FAILED — the adapter drifted "
                "from its pinned sample")
    return True


def report():
    return {"version": PSP_VERSION,
            "adapters": {fid: {"status": a.status, "source": a.source,
                               "ratified_by": list(a.ratified_by)}
                         for fid, a in sorted(PSP_ADAPTERS.items())}}
