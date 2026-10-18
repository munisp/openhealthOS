/* Self-service care helpers (ADR-0017): upcoming/past appointment
 * shaping, the coverage summary, bookable-slot filtering, next-due. */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  upcomingAppointments, pastAppointments, coverageSummary, bookableSlots, nextDue, groupRecord,
} from "../js/logic.js";

const NOW = Date.parse("2026-10-18T09:00:00Z");

test("upcomingAppointments keeps future booked visits, soonest first", () => {
  const appts = [
    { id: "A2", status: "booked", start: "2026-10-20T08:00:00Z" },
    { id: "A1", status: "booked", start: "2026-10-19T08:00:00Z" },
    { id: "A3", status: "cancelled", start: "2026-10-21T08:00:00Z" },
    { id: "A4", status: "booked", start: "2026-10-17T08:00:00Z" }, // past
    { id: "A5", status: "checked-in", start: "2026-10-22T08:00:00Z" },
    null,
  ];
  const up = upcomingAppointments(appts, NOW);
  assert.deepEqual(up.map((a) => a.id), ["A1", "A2", "A5"]);
});

test("pastAppointments excludes cancelled, newest first", () => {
  const appts = [
    { id: "A1", status: "completed", start: "2026-10-10T08:00:00Z" },
    { id: "A2", status: "cancelled", start: "2026-10-12T08:00:00Z" },
    { id: "A3", status: "completed", start: "2026-10-15T08:00:00Z" },
  ];
  assert.deepEqual(pastAppointments(appts, NOW).map((a) => a.id), ["A3", "A1"]);
});

test("coverageSummary shapes the honest headline", () => {
  assert.deepEqual(coverageSummary(null), { status: "unknown", headline: "", detail: "" });
  const active = coverageSummary({
    eligible: true, scheme: "BHCPF 2.0", valid_until: "2027-03-31", benefit_services: 7,
  });
  assert.equal(active.status, "active");
  assert.match(active.detail, /BHCPF 2.0/);
  assert.match(active.detail, /2027-03-31/);
  assert.match(active.detail, /7 benefit services/);
  const inactive = coverageSummary({ eligible: false, reason: "coverage expired 2026-01-01 — renew at your PHC" });
  assert.equal(inactive.status, "inactive");
  assert.match(inactive.detail, /expired/);
});

test("bookableSlots drops full slots and sorts soonest first", () => {
  const slots = [
    { id: "S2", start: "2026-10-19T09:00:00Z", capacity: 2, booked: 2 }, // full
    { id: "S1", start: "2026-10-19T08:00:00Z", capacity: 1, booked: 0 },
    { id: "S3", start: "2026-10-19T10:00:00Z", capacity: 2, booked: 1 },
  ];
  assert.deepEqual(bookableSlots(slots).map((s) => s.id), ["S1", "S3"]);
});

test("nextDue is the soonest upcoming visit or null", () => {
  assert.equal(nextDue([], NOW), null);
  const due = nextDue([
    { id: "A1", status: "booked", start: "2026-10-25T08:00:00Z", facility_id: "Kawo PHC", service: "anc" },
    { id: "A2", status: "booked", start: "2026-10-19T08:00:00Z", facility_id: "Kawo PHC", service: "immunisation" },
  ], NOW);
  assert.equal(due.service, "immunisation");
  assert.equal(due.where, "Kawo PHC");
});

test("groupRecord surfaces DocumentReference in the documents section", () => {
  const bundle = {
    entry: [
      { resource: { resourceType: "DocumentReference", description: "Discharge summary",
        date: "2026-10-01T10:00:00Z",
        content: [{ attachment: { contentType: "application/pdf" } }] } },
      { resource: { resourceType: "DocumentReference", type: { text: "ANC card" },
        date: "2026-09-01T10:00:00Z" } },
    ],
  };
  const { sections } = groupRecord(bundle);
  assert.equal(sections.documents.length, 2);
  assert.equal(sections.documents[0].title, "Discharge summary"); // newest first
  assert.equal(sections.documents[0].value, "application/pdf");
  assert.equal(sections.documents[1].title, "ANC card");
});
