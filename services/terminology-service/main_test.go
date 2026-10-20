package main

import (
	"encoding/json"
	"net/http/httptest"
	"net/http"
	"testing"
)

func setup(t *testing.T) *Store {
	st, err := LoadStore("../../data/terminology")
	if err != nil {
		t.Fatal(err)
	}
	return st
}

func get(st *Store, path string) (*httptest.ResponseRecorder, map[string]any) {
	r := httptest.NewRequest("GET", path, nil)
	w := httptest.NewRecorder()
	mux := http.NewServeMux()
	st.routes(mux)
	mux.ServeHTTP(w, r)
	var out map[string]any
	json.Unmarshal(w.Body.Bytes(), &out)
	return w, out
}

func TestLookupHit(t *testing.T) {
	st := setup(t)
	w, out := get(st, "/CodeSystem/$lookup?system=http://hl7.org/fhir/sid/icd-10&code=I10")
	if w.Code != 200 {
		t.Fatalf("code %d", w.Code)
	}
	params := out["parameter"].([]any)
	last := params[len(params)-1].(map[string]any)
	if last["valueString"] != "Essential (primary) hypertension" {
		t.Fatalf("got %v", last)
	}
}

func TestLookupMiss404(t *testing.T) {
	_, out := get(setup(t), "/CodeSystem/$lookup?system=http://hl7.org/fhir/sid/icd-10&code=ZZZ9")
	params := out["parameter"].([]any)
	if params[0].(map[string]any)["valueString"] != "false" {
		t.Fatalf("expected result=false, got %v", out)
	}
}

func TestValidateCode(t *testing.T) {
	st := setup(t)
	_, ok := get(st, "/ValueSet/$validate-code?system=https://nafdac.gov.ng/drug-codes&code=NG-AMOX500")
	if ok["parameter"].([]any)[0].(map[string]any)["valueString"] != "true" {
		t.Fatal("expected valid")
	}
	_, no := get(st, "/ValueSet/$validate-code?system=https://nafdac.gov.ng/drug-codes&code=NOPE")
	if no["parameter"].([]any)[0].(map[string]any)["valueString"] != "false" {
		t.Fatal("expected invalid")
	}
}

func TestExpandWithFilter(t *testing.T) {
	_, out := get(setup(t), "/ValueSet/$expand?url=https://nhia.gov.ng/tariff&filter=malaria")
	exp := out["expansion"].(map[string]any)
	if exp["total"].(float64) != 2 {
		t.Fatalf("expected 2 malaria tariffs, got %v", exp["total"])
	}
}

func TestSnapshotManifestStableHash(t *testing.T) {
	a := setup(t)
	b := setup(t)
	if a.snapHash != b.snapHash {
		t.Fatal("snapshot hash not deterministic")
	}
}

func TestUnknownSystem(t *testing.T) {
	w, _ := get(setup(t), "/ValueSet/$expand?url=http://unknown")
	if w.Code != 404 {
		t.Fatalf("want 404, got %d", w.Code)
	}
}
