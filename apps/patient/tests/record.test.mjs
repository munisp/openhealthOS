/* My Record grouping conformance (ADR-0016).
 * Run: node --test apps/patient/tests/ */
import { test } from "node:test";
import assert from "node:assert/strict";
import { groupRecord, groupConsults } from "../js/logic.js";

const BUNDLE = {
  resourceType: "Bundle", type: "searchset", total: 8,
  entry: [
    { resource: { resourceType: "Patient", id: "PT-1", name: [{ family: "Bello" }] } },
    { resource: {
      resourceType: "Encounter", id: "enc-1",
      type: [{ text: "Outpatient visit" }],
      reasonCode: [{ text: "Fever" }],
      period: { start: "2026-10-10T09:00:00Z" },
      serviceProvider: { display: "Kawo PHC" },
    } },
    { resource: {
      resourceType: "Observation", id: "obs-1",
      code: { text: "Haemoglobin" },
      valueQuantity: { value: 11.2, unit: "g/dL" },
      interpretation: [{ coding: [{ code: "L", display: "Low" }] }],
      effectiveDateTime: "2026-10-11T10:00:00Z",
      performer: [{ display: "Kawo PHC Lab" }],
    } },
    { resource: {
      resourceType: "Observation", id: "obs-2",
      code: { coding: [{ code: "8480-6", display: "Systolic blood pressure" }] },
      valueString: "128 mmHg",
      effectiveDateTime: "2026-10-12T08:00:00Z",
    } },
    { resource: {
      resourceType: "MedicationRequest", id: "rx-1",
      medicationCodeableConcept: { text: "Artemether/Lumefantrine" },
      dosageInstruction: [{ text: "4 tablets twice daily for 3 days" }],
      authoredOn: "2026-10-10T09:30:00Z",
      requester: { display: "Nurse Amina" },
    } },
    { resource: {
      resourceType: "Immunization", id: "imm-1",
      vaccineCode: { coding: [{ display: "Measles vaccine" }] },
      status: "completed",
      occurrenceDateTime: "2026-09-01T11:00:00Z",
    } },
    { resource: {
      resourceType: "Condition", id: "cond-1",
      code: { text: "Uncomplicated malaria" },
      clinicalStatus: { coding: [{ code: "resolved" }] },
      recordedDate: "2026-10-10",
    } },
    { resource: {
      resourceType: "AllergyIntolerance", id: "alg-1",
      code: { text: "Sulphonamides" },
      criticality: "high",
      recordedDate: "2026-08-15",
    } },
  ],
};

test("groupRecord sorts every resource into its section", () => {
  const { sections, total, skipped } = groupRecord(BUNDLE);
  assert.equal(total, 7);
  assert.equal(skipped, 0);
  assert.equal(sections.encounters[0].title, "Outpatient visit");
  assert.equal(sections.encounters[0].value, "Fever");
  assert.equal(sections.encounters[0].source, "Kawo PHC");
  assert.equal(sections.immunizations[0].title, "Measles vaccine");
  assert.equal(sections.conditions[0].title, "Uncomplicated malaria");
  assert.equal(sections.allergies[0].value, "high");
});

test("groupRecord lab rows carry value + interpretation, newest first", () => {
  const { sections } = groupRecord(BUNDLE);
  assert.equal(sections.labs.length, 2);
  assert.equal(sections.labs[0].title, "Systolic blood pressure"); // 10-12 > 10-11
  assert.equal(sections.labs[1].value, "11.2 g/dL (Low)");
});

test("groupRecord meds row carries the dose instruction", () => {
  const { sections } = groupRecord(BUNDLE);
  assert.equal(sections.meds[0].title, "Artemether/Lumefantrine");
  assert.equal(sections.meds[0].value, "4 tablets twice daily for 3 days");
  assert.equal(sections.meds[0].source, "Nurse Amina");
});

test("groupRecord tolerates empty, null and malformed bundles", () => {
  assert.deepEqual(groupRecord(null), {
    sections: { encounters: [], labs: [], meds: [], immunizations: [], conditions: [], allergies: [] },
    total: 0, skipped: 0,
  });
  const { total, skipped } = groupRecord({ entry: [{}, { resource: {} }, { resource: { resourceType: "AuditEvent" } }] });
  assert.equal(total, 0);
  assert.equal(skipped, 3);
});

test("groupRecord survives resources with no fields at all", () => {
  const { sections, total } = groupRecord({ entry: [{ resource: { resourceType: "Observation" } }] });
  assert.equal(total, 1);
  assert.equal(sections.labs[0].title, "Result");
  assert.equal(sections.labs[0].when, "");
});

test("groupConsults normalises the telehealth thread, newest first", () => {
  const rows = groupConsults([
    { id: "c-1", complaint: "fever", assessment: "malaria", outcome: "routine-closure", status: "closed", created_at: "2026-10-01T09:00:00Z", doctor_id: "DR-3" },
    { id: "c-2", complaint: "cough", status: "open", created_at: "2026-10-15T09:00:00Z" },
  ]);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].title, "cough");          // newest first
  assert.equal(rows[1].value, "malaria — routine-closure");
  assert.equal(rows[1].source, "DR-3");
  assert.deepEqual(groupConsults(null), []);
});
