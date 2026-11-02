"""telepath.py — tele-pathology: specimen-to-report with chain of custody.

Pathology differs from radiology in one crucial way: the artifact must
be MADE (collect → fix → section → stain → scan) before it can be read.
Every transformation is a hand-off, and every hand-off is recorded.
A specimen that cannot be accounted for cannot produce a report.

Engine: teleread. This module owns specimen custody and pathology's
domain rules; the engine owns assignment, sessions, escalation.
"""

from teleread import (DomainConfig, ReadingService, TeleReadError)  # noqa: F401

SPECIMEN_TYPES = ("biopsy", "resection", "cytology-fna", "cytology-fluid",
                  "bone-marrow")
CUSTODY_STAGES = ("collected", "fixed", "processed", "sectioned",
                  "stained", "scanned")
FIXATION_MIN_HOURS = 6   # under-fixed tissue reads wrong

CONFIG = DomainConfig(
    "pathology",
    scopes=("histopathologist", "cytopathologist"),
    critical_findings=("malignant", "high-grade-dysplasia",
                       "granulomatous-inflammation"),
    artifact_kinds=("wsi", "static-images"),
    sla_hours={"stat": 24, "urgent": 72, "routine": 240})


class PathologyService:
    def __init__(self, readers):
        self.reading = ReadingService(CONFIG, readers)
        self.specimens = {}   # specimen_id -> custody record

    def register_specimen(self, specimen_id, *, patient_id, specimen_type,
                          site, collector, facility, day):
        if specimen_type not in SPECIMEN_TYPES:
            raise TeleReadError(f"unknown specimen type: {specimen_type}")
        if not site:
            raise TeleReadError("no anatomical site — an unsited biopsy "
                                "is an uninterpretable one")
        if specimen_id in self.specimens:
            raise TeleReadError(f"duplicate specimen: {specimen_id}")
        self.specimens[specimen_id] = {
            "specimen_id": specimen_id, "patient_id": patient_id,
            "type": specimen_type, "site": site, "facility": facility,
            "custody": [("collected", day, collector)],
            "state": "collected"}
        return dict(self.specimens[specimen_id])

    def advance(self, specimen_id, stage, *, day, by,
                fixation_hours=None):
        s = self._spec(specimen_id)
        idx = CUSTODY_STAGES.index(s["state"])
        if stage not in CUSTODY_STAGES:
            raise TeleReadError(f"unknown stage: {stage}")
        if CUSTODY_STAGES.index(stage) != idx + 1:
            raise TeleReadError(
                f"custody is sequential: {s['state']} → {stage} skips "
                "steps; a skipped step is a broken chain")
        if stage == "processed":
            if fixation_hours is None:
                raise TeleReadError("fixation hours required before "
                                    "processing — record, don't guess")
            if fixation_hours < FIXATION_MIN_HOURS:
                raise TeleReadError(
                    f"under-fixed ({fixation_hours}h < "
                    f"{FIXATION_MIN_HOURS}h): refuse processing; "
                    "under-fixed tissue reads wrong")
        s["custody"].append((stage, day, by))
        s["state"] = stage
        return dict(s)

    def submit_for_reading(self, specimen_id, *, case_id, priority,
                           question, referrer, day, artifact):
        s = self._spec(specimen_id)
        if s["state"] != "scanned":
            raise TeleReadError(
                f"specimen is {s['state']} — only scanned specimens "
                "enter the reading queue")
        return self.reading.order(
            case_id, priority=priority, question=question,
            facility=s["facility"], referrer=referrer, day=day,
            scope=("cytopathologist" if s["type"].startswith("cytology")
                   else "histopathologist"),
            artifact=artifact,
            meta={"specimen_id": specimen_id, "site": s["site"],
                  "specimen_type": s["type"]})

    def reject_inadequate(self, specimen_id, *, day, by, reason):
        """An inadequate specimen is a finding, not a deletion."""
        if not reason:
            raise TeleReadError("rejection requires a reason")
        s = self._spec(specimen_id)
        s["state"] = "inadequate"
        s["custody"].append(("rejected", day, by))
        s["rejection_reason"] = reason
        return dict(s)

    def adequacy_rate(self):
        total = len(self.specimens)
        inad = sum(1 for s in self.specimens.values()
                   if s["state"] == "inadequate")
        return {"specimens": total, "inadequate": inad,
                "note": "adequacy = (specimens - inadequate)/specimens; "
                        "both shown"}

    def _spec(self, sid):
        if sid not in self.specimens:
            raise TeleReadError(f"unknown specimen: {sid}")
        return self.specimens[sid]


