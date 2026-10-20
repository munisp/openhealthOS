/* qrverify — offline verification of every platform-issued QR, in the
 * browser, with zero round trips (ADR-0055).
 *
 * Mirrors the Python/Go verifiers byte-for-byte: HMAC-SHA256 truncated to
 * 16 hex chars over the payload body, keyring with key ids, revocation
 * list. WebCrypto only — works in the facility WebView offline, and in
 * Node's test runtime.
 *
 * Key-distribution doctrine (honest): HMAC is symmetric — any device that
 * can VERIFY can also MINT. So verification keys are provisioned ONLY to
 * staff devices (CHW/nurse/clerk PWAs) via the MDM channel that installs
 * the PWA, never to patient devices, and the server-side verifiers
 * (equipment-service, adjudicator seams) remain the system of record —
 * a browser verdict is a triage answer ("this card looks genuine,
 * proceed"), with the custody/audit spine catching abuse later.
 *
 * Supported payloads:
 *   OHAST1.<facility>.<asset>.<issued>.<sig>          (legacy asset tags)
 *   OHAST2.<kid>.<facility>.<asset>.<issued>.<sig>    (keyring asset tags)
 *   OHMEM1.<kid>.<member>.<scheme>.<yyyymmdd>.<sig>   (member cards)
 *   OHPRS1.<kid>.<facility>.<yyyymmdd>.<sig>          (facility day codes)
 */

const SPECS = {
  OHAST1: { fields: ["facility", "asset", "issued"], keyInPayload: false },
  OHAST2: { fields: ["kid", "facility", "asset", "issued"], keyInPayload: true },
  OHMEM1: { fields: ["kid", "member", "scheme", "valid"], keyInPayload: true },
  OHPRS1: { fields: ["kid", "facility", "day"], keyInPayload: true },
};

function hex(bytes) {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function hmac16(secret, body) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  return hex(new Uint8Array(sig)).slice(0, 16);
}

/* keyring: JSON string '{"k2026":"secret…"}' or a plain legacy secret
 * (folded to k0 — same degradation as the Python/Go keyrings). */
export function parseKeyring(raw) {
  if (!raw) return {};
  const t = raw.trim();
  if (t.startsWith("{")) {
    try { return JSON.parse(t); } catch { return {}; }
  }
  return t.length >= 16 ? { k0: t } : {};
}

/* Verify one scanned payload. Returns a verdict object; NEVER throws —
 * a scanner UI must get a renderable answer for garbage input too.
 *   {ok, kind, fields, warnings[], reason, revoked}
 * nowMs injectable for tests. */
export async function verifyPayload(tag, keyring, { revoked = new Set(), nowMs = Date.now() } = {}) {
  const fail = (reason, extra = {}) => ({ ok: false, reason, warnings: [], ...extra });
  if (typeof tag !== "string" || !tag.trim()) return fail("empty scan");
  const parts = tag.trim().split(".");
  const kind = parts[0];
  const spec = SPECS[kind];
  if (!spec) return fail(`unknown payload type ${kind || "?"} — not a platform code`, { kind });
  if (parts.length !== spec.fields.length + 2) { // prefix + fields + sig
    return fail(`malformed ${kind} — expected ${spec.fields.length + 2} segments, got ${parts.length}`, { kind });
  }
  const body = parts.slice(0, -1).join(".");
  const sig = parts[parts.length - 1];
  const fields = {};
  spec.fields.forEach((f, i) => { fields[f] = parts[i + 1]; });

  const kid = spec.keyInPayload ? fields.kid : "k0";
  const secret = keyring[kid];
  if (!secret) return fail(`unknown key id "${kid}" — key retired, unprovisioned, or forged`, { kind, fields });
  if ((await hmac16(secret, body)) !== sig.toLowerCase()) {
    return fail("bad signature — photocopy or forgery", { kind, fields });
  }
  if (revoked.has(body)) {
    return { ok: false, kind, fields, revoked: true, warnings: [],
             reason: "REVOKED — this exact code is on the revocation list" };
  }

  const warnings = [];
  // semantic checks per kind
  if (kind === "OHMEM1") {
    const v = fields.valid;
    const expiry = Date.parse(`${v.slice(0, 4)}-${v.slice(4, 6)}-${v.slice(6, 8)}T00:00:00Z`);
    if (Number.isNaN(expiry)) return fail("bad validity date", { kind, fields });
    if (nowMs > expiry + 86400000) warnings.push("card EXPIRED — renew, do not refuse care");
  }
  if (kind === "OHPRS1") {
    const d = fields.day;
    const codeDay = `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`;
    const today = new Date(nowMs).toISOString().slice(0, 10);
    if (codeDay !== today) {
      return fail(`day-code is for ${codeDay}, today is ${today} — replay rejected`, { kind, fields });
    }
  }
  if (kind === "OHAST1" || kind === "OHAST2") {
    const issued = parseInt(fields.issued, 10) * 1000;
    if (Number.isNaN(issued)) return fail("bad issue timestamp", { kind, fields });
    if (issued > nowMs + 300000) return fail("issue timestamp in the future", { kind, fields });
    if (nowMs - issued > 5 * 365 * 86400000) warnings.push("tag STALE — over 5 years, re-label");
  }
  return { ok: true, kind, fields, revoked: false, warnings };
}

/* Revocation list: JSONL of {"body": "…"} — same file the services use. */
export function parseRevocations(jsonl) {
  const out = new Set();
  for (const line of String(jsonl || "").split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try {
      const row = JSON.parse(t);
      if (row.body) out.add(row.body);
    } catch { /* malformed line skipped — torn writes happen */ }
  }
  return out;
}
