#!/usr/bin/env python3
"""cli.py — reportkit persistence seam, same contract as teleread/cli.py:

    cli.py DBFILE COMMAND '<json-args>'

Loads state, executes, saves atomically (os.replace). Failures exit 2
with {"ok": false, "error": ...}; engine-down vs domain-error mapping
lives in the Go service.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reportkit import ReportKit, ReportKitError  # noqa: E402


def load(dbfile):
    if not os.path.exists(dbfile):
        return {}
    with open(dbfile) as f:
        return json.load(f)


def save(dbfile, state):
    tmp = dbfile + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, dbfile)


def run(dbfile, command, args):
    state = load(dbfile)
    key = os.environ.get("REPORTKIT_SIGNING_KEY", "").encode() or None
    rk = ReportKit(signing_key=key, reports=state.get("reports", {}))

    if command == "templates":
        return rk.templates(), None
    if command == "template":
        return rk.template(args["template_id"]), None
    if command == "draft":
        return rk.draft(args["report_id"], template_id=args["template_id"],
                        answers=args["answers"], case_id=args["case_id"],
                        author_id=args["author_id"], day=args["day"]), rk
    if command == "sign":
        return rk.sign(args["report_id"], signer_id=args["signer_id"],
                       day=args["day"]), rk
    if command == "verify":
        return rk.verify(args["report_id"]), None
    if command == "export-fhir":
        return rk.export_fhir(args["report_id"]), None
    if command == "export-html":
        return {"html": rk.export_html(args["report_id"])}, None
    if command == "report":
        return dict(rk._report(args["report_id"])), None
    raise ReportKitError(f"unknown command: {command}")


def main():
    if len(sys.argv) != 4:
        print(json.dumps({"ok": False,
                          "error": "usage: cli.py DBFILE COMMAND JSON"}))
        sys.exit(2)
    dbfile, command = sys.argv[1], sys.argv[2]
    try:
        args = json.loads(sys.argv[3])
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"bad JSON: {e}"}))
        sys.exit(2)
    try:
        result, rk = run(dbfile, command, args)
    except (ReportKitError, KeyError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(2)
    if rk is not None:
        state = load(dbfile)
        state["reports"] = rk.reports
        save(dbfile, state)
    print(json.dumps({"ok": True, "result": result}, sort_keys=True))


if __name__ == "__main__":
    main()
