"""domains.py — the domain registry: every reading service is a config.

Adding a specialty is declaring scopes, critical findings, artifacts,
and SLAs. The engine (teleread) does the rest. Radiology is a domain
here too — packages/radiology stays the reference module; the service
serves it through the same engine.
"""

from teleread import DomainConfig

DOMAINS = {
    "radiology": DomainConfig(
        "radiology",
        scopes=("general", "neuro", "msk", "chest", "abdominal",
                "paediatric", "obstetric", "mammography"),
        critical_findings=("pneumothorax", "intracranial-haemorrhage",
                           "aortic-dissection", "bowel-perforation",
                           "ectopic-rupture", "cord-compression"),
        artifact_kinds=("dicom-study",),
        sla_hours={"stat": 2, "urgent": 24, "routine": 72}),
    "pathology": DomainConfig(
        "pathology",
        scopes=("histopathologist", "cytopathologist"),
        critical_findings=("malignant", "high-grade-dysplasia",
                           "granulomatous-inflammation"),
        artifact_kinds=("wsi", "static-images"),
        sla_hours={"stat": 24, "urgent": 72, "routine": 240}),
    "ecg": DomainConfig(
        "ecg",
        scopes=("cardiac-reader",),
        critical_findings=("stemi", "complete-heart-block", "vt",
                           "severe-bradycardia"),
        artifact_kinds=("12-lead", "3-lead", "single-lead"),
        sla_hours={"stat": 1, "urgent": 12, "routine": 48}),
    "obstetric-us": DomainConfig(
        "obstetric-us",
        scopes=("us-overreader",),
        critical_findings=("suspected-ectopic", "no-cardiac-activity",
                           "severe-oligohydramnios"),
        artifact_kinds=("cine", "static-images"),
        sla_hours={"stat": 4, "urgent": 24, "routine": 168}),
    "dr-screening": DomainConfig(
        "dr-screening",
        scopes=("dr-grader",),
        critical_findings=("pdr", "maculopathy"),
        artifact_kinds=("fundus-image",),
        sla_hours={"stat": 24, "urgent": 72, "routine": 336}),
    "dermatology": DomainConfig(
        "dermatology",
        scopes=("derm-reader",),
        critical_findings=("suspected-melanoma", "suspected-scc"),
        artifact_kinds=("dermoscopy", "clinical-photo"),
        sla_hours={"stat": 24, "urgent": 72, "routine": 336}),
    "cervix-screening": DomainConfig(
        "cervix-screening",
        scopes=("cervix-reviewer",),
        critical_findings=("suspicious-of-cancer",),
        artifact_kinds=("cervigram",),
        sla_hours={"stat": 24, "urgent": 72, "routine": 336}),
    "pharmacy-review": DomainConfig(
        "pharmacy-review",
        scopes=("clinical-pharmacist",),
        critical_findings=("contraindicated",),
        artifact_kinds=("prescription",),
        sla_hours={"stat": 1, "urgent": 8, "routine": 24}),
    "teleicu": DomainConfig(
        "teleicu",
        scopes=("intensivist",),
        critical_findings=("imminent-arrest",),
        artifact_kinds=("vitals-stream",),
        sla_hours={"stat": 1, "urgent": 4, "routine": 24}),
}
