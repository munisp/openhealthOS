package webauthn

// Minimal CBOR (RFC 8949) decoder: definite-length items only, the
// subset authenticators emit inside attestation objects and COSE keys —
// unsigned/negative ints, byte strings, text strings, arrays, maps.
// Indefinite-length items, tags and floats are rejected: an
// authenticator never produces them here, and accepting them would only
// widen the attack surface.

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"errors"
	"fmt"
	"math/big"
)

type cborReader struct {
	buf []byte
	pos int
}

func cborParse(raw []byte) (any, error) {
	r := &cborReader{buf: raw}
	v, err := r.item()
	if err != nil {
		return nil, err
	}
	return v, nil
}

// item decodes one CBOR item. Maps are map[any]any because COSE keys
// mix text and integer keys.
func (r *cborReader) item() (any, error) {
	if r.pos >= len(r.buf) {
		return nil, errors.New("cbor: truncated")
	}
	head := r.buf[r.pos]
	r.pos++
	major, addl := head>>5, head&0x1f
	arg, err := r.arg(addl)
	if err != nil {
		return nil, err
	}
	switch major {
	case 0: // unsigned int
		return int64(arg), nil
	case 1: // negative int
		return -1 - int64(arg), nil
	case 2: // byte string
		return r.take(int(arg))
	case 3: // text string
		b, err := r.take(int(arg))
		if err != nil {
			return nil, err
		}
		return string(b), nil
	case 4: // array
		out := make([]any, 0, arg)
		for i := uint64(0); i < arg; i++ {
			v, err := r.item()
			if err != nil {
				return nil, err
			}
			out = append(out, v)
		}
		return out, nil
	case 5: // map
		out := make(map[any]any, arg)
		for i := uint64(0); i < arg; i++ {
			k, err := r.item()
			if err != nil {
				return nil, err
			}
			v, err := r.item()
			if err != nil {
				return nil, err
			}
			out[k] = v
		}
		return out, nil
	}
	return nil, fmt.Errorf("cbor: unsupported major type %d", major)
}

func (r *cborReader) arg(addl byte) (uint64, error) {
	switch {
	case addl < 24:
		return uint64(addl), nil
	case addl == 24:
		b, err := r.take(1)
		if err != nil {
			return 0, err
		}
		return uint64(b[0]), nil
	case addl == 25:
		b, err := r.take(2)
		if err != nil {
			return 0, err
		}
		return uint64(b[0])<<8 | uint64(b[1]), nil
	case addl == 26:
		b, err := r.take(4)
		if err != nil {
			return 0, err
		}
		var v uint64
		for _, x := range b {
			v = v<<8 | uint64(x)
		}
		return v, nil
	case addl == 27:
		b, err := r.take(8)
		if err != nil {
			return 0, err
		}
		var v uint64
		for _, x := range b {
			v = v<<8 | uint64(x)
		}
		return v, nil
	}
	return 0, fmt.Errorf("cbor: indefinite-length / reserved additional info %d", addl)
}

func (r *cborReader) take(n int) ([]byte, error) {
	if n < 0 || r.pos+n > len(r.buf) {
		return nil, errors.New("cbor: truncated")
	}
	out := r.buf[r.pos : r.pos+n]
	r.pos += n
	return out, nil
}

// parseCOSEKey extracts an ES256 (alg -7, EC2 on P-256) public key from
// a COSE_Key map: {1:2, 3:-7, -1:1, -2:x, -3:y}.
func parseCOSEKey(raw []byte) (*ecdsa.PublicKey, error) {
	v, err := cborParse(raw)
	if err != nil {
		return nil, fmt.Errorf("cose: %w", err)
	}
	m, ok := v.(map[any]any)
	if !ok {
		return nil, errors.New("cose: key not a map")
	}
	get := func(k int64) (any, bool) { v, ok := m[k]; return v, ok }
	kty, _ := get(1)
	alg, _ := get(3)
	crv, _ := get(-1)
	if kty != int64(2) || alg != int64(-7) || crv != int64(1) {
		return nil, fmt.Errorf("cose: unsupported key (kty %v alg %v crv %v) — ES256 passkeys only", kty, alg, crv)
	}
	x, ok1 := mustBytes(m[int64(-2)])
	y, ok2 := mustBytes(m[int64(-3)])
	if !ok1 || !ok2 || len(x) != 32 || len(y) != 32 {
		return nil, errors.New("cose: bad EC2 coordinates")
	}
	pub := &ecdsa.PublicKey{
		Curve: elliptic.P256(),
		X:     new(big.Int).SetBytes(x),
		Y:     new(big.Int).SetBytes(y),
	}
	if !pub.Curve.IsOnCurve(pub.X, pub.Y) {
		return nil, errors.New("cose: point not on P-256")
	}
	return pub, nil
}

func mustBytes(v any) ([]byte, bool) {
	b, ok := v.([]byte)
	return b, ok
}
