// reading-service — HTTP seam over the teleread engine.
//
// Go owns transport; Python (packages/teleread/cli.py) owns doctrine.
// Every request is one subprocess call with a JSON state file —
// persistence is a boring atomic file (os.replace), not a promise.
// Calls are mutex-serialized: the file store is single-writer by
// design; Postgres is the scale answer, and the engine won't change
// when it arrives.
//
// Domain errors surface as 422 with the engine's message — fail-loud,
// never a silent 200. Auth is at the api-gateway, same as imaging.
package main

import (
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"sync"
)

var errDomain = errors.New("domain")

type EngineIface interface {
	Call(cmd string, args map[string]any) (json.RawMessage, string, error)
}

type SubprocessEngine struct {
	Python string
	Script string
	DB     string
	mu     sync.Mutex
}

type engineResult struct {
	Ok     bool            `json:"ok"`
	Result json.RawMessage `json:"result"`
	Error  string          `json:"error"`
}

func (e *SubprocessEngine) Call(cmd string, args map[string]any) (json.RawMessage, string, error) {
	payload, err := json.Marshal(args)
	if err != nil {
		return nil, "bad request encoding", errDomain
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	c := exec.Command(e.Python, e.Script, e.DB, cmd, string(payload))
	out, runErr := c.Output()
	var res engineResult
	if len(out) > 0 {
		_ = json.Unmarshal(out, &res)
	}
	if runErr != nil {
		if res.Error != "" {
			return nil, res.Error, errDomain
		}
		return nil, "engine unavailable", runErr
	}
	return res.Result, "", nil
}

type server struct {
	engine EngineIface
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func resolveToolPath(p string) string {
	if exe, err := os.Executable(); err == nil {
		cand := filepath.Join(filepath.Dir(exe), p)
		if _, err := os.Stat(cand); err == nil {
			return cand
		}
	}
	return p
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

// bodyMap decodes the JSON body and folds in query params (day, hours
// as ints) so GET and POST share one engine path.
func bodyMap(r *http.Request) (map[string]any, error) {
	m := map[string]any{}
	if r.Method == http.MethodPost {
		if err := json.NewDecoder(r.Body).Decode(&m); err != nil {
			return nil, err
		}
	}
	q := r.URL.Query()
	for _, k := range []string{"domain", "reader_id", "case_id"} {
		if v := q.Get(k); v != "" {
			m[k] = v
		}
	}
	for _, k := range []string{"day", "hours"} {
		if v := q.Get(k); v != "" {
			if n, err := strconv.Atoi(v); err == nil {
				m[k] = n
			}
		}
	}
	return m, nil
}

func (s server) handle(cmd string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		args, err := bodyMap(r)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON body"})
			return
		}
		res, msg, callErr := s.engine.Call(cmd, args)
		if callErr != nil {
			if errors.Is(callErr, errDomain) {
				writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": msg})
				return
			}
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": "reading engine unavailable"})
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(res)
	}
}

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /readers", s.handle("register-reader"))
	mux.HandleFunc("POST /orders", s.handle("order"))
	mux.HandleFunc("POST /assign", s.handle("assign"))
	mux.HandleFunc("GET /worklist", s.handle("worklist"))
	mux.HandleFunc("POST /sessions", s.handle("session"))
	mux.HandleFunc("POST /reports/draft", s.handle("draft"))
	mux.HandleFunc("POST /reports/verify", s.handle("verify"))
	mux.HandleFunc("POST /reports/addendum", s.handle("addendum"))
	mux.HandleFunc("POST /critical/ack", s.handle("ack"))
	mux.HandleFunc("GET /critical/unack", s.handle("unack"))
	mux.HandleFunc("GET /turnaround", s.handle("turnaround"))
	mux.HandleFunc("POST /second-opinion", s.handle("second-opinion"))
	mux.HandleFunc("POST /transfer", s.handle("transfer"))
	mux.HandleFunc("GET /result-line", s.handle("result-line"))
	mux.HandleFunc("GET /case", s.handle("case"))
	mux.HandleFunc("GET /domains", s.handle("domains"))
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}

func main() {
	engine := &SubprocessEngine{
		Python: env("PYTHON", "python3"),
		Script: resolveToolPath(env("TELEREAD_CLI", "../../packages/teleread/cli.py")),
		DB:     env("TELEREAD_DB", "teleread-state.json"),
	}
	addr := env("LISTEN", ":8101")
	log.Printf("OpenHealth OS reading-service on %s (cli=%s db=%s)", addr, engine.Script, engine.DB)
	log.Fatal(http.ListenAndServe(addr, server{engine: engine}.routes()))
}
