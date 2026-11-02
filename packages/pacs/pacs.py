"""pacs.py — PACS layer: study registry, DICOMweb adapter, integrity, dose.

The platform composes a PACS appliance (e.g. Orthanc) as an EXTERNAL
service over DICOMweb (STOW-RS store, WADO-RS retrieve, QIDO-RS query —
public DICOM standard). Everything here is clean-room orchestration:
what the platform owns is the registry, the honesty, and the workflow.

Ten innovations in this module:
 1. DICOMweb adapter as honest spike — no PACS URL, every call refuses
 2. Study registry with accession+UID dedupe (duplicate/broken studies
    surface, never double-count)
 3. Per-instance sha256 integrity chain — a mutated archive object is
    detectable
 4. Offline store-and-forward spool for modality uploads (studies taken
    during an outage queue with integrity, sync when the link returns)
 5. De-identification for tele/research export: patient tags stripped,
    burned-in-annotation flag FORCES refusal when marked yes (pixels
    may contain the name — never guess)
 6. Radiation dose ledger per patient with cumulative alerts
 7. Modality QC rejects logged with reasons; repeat-rate is a metric
 8. Storage lifecycle tiers with HONEST retrieval latency (nearline
    takes hours; the UI must say so)
 9. Priority/protocol codes fail-closed (STAT/routine, exam protocol
    from a versioned list)
10. AI pre-analysis queue governance: results land as DRAFT, a
    radiologist confirms — a nudge never executes
11. Pull-sync from the hub archive (dcm4chee QIDO-RS): unknown studies
    are registered, malformed records are findings, sync never
    overwrites governed state

Doctrine: the registry is the source of truth; the PACS appliance is
storage. A study in the appliance but not the registry is an orphan;
a registry study the appliance lost is a disaster — both are FINDINGS.
"""

import hashlib
import json

PRIORITIES = ("routine", "urgent", "stat")
STORAGE_TIERS = {"online": "seconds", "nearline": "hours", "offline": "days"}
DOSE_ALERT_MGY = {"ct": 50.0, "fluoroscopy": 100.0}   # per-study alert
DOSE_CUMULATIVE_ALERT_MGY = 500.0                      # rolling per patient



# Hanging protocols: per-protocol layout presets as versioned data.
# The layout is part of the read — the priors pane exists because the
# protocol demands it, not because the reader remembered to open it.
HANGING_VERSION = "hanging@1.0.0"
HANGING_PROTOCOLS = {
    "ct-chest": {"name": "ct-chest-comparison",
                 "panes": [{"role": "current", "position": 0},
                           {"role": "most-recent-prior", "position": 1}],
                 "sync_scrolling": True},
    "ct-head": {"name": "ct-head-comparison",
                "panes": [{"role": "current", "position": 0},
                          {"role": "most-recent-prior", "position": 1}],
                "sync_scrolling": True},
    "cr-chest": {"name": "chest-pa-with-prior",
                 "panes": [{"role": "current", "position": 0},
                           {"role": "most-recent-prior", "position": 1}],
                 "sync_scrolling": False},
    "mg-mammo": {"name": "mammo-bilateral-with-priors",
                 "panes": [{"role": "current", "position": 0},
                           {"role": "current-mirror", "position": 1},
                           {"role": "most-recent-prior", "position": 2},
                           {"role": "prior-mirror", "position": 3}],
                 "sync_scrolling": True},
}

class PACSError(Exception):
    pass


def _dicom_value(record, tag):
    """DICOM JSON model: {"0020000D": {"vr": "UI", "Value": ["1.2.3"]}}.
    Missing or empty → None (caller decides whether that's a finding)."""
    values = record.get(tag, {}).get("Value") or []
    return values[0] if values else None


