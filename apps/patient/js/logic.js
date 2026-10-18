/* Patient app logic — pure functions, no DOM. Runs in the browser and
 * under node --test (CI). The patient app is read-mostly: it renders the
 * notification rail's feed (appointments, results ready, pickup,
 * referrals, immunisation reminders) with calm, plain-language cards.
 */

/* Kind metadata: glyph + i18n title key + accent class. */
export const KINDS = {
  appointment:   { glyph: "◷", titleKey: "kind_appointment", accent: "ok" },
  lab_ready:     { glyph: "⚗", titleKey: "kind_lab", accent: "info" },
  pickup:        { glyph: "✚", titleKey: "kind_pickup", accent: "ok" },
  referral:      { glyph: "➜", titleKey: "kind_referral", accent: "warn" },
  immunization:  { glyph: "❋", titleKey: "kind_immunization", accent: "info" },
  general:       { glyph: "◈", titleKey: "kind_general", accent: "info" },
};

export function kindMeta(kind) {
  return KINDS[kind] || KINDS.general;
}

/* Group a feed (newest first) into "action needed" vs "information".
 * Action-needed = referral updates and pickup/result-ready items not yet
 * older than 14 days; everything else is informational history. */
export function groupFeed(notifications, now = Date.now()) {
  const TWO_WEEKS = 14 * 86400000;
  const action = [], info = [];
  for (const n of notifications || []) {
    const ts = Date.parse(n.created_at || "") || 0;
    const fresh = now - ts < TWO_WEEKS;
    const needsAction = (n.kind === "referral" || n.kind === "pickup" || n.kind === "lab_ready") && fresh;
    (needsAction ? action : info).push(n);
  }
  return { action, info };
}

