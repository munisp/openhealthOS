// RESP2 client (cachex) — speaks to Redis or Valkey over TCP, stdlib-only.
// Commands are the minimal cache set: PING, GET, SET (with EX), DEL.
// One connection with a mutex: boring, correct, fast enough for a cache
// that is an accelerator rather than a hot path.
package cachex

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"sync"
	"time"
)

// RESP is a Redis/Valkey client. Safe for concurrent use.
type RESP struct {
	addr    string
	timeout time.Duration

	mu   sync.Mutex
	conn net.Conn
	r    *bufio.Reader
}

// NewRESP dials lazily — construction never fails; the first op does.
func NewRESP(addr string, timeout time.Duration) *RESP {
	if timeout <= 0 {
		timeout = 2 * time.Second
	}
	return &RESP{addr: addr, timeout: timeout}
}

// Get implements Cache.
func (c *RESP) Get(key string) ([]byte, bool) {
	v, ok, _ := c.getE(key)
	return v, ok
}

// Set implements Cache (ttl rounds up to whole seconds; 0 = persist).
func (c *RESP) Set(key string, val []byte, ttl time.Duration) { _ = c.setE(key, val, ttl) }

// Delete implements Cache.
func (c *RESP) Delete(key string) { _ = c.delE(key) }

func (c *RESP) getE(key string) ([]byte, bool, error) {
	resp, err := c.do("GET", key)
	if err != nil {
		return nil, false, err
	}
	if resp == nil {
		return nil, false, nil
	}
	return resp, true, nil
}

func (c *RESP) setE(key string, val []byte, ttl time.Duration) error {
	args := []string{"SET", key, string(val)}
	if ttl > 0 {
		sec := int64(ttl / time.Second)
		if sec < 1 {
			sec = 1
		}
		args = append(args, "EX", strconv.FormatInt(sec, 10))
	}
	_, err := c.do(args...)
	return err
}

func (c *RESP) delE(key string) error {
	_, err := c.do("DEL", key)
	return err
}

// Ping checks liveness (used by /healthz-style probes and tests).
func (c *RESP) Ping() error {
	_, err := c.do("PING")
	return err
}

// do sends one command and reads one reply, redialling once on error.
func (c *RESP) do(args ...string) ([]byte, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for attempt := 0; attempt < 2; attempt++ {
		if err := c.ensureConn(); err != nil {
			return nil, err
		}
		_ = c.conn.SetDeadline(time.Now().Add(c.timeout))
		if err := writeCommand(c.conn, args); err == nil {
			if v, err := readReply(c.r); err == nil {
				return v, nil
			}
		}
		c.closeLocked() // stale conn — one redial before giving up
	}
	return nil, errors.New("cachex: server unreachable")
}

func (c *RESP) ensureConn() error {
	if c.conn != nil {
		return nil
	}
	conn, err := net.DialTimeout("tcp", c.addr, c.timeout)
	if err != nil {
		return err
	}
	c.conn = conn
	c.r = bufio.NewReader(conn)
	return nil
}

func (c *RESP) closeLocked() {
	if c.conn != nil {
		_ = c.conn.Close()
		c.conn = nil
		c.r = nil
	}
}

// Close releases the connection (tests, shutdown).
func (c *RESP) Close() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.closeLocked()
}

// ---------- wire format ----------

func writeCommand(w io.Writer, args []string) error {
	var b []byte
	b = append(b, '*')
	b = strconv.AppendInt(b, int64(len(args)), 10)
	b = append(b, '\r', '\n')
	for _, a := range args {
		b = append(b, '$')
		b = strconv.AppendInt(b, int64(len(a)), 10)
		b = append(b, '\r', '\n')
		b = append(b, a...)
		b = append(b, '\r', '\n')
	}
	_, err := w.Write(b)
	return err
}

// readReply parses one RESP2 reply. Returns (nil, nil) for a null bulk
// (cache miss). Simple strings return their bytes; integers their ASCII.
func readReply(r *bufio.Reader) ([]byte, error) {
	line, err := r.ReadString('\n')
	if err != nil {
		return nil, err
	}
	if len(line) < 3 {
		return nil, errors.New("cachex: short reply")
	}
	body := line[1 : len(line)-2]
	switch line[0] {
	case '+':
		return []byte(body), nil
	case '-':
		return nil, fmt.Errorf("cachex: server error: %s", body)
	case ':':
		return []byte(body), nil
	case '$':
		n, err := strconv.Atoi(body)
		if err != nil {
			return nil, errors.New("cachex: bad bulk length")
		}
		if n < 0 {
			return nil, nil // null bulk = miss
		}
		buf := make([]byte, n+2)
		if _, err := io.ReadFull(r, buf); err != nil {
			return nil, err
		}
		return buf[:n], nil
	default:
		return nil, fmt.Errorf("cachex: unexpected reply type %q", line[0])
	}
}
