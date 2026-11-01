"""cli.py — JSON-in/JSON-out CLI for the teleread engine.

This is the persistence + service seam: Go services (and demo scripts)
call `python3 cli.py DBFILE COMMAND '<json-args>'`. State lives in one
JSON document (boring, inspectable, backup-friendly); every invocation
loads → executes → saves, so a crash never loses more than the failed
command. Domain errors exit 2 with {"ok": false, "error": ...} —
fail-loud, never a silent 200.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from teleread import ReaderRegistry, ReadingService, TeleReadError  # noqa: E402
from domains import DOMAINS  # noqa: E402


def load(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"readers": {}, "services": {}}


def save(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, path)   # atomic: a torn write never corrupts


def build(state, domain):
    if domain not in DOMAINS:
        raise TeleReadError(f"unknown domain: {domain} "
                            f"(known: {sorted(DOMAINS)})")
    readers = ReaderRegistry()
    readers.readers = state["readers"]
    svc = ReadingService(DOMAINS[domain], readers)
    s = state["services"].get(domain)
    if s:
        svc.cases, svc.reports = s["cases"], s["reports"]
        svc.sessions, svc.escalations = s["sessions"], s["escalations"]
        svc.transfers, svc._n = s["transfers"], s["n"]
    return svc


def store(state, domain, svc):
    state["services"][domain] = {
        "cases": svc.cases, "reports": svc.reports,
        "sessions": svc.sessions, "escalations": svc.escalations,
        "transfers": svc.transfers, "n": svc._n}


def cmd_register_reader(state, a):
    readers = ReaderRegistry()
    readers.readers = state["readers"]
    out = readers.register(a["reader_id"], licence_id=a["licence_id"],
                           licence_expiry_day=a["licence_expiry_day"],
                           scopes=a["scopes"],
                           home_site=a.get("home_site"))
    state["readers"] = readers.readers
    return out


def cmd_order(state, a):
    svc = build(state, a["domain"])
    out = svc.order(a["case_id"], priority=a["priority"],
                    question=a["question"], facility=a["facility"],
                    referrer=a["referrer"], day=a["day"],
                    scope=a.get("scope"), artifact=a.get("artifact"),
                    meta=a.get("meta"))
    store(state, a["domain"], svc)
    return out


def cmd_assign(state, a):
    svc = build(state, a["domain"])
    out = svc.assign(a["case_id"], day=a["day"],
                     candidates=a.get("candidates"),
                     prefer=a.get("prefer"))
    store(state, a["domain"], svc)
    return out


def cmd_worklist(state, a):
    return build(state, a["domain"]).worklist(a["reader_id"],
                                              day=a["day"])


def cmd_session(state, a):
    svc = build(state, a["domain"])
    out = svc.open_session(a["reader_id"], a["case_id"], day=a["day"],
                           location=a["location"],
                           network=a.get("network", "offline"))
    store(state, a["domain"], svc)
    return out


def cmd_draft(state, a):
    svc = build(state, a["domain"])
    out = svc.draft_report(a["case_id"], reader_id=a["reader_id"],
                           findings=a["findings"],
                           impression=a["impression"], day=a["day"],
                           critical_findings=a.get("critical_findings",
                                                   ()),
                           template_version=a["template_version"])
    store(state, a["domain"], svc)
    return out


def cmd_verify(state, a):
    svc = build(state, a["domain"])
    out = svc.verify_report(a["case_id"], reader_id=a["reader_id"],
                            day=a["day"])
    store(state, a["domain"], svc)
    return out


def cmd_addendum(state, a):
    svc = build(state, a["domain"])
    out = svc.add_addendum(a["case_id"], reader_id=a["reader_id"],
                           day=a["day"], text=a["text"],
                           reason=a["reason"])
    store(state, a["domain"], svc)
    return out


def cmd_ack(state, a):
    svc = build(state, a["domain"])
    out = svc.acknowledge_critical(a["case_id"], day=a["day"],
                                   by=a["by"])
    store(state, a["domain"], svc)
    return out


def cmd_unack(state, a):
    svc = build(state, a["domain"])
    return svc.unacknowledged_criticals(a["day"], a.get("hours", 4))


def cmd_turnaround(state, a):
    return build(state, a["domain"]).turnaround(a["day"])


def cmd_second_opinion(state, a):
    svc = build(state, a["domain"])
    out = svc.request_second_opinion(a["case_id"],
                                     requested_by=a["requested_by"],
                                     day=a["day"], reason=a["reason"])
    store(state, a["domain"], svc)
    return out


def cmd_transfer(state, a):
    svc = build(state, a["domain"])
    out = svc.transfer_case(a["case_id"], destination=a["destination"],
                            day=a["day"], release_id=a["release_id"],
                            bundle_hash=a.get("bundle_hash"),
                            expected_hash=a.get("expected_hash"))
    store(state, a["domain"], svc)
    return out


def cmd_result_line(state, a):
    return build(state, a["domain"]).result_ready_line(a["case_id"],
                                                       day=a["day"])


def cmd_case(state, a):
    svc = build(state, a["domain"])
    c = dict(svc._case(a["case_id"]))
    if a["case_id"] in svc.reports:
        c["report"] = svc.reports[a["case_id"]]
    return c


def cmd_domains(state, a):
    return {d: {"scopes": c.scopes, "sla_hours": c.sla_hours,
                "critical_findings": c.critical_findings}
            for d, c in DOMAINS.items()}


COMMANDS = {
    "register-reader": cmd_register_reader, "order": cmd_order,
    "assign": cmd_assign, "worklist": cmd_worklist, "session": cmd_session,
    "draft": cmd_draft, "verify": cmd_verify, "addendum": cmd_addendum,
    "ack": cmd_ack, "unack": cmd_unack, "turnaround": cmd_turnaround,
    "second-opinion": cmd_second_opinion, "transfer": cmd_transfer,
    "result-line": cmd_result_line, "case": cmd_case,
    "domains": cmd_domains,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False,
                          "error": "usage: cli.py DBFILE COMMAND "
                                   "'<json-args>'"}))
        sys.exit(2)
    db, cmd = sys.argv[1], sys.argv[2]
    args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    if cmd not in COMMANDS:
        print(json.dumps({"ok": False,
                          "error": f"unknown command: {cmd}"}))
        sys.exit(2)
    state = load(db)
    try:
        out = COMMANDS[cmd](state, args)
    except TeleReadError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(2)
    except KeyError as e:
        print(json.dumps({"ok": False,
                          "error": f"missing field: {e}"}))
        sys.exit(2)
    save(db, state)
    print(json.dumps({"ok": True, "result": out}, sort_keys=True))


if __name__ == "__main__":
    main()
