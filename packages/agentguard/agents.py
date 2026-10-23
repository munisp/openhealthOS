"""agents.py — the seven doctor-gap agents, all inside the guard envelope.

Deterministic cores are the shipped behaviour; the provider (when live)
polishes language, never facts. Every clinical claim carries a source
reference; every action is a draft awaiting a scoped human.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guard import Agent  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "smartflow"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "protocolpack"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "protocolpack", "packs"))


# ---- 1. intake-and-summarise ----
class Summariser(Agent):
    NAME = "summariser"
    SCOPES = frozenset({"summary"})
    REQUIRES_HUMAN_SCOPE = "chart:read"

    def summarise(self, patient_ref, history, now=None):
        """history: [{at, type, summary}] record entries. Every sentence
        cites its source index — a claim without a source is dropped."""
        claims = []
        for i, e in enumerate(history[-8:]):  # recency-bounded
            claims.append({"text": e["summary"], "source": i})
        llm_active = bool(self.provider and getattr(self.provider, "available", False))
        if llm_active:
            prose = self.provider.complete(
                "summarise clinically, cite [n] per claim: "
                + "; ".join(f"[{i}] {c['text']}" for i, c in enumerate(claims)))
        else:
            prose = " ".join(f"{c['text']} [{c['source']}]." for c in claims)
        return self.draft("summary", {"patient_ref": patient_ref,
                                      "prose": prose, "claims": claims,
                                      "regime": "model" if llm_active
                                      else "template"}, now)


# ---- 2. documentation scribe ----
class Scribe(Agent):
    NAME = "scribe"
    SCOPES = frozenset({"coded-draft", "clarification-question"})
    REQUIRES_HUMAN_SCOPE = "chart:write"

    def transcribe(self, narrative, now=None):
        import capture
        d = capture.capture(narrative)
        # the payload IS the capture draft: fields + warnings + provenance
        draft = self.draft("coded-draft", d, now)
        # ambiguity -> ONE question, never a guess
        if d["unmapped"]:
            q = self.draft("clarification-question", {
                "about": [m["text"] for m in d["unmapped"]],
                "question": "I could not code: "
                            + ", ".join(m["text"] for m in d["unmapped"])
                            + " — what did you mean?"}, now)
            return {"draft": draft, "question": q}
        return {"draft": draft}


# ---- 3. care-coordination shepherd ----
class Shepherd(Agent):
    NAME = "shepherd"
    SCOPES = frozenset({"action-draft"})
    REQUIRES_HUMAN_SCOPE = "workflow:manage"

    RULES = {  # event_type -> drafted follow-up action
        "result_released": "draft.patient-notification",
        "referral_accepted": "draft.receiving-facility-bundle",
        "appointment_missed": "draft.reschedule-offer",
    }

    def on_event(self, event, now=None):
        kind = self.RULES.get(event.get("type"))
        if not kind:
            return None  # not everything needs shepherding — silence ok
        return self.draft("action-draft", {"trigger": event["type"],
                                           "action": kind,
                                           "patient_ref": event.get("patient_ref")}, now)


# ---- 4. formulary/stock watchdog ----
class Watchdog(Agent):
    NAME = "watchdog"
    SCOPES = frozenset({"restock-draft", "redistribution-draft"})
    REQUIRES_HUMAN_SCOPE = "pharmacy:manage"

    def assess(self, stock, consumption, expiring, now=None):
        import restock
        drafts = []
        r = restock.draft(stock, consumption)
        if r["lines"]:
            drafts.append(self.draft("restock-draft", r, now))
        for e in expiring:  # {item, qty, days_left, facilities_burning}
            if e["days_left"] <= 60 and e.get("facilities_burning"):
                drafts.append(self.draft("redistribution-draft", {
                    "item": e["item"], "qty": e["qty"],
                    "to": e["facilities_burning"][0],
                    "reason": f"expires in {e['days_left']}d"}, now))
        return drafts


# ---- 5. protocol co-pilot (CHW force multiplier) ----
class CoPilot(Agent):
    NAME = "copilot"
    SCOPES = frozenset({"pack-answer", "escalation-advice"})
    REQUIRES_HUMAN_SCOPE = "protocol:execute"

    def __init__(self, key, provider=None, packs=None):
        super().__init__(key, provider)
        self.packs = packs or {}

    def ask(self, pack_name, question, now=None):
        """Answers cite pack version + step. No citation, no answer."""
        pack = self.packs.get(pack_name)
        if not pack:
            return self.draft("pack-answer", {
                "answer": None, "reason": "pack-not-loaded"}, now)
        q = question.lower()
        for sid, step in pack["steps"].items():
            if any(w in q for w in step["text"].lower().split()[:4]):
                return self.draft("pack-answer", {
                    "answer": step["text"],
                    "citation": {"pack": pack["version"], "step": sid}}, now)
        return self.draft("pack-answer", {
            "answer": "not covered by the standing order — escalate",
            "citation": {"pack": pack["version"], "step": None},
            "escalate": True}, now)

    def sniff(self, session_path, now=None):
        """Escalation advocacy: the co-pilot may only ever argue FOR
        escalation, never against — narrowing scope is its only power."""
        n = len(session_path)
        if n >= 4:  # long wandering sessions correlate with hard cases
            return self.draft("escalation-advice", {
                "advice": "long protocol path — consider referral",
                "direction": "escalate-only"}, now)
        return None


# ---- 6. population-health scout ----
class Scout(Agent):
    NAME = "scout"
    SCOPES = frozenset({"signal-draft", "bulletin-draft"})
    REQUIRES_HUMAN_SCOPE = "publichealth:publish"

    def scan(self, weekly_counts, baseline, now=None):
        """weekly_counts: {signal: [w1, w2, ...]} gold-layer aggregates.
        Uptick = latest week > 2x trailing baseline. Drafts only."""
        drafts = []
        for sig, weeks in weekly_counts.items():
            if len(weeks) < 3:
                continue
            base = baseline.get(sig, sum(weeks[:-1]) / len(weeks[:-1]))
            if base > 0 and weeks[-1] > 2 * base:
                drafts.append(self.draft("signal-draft", {
                    "signal": sig, "this_week": weeks[-1],
                    "baseline": round(base, 1),
                    "ratio": round(weeks[-1] / base, 2)}, now))
        return drafts


# ---- 7. training mentor ----
class Mentor(Agent):
    NAME = "mentor"
    SCOPES = frozenset({"teaching-answer", "competency-note"})
    REQUIRES_HUMAN_SCOPE = "protocol:execute"

    def __init__(self, key, provider=None, packs=None):
        super().__init__(key, provider)
        self.packs = packs or {}

    def teach(self, question, now=None):
        """Grounded in versioned packs ONLY. Best-match by keyword
        overlap; uncitable = unanswered."""
        q = question.lower()
        best = None  # (score, pack_name, pack, sid, step)
        for name, pack in self.packs.items():
            for sid, step in pack["steps"].items():
                words = [w.strip("—,?.") for w in step["text"].lower().split()
                         if len(w) > 4]
                score = sum(1 for w in words if w in q)
                if score and (best is None or score > best[0]):
                    best = (score, name, pack, sid, step)
        if best:
            _, name, pack, sid, step = best
            return self.draft("teaching-answer", {
                "answer": step["text"],
                "citation": {"pack": pack["version"], "step": sid},
                "protocol": name}, now)
        return self.draft("teaching-answer", {
            "answer": None,
            "reason": "no pack citation found — a mentor that cannot "
                      "cite its source does not answer"}, now)

    def assess(self, trainee, sim_results, now=None):
        """sim_results: {scenario: passed bool} -> competency gaps note
        for the HR dashboard. Aggregate competency data, no patients."""
        gaps = [s for s, ok in sim_results.items() if not ok]
        return self.draft("competency-note", {
            "trainee": trainee, "gaps": gaps,
            "ready": not gaps}, now)
