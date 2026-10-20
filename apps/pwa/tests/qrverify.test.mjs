/* qrverify tests — Node WebCrypto, same subtle API as the WebView.
 * Cross-checked against the Python/Go verifiers: a tag minted by
 * packages/membercard must verify here byte-for-byte. */
import test from "node:test";
import assert from "node:assert/strict";
import { verifyPayload, parseKeyring, parseRevocations } from "../js/qrverify.js";

const RING = { k2026: "membercard-secret-0001", k2025: "old-secret-0000000001" };
const NOW = Date.UTC(2026, 9, 20, 12, 0, 0); // 2026-10-20 12:00Z

// HMAC helper to mint tags in-test (mirrors Python sign()).
async function mint(body, secret) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  const hex = Array.from(new Uint8Array(sig), (b) => b.toString(16).padStart(2, "0")).join("");
  return `${body}.${hex.slice(0, 16)}`;
}

test("membercard roundtrip + expiry warning", async () => {
  const tag = await mint("OHMEM1.k2026.NG-0001.BHCPF-VUL.20270331", RING.k2026);
  const v = await verifyPayload(tag, RING, { nowMs: NOW });
  assert.equal(v.ok, true);
  assert.equal(v.fields.member, "NG-0001");
  assert.equal(v.warnings.length, 0);

  const expired = await mint("OHMEM1.k2026.NG-0001.BHCPF-VUL.20260101", RING.k2026);
  const v2 = await verifyPayload(expired, RING, { nowMs: NOW });
  assert.equal(v2.ok, true); // expired is a warning, never a silent fail
  assert.match(v2.warnings[0], /EXPIRED/);
});

test("presence day-code: today ok, yesterday replay-rejected", async () => {
  const today = await mint("OHPRS1.k2026.KDH-01.20261020", RING.k2026);
  assert.equal((await verifyPayload(today, RING, { nowMs: NOW })).ok, true);
  const yesterday = await mint("OHPRS1.k2026.KDH-01.20261019", RING.k2026);
  const v = await verifyPayload(yesterday, RING, { nowMs: NOW });
  assert.equal(v.ok, false);
  assert.match(v.reason, /replay/);
});

test("asset v2: rotation keeps old keys verifying", async () => {
  const old = await mint("OHAST2.k2025.KDH-01.MRI-03.1792497600", RING.k2025);
  const v = await verifyPayload(old, RING, { nowMs: NOW });
  assert.equal(v.ok, true);
  assert.equal(v.fields.asset, "MRI-03");
});

test("forgery and unknown kid rejected", async () => {
  const tag = await mint("OHMEM1.k2026.NG-0001.BHCPF-VUL.20270331", RING.k2026);
  const tampered = tag.replace("BHCPF-VUL", "PRIVATE-HMO");
  const v = await verifyPayload(tampered, RING, { nowMs: NOW });
  assert.equal(v.ok, false);
  assert.match(v.reason, /signature/);
  const wrongKey = await mint("OHMEM1.k9999.NG-0001.BHCPF-VUL.20270331", "attacker-secret-000");
  const v2 = await verifyPayload(wrongKey, RING, { nowMs: NOW });
  assert.equal(v2.ok, false);
  assert.match(v2.reason, /unknown key id/);
});

test("revocation hard-rejects a validly signed tag", async () => {
  const body = "OHAST2.k2026.KDH-01.MRI-03.1792497600";
  const tag = await mint(body, RING.k2026);
  const revoked = parseRevocations(`{"body":"${body}"}\ngarbage\n`);
  const v = await verifyPayload(tag, RING, { revoked, nowMs: NOW });
  assert.equal(v.ok, false);
  assert.equal(v.revoked, true);
});

test("legacy v1 asset tag under plain secret", async () => {
  const legacy = parseKeyring("single-secret-value-1234");
  const tag = await mint("OHAST1.F1.EQ-1.1792497600", legacy.k0);
  const v = await verifyPayload(tag, legacy, { nowMs: NOW });
  assert.equal(v.ok, true);
});

test("garbage never throws", async () => {
  for (const junk of ["", "hello", "OHMEM1", "OHMEM1.a.b", null, 42]) {
    const v = await verifyPayload(junk, RING, { nowMs: NOW });
    assert.equal(v.ok, false);
    assert.ok(v.reason);
  }
});

test("stale asset tag warns", async () => {
  const old = await mint("OHAST2.k2026.F1.EQ-9.1000000000", RING.k2026); // 2001
  const v = await verifyPayload(old, RING, { nowMs: NOW });
  assert.equal(v.ok, true);
  assert.match(v.warnings[0], /STALE/);
});
