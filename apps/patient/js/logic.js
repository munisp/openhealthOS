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