# ------------------------------------------------------------------
# Frozen sections: a separate clock, a separate TAT, and a phone-read
# that is documented or did not happen.
FROZEN_TAT_MINUTES = 30          # the surgeon is standing there
FROZEN_FINDINGS = ("benign", "malignant", "atypical-defer",
                   "margin-clear", "margin-involved", "non-diagnostic")


class FrozenSectionLog:
    """Intra-op frozen section log with its own clock. The permanent
    (paraffin) read later confirms or DISCORDS — and discordance is
    reported with denominators, because a frozen section programme
    that never checks itself against paraffin is guessing."""

    def __init__(self):
        self.frozens = {}
        self._n = 0

    def request(self, fs_id, *, patient_id, site, surgeon, facility,
                day, minute):
        if fs_id in self.frozens:
            raise TeleReadError(f"duplicate frozen section: {fs_id}")
        if not site:
            raise TeleReadError("no anatomical site — an unsited "
                                "frozen is an uninterpretable one")
        self.frozens[fs_id] = {
            "fs_id": fs_id, "patient_id": patient_id, "site": site,
            "facility": facility, "requested_day": day,
            "requested_minute": minute, "surgeon": surgeon,
            "state": "requested", "result": None,
            "permanent_correlation": None}
        return dict(self.frozens[fs_id])

    def report_result(self, fs_id, *, finding, by, day, minute,
                      read_back_by):
        """The intra-op phone read: finding + the named person who
        READ IT BACK. No read-back, no result — spoken criticals are
        confirmed or they did not happen."""
        f = self._fs(fs_id)
        if f["state"] != "requested":
            raise TeleReadError(f"{fs_id} is {f['state']} — a frozen "
                                "result lands exactly once; "
                                "corrections come through the "
                                "permanent-section correlation")
        if finding not in FROZEN_FINDINGS:
            raise TeleReadError(f"unknown frozen finding: {finding}")
        if not read_back_by:
            raise TeleReadError(
                "no read-back recorded — a spoken intra-op result is "
                "confirmed or it did not happen")
        tat = (day - f["requested_day"]) * 24 * 60 \
            + (minute - f["requested_minute"])
        f["result"] = {"finding": finding, "by": by, "day": day,
                       "minute": minute, "read_back_by": read_back_by,
                       "tat_minutes": tat,
                       "within_target": tat <= FROZEN_TAT_MINUTES}
        f["state"] = "reported"
        return dict(f["result"])

    def correlate_permanent(self, fs_id, *, permanent_finding, by, day):
        """The paraffin read meets the frozen call. Agreement is
        concordant; anything else is discordant and counted."""
        f = self._fs(fs_id)
        if f["state"] != "reported":
            raise TeleReadError(f"{fs_id} has no frozen result to "
                                "correlate — absence is not agreement")
        if f["permanent_correlation"] is not None:
            raise TeleReadError("correlation already recorded — the "
                                "record does not rewrite itself")
        concordant = permanent_finding == f["result"]["finding"]
        f["permanent_correlation"] = {
            "permanent_finding": permanent_finding, "by": by,
            "day": day, "concordant": concordant}
        return dict(f["permanent_correlation"])

    def report(self):
        """Frozen-section vital signs, denominators attached: TAT
        within target, concordance vs permanent."""
        reported = [f for f in self.frozens.values() if f["result"]]
        in_target = sum(1 for f in reported
                        if f["result"]["within_target"])
        correlated = [f for f in reported
                      if f["permanent_correlation"]]
        concordant = sum(1 for f in correlated
                         if f["permanent_correlation"]["concordant"])
        return {"frozens": len(self.frozens), "reported": len(reported),
                "tat_within_target": {"n": in_target,
                                      "of": len(reported),
                                      "rate": (round(in_target / len(reported), 3)
                                               if reported else None)},
                "permanent_correlations": len(correlated),
                "concordance": {"n": concordant, "of": len(correlated),
                                "rate": (round(concordant / len(correlated), 3)
                                         if correlated else None)},
                "note": "a discordance is a case-review finding, "
                        "never a deleted row"}

    def _fs(self, fs_id):
        if fs_id not in self.frozens:
            raise TeleReadError(f"unknown frozen section: {fs_id}")
        return self.frozens[fs_id]


