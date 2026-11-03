"""datasetforge.py — from clinical pixels to training sets, honestly.

The platform's imaging lanes (pacs, pathology scanners, dermatology,
fundus cameras) stop at viewing and diagnosis. Training local models —
cervical triage, DR screening, lesion QA, pathology pre-analysis —
needs a dataset lane: labeled, de-identified, integrity-verified,
reproducible. This is that lane. The conversion to a training
container (.omnia-shaped) runs as a service-boundary job — composed,
never embedded — and the registry is what makes a pile of files a
dataset.

Doctrine:
  - A TILE WITHOUT A SOURCE IS AN ORPHAN: every case carries its
    source study ref and the de-id policy version that released it.
  - A LABEL WITHOUT A LABELER IS A GUESS: labeled_by is mandatory,
    and labels come from the task's versioned schema only.
  - SPLITS ARE PATIENT-LEVEL: the same patient never crosses
    train/val/test — leakage is how models learn the hospital instead
    of the disease.
  - VERIFICATION IS A GATE, NOT A VIBES CHECK: a dataset ships with a
    CRC verification record or it ships as unverified, stated loudly.
  - DIAGNOSIS NEVER READS FROM HERE: datasets are derivatives; the
    clinical read path stays on the original pixels in PACS.
"""
import hashlib

FORGE_VERSION = "dataset-forge@1.0.0"


class ForgeError(Exception):
    pass


