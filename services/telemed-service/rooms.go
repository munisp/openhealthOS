// LiveKit room provisioning and join tokens (ADR-0026).
//
// Tokens are HS256 JWTs signed with the LiveKit API secret — stdlib
// crypto/hmac, no SDK dependency, so tokens mint offline at the edge. The
// claims shape is LiveKit's documented grant format; the client SDK in the
// PWA validates and joins.
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

func b64url(b []byte) string {
	return base64.RawURLEncoding.EncodeToString(b)
}

// LiveKitToken mints a room-join JWT. video=false produces an audio-tier
// participant (canSubscribe + canPublish audio; the PWA disables camera) —
// the same room name works for both tiers, the tier is a client concern.
func LiveKitToken(apiKey, apiSecret, room, identity string, video bool, ttl time.Duration) (string, error) {
	if apiKey == "" || apiSecret == "" {
		return "", errors.New("livekit api key/secret required")
	}
	if room == "" || identity == "" {
		return "", errors.New("room and identity required")
	}
	now := time.Now()
	header, _ := json.Marshal(map[string]string{"alg": "HS256", "typ": "JWT"})
	claims, _ := json.Marshal(map[string]any{
		"iss":  apiKey,
		"sub":  identity,
		"iat":  now.Unix(),
		"nbf":  now.Unix(),
		"exp":  now.Add(ttl).Unix(),
		"name": identity,
		"video": map[string]any{
			"roomJoin":     true,
			"room":         room,
			"canPublish":   true,
			"canSubscribe": true,
		},
		"metadata": fmt.Sprintf(`{"video":%t}`, video),
	})
	unsigned := b64url(header) + "." + b64url(claims)
	mac := hmac.New(sha256.New, []byte(apiSecret))
	mac.Write([]byte(unsigned))
	return unsigned + "." + b64url(mac.Sum(nil)), nil
}

// VerifyLiveKitToken is for tests and for the PWA's own sanity checks:
// signature + expiry, claims returned.
func VerifyLiveKitToken(apiSecret, token string) (map[string]any, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return nil, errors.New("malformed token")
	}
	mac := hmac.New(sha256.New, []byte(apiSecret))
	mac.Write([]byte(parts[0] + "." + parts[1]))
	if !hmac.Equal([]byte(b64url(mac.Sum(nil))), []byte(parts[2])) {
		return nil, errors.New("bad signature")
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return nil, err
	}
	var claims map[string]any
	if err := json.Unmarshal(payload, &claims); err != nil {
		return nil, err
	}
	if exp, ok := claims["exp"].(float64); !ok || int64(exp) < time.Now().Unix() {
		return nil, errors.New("expired")
	}
	return claims, nil
}

var roomSeq int

// ProvisionRoom names a room for a consult. LiveKit creates rooms on first
// join — "provisioning" here is naming + recording, which is exactly what
// the dispatch rule (room_prefix emergency-/consult-) expects.
func ProvisionRoom(consultID string) string {
	roomSeq++
	return fmt.Sprintf("consult-%s-%d", strings.ToLower(consultID), roomSeq)
}