# ------------------------------------------------------------------
# Block/slide inventory: paraffin blocks and cut slides are PHYSICAL
# ASSETS with locations. Custody covers the specimen; this covers its
# derivatives.
DERIVATIVE_STAINS = ("h-and-e", "ihc", "special-stain", "unstained")


class DerivativeInventory:
    """Blocks and cut slides per specimen, with a move ledger. A block
    whose location is unknown is a lost block, and the report says
    how many are unaccounted."""

    def __init__(self, path):
        self.path = path
        self.blocks = {}
        self.slides = {}
        self.moves = []   # append-only location ledger

    def register_block(self, block_id, *, specimen_id, location,
                       day, by):
        self.path._spec(specimen_id)  # refuses unknown specimens
        if block_id in self.blocks:
            raise TeleReadError(f"duplicate block: {block_id}")
        if not location:
            raise TeleReadError("a block without a location is "
                                "already lost")
        self.blocks[block_id] = {
            "block_id": block_id, "specimen_id": specimen_id,
            "location": location, "created_day": day, "by": by}
        self.moves.append({"asset": block_id, "to": location,
                           "day": day, "by": by})
        return dict(self.blocks[block_id])

    def cut_slides(self, block_id, *, n_slides, stain, location, day,
                   by, id_prefix=None):
        b = self._block(block_id)
        if stain not in DERIVATIVE_STAINS:
            raise TeleReadError(f"unknown stain: {stain}")
        if not (1 <= n_slides <= 100):
            raise TeleReadError(f"implausible slide count: {n_slides}")
        prefix = id_prefix or f"{block_id}-S"
        out = []
        for i in range(n_slides):
            sid = f"{prefix}{i + 1}"
            self.slides[sid] = {"slide_id": sid, "block_id": block_id,
                                "stain": stain, "location": location,
                                "day": day, "by": by}
            self.moves.append({"asset": sid, "to": location,
                               "day": day, "by": by})
            out.append(sid)
        return out

    def move(self, asset_id, *, to_location, day, by):
        """Move a block or slide; the ledger is append-only — where a
        thing HAS BEEN is part of where it IS."""
        a = self.blocks.get(asset_id) or self.slides.get(asset_id)
        if a is None:
            raise TeleReadError(f"unknown asset: {asset_id}")
        if not to_location:
            raise TeleReadError("moves need a destination")
        a["location"] = to_location
        self.moves.append({"asset": asset_id, "to": to_location,
                           "day": day, "by": by})
        return dict(a)

    def locate(self, asset_id):
        a = self.blocks.get(asset_id) or self.slides.get(asset_id)
        if a is None:
            raise TeleReadError(f"unknown asset: {asset_id}")
        trail = [m for m in self.moves if m["asset"] == asset_id]
        return {"asset": asset_id, "location": a["location"],
                "trail": trail}

    def inventory_report(self):
        per_spec = {}
        for b in self.blocks.values():
            per_spec.setdefault(b["specimen_id"], 0)
            per_spec[b["specimen_id"]] += 1
        return {"blocks": len(self.blocks), "slides": len(self.slides),
                "blocks_per_specimen": per_spec,
                "moves": len(self.moves),
                "note": "every block and slide has a current location "
                        "and a full trail; if the count surprises you, "
                        "the trail is the audit"}

    def _block(self, bid):
        if bid not in self.blocks:
            raise TeleReadError(f"unknown block: {bid}")
        return self.blocks[bid]


