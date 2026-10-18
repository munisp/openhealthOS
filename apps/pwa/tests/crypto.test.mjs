/* At-rest encryption tests (ADR-0013): seal/open roundtrip, tamper
 * fails closed, legacy plaintext passes through, key persists. Runs on
 * Node's WebCrypto — the same subtle API the PWA uses in the WebView. */
import test from "node:test";
import assert from "node:assert/strict";

import { cryptoAvailable, ensureDeviceKey, rotateDeviceKey, sealValue, openValue, isSealed } from "../js/crypto.js";

function memoryMeta() {
  const m = new Map();
  return [async (k) => m.get(k), async (k, v) => { m.set(k, v); }, m];
}

test("webcrypto available in the test runtime", () => {
  assert.equal(cryptoAvailable(), true);
});

test("seal hides the plaintext; open restores it exactly", async () => {
  const [get, set] = memoryMeta();
  const key = await ensureDeviceKey(get, set);
  const clinical = { name: "Adaeze Nwosu", diagnosis: "malaria", muac: 10.9 };

  const sealed = await sealValue(key, clinical);
  assert.ok(isSealed(sealed));
  assert.ok(!JSON.stringify(sealed).includes("Adaeze"), "ciphertext must not leak the name");

  const opened = await openValue(key, sealed);
  assert.deepEqual(opened, clinical);
});

test("device key persists across ensure calls (same key unwraps later rows)", async () => {
  const [get, set, m] = memoryMeta();
  const key1 = await ensureDeviceKey(get, set);
  assert.ok(m.has("deviceKey"));
  const sealed = await sealValue(key1, { secret: "hiv-status" });

  const key2 = await ensureDeviceKey(get, set); // reload — same stored key
  assert.deepEqual(await openValue(key2, sealed), { secret: "hiv-status" });
});

test("tampered ciphertext rejects — never a partial decrypt", async () => {
  const [get, set] = memoryMeta();
  const key = await ensureDeviceKey(get, set);
  const sealed = await sealValue(key, { a: 1 });
  const blob = atob(sealed.ct);
  const flipped = btoa(blob.slice(0, -2) + String.fromCharCode(blob.charCodeAt(blob.length - 2) ^ 0xff) + blob.slice(-1));
  await assert.rejects(() => openValue(key, { sealed: "SXW1", ct: flipped }));
});

test("legacy plaintext rows pass through (mixed-mode migration)", async () => {
  const [get, set] = memoryMeta();
  const key = await ensureDeviceKey(get, set);
  assert.deepEqual(await openValue(key, { v: { legacy: true } }), { legacy: true });
  assert.deepEqual(await openValue(key, { bare: "row" }), { bare: "row" });
  assert.equal(await openValue(key, null), null);
});

test("null key is plaintext passthrough (memory fallback / legacy WebView)", async () => {
  const sealed = await sealValue(null, { x: 5 });
  assert.equal(isSealed(sealed), false);
  assert.deepEqual(await openValue(null, sealed), { x: 5 });
});

test("keyring: rows are stamped with the current key id (SXW2)", async () => {
  const [get, set] = memoryMeta();
  const ring = await ensureDeviceKey(get, set);
  const sealed = await sealValue(ring, { secret: "tb-status" });
  assert.equal(sealed.sealed, "SXW2");
  assert.equal(sealed.kid, "k1");
  assert.ok(isSealed(sealed));
  assert.deepEqual(await openValue(ring, sealed), { secret: "tb-status" });
});

test("rotation: old rows keep opening, new rows use the new key", async () => {
  const [get, set] = memoryMeta();
  const ring1 = await ensureDeviceKey(get, set);
  const oldRow = await sealValue(ring1, { sealed: "before rotation" });

  const ring2 = await rotateDeviceKey(get, set);
  assert.equal(ring2.current, "k2");
  const newRow = await sealValue(ring2, { sealed: "after rotation" });
  assert.equal(newRow.kid, "k2");

  // A fresh load (app restart) opens both eras.
  const reloaded = await ensureDeviceKey(get, set);
  assert.deepEqual(await openValue(reloaded, oldRow), { sealed: "before rotation" });
  assert.deepEqual(await openValue(reloaded, newRow), { sealed: "after rotation" });
});

test("unknown key id fails closed", async () => {
  const [get, set] = memoryMeta();
  const ring = await ensureDeviceKey(get, set);
  const sealed = await sealValue(ring, { a: 1 });
  await assert.rejects(() => openValue(ring, { sealed: "SXW2", kid: "k99", ct: sealed.ct }));
});

test("tampered SXW2 ciphertext rejects — no partial decrypt", async () => {
  const [get, set] = memoryMeta();
  const ring = await ensureDeviceKey(get, set);
  const sealed = await sealValue(ring, { a: 1 });
  const blob = atob(sealed.ct);
  const flipped = btoa(blob.slice(0, -2) + String.fromCharCode(blob.charCodeAt(blob.length - 2) ^ 0xff) + blob.slice(-1));
  await assert.rejects(() => openValue(ring, { sealed: "SXW2", kid: "k1", ct: flipped }));
});

test("migration: pre-rotation device key folds into the ring, SXW1 rows open", async () => {
  // Simulate a device that sealed rows before rotation shipped: only the
  // legacy "deviceKey" meta exists, rows are SXW1 (no kid).
  const [get, set, m] = memoryMeta();
  const raw = new Uint8Array(32);
  for (let i = 0; i < 32; i++) raw[i] = i;
  m.set("deviceKey", btoa(String.fromCharCode(...raw)));
  const legacyKey = await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
  const legacyRow = await sealValue(legacyKey, { era: "SXW1" }); // bare key → SXW1
  assert.equal(legacyRow.sealed, "SXW1");

  const ring = await ensureDeviceKey(get, set); // migration happens here
  assert.equal(ring.legacyKid, "k1");
  assert.deepEqual(await openValue(ring, legacyRow), { era: "SXW1" });
  // New writes after migration are keyring-stamped.
  const newRow = await sealValue(ring, { era: "SXW2" });
  assert.equal(newRow.sealed, "SXW2");
  assert.deepEqual(await openValue(ring, newRow), { era: "SXW2" });
});
