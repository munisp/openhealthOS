/* stepup.js — client side of ADR-0039: the PHI step-up gate.
 *
 * elevatedFetch() wraps any PHI fetch: a 428 step_up_required triggers a
 * challenge — a 6-digit code sent to the patient's own channel (their
 * howUdeyChat room when bound, SMS otherwise) — the sheet collects it, we
 * verify, stash the 10-minute token in sessionStorage, and retry the
 * original request. Random re-auth bounces simply re-run the flow.
 *
 * Protocol and UI are separated: elevatedFetch owns challenge/verify/token
 * (fully unit-testable); the sheet only collects the code. Tokens never
 * touch localStorage — sessionStorage dies with the tab.
 */

import { t } from "./i18n.js";

const tokKey = (pid) => `oh-stepup-${pid}`;

function readToken(pid) {
  try {
    const s = JSON.parse(sessionStorage.getItem(tokKey(pid)) || "null");
    if (s && s.exp > Date.now()) return s.token;
  } catch { /* corrupt — treat as absent */ }
  return null;
}
function writeToken(pid, token, ttlSec) {
  try {
    sessionStorage.setItem(tokKey(pid), JSON.stringify({ token, exp: Date.now() + ttlSec * 1000 }));
  } catch { /* private mode — elevation still works for this fetch */ }
}

/* The challenge sheet — DOM only: show status, collect a code, return it
 * (or null on cancel). Verify errors come back in via {hadError}. */
function challengeSheet(patientId, { hadError = false } = {}) {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.id = "stepup-sheet";
    wrap.innerHTML = `
      <div class="su-card" role="dialog" aria-modal="true">
        <h2>${t("stepup_title")}</h2>
        <p class="su-body">${t("stepup_body")}</p>
        <p class="su-status" data-su="status">${hadError ? t("stepup_wrong") : t("stepup_sent")}</p>
        <input data-su="code" inputmode="numeric" maxlength="6" autocomplete="one-time-code"
               placeholder="${t("stepup_code")}">
        <div class="su-row">
          <button data-su="ok">${t("stepup_verify")}</button>
          <button data-su="cancel" class="su-cancel">${t("stepup_cancel")}</button>
        </div>
      </div>`;
    document.body.appendChild(wrap);
    const $ = (sel) => wrap.querySelector(`[data-su="${sel}"]`);
    const done = (code) => { wrap.remove(); resolve(code); };
    $("cancel").onclick = () => done(null);
    $("ok").onclick = () => {
      const code = $("code").value.trim();
      if (/^\d{6}$/.test(code)) done(code);
      else $("status").textContent = t("stepup_wrong");
    };
    $("code").focus();
  });
}

/* sheetFactory is the UI seam — tests (or a native prompt in the mobile
 * shells) replace it; production gets the DOM sheet. */
export let sheetFactory = challengeSheet;
export function setSheetFactory(f) { sheetFactory = f; }

/* elevate: send the challenge, collect a code (up to 3 tries), verify,
 * store the token. false = user cancelled or delivery unavailable. */
async function elevate(apiBase, patientId, phone) {
  const ch = await fetch(`${apiBase}/portal/stepup/challenge`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ patient_id: patientId, phone }),
  });
  if (!ch.ok) return false;
  let hadError = false;
  for (let tries = 0; tries < 3; tries++) {
    const code = await sheetFactory(patientId, { hadError });
    if (!code) return false;
    const v = await fetch(`${apiBase}/portal/stepup/verify`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patient_id: patientId, phone, code }),
    });
    if (v.ok) {
      const out = await v.json();
      writeToken(patientId, out.stepup_token, parseInt(out.expires_in || "600", 10));
      return true;
    }
    hadError = true; // wrong/expired code — server said nothing more, by design
  }
  return false;
}

/* elevatedFetch — fetch behind the step-up gate. 428 → elevate → retry;
 * a random re-auth bounce on the retry just re-elevates (bounded loop). */
export async function elevatedFetch(apiBase, path, { patientId, phone }, opts = {}) {
  let last = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    const headers = { ...(opts.headers || {}) };
    const tok = readToken(patientId);
    if (tok) headers["X-StepUp-Token"] = tok;
    last = await fetch(`${apiBase}${path}`, { cache: "no-store", ...opts, headers });
    if (last.status !== 428) return last;
    if (!(await elevate(apiBase, patientId, phone))) return last; // cancelled: honest 428
  }
  return last;
}
