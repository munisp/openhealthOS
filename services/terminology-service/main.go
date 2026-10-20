// terminology-service — FHIR R4 terminology operations ($lookup,
// $validate-code, $expand) over local code-system snapshots (ADR-0057).
// Doctrine: snapshots are plain JSON in data/terminology/ — SNOMED-CT is
// licence-gated and loaded by the deployer (never embedded); LOINC-free
// subsets, ICD-10, NAFDAC drug codes and NHIA tariff codes ship openly.
// Edge facilities sync snapshots (GET /snapshots) for offline validation.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type Concept struct {
	Code    string `json:"code"`
	Display string `json:"display"`
	System  string `json:"system"`
}

type CodeSystem struct {
	URL      string    `json:"url"`
	Name     string    `json:"name"`
	Version  string    `json:"version"`
	Concepts []Concept `json:"concepts"`
}

type Store struct {
	systems   map[string]*CodeSystem // by URL
	byCode    map[string]Concept     // system|code -> concept
	dir       string
	snapHash  string
	snapTime  time.Time
}

func LoadStore(dir string) (*Store, error) {
	st := &Store{systems: map[string]*CodeSystem{}, byCode: map[string]Concept{}, dir: dir}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	h := sha256.New()
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			return nil, err
		}
		h.Write(raw)
		var cs CodeSystem
		if err := json.Unmarshal(raw, &cs); err != nil {
			return nil, err
		}
		st.systems[cs.URL] = &cs
		for _, c := range cs.Concepts {
			c.System = cs.URL
			st.byCode[cs.URL+"|"+c.Code] = c
		}
	}
	st.snapHash = hex.EncodeToString(h.Sum(nil))[:16]
	st.snapTime = time.Now().UTC()
	return st, nil
}

func params(w http.ResponseWriter, r *http.Request) map[string]string {
	out := map[string]string{}
	q := r.URL.Query()
	for _, k := range []string{"code", "system", "display", "filter", "url"} {
		if v := q.Get(k); v != "" {
			out[k] = v
		}
	}
	if r.Method == http.MethodPost {
		var body struct {
			Parameter []struct {
				Name        string `json:"name"`
				ValueString string `json:"valueString"`
				ValueCode   string `json:"valueCode"`
			} `json:"parameter"`
		}
		if json.NewDecoder(r.Body).Decode(&body) == nil {
			for _, p := range body.Parameter {
				v := p.ValueString
				if v == "" {
					v = p.ValueCode
				}
				out[p.Name] = v
			}
		}
	}
	return out
}

func paramResp(pairs ...[2]string) map[string]any {
	ps := []map[string]any{}
	for _, kv := range pairs {
		ps = append(ps, map[string]any{"name": kv[0], "valueString": kv[1]})
	}
	return map[string]any{"resourceType": "Parameters", "parameter": ps}
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/fhir+json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func (st *Store) routes(mux *http.ServeMux) {
	lookup := func(w http.ResponseWriter, r *http.Request) {
		p := params(w, r)
		c, ok := st.byCode[p["system"]+"|"+p["code"]]
		if !ok {
			writeJSON(w, 404, paramResp([2]string{"result", "false"},
				[2]string{"message", "code not found in " + p["system"]}))
			return
		}
		writeJSON(w, 200, paramResp([2]string{"name", st.systems[c.System].Name},
			[2]string{"version", st.systems[c.System].Version},
			[2]string{"display", c.Display}))
	}
	mux.HandleFunc("GET /CodeSystem/$lookup", lookup)
	mux.HandleFunc("POST /CodeSystem/$lookup", lookup)
	mux.HandleFunc("GET /ValueSet/$validate-code", func(w http.ResponseWriter, r *http.Request) {
		p := params(w, r)
		_, ok := st.byCode[p["system"]+"|"+p["code"]]
		res := "false"
		if ok {
			res = "true"
		}
		writeJSON(w, 200, paramResp([2]string{"result", res}))
	})
	mux.HandleFunc("GET /ValueSet/$expand", func(w http.ResponseWriter, r *http.Request) {
		p := params(w, r)
		cs, ok := st.systems[p["url"]]
		if !ok {
			writeJSON(w, 404, paramResp([2]string{"message", "unknown code system " + p["url"]}))
			return
		}
		filter := strings.ToLower(p["filter"])
		contains := []map[string]any{}
		for _, c := range cs.Concepts {
			if filter != "" && !strings.Contains(strings.ToLower(c.Display), filter) &&
				!strings.Contains(strings.ToLower(c.Code), filter) {
				continue
			}
			contains = append(contains, map[string]any{"system": cs.URL, "code": c.Code, "display": c.Display})
		}
		writeJSON(w, 200, map[string]any{"resourceType": "ValueSet",
			"expansion": map[string]any{"timestamp": time.Now().UTC().Format(time.RFC3339),
				"total": len(contains), "contains": contains}})
	})
	mux.HandleFunc("GET /CodeSystem", func(w http.ResponseWriter, r *http.Request) {
		out := []map[string]string{}
		for _, cs := range st.systems {
			out = append(out, map[string]string{"url": cs.URL, "name": cs.Name, "version": cs.Version})
		}
		sort.Slice(out, func(i, j int) bool { return out[i]["url"] < out[j]["url"] })
		writeJSON(w, 200, map[string]any{"systems": out, "count": len(out)})
	})
	mux.HandleFunc("GET /snapshots/manifest", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"hash": st.snapHash,
			"generated": st.snapTime.Format(time.RFC3339), "systems": len(st.systems),
			"url": "/snapshots/download"})
	})
	mux.HandleFunc("GET /snapshots/download", func(w http.ResponseWriter, r *http.Request) {
		bundle := map[string]any{"hash": st.snapHash, "systems": st.systems}
		writeJSON(w, 200, bundle)
	})
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"ok":true}`))
	})
}

func main() {
	dir := os.Getenv("TERMINOLOGY_DIR")
	if dir == "" {
		dir = "../../data/terminology"
	}
	st, err := LoadStore(dir)
	if err != nil {
		log.Fatalf("load terminology: %v", err)
	}
	log.Printf("loaded %d code systems (snapshot %s)", len(st.systems), st.snapHash)
	mux := http.NewServeMux()
	st.routes(mux)
	port := os.Getenv("PORT")
	if port == "" {
		port = "8111"
	}
	log.Printf("terminology-service listening on :%s", port)
	log.Fatal(http.ListenAndServe(":"+port, mux))
}
