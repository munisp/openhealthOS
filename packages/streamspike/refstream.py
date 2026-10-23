"""spike.py — stdlib reference engine for the ADR-0066 stream contract.

Pins the invariants the Fluvio consumer path must honour, independent of
any cluster: per-key ordering, at-least-once delivery with consumer-side
dedupe on event id, and replay-from-offset after a consumer restart.
The live path (run_fluvio.sh) diffs real cluster output against the
goldens this engine produces.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


class RefTopic:
    """Append-only log with keyed records — the minimal honest model of
    one Fluvio topic partition."""

    def __init__(self, name):
        self.name = name
        self.log = []  # list of {offset, key, event}

    def produce(self, key, event):
        rec = {"offset": len(self.log), "key": key, "event": event}
        self.log.append(rec)
        return rec["offset"]


class RefConsumer:
    """At-least-once consumer: tracks a committed offset, dedupes on
    event id, can be restarted and replayed from any offset."""

    def __init__(self, topic):
        self.topic = topic
        self.offset = 0
        self.seen = set()
        self.delivered = []

    def poll(self):
        """Deliver everything past the committed offset. At-least-once:
        duplicates are possible if the caller replays; dedupe is the
        CONSUMER's job (event id), never the log's."""
        out = []
        while self.offset < len(self.topic.log):
            rec = self.topic.log[self.offset]
            eid = rec["event"].get("event_id")
            if eid not in self.seen:
                self.seen.add(eid)
                self.delivered.append(rec["event"])
                out.append(rec["event"])
            self.offset += 1
        return out

    def replay_from(self, offset):
        """Consumer restart: re-read from an earlier offset; dedupe
        guarantees no event is applied twice downstream."""
        assert 0 <= offset <= len(self.topic.log)
        self.offset = offset
        return self.poll()


def per_key_order_ok(topic):
    """Within a key, offsets must be monotonically non-decreasing in
    event sequence numbers (producer-assigned `seq`)."""
    last = {}
    for rec in topic.log:
        k, seq = rec["key"], rec["event"].get("seq", 0)
        if k in last and seq < last[k]:
            return False
        last[k] = seq
    return True


def load_fixture(path=None):
    path = path or os.path.join(HERE, "fixture_events.jsonl")
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def build_stream(events, topic_name="lakefeed.clinical"):
    t = RefTopic(topic_name)
    for ev in events:
        t.produce(ev["facility_id"], ev)
    return t
