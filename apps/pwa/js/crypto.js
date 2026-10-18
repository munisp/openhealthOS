/* At-rest encryption for the edge database (ADR-0013).
 *
 * WebCrypto AES-256-GCM. One device key, generated on first launch and
 * kept in the meta store. Documents and outbox events are sealed before
 * they touch IndexedDB and opened on read; envelope fields (id, type, ts)
 * stay in the clear so indexes and filters keep working.
 *
 * Honest threat model: this defeats casual extraction of the browser's
 * IndexedDB files from a lost or shared Android device — the common case
 * at a PHC. It does not defeat a rooted device or XSS; the key lives on
 * the same device as the data because the app must work offline with no
 * unlock ceremony. Serverside tiers (hub spill, sync outbox) use sealx.
 *
 * Feature-detected: WebViews without crypto.subtle run plaintext and log
 * a warning — availability of care always outranks confidentiality.
 */

const SEAL_TAG = "SXW1";

export function cryptoAvailable() {
  return typeof crypto !== "undefined" && !!crypto.subtle;
}

/* Load or create the device key. getMeta/setMeta are the LocalDB meta
 * accessors — passed in so this module never touches IndexedDB itself. */
export async function ensureDeviceKey(getMeta, setMeta) {
  if (!cryptoAvailable()) return null;
  const existing = await getMeta("deviceKey");
  if (existing) {
    const raw = Uint8Array.from(atob(existing), (c) => c.charCodeAt(0));
    return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
  }
  const key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
  const raw = new Uint8Array(await crypto.subtle.exportKey("raw", key));
  await setMeta("deviceKey", btoa(String.fromCharCode(...raw)));
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

/* Seal a JSON-able value. Returns the wrapped shape; callers store the
 * envelope fields alongside it. Null key = plaintext passthrough. */
export async function sealValue(key, value) {
  if (!key) return { v: value };
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const pt = new TextEncoder().encode(JSON.stringify(value));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, pt));
  const blob = new Uint8Array(iv.length + ct.length);
  blob.set(iv);
  blob.set(ct, iv.length);
  return { sealed: SEAL_TAG, ct: btoa(String.fromCharCode(...blob)) };
}

/* Open a possibly-sealed value. Legacy plaintext rows ({v: ...} or bare
 * values from before encryption shipped) pass through untouched. A
 * tampered or wrongly-keyed row rejects — no partial decrypts. */
export async function openValue(key, wrapped) {
  if (wrapped == null) return wrapped;
  if (wrapped.sealed !== SEAL_TAG) return wrapped.v !== undefined ? wrapped.v : wrapped;
  if (!key) throw new Error("sealed data but no device key");
  const blob = Uint8Array.from(atob(wrapped.ct), (c) => c.charCodeAt(0));
  const iv = blob.slice(0, 12);
  const ct = blob.slice(12);
  const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct);
  return JSON.parse(new TextDecoder().decode(pt));
}

export function isSealed(wrapped) {
  return wrapped != null && wrapped.sealed === SEAL_TAG;
}