class DICOMwebAdapter:
    """Honest spike: talks STOW/WADO/QIDO to an external PACS. No base
    URL -> every call refuses loudly.

    The appliance is composed, never embedded: Orthanc (GPLv3) at edge
    facilities, dcm4chee-arc-light (MPL 1.1) at the state/national hub —
    same DICOMweb contract, same adapter (see ADR-0068)."""

    def __init__(self, base_url=None, token=None, fetch=None,
                 oidc_token_url=None, oidc_client_id=None,
                 oidc_client_secret=None):
        self.base = base_url
        self.token = token
        self.fetch = fetch
        self.oidc_token_url = oidc_token_url
        self.oidc_client_id = oidc_client_id
        self.oidc_client_secret = oidc_client_secret

    def _require(self):
        if not self.base or not self.fetch:
            raise PACSError(
                "no PACS endpoint configured — refusing to pretend. "
                "Set base_url to the DICOMweb root of the external "
                "appliance (e.g. Orthanc /dicom-web, dcm4chee "
                "/dcm4chee-arc/aets/DCM4CHEE/rs).")

    def access_token(self):
        """Keycloak client-credentials token for a SECURED archive.
        Honest: without an OIDC config or a transport, refuse."""
        if not (self.oidc_token_url and self.oidc_client_id
                and self.oidc_client_secret):
            raise PACSError(
                "no OIDC client configured — a secured dcm4chee refuses "
                "anonymous REST and so do we; configure "
                "oidc_token_url/client_id/client_secret (Keycloak realm "
                "'dcm4che').")
        if not self.fetch:
            raise PACSError("no transport — refusing to pretend")
        resp = self.fetch(self.oidc_token_url, {
            "grant_type": "client_credentials",
            "client_id": self.oidc_client_id,
            "client_secret": self.oidc_client_secret})
        tok = resp.get("access_token") if isinstance(resp, dict) else None
        if not tok:
            raise PACSError("token endpoint returned no access_token — "
                            "fail loud, never send unsigned requests")
        self.token = tok
        return tok

    def stow(self, instances):
        self._require()
        return self.fetch(f"{self.base}/stow-rs/studies", instances)

    def qido_studies(self, query):
        self._require()
        return self.fetch(f"{self.base}/qido-rs/studies", query)

    def wado_instance(self, study_uid, series_uid, instance_uid):
        self._require()
        return self.fetch(
            f"{self.base}/wado-rs/studies/{study_uid}/series/"
            f"{series_uid}/instances/{instance_uid}", None)


