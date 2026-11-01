#!/usr/bin/env python3
"""cli.py — resultloop persistence seam, same contract as teleread/cli.py:

    cli.py DBFILE COMMAND '<json-args>'

The notification rail endpoint comes from the environment:

    RESULTLOOP_CHANNEL — e.g. http://notification-service:8080/notify

Without it the channel is an honest spike: deliveries queue and nothing
claims 'sent'.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resultloop import ChannelAdapter, LoopError, ResultLoop  # noqa: E402


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


def build(dbfile):
    state = load(dbfile)
    deliveries = state.get("deliveries", {})
    # tuples survive JSON as lists; restore the timeline's shape
    for d in deliveries.values():
        d["timeline"] = [tuple(t) for t in d["timeline"]]
    return ResultLoop(
        channel=ChannelAdapter(os.environ.get("RESULTLOOP_CHANNEL")),
        deliveries=deliveries)


def run(dbfile, command, args):
    loop = build(dbfile)
    dirty = True
    if command == "dispatch":
        result = loop.dispatch(
            case_id=args["case_id"], facility=args["facility"],
            referrer=args["referrer"], critical=args.get("critical",
                                                         False),
            day=args["day"], content_ref=args["content_ref"],
            urgency=args.get("urgency"))
    elif command == "mark-sent":
        result = loop.mark_sent(args["delivery_id"], day=args["day"],
                                receipt_ref=args["receipt_ref"])
    elif command == "mark-delivered":
        result = loop.mark_delivered(args["delivery_id"], day=args["day"],
                                     receipt_ref=args["receipt_ref"])
    elif command == "ack":
        result = loop.acknowledge(args["delivery_id"], day=args["day"],
                                  by=args["by"],
                                  delegate_reason=args.get(
                                      "delegate_reason"))
    elif command == "unclosed":
        result = loop.unclosed(args["day"])
        dirty = False
    elif command == "escalate":
        result = loop.escalate(args["day"])
    elif command == "stats":
        result = loop.loop_stats()
        dirty = False
    else:
        raise LoopError(f"unknown command: {command}")
    return result, (loop if dirty else None)


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
        result, loop = run(dbfile, command, args)
    except (LoopError, KeyError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(2)
    if loop is not None:
        state = load(dbfile)
        state["deliveries"] = loop.deliveries
        save(dbfile, state)
    print(json.dumps({"ok": True, "result": result}, sort_keys=True,
                     default=list))


if __name__ == "__main__":
    main()
