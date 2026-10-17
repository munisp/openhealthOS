// Package crdt implements the edge replication core for OpenHealth OS:
// hybrid-logical-clock timestamps, per-field last-writer-wins registers,
// add-only sets for clinical facts, and tombstones — stdlib-only.
//
// Design rule (ADR-0001): connectivity is an optimisation. Two nodes that
// have never met must be able to merge months of divergent work into one
// state with zero data loss and zero central arbitration.
package crdt

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

// HLC is a hybrid logical clock: physical milliseconds plus a logical
// counter, tie-broken by node id so ordering is total and deterministic.
type HLC struct {
	Wall  int64  `json:"w"` // unix milliseconds
	Logic uint32 `json:"l"`
	Node  string `json:"n"`
}

// Clock issues monotonically increasing HLCs for one node.
type Clock struct {
	node    string
	last    HLC
	nowFunc func() int64 // injectable for tests
}

// NewClock returns a clock for node. node ids must be unique per device.
func NewClock(node string) *Clock {
	return &Clock{node: node, nowFunc: func() int64 { return time.Now().UnixMilli() }}
}

// Tick stamps a local event.
func (c *Clock) Tick() HLC {
	wall := c.nowFunc()
	if wall > c.last.Wall {
		c.last = HLC{Wall: wall, Logic: 0, Node: c.node}
	} else {
		c.last.Logic++
	}
	return c.last
}

// Observe merges a remote timestamp (receive event) and returns the next
// local stamp, preserving causality.
func (c *Clock) Observe(remote HLC) HLC {
	wall := c.nowFunc()
	maxWall := wall
	if c.last.Wall > maxWall {
		maxWall = c.last.Wall
	}
	if remote.Wall > maxWall {
		maxWall = remote.Wall
	}
	switch maxWall {
	case c.last.Wall:
		if remote.Wall == c.last.Wall && remote.Logic > c.last.Logic {
			c.last.Logic = remote.Logic
		}
		c.last.Logic++
	case remote.Wall:
		c.last = HLC{Wall: remote.Wall, Logic: remote.Logic + 1, Node: c.node}
	default:
		c.last = HLC{Wall: wall, Logic: 0, Node: c.node}
	}
	c.last.Node = c.node
	return c.last
}

// After reports whether a strictly dominates b in total order.
func (a HLC) After(b HLC) bool {
	if a.Wall != b.Wall {
		return a.Wall > b.Wall
	}
	if a.Logic != b.Logic {
		return a.Logic > b.Logic
	}
	return a.Node > b.Node
}

func (a HLC) String() string {
	return fmt.Sprintf("%d.%d@%s", a.Wall, a.Logic, a.Node)
}

// ParseHLC parses the String() form; used by wire codecs and tests.
func ParseHLC(s string) (HLC, error) {
	at := strings.LastIndex(s, "@")
	dot := strings.Index(s, ".")
	if at < 0 || dot < 0 || dot > at {
		return HLC{}, fmt.Errorf("crdt: malformed hlc %q", s)
	}
	wall, err := strconv.ParseInt(s[:dot], 10, 64)
	if err != nil {
		return HLC{}, fmt.Errorf("crdt: malformed hlc %q: %w", s, err)
	}
	logic, err := strconv.ParseUint(s[dot+1:at], 10, 32)
	if err != nil {
		return HLC{}, fmt.Errorf("crdt: malformed hlc %q: %w", s, err)
	}
	return HLC{Wall: wall, Logic: uint32(logic), Node: s[at+1:]}, nil
}
