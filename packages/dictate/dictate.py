"""dictate.py — the voice seam: speech in, structured DRAFT out.

Doctrine:
- THE DRAFT IS THE CEILING. Dictation and ambient drafting produce
  reported-draft state and nothing more. Verification stays a human
  act with a credential behind it; no transcription engine, however
  good, signs.
- PROVENANCE OR IT DIDN'T HAPPEN: every dictated draft names its
  engine and engine version. A draft without provenance is a rumour.
- TRANSCRIPTION vs GENERATION are different risk postures:
  transcription renders the reader's own words; ambient-generative
  engines compose from the encounter and must be registered with
  aigov first — unregistered generative engines refuse.
- THE READER'S WORDS ARE THE READER'S: the transcript is stored
  verbatim alongside the draft; a revision is a new version, never
  an overwrite. What the clinician actually said is the audit anchor
  if the composed text is ever disputed.
"""

DICTATE_VERSION = "dictate@1.0.0"

ENGINE_KINDS = ("transcription", "ambient-generative")


class DictateError(Exception):
    pass


class DictationService:
    """Speech-to-draft seam over a teleread ReadingService, with an
    optional aigov ModelRegistry gate for generative engines."""

    def __init__(self, reading_svc, model_registry=None):
        self.svc = reading_svc
        self.aigov = model_registry
        self.sessions = []   # append-only dictation audit

    def dictate(self, case_id, *, reader_id, transcript, engine,
                engine_version, engine_kind="transcription", day,
                findings=None, impression=None,
                template_version=None):
        """Turn a transcript into a structured DRAFT.

        findings/impression default to the transcript itself when the
        engine only transcribes (the reader's words ARE the draft); a
        generative engine must supply composed fields explicitly.
        """
        if engine_kind not in ENGINE_KINDS:
            raise DictateError(f"unknown engine kind: {engine_kind}")
        if not engine or not engine_version:
            raise DictateError(
                "engine and engine version required — a draft without "
                "provenance is a rumour")
        if not transcript or not transcript.strip():
            raise DictateError("empty transcript — silence is not a "
                               "report")
        if engine_kind == "ambient-generative":
            if self.aigov is None:
                raise DictateError(
                    "ambient-generative engines require an aigov "
                    "registry — unregistered generative models do not "
                    "compose clinical text here")
            m = self.aigov.models.get((engine, engine_version)) \
                if hasattr(self.aigov, "models") else None
            if m is None:
                raise DictateError(
                    f"{engine}@{engine_version} is not registered "
                    "with aigov — register the model (licence class, "
                    "task, evidence) before it touches a chart")
            if m.get("suspended"):
                raise DictateError(
                    f"{engine}@{engine_version} is suspended — a "
                    "stopped model stays stopped until re-cleared")
            if not findings or not impression:
                raise DictateError(
                    "a generative engine must supply the composed "
                    "findings and impression explicitly — the "
                    "transcript is the encounter, not the report")
        findings = findings if findings is not None else transcript
        impression = impression if impression is not None else transcript
        if template_version is None:
            raise DictateError("report template version required — "
                               "an unversioned draft is an "
                               "unaccountable one")

        # the engine underneath enforces: only the assigned reader
        # drafts, credential gates, template binding — this seam adds
        # provenance, never weakens the engine's refusals
        rep = self.svc.draft_report(
            case_id, reader_id=reader_id, findings=findings,
            impression=impression, template_version=template_version,
            day=day)
        session = {"case_id": case_id, "reader_id": reader_id,
                   "engine": engine, "engine_version": engine_version,
                   "engine_kind": engine_kind, "day": day,
                   "transcript": transcript,  # verbatim, the anchor
                   "report_id": rep["report_id"],
                   "version": DICTATE_VERSION}
        self.sessions.append(session)
        return {"report_id": rep["report_id"], "state": "draft",
                "provenance": {"engine": engine,
                               "engine_version": engine_version,
                               "kind": engine_kind},
                "note": "the draft is the ceiling — verification is a "
                        "human act with a credential behind it"}

    def provenance_report(self):
        """Engine mix with denominators — who drafts with what."""
        total = len(self.sessions)
        by_engine = {}
        for s in self.sessions:
            key = f"{s['engine']}@{s['engine_version']}"
            by_engine[key] = by_engine.get(key, 0) + 1
        gen = sum(1 for s in self.sessions
                  if s["engine_kind"] == "ambient-generative")
        return {"dictated_drafts": total, "by_engine": by_engine,
                "generative_share": (round(gen / total, 3)
                                     if total else None),
                "version": DICTATE_VERSION,
                "note": "every one of these is a draft; the verified "
                        "count lives in the reading service, with its "
                        "own denominator"}