class DatasetForge:
    """The dataset registry: tasks, labeled cases, patient-level
    splits, conversion manifests, verification records."""

    def __init__(self):
        self.tasks = {}      # task_id -> {schema, version, ...}
        self.cases = []      # append-only
        self.datasets = []   # append-only (conversion records)
        self._ds = 0

    # ---- 1. tasks: the label schema is versioned law ----
    def register_task(self, task_id, *, label_schema, version):
        if not label_schema:
            raise ForgeError("a task without a label schema labels "
                             "nothing — the schema is the law")
        if task_id in self.tasks:
            raise ForgeError(f"duplicate task: {task_id} — version "
                             "the schema, register a new task")
        self.tasks[task_id] = {"task_id": task_id,
                               "label_schema": tuple(label_schema),
                               "version": version}
        return dict(self.tasks[task_id])

    def _task(self, task_id):
        t = self.tasks.get(task_id)
        if t is None:
            raise ForgeError(f"unknown task: {task_id}")
        return t

    # ---- 2. cases: provenance + labeler + patient key ----
    def register_case(self, task_id, *, case_ref, source_ref,
                      patient_key, label, labeled_by, day,
                      deid_policy_version):
        t = self._task(task_id)
        if label not in t["label_schema"]:
            raise ForgeError(f"label {label!r} not in task schema "
                             f"{t['label_schema']} — unbound is not a "
                             "code; extend the schema with a version, "
                             "not a shrug")
        if not labeled_by:
            raise ForgeError("a label without a labeler is a guess — "
                             "attribution is mandatory")
        if not source_ref:
            raise ForgeError("a case without a source study is an "
                             "orphan — provenance is mandatory")
        if not patient_key:
            raise ForgeError("no patient key — splits cannot be "
                             "patient-level without one")
        if any(c["case_ref"] == case_ref for c in self.cases):
            raise ForgeError(f"duplicate case: {case_ref}")
        rec = {"task_id": task_id, "case_ref": case_ref,
               "source_ref": source_ref, "patient_key": patient_key,
               "label": label, "labeled_by": labeled_by, "day": day,
               "deid_policy_version": deid_policy_version}
        self.cases.append(rec)
        return dict(rec)

    # ---- 3. splits: patient-level, seeded, reproducible ----
    def split(self, task_id, *, ratios=(0.8, 0.1, 0.1), seed=0):
        """Deterministic patient-level split. Same seed + same case
        set → same split; a split you cannot reproduce is a result
        you cannot reproduce."""
        import random
        if abs(sum(ratios) - 1.0) > 1e-9 or len(ratios) != 3:
            raise ForgeError("split ratios must be (train, val, test) "
                             "summing to 1")
        cases = [c for c in self.cases if c["task_id"] == task_id]
        if not cases:
            raise ForgeError("no cases for this task — a split of "
                             "nothing trains nothing")
        patients = sorted({c["patient_key"] for c in cases})
        rng = random.Random(seed)
        rng.shuffle(patients)
        n = len(patients)
        n_train = int(n * ratios[0])
        n_val = int(n * ratios[1])
        buckets = {"train": set(patients[:n_train]),
                   "val": set(patients[n_train:n_train + n_val]),
                   "test": set(patients[n_train + n_val:])}
        out = {"train": [], "val": [], "test": []}
        for c in cases:
            for name, pats in buckets.items():
                if c["patient_key"] in pats:
                    out[name].append(c["case_ref"])
                    break
        overlap = (set(out["train"]) & set(out["val"])
                   | set(out["train"]) & set(out["test"])
                   | set(out["val"]) & set(out["test"]))
        if overlap:
            raise ForgeError("split leakage detected — the same case "
                             "crossed splits; refuse to ship")
        return {**out, "seed": seed, "ratios": ratios,
                "patients": {k: len(v) for k, v in buckets.items()},
                "task_version": self._task(task_id)["version"]}

    # ---- 4. conversion: composed, never embedded ----
    def export_manifest(self, task_id, *, split_name=None):
        """The conversion job's work order: cases (+ split filter)
        with source refs. The converter runs at the service boundary
        (omnia-shaped container, CRC per block); without it there is
        no conversion, only a manifest that says so."""
        self._task(task_id)
        cases = [c for c in self.cases if c["task_id"] == task_id]
        if split_name:
            split_map = self.split(task_id)
            wanted = set(split_map.get(split_name, []))
            cases = [c for c in cases if c["case_ref"] in wanted]
        return {"task_id": task_id, "split": split_name,
                "cases": [dict(c) for c in cases],
                "count": len(cases),
                "forge_version": FORGE_VERSION}

    def convert(self, manifest, *, converter=None, day):
        """Run the conversion through the deployment transport
        (the omnia-shaped converter service). No converter → refuse:
        a manifest without a converter is a wish, not a dataset."""
        if converter is None:
            raise ForgeError(
                "no converter supplied — the container conversion is a "
                "service-boundary job (composed, never embedded); wire "
                "the converter at deployment")
        if not manifest.get("cases"):
            raise ForgeError("converting an empty manifest — a dataset "
                             "of nothing trains nothing")
        result = converter(manifest)
        if not result or not result.get("dataset_ref"):
            raise ForgeError("converter returned no dataset reference "
                             "— an unconfirmed conversion did not "
                             "happen")
        self._ds += 1
        ds = {"dataset_id": f"DS-{self._ds}",
              "task_id": manifest["task_id"],
              "split": manifest.get("split"),
              "dataset_ref": result["dataset_ref"],
              "container_sha256": result.get("container_sha256"),
              "cases": manifest["count"], "day": day,
              "verified": None,  # absence is not verification
              "forge_version": FORGE_VERSION}
        self.datasets.append(ds)
        return dict(ds)

    # ---- 5. verification: the CRC is the contract ----
    def verify(self, dataset_id, *, verifier=None):
        ds = next((d for d in self.datasets
                   if d["dataset_id"] == dataset_id), None)
        if ds is None:
            raise ForgeError(f"unknown dataset: {dataset_id}")
        if verifier is None:
            raise ForgeError("no verifier supplied — CRC verification "
                             "runs through the container tooling at the "
                             "service boundary; unverified stays "
                             "unverified, stated loudly")
        ok, detail = verifier(ds["dataset_ref"])
        ds["verified"] = bool(ok)
        ds["verification_detail"] = detail
        if not ok:
            raise ForgeError(f"{dataset_id} failed verification: "
                             f"{detail} — a corrupt training container "
                             "trains corrupt models; quarantined")
        return {"dataset_id": dataset_id, "verified": True,
                "detail": detail}

    # ---- 6. the report: denominators, always ----
    def report(self, task_id=None):
        cases = [c for c in self.cases
                 if task_id is None or c["task_id"] == task_id]
        by_label = {}
        for c in cases:
            by_label[c["label"]] = by_label.get(c["label"], 0) + 1
        datasets = [d for d in self.datasets
                    if task_id is None or d["task_id"] == task_id]
        return {"task_id": task_id, "cases": len(cases),
                "patients": len({c["patient_key"] for c in cases}),
                "by_label": by_label,
                "datasets": len(datasets),
                "verified": sum(1 for d in datasets
                                if d["verified"] is True),
                "unverified": sum(1 for d in datasets
                                  if d["verified"] is not True),
                "note": "unverified datasets are stated, not hidden — "
                        "the CRC is the contract",
                "forge_version": FORGE_VERSION}

    @staticmethod
    def patient_key_from(patient_id, *, salt):
        """Pseudonymous patient key: the registry never stores the
        patient id, only its salted hash — enough to keep splits
        patient-level, never enough to re-identify."""
        if not salt:
            raise ForgeError("a patient-key salt is mandatory — an "
                             "unsalted hash of an MRN is a lookup "
                             "table, not a pseudonym")
        return hashlib.sha256(f"{salt}:{patient_id}"
                              .encode()).hexdigest()[:16]
