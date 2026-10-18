/* Questionnaire builder (ADR-0017): response shaping, required-item
 * honesty, and danger-sign detection from the bundled extension. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildQuestionnaireResponse } from "../js/logic.js";

const Q = JSON.parse(readFileSync(new URL("../questionnaires/anc-screening.json", import.meta.url)));

test("complete answers build a completed response, typed per item", () => {
  const { response, missing, danger } = buildQuestionnaireResponse(Q, {
    weeks: 28, bleeding: false, headache: false, swelling: false, fever: false, movements: false, notes: "",
  });
  assert.deepEqual(missing, []);
  assert.equal(response.status, "completed");
  assert.equal(response.questionnaire, "Questionnaire/anc-screening");
  assert.equal(danger, false);
  const byId = Object.fromEntries(response.item.map((i) => [i.linkId, i.answer[0]]));
  assert.deepEqual(byId.weeks, { valueInteger: 28 });
  assert.deepEqual(byId.bleeding, { valueBoolean: false });
});

test("required items left blank are listed, status is in-progress", () => {
  const { missing, response } = buildQuestionnaireResponse(Q, { weeks: 28 });
  assert.deepEqual(missing.sort(), ["bleeding", "fever", "headache", "swelling"]);
  assert.equal(response.status, "in-progress");
});

test("any danger item true flags the whole response", () => {
  const { danger } = buildQuestionnaireResponse(Q, {
    bleeding: true, headache: false, swelling: false, fever: false,
  });
  assert.equal(danger, true);
  // optional notes do not drive danger
  const calm = buildQuestionnaireResponse(Q, {
    bleeding: false, headache: false, swelling: false, fever: false, notes: "tired",
  });
  assert.equal(calm.danger, false);
});
