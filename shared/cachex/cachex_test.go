package cachex

import (
	"bufio"
	"io"
	"net"
	"strings"
	"sync"
	"testing"
	"time"
)

// ---------- LRU ----------

func TestLRUBasicsAndTTL(t *testing.T) {
	c := NewLRU(2)
	c.now = func() time.Time { return time.Unix(1000, 0) }
	c.Set("a", []byte("1"), 0)
	c.Set("b", []byte("2"), time.Minute)
	if v, ok := c.Get("a"); !ok || string(v) != "1" {
		t.Fatalf("get a: %q %v", v, ok)
	}
	// cap=2: adding c evicts the least-recently-used (b — a was just read)
	c.Set("c", []byte("3"), 0)
	if _, ok := c.Get("b"); ok {
		t.Fatal("b should have been evicted")
	}
	// TTL expiry: set at t=1000 with 30s, then advance past the deadline
	c.Set("d", []byte("4"), 30*time.Second)
	c.now = func() time.Time { return time.Unix(1061, 0) }
	if _, ok := c.Get("d"); ok {
		t.Fatal("d should be expired")
	}
	c.Delete("a")
	if _, ok := c.Get("a"); ok {
		t.Fatal("a should be deleted")
	}
}

// ---------- fake RESP server ----------

// fakeRedis is an in-memory RESP2 server good enough for the cache command
// set. It can be told to die mid-test to exercise failover.
type fakeRedis struct {
	ln     net.Listener
	mu     sync.Mutex
	store  map[string]string
	expiry map[string]time.Time
	dead   bool
}

func newFakeRedis(t *testing.T) *fakeRedis {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	f := &fakeRedis{ln: ln, store: map[string]string{}, expiry: map[string]time.Time{}}
	go f.serve()
	t.Cleanup(func() { ln.Close() })
	return f
}

func (f *fakeRedis) addr() string { return f.ln.Addr().String() }

func (f *fakeRedis) kill() { f.mu.Lock(); f.dead = true; f.mu.Unlock() }

func (f *fakeRedis) serve() {
	for {
		conn, err := f.ln.Accept()
		if err != nil {
			return
		}
		go f.handle(conn)
	}
}

func readArgs(r *bufio.Reader) ([]string, error) {
	line, err := r.ReadString('\n')
	if err != nil || len(line) < 1 || line[0] != '*' {
		return nil, io.ErrUnexpectedEOF
	}
	n, _ := strings.CutSuffix(strings.TrimSpace(line[1:]), "\r")
	count := 0
	for _, ch := range n {
		count = count*10 + int(ch-'0')
	}
	args := make([]string, 0, count)
	for i := 0; i < count; i++ {
		if _, err := r.ReadString('\n'); err != nil { // $len
			return nil, err
		}
		s, err := r.ReadString('\n')
		if err != nil {
			return nil, err
		}
		args = append(args, strings.TrimRight(s, "\r\n"))
	}
	return args, nil
}

func (f *fakeRedis) handle(conn net.Conn) {
	defer conn.Close()
	r := bufio.NewReader(conn)
	for {
		args, err := readArgs(r)
		if err != nil {
			return
		}
		f.mu.Lock()
		dead := f.dead
		f.mu.Unlock()
		if dead {
			return // drop the connection: the client must redial and fail
		}
		cmd := strings.ToUpper(args[0])
		switch cmd {
		case "PING":
			conn.Write([]byte("+PONG\r\n"))
		case "GET":
			f.mu.Lock()
			v, ok := f.store[args[1]]
			if exp, has := f.expiry[args[1]]; has && time.Now().After(exp) {
				ok = false
			}
			f.mu.Unlock()
			if !ok {
				conn.Write([]byte("$-1\r\n"))
			} else {
				io.WriteString(conn, "$"+itoa(len(v))+"\r\n"+v+"\r\n")
			}
		case "SET":
			f.mu.Lock()
			f.store[args[1]] = args[2]
			for i := 3; i+1 < len(args); i += 2 {
				if strings.ToUpper(args[i]) == "EX" {
					sec := atoi(args[i+1])
					f.expiry[args[1]] = time.Now().Add(time.Duration(sec) * time.Second)
				}
			}
			f.mu.Unlock()
			conn.Write([]byte("+OK\r\n"))
		case "DEL":
			f.mu.Lock()
			delete(f.store, args[1])
			delete(f.expiry, args[1])
			f.mu.Unlock()
			conn.Write([]byte(":1\r\n"))
		default:
			conn.Write([]byte("-ERR unknown command\r\n"))
		}
	}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [8]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}