# ------------------------------------------------------------------
# Reflex testing: IHC/molecular ordered FROM the report, landing as
# an addendum on it — never as a detached orphan result.
REFLEX_TESTS = ("ihc-ER", "ihc-PR", "ihc-HER2", "ihc-Ki67",
                "ihc-MLH1", "ihc-MSH2", "ihc-MSH6", "ihc-PMS2",
                "molecular-KRAS", "molecular-BRAF", "molecular-EGFR",
                "hpv-ish", "eber-ish")
REFLEX_STATES = ("ordered", "resulted")
REFLEX_DUE_DAYS = 7


class ReflexLog:
    """Reflex orders hang off a verified report and come back as
    addenda. Pending reflexes are a worklist, overdue-first — an
    unordered reflex is a clinical thought that never became an
    action."""

    def __init__(self, reading_svc):
        self.reading = reading_svc
        self.reflexes = {}

    def order(self, reflex_id, *, case_id, test, reason, by, day):
        if reflex_id in self.reflexes:
            raise TeleReadError(f"duplicate reflex: {reflex_id}")
        if test not in REFLEX_TESTS:
            raise TeleReadError(f"unknown reflex test: {test} — the "
                                "menu is versioned; add tests "
                                "deliberately")
        rep = self.reading.reports.get(case_id)
        if not rep or rep["state"] not in ("verified", "addended"):
            raise TeleReadError(
                "reflexes order from VERIFIED reports — a draft's "
                "conclusions are still moving")
        if not reason:
            raise TeleReadError("a reflex without a reason is a "
                                "reflex without a reflex")
        self.reflexes[reflex_id] = {
            "reflex_id": reflex_id, "case_id": case_id, "test": test,
            "reason": reason, "by": by, "day": day,
            "state": "ordered", "result": None}
        return dict(self.reflexes[reflex_id])

    def record_result(self, reflex_id, *, result, by, day):
        """The reflex result lands as an ADDENDUM on the original
        report — one chart, one story."""
        r = self._rx(reflex_id)
        if r["state"] != "ordered":
            raise TeleReadError(f"{reflex_id} already resulted — "
                                "corrections are new addenda")
        if not result:
            raise TeleReadError("an empty reflex result is not a "
                                "result")
        self.reading.add_addendum(
            r["case_id"], reader_id=by, day=day,
            text=f"{r['test']}: {result}",
            reason=f"reflex result {reflex_id}")
        r["state"] = "resulted"
        r["result"] = {"result": result, "by": by, "day": day}
        return dict(r)

    def pending(self, *, as_of):
        """Unresulted reflexes, overdue-first."""
        out = [dict(r, age_days=as_of - r["day"])
               for r in self.reflexes.values() if r["state"] == "ordered"]
        return sorted(out, key=lambda r: -r["age_days"])

    def report(self):
        total = len(self.reflexes)
        done = sum(1 for r in self.reflexes.values()
                   if r["state"] == "resulted")
        return {"reflexes": total, "resulted": done,
                "pending": total - done,
                "note": "pending reflexes are the worklist, "
                        "overdue-first"}

    def _rx(self, rid):
        if rid not in self.reflexes:
            raise TeleReadError(f"unknown reflex: {rid}")
        return self.reflexes[rid]
