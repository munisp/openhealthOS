"""demo_sync.py — deterministic offline-sync demo seeder.

One ward morning, two countries: a nurse's phone held three mutations
overnight. One applies clean, one is refused by the real eMAR engine
(wrong medication barcode), one meets a flowsheet conflict. A retry of
the first is answered from the stored verdict — replayed, never
double-applied. The checkpoint chain advances and verifies; local
retention evicts what the server now holds, with stated reasons.

Same seed, same payload, byte for byte. The checksum is the contract.
"""

import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _pkg in ("syncmesh", "emar", "localepack"):
    sys.path.insert(0, os.path.join(_HERE, "..", _pkg))

from syncmesh import (CheckpointLog, RetentionPolicy, SyncRuleRegistry,  # noqa: E402
                      UploadGate, Principal, VERSION as SYNCMESH_VERSION)
from emar import MAR  # noqa: E402
from localepack import get_locale  # noqa: E402

VERSION = "syncdemo@1.0.0"


def _sha(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _names(locale):
    pools = locale["name_pools"]
    given = pools.get("given", ["Ada"])
    family = pools.get("family", ["Okafor"])
    return f"{given[0]} {family[0]}"


def _emar_validator(mar):
    def validate(mutation, *, day):
        slot = mar.slots[mutation["payload"]["slot_id"]]
        try:
            slot.record(mutation["payload"]["state"],
                        by=mutation["payload"]["by"],
                        minute=mutation["payload"]["minute"],
                        patient_barcode=mutation["payload"].get(
                            "patient_barcode"),
                        medication_barcode=mutation["payload"].get(
                            "medication_barcode"))
            return {"verdict": "applied"}
        except Exception as exc:
            return {"verdict": "refused", "reason": str(exc)}
    return validate


def seed_sync(locale_code, seed=42):
    """Seed one sync morning for a locale. Unknown locales refuse in
    localepack; this function never invents a country."""
    locale = get_locale(locale_code)
    fac_main = locale["facilities"][0]["id"]
    nurse = _names(locale)
    drug = f'{locale["formulary_prefix"]}-AMOX500'

    reg = SyncRuleRegistry()
    reg.propose("r-ward-emar", {"entity_class": "emar-administration",
                                "bucket": "ward_emar",
                                "scopes": ["facility", "role"],
                                "retention": "episode",
                                "rule_version": 1}, by="cmio", day=1)
    reg.propose("r-ward-flowsheet", {"entity_class": "flowsheet-row",
                                     "bucket": "ward_flowsheets",
                                     "scopes": ["facility"],
                                     "retention": "rolling-30d",
                                     "rule_version": 1}, by="cmio", day=1)
    reg.ratify("r-ward-emar", by="cnio", day=1)
    reg.ratify("r-ward-flowsheet", by="cnio", day=1)

    mar = MAR("mar-1", "p-1", "adm-1", "wristband-p1")
    mar.add_order({"order_id": "ord-1", "patient_ref": "p-1",
                   "state": "dispensed", "drug_code": drug,
                   "dose": "500mg", "frequency": "tds",
                   "medication_barcode": "med-amox"}, day=1)

    gate = UploadGate(reg)
    gate.register_validator("emar-administration", _emar_validator(mar))
    gate.register_validator(
        "flowsheet-row",
        lambda m, day: {"verdict": "conflict",
                        "reason": "row changed under you — amend, "
                                  "never overwrite"})

    principal = Principal(f"nurse-{nurse.split()[0].lower()}",
                          facility=fac_main, roles={"nurse"})

    def emar_mutation(key, slot_id, minute, med_barcode):
        return {"idempotency_key": key,
                "entity_class": "emar-administration",
                "rule_id": "r-ward-emar", "base_sha256": "0" * 64,
                "op": "record",
                "payload": {"slot_id": slot_id, "state": "given",
                            "by": nurse, "minute": minute,
                            "patient_barcode": "wristband-p1",
                            "medication_barcode": med_barcode,
                            "facility": fac_main,
                            "role_required": "nurse"}}

    verdicts = []
    # 1. clean apply — 08:00 dose, correct barcodes
    v = gate.submit(emar_mutation("up-1", "mar-1-S1", 480, "med-amox"),
                    principal=principal, day=1)
    verdicts.append({"key": "up-1", "kind": "emar-administration",
                     **{k: v.get(k) for k in ("verdict", "reason")}})
    # 2. refused by the real engine — wrong medication barcode
    v = gate.submit(emar_mutation("up-2", "mar-1-S2", 840, "med-wrong"),
                    principal=principal, day=1)
    verdicts.append({"key": "up-2", "kind": "emar-administration",
                     **{k: v.get(k) for k in ("verdict", "reason")}})
    # 3. flowsheet conflict — amend-not-edit
    v = gate.submit({"idempotency_key": "up-3",
                     "entity_class": "flowsheet-row",
                     "rule_id": "r-ward-flowsheet",
                     "base_sha256": "0" * 64, "op": "record",
                     "payload": {"facility": fac_main,
                                 "row": "vitals-08:00"}},
                    principal=principal, day=1)
    verdicts.append({"key": "up-3", "kind": "flowsheet-row",
                     "policy": v.get("policy"),
                     **{k: v.get(k) for k in ("verdict", "reason")}})
    # 4. the retry — answered from the stored verdict, never re-applied
    v = gate.submit(emar_mutation("up-1", "mar-1-S1", 480, "med-amox"),
                    principal=principal, day=1)
    verdicts.append({"key": "up-1", "kind": "emar-administration",
                     "replayed": bool(v.get("replayed")),
                     **{k: v.get(k) for k in ("verdict", "reason")}})

    log = CheckpointLog()
    log.append("batch-1", applied_keys=["up-1"], day=1)
    log.append("batch-2", applied_keys=[], day=1)
    chain_ok = log.verify_chain()

    rp = RetentionPolicy()
    kept, evicted = rp.evaluate([
        {"ref": "mar-1-S1", "retention": "episode", "episode_open": True},
        {"ref": "obs-77", "retention": "rolling-30d",
         "last_touch_day": 60},
        {"ref": "obs-12", "retention": "rolling-30d",
         "last_touch_day": 1},
        {"ref": "careplan-p1", "retention": "pinned"},
    ], day=100)

    payload = {
        "locale": locale_code,
        "country": locale["country"],
        "currency": locale["currency"]["code"],
        "facility": locale["facilities"][0]["name"],
        "nurse": nurse,
        "rules": [{"rule_id": rid, "entity_class": r["entity_class"],
                   "bucket": r["bucket"], "retention": r["retention"]}
                  for rid, r in sorted(reg.live().items())],
        "verdicts": verdicts,
        "conflict_report": gate.conflict_report(),
        "checkpoint": {"head": log.head(), "batches": len(log.entries),
                       "chain_ok": chain_ok},
        "retention": {
            "kept": [r["ref"] for r in kept],
            "evicted": [{"ref": r["ref"], "reason": r["reason"]}
                        for r in rp.eviction_log]},
        "mar_state": {sid: s.state for sid, s in sorted(mar.slots.items())},
        "syncmesh": SYNCMESH_VERSION,
        "version": VERSION, "seed": seed,
    }
    payload["sha256"] = _sha({k: v for k, v in payload.items()
                              if k != "sha256"})
    return payload


def export_all(out_dir):
    for code in ("ng", "cv"):
        payload = seed_sync(code)
        path = os.path.join(out_dir, f"sync.{code}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
    return True


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        _HERE, "..", "..", "apps", "pwa")
    export_all(out)
    print(f"sync payloads written to {out}")
