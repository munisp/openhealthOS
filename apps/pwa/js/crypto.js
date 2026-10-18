/* At-rest encryption for the edge database (ADR-0013), with key rotation
 * (ADR-0015).
 *
 * WebCrypto AES-256-GCM. Device keys live in a KEYRING: every newly sealed
 * value is stamped with the id of the key that sealed it (SXW2 + kid), so
 * rotating to a fresh key never orphans old rows — they keep opening with
 * their original key while all new writes use the current one. Rotation:
 *
 *   await rotateDeviceKey(getMeta, setMeta)   // new current key
 *   // rows reseal lazily on next write, or sweep with openValue+sealValue
 *
 * Rows sealed before rotation shipped (SXW1, no kid) open with the legacy
 * key, which is folded into the ring as k1 on first launch after upgrade.
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

const SEAL_TAG = "SXW1"; // legacy single-key rows (pre-rotation)
const SEAL_TAG_V2 = "SXW2"; // keyring rows: {sealed, kid, ct}

export function cryptoAvailable() {
  return typeof crypto !== "undefined" && !!crypto.subtle;
}

function b64ToBytes(b64) {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}
function bytesToB64(bytes) {
  return btoa(String.fromCharCode(...bytes));
}
async function importAes(raw) {
  // extractable: the ring re-persists imported keys; the raw bytes already
  // live in the meta store, so non-extractability would buy nothing.
  return crypto.subtle.importKey("raw", raw, "AES-GCM", true, ["encrypt", "decrypt"]);
}
async function generateAes() {
  const key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
  return bytesToB64(new Uint8Array(await crypto.subtle.exportKey("raw", key)));
}

/* A ring is { current: kid, legacyKid: kid|null, keys: {kid: CryptoKey} }.
 * Bare CryptoKeys (old callers, single-key devices) still work everywhere —
 * sealValue/openValue accept either. */
function isRing(k) {
  return !!k && typeof k === "object" && !!k.keys && typeof k.current === "string";
}

async function loadRing(getMeta) {
  const stored = await getMeta("deviceKeyring"); // {current, legacyKid, keys:{kid:b64}}
  if (!stored) return null;
  const parsed = typeof stored === "string" ? JSON.parse(stored) : stored;
  const keys = {};
  for (const [kid, b64] of Object.entries(parsed.keys || {})) {
    keys[kid] = await importAes(b64ToBytes(b64));
  }
  return { current: parsed.current, legacyKid: parsed.legacyKid ?? null, keys };
}

async function persistRing(setMeta, ring) {
  const keys = {};
  for (const [kid, key] of Object.entries(ring.keys)) {
    keys[kid] = bytesToB64(new Uint8Array(await crypto.subtle.exportKey("raw", key)));
  }
  await setMeta("deviceKeyring", JSON.stringify({ current: ring.current, legacyKid: ring.legacyKid, keys }));
}

/* Load or create the device keyring. getMeta/setMeta are the LocalDB meta
 * accessors — passed in so this module never touches IndexedDB itself.
 *
 * Migration: a pre-rotation device has only "deviceKey" (the SXW1 key).
 * It becomes ring key k1 AND the legacyKid, so SXW1 rows keep opening.
 * The old "deviceKey" entry is left in place so a downgraded app build
 * can still read the data it wrote. */
export async function ensureDeviceKey(getMeta, setMeta) {
  if (!cryptoAvailable()) return null;
  const existing = await loadRing(getMeta);
  if (existing) return existing;

  const legacyB64 = await getMeta("deviceKey");
  const ring = { current: "k1", legacyKid: legacyB64 ? "k1" : null, keys: {} };
  ring.keys.k1 = await importAes(legacyB64 ? b64ToBytes(legacyB64) : b64ToBytes(await generateAes()));
  if (!legacyB64) {
    await setMeta("deviceKey", bytesToB64(new Uint8Array(await crypto.subtle.exportKey("raw", ring.keys.k1))));
  }
  await persistRing(setMeta, ring);
  return ring;
}

/* Rotate to a fresh current key. Old keys stay in the ring — old rows
 * keep opening. Returns the updated ring. */
export async function rotateDeviceKey(getMeta, setMeta) {
  const ring = await ensureDeviceKey(getMeta, setMeta);
  if (!ring) return null;
  const n = Object.keys(ring.keys).length + 1;
  const kid = "k" + n;
  ring.keys[kid] = await importAes(b64ToBytes(await generateAes()));
  ring.current = kid;
  await persistRing(setMeta, ring);
  return ring;
}

/* Seal a JSON-able value. Ring → SXW2 stamped with the current kid;
 * bare CryptoKey → legacy SXW1 (byte-identical to pre-rotation);
 * null → plaintext passthrough. */
export async function sealValue(keyOrRing, value) {
  if (!keyOrRing) return { v: value };
  const key = isRing(keyOrRing) ? keyOrRing.keys[keyOrRing.current] : keyOrRing;
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const pt = new TextEncoder().encode(JSON.stringify(value));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, pt));
  const blob = new Uint8Array(iv.length + ct.length);
  blob.set(iv);
  blob.set(ct, iv.length);
  if (isRing(keyOrRing)) {
    return { sealed: SEAL_TAG_V2, kid: keyOrRing.current, ct: bytesToB64(blob) };
  }
  return { sealed: SEAL_TAG, ct: bytesToB64(blob) };
}

/* Open a possibly-sealed value: SXW2 dispatches on kid (unknown kid
 * rejects — fail closed), SXW1 uses the ring's legacy key (or the bare
 * key for old callers), plaintext rows pass through untouched. A
 * tampered or wrongly-keyed row rejects — no partial decrypts. */
export async function openValue(keyOrRing, wrapped) {
  if (wrapped == null) return wrapped;
  if (wrapped.sealed === SEAL_TAG_V2) {
    if (!isRing(keyOrRing)) throw new Error("keyring-sealed data but no keyring");
    const key = keyOrRing.keys[wrapped.kid];
    if (!key) throw new Error("sealed with unknown key id: " + wrapped.kid);
    return decryptWith(key, wrapped.ct);
  }
  if (wrapped.sealed === SEAL_TAG) {
    let key = keyOrRing;
    if (isRing(keyOrRing)) {
      key = keyOrRing.legacyKid ? keyOrRing.keys[keyOrRing.legacyKid] : null;
    }
    if (!key) throw new Error("sealed data but no legacy device key");
    return decryptWith(key, wrapped.ct);
  }
  return wrapped.v !== undefined ? wrapped.v : wrapped;
}

async function decryptWith(key, ctB64) {
  const blob = b64ToBytes(ctB64);
  const iv = blob.slice(0, 12);
  const ct = blob.slice(12);
  const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct);
  return JSON.parse(new TextDecoder().decode(pt));
}

export function isSealed(wrapped) {
  return wrapped != null && (wrapped.sealed === SEAL_TAG || wrapped.sealed === SEAL_TAG_V2);
}