class StudyRegistry:
    def __init__(self, adapter=None):
        self.adapter = adapter or DICOMwebAdapter()
        self.studies = {}          # study_uid -> record
        self.accessions = {}       # accession -> study_uid
        self.spool = []            # offline uploads awaiting sync
        self.dose_ledger = {}      # patient_id -> [dose entries]
        self.qc_rejects = []
        self.ai_queue = []
        self._hash_chain = "0" * 64

    # ---- 2. registration with dedupe ----
    def register_study(self, *, study_uid, accession, patient_id,
                       modality, exam_protocol, priority, facility, day,
                       instances=1, burned_in_annotations=False):
        if priority not in PRIORITIES:
            raise PACSError(f"unknown priority: {priority}")
        if modality not in ("CR", "DX", "CT", "MR", "US", "XA", "MG"):
            raise PACSError(f"unknown modality: {modality}")
        if not exam_protocol:
            raise PACSError("exam protocol required (versioned list)")
        if study_uid in self.studies:
            raise PACSError(f"duplicate study UID: {study_uid}")
        if accession in self.accessions:
            raise PACSError(
                f"accession {accession} already bound to "
                f"{self.accessions[accession]} — possible duplicate exam")
        rec = {"study_uid": study_uid, "accession": accession,
               "patient_id": patient_id, "modality": modality,
               "exam_protocol": exam_protocol, "priority": priority,
               "facility": facility, "day": day, "instances": instances,
               "burned_in_annotations": bool(burned_in_annotations),
               "state": "acquired", "tier": "online"}
        self.studies[study_uid] = rec
        self.accessions[accession] = study_uid
        return dict(rec)

    # ---- 3. integrity chain ----
    def seal_instances(self, study_uid, instance_hashes):
        s = self._study(study_uid)
        for h in instance_hashes:
            self._hash_chain = hashlib.sha256(
                (self._hash_chain + h).encode()).hexdigest()
        s["integrity_sealed"] = True
        return {"chain_head": self._hash_chain}

    # ---- 4. offline spool ----
    def spool_upload(self, study_uid, blob, *, day):
        s = self._study(study_uid)
        digest = hashlib.sha256(blob).hexdigest()
        self.spool.append({"study_uid": study_uid, "day": day,
                           "sha256": digest, "bytes": len(blob)})
        s["state"] = "spooled"
        return {"spooled": True, "sha256": digest}

    def sync_spool(self, sender):
        """sender(entry) -> ok or raises; failures stay queued."""
        remaining, synced = [], 0
        for e in self.spool:
            try:
                sender(e)
                self.studies[e["study_uid"]]["state"] = "acquired"
                synced += 1
            except Exception:
                remaining.append(e)
        self.spool = remaining
        return {"synced": synced, "still_queued": len(remaining),
                "note": "hub silence is not study loss"}

    # ---- 5. de-id for export ----
    def export_metadata(self, study_uid, *, purpose):
        s = self._study(study_uid)
        if s["burned_in_annotations"]:
            raise PACSError(
                "burned-in annotations may contain identifiers in the "
                "PIXELS — export refused until pixel de-id is verified")
        if purpose not in ("teleradiology", "research", "transfer"):
            raise PACSError(f"unknown export purpose: {purpose}")
        out = {k: v for k, v in s.items() if k != "patient_id"}
        out["purpose"] = purpose
        out["deidentified"] = True
        return out

    # ---- 6. dose ledger ----
    def record_dose(self, study_uid, dose_mgy, *, day):
        s = self._study(study_uid)
        if dose_mgy <= 0 or dose_mgy > 10000:
            raise PACSError(f"implausible dose: {dose_mgy}mGy")
        s["dose_mgy"] = dose_mgy
        entry = {"study_uid": study_uid, "dose_mgy": dose_mgy, "day": day}
        self.dose_ledger.setdefault(s["patient_id"], []).append(entry)
        alerts = []
        if s["modality"] == "CT" and dose_mgy > DOSE_ALERT_MGY["ct"]:
            alerts.append(f"single-study CT dose {dose_mgy}mGy exceeds "
                          f"{DOSE_ALERT_MGY['ct']}mGy")
        cumulative = sum(e["dose_mgy"]
                         for e in self.dose_ledger[s["patient_id"]])
        if cumulative > DOSE_CUMULATIVE_ALERT_MGY:
            alerts.append(f"cumulative dose {cumulative:.1f}mGy exceeds "
                          f"{DOSE_CUMULATIVE_ALERT_MGY}mGy")
        return {"recorded": True, "cumulative_mgy": cumulative,
                "alerts": alerts}

    # ---- 7. QC rejects / repeat rate ----
    def record_qc_reject(self, study_uid, *, reason, day, retaken=False):
        s = self._study(study_uid)
        if not reason:
            raise PACSError("QC reject requires a reason")
        self.qc_rejects.append({"study_uid": study_uid, "reason": reason,
                                "day": day, "retaken": bool(retaken)})
        s["state"] = "rejected"
        return {"rejected": True}

    def repeat_rate(self):
        total = len(self.studies)
        rejects = len(self.qc_rejects)
        return {"studies": total, "rejects": rejects,
                "note": "repeat rate = rejects/studies; both shown"}

    # ---- 8. storage lifecycle ----
    def set_tier(self, study_uid, tier):
        if tier not in STORAGE_TIERS:
            raise PACSError(f"unknown tier: {tier}")
        s = self._study(study_uid)
        s["tier"] = tier
        return {"study_uid": study_uid, "tier": tier,
                "expected_retrieval": STORAGE_TIERS[tier],
                "note": "nearline means hours; say so at the UI"}

    # ---- 10. AI pre-analysis governance ----
    def submit_ai_result(self, study_uid, *, model_id, model_version,
                         findings):
        s = self._study(study_uid)
        if not model_version:
            raise PACSError("AI results require a model version — an "
                            "unversioned model's opinion is unauditable")
        entry = {"study_uid": study_uid, "model": model_id,
                 "version": model_version, "findings": findings,
                 "status": "draft-awaiting-radiologist"}
        self.ai_queue.append(entry)
        return dict(entry)

    def confirm_ai_result(self, study_uid, *, radiologist, agree, note=""):
        for e in self.ai_queue:
            if (e["study_uid"] == study_uid and
                    e["status"] == "draft-awaiting-radiologist"):
                e["confirmed"] = {"by": radiologist, "agree": bool(agree),
                                  "note": note}
                e["status"] = "confirmed" if agree else "overruled"
                return dict(e)
        raise PACSError("no pending AI result for this study")

    def orphans_and_gaps(self, appliance_uids):
        """Reconciliation: registry vs appliance. Both directions are
        findings."""
        reg = set(self.studies)
        app = set(appliance_uids)
        return {"orphans_in_appliance": sorted(app - reg),
                "missing_from_appliance": sorted(reg - app),
                "note": "both directions are findings, never cosmetic"}

    # ---- 11. pull-sync from the hub archive (dcm4chee) ----
    def sync_from_archive(self, qido_studies, *, day, facility):
        """Consume QIDO-RS results (DICOM JSON model) from the hub
        archive, e.g. GET {base}/dcm4chee-arc/aets/DCM4CHEE/rs/studies.

        Governance posture: the archive is storage; this registry is
        truth. A study the archive holds but we don't know gets
        registered (it EXISTS — a patient was irradiated; ignoring it
        is not an option). A study we already govern is untouched —
        sync never overwrites local state. A malformed record is a
        FINDING, never a crash: garbage in the archive means something
        upstream is broken and silence would hide it.
        """
        registered, already_known, malformed = 0, 0, []
        archive_uids = set()
        for raw in qido_studies:
            uid = _dicom_value(raw, "0020000D")       # StudyInstanceUID
            if not uid:
                malformed.append({"record_keys": sorted(raw),
                                  "reason": "no StudyInstanceUID"})
                continue
            archive_uids.add(uid)
            if uid in self.studies:
                already_known += 1
                continue
            modality = _dicom_value(raw, "00080061")  # ModalitiesInStudy
            accession = (_dicom_value(raw, "00080050")  # AccessionNumber
                         or f"ARCHIVE-{uid[:24]}")
            patient = (_dicom_value(raw, "00100020")   # PatientID
                       or "archive-unknown-patient")
            try:
                self.register_study(
                    study_uid=uid, accession=accession,
                    patient_id=patient, modality=modality,
                    exam_protocol="archive-ingested",
                    priority="routine", facility=facility, day=day)
                registered += 1
            except PACSError as e:
                malformed.append({"study_uid": uid, "reason": str(e)})
        recon = self.orphans_and_gaps(archive_uids)
        return {"registered": registered, "already_known": already_known,
                "malformed": malformed, "reconciliation": recon,
                "note": "garbage in the archive is a finding, not a "
                        "crash; sync never overwrites governed state"}

    # ---- 9. hanging protocols: the layout is part of the read ----
    def hanging_protocol(self, study_uid):
        """The layout the reader opens with, resolved against the
        patient's archive: current + priors in named panes. A pane
        with no prior says so EXPLICITLY — an empty pane is a fact
        about the archive, not a blank wall."""
        s = self._study(study_uid)
        proto = HANGING_PROTOCOLS.get(s["exam_protocol"])
        if not proto:
            raise PACSError(
                f"no hanging protocol for {s['exam_protocol']} — a "
                "reader improvising layout per study is a reader "
                "missing the priors pane when it matters")
        priors = sorted(
            [x for x in self.studies.values()
             if x["patient_id"] == s["patient_id"]
             and x["modality"] == s["modality"]
             and x["study_uid"] != study_uid
             and x["day"] < s["day"]],   # a future study is not a
                                         # prior
            key=lambda x: -x["day"])
        panes = []
        for pane in proto["panes"]:
            if pane["role"] == "current":
                panes.append({**pane, "study_uid": study_uid})
            elif priors:
                panes.append({**pane,
                              "study_uid": priors[0]["study_uid"]})
                priors = priors[1:]
            else:
                panes.append({**pane, "study_uid": None,
                              "empty": "no prior in archive"})
        return {"study_uid": study_uid,
                "protocol": proto["name"], "panes": panes,
                "sync_scrolling": proto["sync_scrolling"],
                "protocol_version": HANGING_VERSION}

    # ---- 10. key-image pushback: annotations belong to the study ----
    def push_key_images(self, study_uid, *, selections, by, day):
        """Key images are pushed BACK to the study record — an
        annotation that lives only in our registry is invisible to the
        next reader, the referring clinician, and the archive. With an
        archive adapter the set is STOWed immediately; without one it
        is marked pending-archive-sync — honestly, not optimistically.
        Selections reference instances by index; pixels are never
        burned in (burned-in annotation is the export-refusal rule
        this registry already enforces)."""
        s = self._study(study_uid)
        if not by:
            raise PACSError("unsigned key images are no key images")
        if not selections:
            raise PACSError("an empty key-image set annotates nothing")
        for sel in selections:
            idx = sel.get("instance_index")
            if idx is None or not (0 <= idx < s["instances"]):
                raise PACSError(
                    f"instance_index {idx} outside 0.."
                    f"{s['instances'] - 1} — a key image pointing "
                    "nowhere annotates nothing")
            if not sel.get("label"):
                raise PACSError("a key image without a label is a "
                                "bookmark nobody can read")
        record = {"selections": [dict(x) for x in selections],
                  "by": by, "day": day,
                  "sync": "pending-archive-sync"}
        if self.adapter is not None and self.adapter.base and \
                self.adapter.fetch:
            self.adapter.stow([{"study_uid": study_uid,
                                "key_images": selections}])
            record["sync"] = "pushed"
        s.setdefault("key_image_sets", []).append(record)
        return dict(record)

    def key_images(self, study_uid):
        return list(self._study(study_uid).get("key_image_sets", []))

    def _study(self, uid):
        if uid not in self.studies:
            raise PACSError(f"unknown study: {uid}")
        return self.studies[uid]
