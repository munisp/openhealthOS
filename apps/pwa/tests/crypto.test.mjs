/* At-rest encryption tests (ADR-0013): seal/open roundtrip, tamper
 * fails closed, legacy plaintext passes through, key persists. Runs on
 * Node's WebCrypto — the same subtle API the PWA uses in the WebView. */
import test from "node:test";
import assert from "node:assert/strict";

import { cryptoAvailable, ensureDeviceKey, sealValue, openValue, isSealed } from "../js/crypto.js";

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
