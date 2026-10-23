"""day_in_life.py — end-to-end composition proof.

One continuous flow across six packages, asserting they compose into a
single accountable day at a 1-doctor LGA:

  07:10  CHW runs a live (signed-off) fever protocol; RDT negative
         -> protocol escalates
  07:12  escalation becomes a signed case bundle -> mesh -> doctor queue
  08:30  doctor reviews: take-over, referral needed
  08:31  referral router finds the facility with attested capability
  08:32  shepherd drafts the receiving-facility bundle + notification
  08:33  summariser drafts the chart summary for the receiving doctor
  09:00  patient arrives; queueintel places them by triage class

Every artifact is signed, every escalation is recorded, every draft
waits for its human. If any seam breaks, this suite fails — that is
what 'the platform' means: not ten packages, one flow.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for pkg in ("protocolpack", "protocolpack/packs", "telesupervision",
            "queueintel", "referral", "agentguard", "smartflow"):
    sys.path.insert(0, os.path.join(ROOT, pkg))

from packreview import PackRegistry  # noqa: E402
from packs.ng_standing_orders import PACKS  # noqa: E402
from telesupervision import CaseBundle, ReviewBoard  # noqa: E402
from router import CapabilityRegistry, route  # noqa: E402
from agents import Shepherd, Summariser  # noqa: E402
from queueintel import Queue  # noqa: E402

KEY = b"lga-shared-hmac-key-32-bytes-00000"
DAY0 = 1761609600  # a Tuesday, in day-epoch terms


def run_day():
    log = {}

    # --- governance first: the pack must be LIVE ---
    reg = PackRegistry(KEY)
    reg.register("fever", PACKS["fever"], author="clinical-lead", now=1)
    r = reg.review("fever", reviewer="dr-eze", role="consultant",
                   approve=True, note="reviewed against national standing orders", now=2)
    assert r["ok"], r
    log["pack_status"] = reg.packs["fever"]["status"]

    # --- 07:10 CHW protocol session ---
    sess = reg.session("fever", "chw")  # live pack: allowed
    sess.answer("2-7 days")
    sess.answer("no")
    st = sess.answer("negative")  # RDT negative -> escalate
    assert st["escalate"] and st["outcome"] == "refer-for-review"
    log["session"] = st

    # --- 07:12 case bundle to the doctor ---
    bundle = CaseBundle.create("chw-amina", "pseudo-0042", {
        "protocol_outcome": st, "vitals": {"temp": 38.4},
        "pack_status": sess.pack_status}, KEY, now=3)
    board = ReviewBoard(KEY)
    assert board.submit(bundle)
    q = board.queue()
    assert len(q["escalations"]) == 1 and not q["quarantined"]

    # --- 08:30 doctor reviews: take over, refer for malaria smear ---
    rev = board.review(bundle["sig"], "dr-eze", "take-over",
                       "needs microscopy + possible IM antimalarial", KEY, now=4)
    assert rev["ok"]

    # --- 08:31 route the referral ---
    caps = CapabilityRegistry()
    caps.attest("OHF-LGA-GENERAL", "microscopy", DAY0 - 10)
    caps.attest("OHF-OLD-CLINIC", "microscopy", DAY0 - 200)  # stale
    rt = route("microscopy", caps, today=DAY0, fallback="state-general")
    assert rt["ok"] and rt["facility"] == "OHF-LGA-GENERAL"
    assert rt["stale_noted"] == ["OHF-OLD-CLINIC"]
    log["referral"] = rt

    # --- 08:32 shepherd drafts the follow-ups ---
    sh = Shepherd(KEY)
    d1 = sh.on_event({"type": "referral_accepted",
                      "patient_ref": "pseudo-0042"}, now=5)
    d2 = sh.on_event({"type": "result_released",
                      "patient_ref": "pseudo-0042"}, now=6)
    assert d1 and d2
    # the drafts become real only on a scoped human's confirmation
    c = sh.confirm(d1, "nurse-blessing", {"workflow:manage"}, now=7)
    assert c["ok"]
    log["shepherd"] = {"drafted": 2, "confirmed": 1}

    # --- 08:33 receiving doctor gets a cited summary ---
    summ = Summariser(KEY).summarise("pseudo-0042", [
        {"at": 1, "type": "encounter", "summary": "fever 3 days, RDT negative"},
        {"at": 2, "type": "review", "summary": "consultant take-over, refer for smear"},
    ], now=8)
    assert "[0]" in summ["payload"]["prose"]
    log["summary_regime"] = summ["payload"]["regime"]

    # --- 09:00 arrival queue: triage class, not arrival order ---
    qn = Queue()
    qn.arrive("pseudo-0099", "routine", now=0)
    qn.arrive("pseudo-0042", "visit", now=1)
    nxt = qn.serve(now=2)
    assert nxt["patient_ref"] == "pseudo-0042"
    log["queue_first"] = nxt["patient_ref"]

    return log


if __name__ == "__main__":
    import json
    print(json.dumps(run_day(), indent=2, default=str))
