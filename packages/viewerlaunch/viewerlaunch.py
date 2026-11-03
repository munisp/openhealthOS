"""Diagnostic viewer launch layer — the pixels are the point.

The gap doc's first cross-cutting P0: a worklist that cannot open the
images sends the reader in with both eyes closed. This package is the
launch seam: it does NOT embed a viewer (OHIF is Apache-2.0,
OpenSeadragon is BSD-3-Clause — both composed as upstream deployments,
never vendored, never forked). It answers three questions honestly:

  1. WHICH viewer for this artifact (DICOM study -> OHIF-shaped
     deep-zoom of DICOMweb; whole-slide image -> OpenSeadragon-shaped
     DZI/IIIF tiles).
  2. WHERE it lives (viewer_base / slide_base configured at deploy
     time; unconfigured -> refuse loudly, same doctrine as the PACS
     adapter: no endpoint, no pretend).
  3. WHO opened WHAT and WHEN (append-only launch audit) — so
     "verified without ever opening the pixels" is a detectable event,
     not a rumour.

Doctrine:
  - a launch is an audit record, not a side effect
  - no endpoint is configured -> refuse; a grey box is a lie
  - the viewer is referenced, the data stays in the archive
  - absence is not a value: a study with no launch session was not
    viewed, whatever the report says
"""

VIEWER_VERSION = "viewer-launch@1.0.0"

# Composed, never embedded. Both licences are embed-clean; the doctrine
# is still reference-upstream so security patches ride the vendor's
# release train instead of our fork.
VIEWER_ENGINES = {
    "dicom": {
        "engine": "ohif",
        "license": "Apache-2.0",
        "upstream": "https://github.com/OHIF/Viewers",
        "transport": "dicomweb",
        "note": ("deploy OHIF (or any Cornerstone-based viewer) beside "
                 "the archive; it speaks DICOMweb (QIDO/WADO-RS) to the "
                 "same endpoint pacs.py already uses"),
    },
    "slide": {
        "engine": "openseadragon",
        "license": "BSD-3-Clause",
        "upstream": "https://github.com/openseadragon/openseadragon",
        "transport": "dzi/iiif",
        "note": ("deep-zoom tiles served by the slide archive "
                 "(DZI or IIIF Image API); the page references the "
                 "upstream viewer from CDN or a deployed copy"),
    },
}


class ViewerError(Exception):
    pass


class ViewerLauncher:
    """Launches viewer sessions against composed upstream viewers."""

    def __init__(self, pacs_registry=None, *, viewer_base=None,
                 slide_base=None):
        self.registry = pacs_registry
        self.viewer_base = viewer_base  # OHIF deployment root URL
        self.slide_base = slide_base    # tile-server root URL
        self.sessions = []              # append-only audit
        self._seq = 0

    # -- launches ---------------------------------------------------

    def launch_study(self, study_uid, *, reader_id, purpose, day,
                     case_id=None):
        """Build an OHIF-shaped launch manifest for a DICOM study."""
        if self.registry is not None:
            self.registry._study(study_uid)  # raises if unknown
        if not self.viewer_base:
            raise ViewerError(
                "no viewer endpoint configured — refusing to pretend. "
                "Deploy OHIF (Apache-2.0) beside the archive and set "
                "viewer_base; a grey box where the pixels should be is "
                "a lie with a toolbar")
        if not purpose:
            raise ViewerError("a launch without a purpose is a browse — "
                              "state the clinical question")
        hanging = None
        key_images = []
        if self.registry is not None:
            try:
                hanging = self.registry.hanging_protocol(study_uid)
            except Exception:
                hanging = None  # no protocol on file — stated, not faked
            key_images = self.registry.key_images(study_uid)
        manifest = {
            "engine": VIEWER_ENGINES["dicom"]["engine"],
            "study_uid": study_uid,
            "url": (f"{self.viewer_base}/viewer"
                    f"?StudyInstanceUIDs={study_uid}"),
            "hanging": hanging,
            "key_images": key_images,
            "viewer_version": VIEWER_VERSION,
        }
        return self._record(manifest, reader_id=reader_id,
                            purpose=purpose, day=day, case_id=case_id,
                            artifact=study_uid, kind="dicom")

    def launch_slide(self, slide_id, tile_source, *, reader_id, purpose,
                     day, case_id=None):
        """Build an OpenSeadragon-shaped manifest for a whole slide."""
        if not self.slide_base:
            raise ViewerError(
                "no tile-server endpoint configured — refusing to "
                "pretend. Set slide_base to the DZI/IIIF root")
        if not tile_source:
            raise ViewerError("a slide launch without a tile source is "
                              "a magnifying glass over an empty desk")
        if not purpose:
            raise ViewerError("a launch without a purpose is a browse — "
                              "state the clinical question")
        manifest = {
            "engine": VIEWER_ENGINES["slide"]["engine"],
            "slide_id": slide_id,
            "url": f"{self.slide_base}/{tile_source}",
            "viewer_version": VIEWER_VERSION,
        }
        return self._record(manifest, reader_id=reader_id,
                            purpose=purpose, day=day, case_id=case_id,
                            artifact=slide_id, kind="slide")

    # -- audit ------------------------------------------------------

    def _record(self, manifest, *, reader_id, purpose, day, case_id,
                artifact, kind):
        self._seq += 1
        s = {"session_id": f"VS-{self._seq}", "kind": kind,
             "artifact": artifact, "case_id": case_id,
             "reader": reader_id, "purpose": purpose, "day": day,
             "manifest": manifest}
        self.sessions.append(s)
        return {"session_id": s["session_id"], "manifest": manifest}

    def sessions_for(self, artifact):
        return [dict(s) for s in self.sessions
                if s["artifact"] == artifact]

    def viewed_by(self, artifact, reader_id):
        """Did THIS reader open THIS artifact? Absence is not a view."""
        return any(s["artifact"] == artifact and s["reader"] == reader_id
                   for s in self.sessions)

    def coverage_report(self, verified_reports, case_artifacts):
        """The paper-exercise detector: of all verified reports on
        cases WITH a viewable artifact, how many were verified by a
        reader who never launched it? Rate ships with its denominator.
        verified_reports: iterable of dicts with case_id / reader /
        state == 'verified' (or addended).
        case_artifacts: {case_id: study_uid-or-slide_id}."""
        total = 0
        unviewed = []
        for rep in verified_reports:
            artifact = case_artifacts.get(rep["case_id"])
            if not artifact:
                continue  # nothing to open — no viewability claim
            if rep.get("state") not in ("verified", "addended"):
                continue
            total += 1
            if not self.viewed_by(artifact, rep["reader"]):
                unviewed.append(rep["case_id"])
        return {
            "verified_with_artifact": total,
            "verified_without_launch": len(unviewed),
            "unviewed_cases": sorted(unviewed),
            "viewed_rate": (round((total - len(unviewed)) / total, 3)
                            if total else None),
            "version": VIEWER_VERSION,
        }