/* Human relative time ("today", "yesterday", "3 days ago", date). */
export function relTime(iso, now = Date.now()) {
  const ts = Date.parse(iso || "");
  if (!ts) return "";
  const days = Math.floor((now - ts) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return new Date(ts).toLocaleDateString();
}

/* Delivery state badge for a card. */
export function stateLabel(state) {
  return state === "sent" ? "sent" : state === "failed" ? "failed" : "pending";
}

/* ------------------------------------------------------------------ */
/* My Record (ADR-0016): normalise the patient's own FHIR $everything  */
/* bundle into plain sections the phone can render. Defensive against  */
/* missing fields — a half-written record must still show its truths.  */
/* ------------------------------------------------------------------ */

const RECORD_SECTIONS = {
  Encounter: "encounters",
  Observation: "labs",
  MedicationRequest: "meds",
  MedicationStatement: "meds",
  Immunization: "immunizations",
  Condition: "conditions",
  AllergyIntolerance: "allergies",
  DocumentReference: "documents",
};

const text = (cc) =>
  (cc && (cc.text || (cc.coding && cc.coding[0] && (cc.coding[0].display || cc.coding[0].code)))) || "";

const refName = (ref) => (ref && (ref.display || (ref.reference || "").split("/").pop())) || "";

function recordRow(resource) {
  switch (resource.resourceType) {
    case "Encounter":
      return {
        title: text(resource.type && resource.type[0]) || text(resource.serviceType) || "Visit",
        value: text(resource.reasonCode && resource.reasonCode[0]) || "",
        when: (resource.period && resource.period.start) || resource.date || "",
        source: refName(resource.serviceProvider),
      };
    case "Observation": {
      let value = "";
      if (resource.valueQuantity) value = `${resource.valueQuantity.value} ${resource.valueQuantity.unit || ""}`.trim();
      else if (resource.valueString) value = resource.valueString;
      else if (resource.valueCodeableConcept) value = text(resource.valueCodeableConcept);
      const interp = resource.interpretation && resource.interpretation[0];
      if (interp && text(interp)) value = `${value} (${text(interp)})`.trim();
      return {
        title: text(resource.code) || "Result",
        value,
        when: resource.effectiveDateTime || resource.issued || "",
        source: refName(resource.performer && resource.performer[0]),
      };
    }
    case "MedicationRequest":
      return {
        title: text(resource.medicationCodeableConcept) || refName(resource.medicationReference) || "Medication",
        value: (resource.dosageInstruction && resource.dosageInstruction[0] && resource.dosageInstruction[0].text) || "",
        when: resource.authoredOn || "",
        source: refName(resource.requester),
      };
    case "MedicationStatement":
      return {
        title: text(resource.medicationCodeableConcept) || refName(resource.medicationReference) || "Medication",
        value: resource.status || "",
        when: resource.effectiveDateTime || resource.dateAsserted || "",
        source: refName(resource.informationSource),
      };
    case "Immunization":
      return {
        title: text(resource.vaccineCode) || "Immunisation",
        value: resource.status || "",
        when: resource.occurrenceDateTime || resource.recorded || "",
        source: refName(resource.performer && resource.performer[0] && resource.performer[0].actor),
      };
    case "Condition":
      return {
        title: text(resource.code) || "Condition",
        value: text(resource.clinicalStatus) || "",
        when: resource.onsetDateTime || resource.recordedDate || "",
        source: refName(resource.recorder),
      };
    case "AllergyIntolerance":
      return {
        title: text(resource.code) || "Allergy",
        value: resource.criticality || "",
        when: resource.recordedDate || "",
        source: refName(resource.recorder),
      };
    case "DocumentReference": {
      const content = resource.content && resource.content[0];
      const attach = content && content.attachment;
      return {
        title: (resource.description || (resource.type && text(resource.type)) || (attach && attach.title) || "Document"),
        value: (attach && attach.contentType) || "",
        when: resource.date || "",
        source: refName(resource.author && resource.author[0]),
      };
    }
    default:
      return null;
  }
}

/* Group a $everything bundle into display sections, newest first.
 * Returns counts too, so the UI can show honest empty states. */
export function groupRecord(bundle) {
  const sections = {
    encounters: [], labs: [], meds: [],
    immunizations: [], conditions: [], allergies: [], documents: [],
  };
  let skipped = 0;
  const entries = (bundle && bundle.entry) || [];
  for (const e of entries) {
    const r = e && e.resource;
    if (!r || !r.resourceType) { skipped++; continue; }
    if (r.resourceType === "Patient") continue; // the cover page, not a row
    const key = RECORD_SECTIONS[r.resourceType];
    const row = recordRow(r);
    if (!key || !row) { skipped++; continue; }
    sections[key].push(row);
  }
  for (const key of Object.keys(sections)) {
    sections[key].sort((a, b) => Date.parse(b.when || "") - Date.parse(a.when || ""));
  }
  const total = Object.values(sections).reduce((n, rows) => n + rows.length, 0);
  return { sections, total, skipped };
}

/* Normalise the telehealth consult thread for the record view. */
export function groupConsults(consults) {
  return (consults || [])
    .map((c) => ({
      title: c.complaint || "Consultation",
      value: [c.assessment, c.outcome].filter(Boolean).join(" — "),
      when: c.created_at || "",
      source: c.doctor_id || "",
      status: c.status || "",
    }))
    .sort((a, b) => Date.parse(b.when || "") - Date.parse(a.when || ""));
}

/* ------------------------------------------------------------------ */
/* Self-service care helpers (ADR-0017) — pure, testable.              */

/* Appointments the patient can still act on: booked/checked-in, start
 * in the future, soonest first. Cancelled and past visits fall out. */
export function upcomingAppointments(appts, now = Date.now()) {
  return (appts || [])
    .filter((a) => a && (a.status === "booked" || a.status === "checked-in")
      && Date.parse(a.start || "") >= now)
    .sort((a, b) => Date.parse(a.start || "") - Date.parse(b.start || ""));
}

/* Past visits, newest first — the record-like tail of the list. */
export function pastAppointments(appts, now = Date.now()) {
  return (appts || [])
    .filter((a) => a && a.status !== "cancelled" && Date.parse(a.start || "") < now)
    .sort((a, b) => Date.parse(b.start || "") - Date.parse(a.start || ""));
}

/* The one-line coverage summary for the card: the eligibility payload
 * already carries the honest reason text; we only shape the headline. */
export function coverageSummary(payload) {
  if (!payload || typeof payload !== "object") {
    return { status: "unknown", headline: "", detail: "" };
  }
  if (payload.eligible === true) {
    return {
      status: "active",
      headline: "active",
      detail: [
        payload.scheme || "",
        payload.valid_until ? `until ${payload.valid_until}` : "",
        typeof payload.benefit_services === "number" ? `${payload.benefit_services} benefit services` : "",
      ].filter(Boolean).join(" · "),
    };
  }
  return {
    status: payload.eligible === false ? "inactive" : "unknown",
    headline: payload.eligible === false ? "inactive" : "",
    detail: payload.reason || "",
  };
}

/* Slots for one day, soonest first, only those still bookable. */
export function bookableSlots(slots) {
  return (slots || [])
    .filter((s) => s && s.id && (s.booked || 0) < (s.capacity || 0))
    .sort((a, b) => Date.parse(a.start || "") - Date.parse(b.start || ""));
}

/* Next visit line for the care card, or null when nothing is due. */
export function nextDue(appts, now = Date.now()) {
  const up = upcomingAppointments(appts, now)[0];
  return up ? { when: up.start, where: up.facility_id || "", service: up.service || "" } : null;
}

/* Build a FHIR QuestionnaireResponse from rendered answers (ADR-0017).
 * answers: { linkId: value } — booleans, integers, strings. Required
 * items with no answer are listed in missing[] so the UI can refuse
 * honestly instead of submitting a half-form. */
export function buildQuestionnaireResponse(questionnaire, answers) {
  const missing = [];
  const items = [];
  for (const item of (questionnaire && questionnaire.item) || []) {
    const v = answers ? answers[item.linkId] : undefined;
    if (v === undefined || v === null || v === "") {
      if (item.required) missing.push(item.linkId);
      continue;
    }
    let answer;
    switch (item.type) {
      case "boolean": answer = [{ valueBoolean: !!v }]; break;
      case "integer": answer = [{ valueInteger: Number(v) }]; break;
      case "decimal": answer = [{ valueDecimal: Number(v) }]; break;
      default: answer = [{ valueString: String(v) }];
    }
    items.push({ linkId: item.linkId, text: item.text || "", answer });
  }
  const dangerItems = (((questionnaire && questionnaire.extension) || [])
    .filter((e) => e.url && e.url.endsWith("danger-items"))
    .map((e) => String(e.valueString || "").split(","))[0]) || [];
  const danger = dangerItems.some((id) => answers && answers[id] === true);
  return {
    response: {
      resourceType: "QuestionnaireResponse",
      questionnaire: "Questionnaire/" + ((questionnaire && questionnaire.id) || "unknown"),
      status: missing.length ? "in-progress" : "completed",
      authored: new Date().toISOString(),
      item: items,
    },
    missing,
    danger,
  };
}
