// Package cachex — the distributed-cache seam (the Redis row, ADR-0015).
//
// The platform's doctrine is that services must never depend on a cache
// for correctness: state lives in Postgres, so a cache is an accelerator
// and a resilience aid, never a single point of failure. That is why the
// answer to "where is Redis" is not "embed a client everywhere" but:
//
//  1. Cache is a three-method interface. Domain code cannot tell LRU
//     from Redis — swapping is configuration, not a code change.
//  2. A stdlib RESP2 client speaks to Redis or Valkey (BSD-licensed —
//     the designated server) when CACHE_URL is set. No third-party
//     dependency: the protocol is forty lines and security-reviewable.
//  3. Every op falls back to a shadow in-process LRU on primary error,
//     so a dead cache server degrades hit rate, never availability.
//
// The first production consumer is shared/authz: realm JWKS is written
// through on every successful IdP fetch, so when the IdP is down, any
// gateway replica that ever saw the keys can still verify tokens.
package cachex

import (
	"container/list"
	"errors"
	"fmt"
	"sync"
	"time"
)

// Cache is the seam. Get returns (value, true) on hit. Set honours ttl
// (0 = no expiry). Implementations must be safe for concurrent use.
type Cache interface {
	Get(key string) ([]byte, bool)
	Set(key string, val []byte, ttl time.Duration)
	Delete(key string)
}

// ---------- in-process LRU (edge default, shadow for failover) ----------

type lruEntry struct {
	key     string
	val     []byte
	expires time.Time // zero = never
}

// LRU is a bounded in-process cache with per-key TTL.
type LRU struct {
	mu   sync.Mutex
	cap  int
	ll   *list.List
	keys map[string]*list.Element
	now  func() time.Time // test seam
}

// NewLRU returns an LRU holding at most cap entries.
func NewLRU(cap int) *LRU {
	if cap <= 0 {
		cap = 1024
	}
	return &LRU{cap: cap, ll: list.New(), keys: map[string]*list.Element{},
		now: func() time.Time { return time.Now().UTC() }}
}

func (c *LRU) Get(key string) ([]byte, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	el, ok := c.keys[key]
	if !ok {
		return nil, false
	}
	e := el.Value.(lruEntry)
	if !e.expires.IsZero() && c.now().After(e.expires) {
		c.ll.Remove(el)
		delete(c.keys, key)
		return nil, false
	}
	c.ll.MoveToFront(el)
	return e.val, true
}

func (c *LRU) Set(key string, val []byte, ttl time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	e := lruEntry{key: key, val: val}
	if ttl > 0 {
		e.expires = c.now().Add(ttl)
	}
	if el, ok := c.keys[key]; ok {
		el.Value = e
		c.ll.MoveToFront(el)
		return
	}
	c.keys[key] = c.ll.PushFront(e)
	for c.ll.Len() > c.cap {
		back := c.ll.Back()
		delete(c.keys, back.Value.(lruEntry).key)
		c.ll.Remove(back)
	}
}

func (c *LRU) Delete(key string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if el, ok := c.keys[key]; ok {
		c.ll.Remove(el)
		delete(c.keys, key)
	}
}

// ---------- failover wrapper ----------

// Fallback tries primary first; any primary error (or miss-causing
// failure) drops to the shadow. Writes go to both so the shadow stays
// warm for the next failure. This is the availability doctrine: the
// cache server dying is a hit-rate event, not an outage.
type Fallback struct {
	Primary Cache
	Shadow  Cache
	// OnPrimaryError, when set, is called with each primary failure —
	// the caller logs; cachex itself is silent (a library, not a logger).
	OnPrimaryError func(op string, err error)
}

func (f *Fallback) Get(key string) ([]byte, bool) {
	v, ok, err := getE(f.Primary, key)
	if err != nil {
		f.report("get", err)
		return f.Shadow.Get(key)
	}
	if ok {
		return v, true
	}
	return f.Shadow.Get(key)
}

func (f *Fallback) Set(key string, val []byte, ttl time.Duration) {
	if err := setE(f.Primary, key, val, ttl); err != nil {
		f.report("set", err)
	}
	f.Shadow.Set(key, val, ttl)
}

func (f *Fallback) Delete(key string) {
	if err := delE(f.Primary, key); err != nil {
		f.report("delete", err)
	}
	f.Shadow.Delete(key)
}

func (f *Fallback) report(op string, err error) {
	if f.OnPrimaryError != nil {
		f.OnPrimaryError(op, err)
	}
}

// Cache implementations that can fail (network) expose error-returning
// internals through these adapters; LRU never errors.
type errCache interface {
	getE(key string) ([]byte, bool, error)
	setE(key string, val []byte, ttl time.Duration) error
	delE(key string) error
}

func getE(c Cache, key string) ([]byte, bool, error) {
	if ec, ok := c.(errCache); ok {
		return ec.getE(key)
	}
	v, hit := c.Get(key)
	return v, hit, nil
}

func setE(c Cache, key string, val []byte, ttl time.Duration) error {
	if ec, ok := c.(errCache); ok {
		return ec.setE(key, val, ttl)
	}
	c.Set(key, val, ttl)
	return nil
}

func delE(c Cache, key string) error {
	if ec, ok := c.(errCache); ok {
		return ec.delE(key)
	}
	c.Delete(key)
	return nil
}

var ErrNoServer = errors.New("cachex: no server configured")

// ---------- environment wiring ----------

// FromEnv wires the cache from configuration:
//
//	CACHE_URL=redis://host:6379  → RESP2 client with LRU shadow failover
//	(unset)                      → in-process LRU (edge/dev default)
//
// The returned Cache is always usable; a dead server degrades to the
// shadow rather than failing construction.
func FromEnv(getenv func(string) string) Cache {
	url := getenv("CACHE_URL")
	lru := NewLRU(4096)
	if url == "" {
		return lru
	}
	addr, err := parseURL(url)
	if err != nil {
		return lru // misconfiguration must not crash a service — log at call site via Ping if desired
	}
	resp := NewRESP(addr, 2*time.Second)
	return &Fallback{Primary: resp, Shadow: lru}
}

func parseURL(url string) (string, error) {
	const p = "redis://"
	if len(url) <= len(p) || url[:len(p)] != p {
		return "", fmt.Errorf("cachex: unsupported CACHE_URL %q (want redis://host:port)", url)
	}
	return url[len(p):], nil
}
