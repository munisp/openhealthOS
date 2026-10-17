/* Patient app logic conformance. Run: node --test apps/patient/tests/ */
import { test } from "node:test";
import assert from "node:assert/strict";
import { kindMeta, groupFeed, relTime, stateLabel, KINDS } from "../js/logic.js";

const NOW = Date.parse("2026-10-18T12:00:00Z");

test("every notification kind has metadata; unknown falls back", () => {
  for (const k of ["appointment", "lab_ready", "pickup", "referral", "immunization"]) {
    assert.ok(KINDS[k], k);
    assert.ok(kindMeta(k).titleKey);
  }
  assert.equal(kindMeta("telemetry").titleKey, "kind_general");
});

test("groupFeed separates fresh action items from history", () => {
  const feed = [
    { kind: "pickup", created_at: "2026-10-17T09:00:00Z" },          // fresh, action
    { kind: "lab_ready", created_at: "2026-10-01T09:00:00Z" },        // stale >14d → info
    { kind: "referral", created_at: "2026-10-18T06:00:00Z" },         // fresh, action
    { kind: "appointment", created_at: "2026-10-16T09:00:00Z" },      // appointment → info
    { kind: "immunization", created_at: "2026-10-10T09:00:00Z" },     // info
  ];
  const { action, info } = groupFeed(feed, NOW);
  assert.deepEqual(action.map((n) => n.kind), ["pickup", "referral"]);
  assert.equal(info.length, 3);
});

test("groupFeed tolerates empty and malformed input", () => {
  assert.deepEqual(groupFeed(null, NOW), { action: [], info: [] });
  const { action, info } = groupFeed([{ kind: "pickup" }], NOW); // no created_at
  assert.equal(action.length + info.length, 1);
});

test("relTime buckets", () => {
  assert.equal(relTime("2026-10-18T10:00:00Z", NOW), "today");
  assert.equal(relTime("2026-10-17T10:00:00Z", NOW), "yesterday");
  assert.equal(relTime("2026-10-15T10:00:00Z", NOW), "3 days ago");
  assert.equal(relTime("2026-09-01T10:00:00Z", NOW), new Date("2026-09-01T10:00:00Z").toLocaleDateString());
  assert.equal(relTime("garbage", NOW), "");
});

test("state labels map to known keys", () => {
  assert.equal(stateLabel("sent"), "sent");
  assert.equal(stateLabel("failed"), "failed");
  assert.equal(stateLabel("queued"), "pending");
});
