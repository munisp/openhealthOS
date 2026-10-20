package main

// shlfile.go — SHL content-file encryption for the ipsqr CLI (ADR-0054).
// Thin wrapper over shared/shlx so the SHR service and this tool share
// one implementation (one crypto seam, one audit surface).

import "github.com/munisp/openhealthOS/shared/shlx"

// EncryptSHLFile encrypts plaintext with the 32-byte base64url key from
// the SHL payload, returning the envelope string to serve at payload.url.
func EncryptSHLFile(plaintext []byte, keyB64 string) (string, error) {
	return shlx.Encrypt(plaintext, keyB64)
}

// DecryptSHLFile reverses EncryptSHLFile. Fail-closed: any tamper,
// truncation, or wrong key is an authentication error, never partial data.
func DecryptSHLFile(envelope, keyB64 string) ([]byte, error) {
	return shlx.Decrypt(envelope, keyB64)
}
