#!/usr/bin/env python3
"""cli.py — viewlink persistence seam (audit log), same contract as
teleread/cli.py. Viewer base URLs come from the environment:

    OHIF_BASE   — OHIF deployment against the dcm4chee DICOMweb source
    SLIDE_BASE  — OpenSeadragon deployment for whole-slide images
    ECG_BASE    — waveform canvas page

    cli.py DBFILE launch '{"kind":"dicom-ohif", ...}'
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from viewlink import ViewerLauncher, ViewLinkError  # noqa: E402


def load(dbfile):
    if not os.path.exists(dbfile):
        return []
    with open(dbfile) as f:
        return json.load(f)


def save(dbfile, audit):
    tmp = dbfile + ".tmp"
    with open(tmp, "w") as f:
        json.dump(audit, f)
    os.replace(tmp, dbfile)


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
    audit = load(dbfile)
    v = ViewerLauncher(ohif_base=os.environ.get("OHIF_BASE"),
                       slide_base=os.environ.get("SLIDE_BASE"),
                       ecg_base=os.environ.get("ECG_BASE"), audit=audit)
    try:
        if command == "launch":
            result = v.launch(args["kind"], artifact_ref=args["artifact_ref"],
                              reader_id=args["reader_id"],
                              session_id=args["session_id"],
                              case_id=args["case_id"], day=args["day"])
            save(dbfile, audit)
        elif command == "access-log":
            result = v.access_log(case_id=args.get("case_id"),
                                  reader_id=args.get("reader_id"))
        elif command == "configured":
            result = {"configured": v.configured(args["kind"])}
        else:
            raise ViewLinkError(f"unknown command: {command}")
    except (ViewLinkError, KeyError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(2)
    print(json.dumps({"ok": True, "result": result}, sort_keys=True))


if __name__ == "__main__":
    main()