func atoi(s string) int {
	n := 0
	for _, ch := range s {
		n = n*10 + int(ch-'0')
	}
	return n
}

// ---------- RESP client ----------

func TestRESPRoundTrip(t *testing.T) {
	srv := newFakeRedis(t)
	c := NewRESP(srv.addr(), time.Second)
	defer c.Close()
	if err := c.Ping(); err != nil {
		t.Fatal(err)
	}
	if _, ok := c.Get("missing"); ok {
		t.Fatal("miss should report not-ok")
	}
	c.Set("k", []byte("v"), time.Minute)
	if v, ok := c.Get("k"); !ok || string(v) != "v" {
		t.Fatalf("get k: %q %v", v, ok)
	}
	c.Delete("k")
	if _, ok := c.Get("k"); ok {
		t.Fatal("deleted key still present")
	}
}

func TestRESPSetWithTTL(t *testing.T) {
	srv := newFakeRedis(t)
	c := NewRESP(srv.addr(), time.Second)
	defer c.Close()
	c.Set("k", []byte("v"), 500*time.Millisecond) // rounds up to EX 1
	srv.mu.Lock()
	_, hasExp := srv.expiry["k"]
	srv.mu.Unlock()
	if !hasExp {
		t.Fatal("sub-second ttl should round up to EX 1")
	}
}

// ---------- failover ----------

func TestFallbackDegradesToShadow(t *testing.T) {
	srv := newFakeRedis(t)
	primary := NewRESP(srv.addr(), 300*time.Millisecond)
	shadow := NewLRU(8)
	var errs []string
	fb := &Fallback{Primary: primary, Shadow: shadow,
		OnPrimaryError: func(op string, err error) { errs = append(errs, op) }}

	fb.Set("k", []byte("v"), time.Minute) // warms both
	srv.kill()                            // primary is now dead
	primary.Close()

	if v, ok := fb.Get("k"); !ok || string(v) != "v" {
		t.Fatalf("shadow should serve k after primary death: %q %v", v, ok)
	}
	fb.Set("k2", []byte("w"), 0) // must not panic with dead primary
	if v, ok := fb.Get("k2"); !ok || string(v) != "w" {
		t.Fatalf("shadow should hold k2: %q %v", v, ok)
	}
	if len(errs) == 0 {
		t.Fatal("primary errors should be reported")
	}
}

// ---------- env wiring ----------

func TestFromEnv(t *testing.T) {
	if _, ok := FromEnv(func(string) string { return "" }).(*LRU); !ok {
		t.Fatal("unset CACHE_URL should give plain LRU")
	}
	srv := newFakeRedis(t)
	c := FromEnv(func(k string) string {
		if k == "CACHE_URL" {
			return "redis://" + srv.addr()
		}
		return ""
	})
	if _, ok := c.(*Fallback); !ok {
		t.Fatal("CACHE_URL should give a Fallback over RESP")
	}
	c.Set("x", []byte("y"), time.Minute)
	if v, ok := c.Get("x"); !ok || string(v) != "y" {
		t.Fatalf("wired cache round-trip: %q %v", v, ok)
	}
	if got := FromEnv(func(k string) string { return "bogus" }); got == nil {
		t.Fatal("misconfiguration must degrade, not crash")
	}
}
