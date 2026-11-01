package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type fakeEngine struct {
	lastCmd  string
	lastArgs map[string]any
	result   string
	errMsg   string
	err      error
}

func (f *fakeEngine) Call(cmd string, args map[string]any) (json.RawMessage, string, error) {
	f.lastCmd, f.lastArgs = cmd, args
	return json.RawMessage(f.result), f.errMsg, f.err
}

func TestOrderPassesThrough(t *testing.T) {
	f := &fakeEngine{result: `{"case_id":"S1","state":"unassigned"}`}
	srv := server{engine: f}
	req := httptest.NewRequest(http.MethodPost, "/orders",
		strings.NewReader(`{"domain":"ecg","case_id":"S1","day":0}`))
	w := httptest.NewRecorder()
	srv.routes().ServeHTTP(w, req)
	if w.Code != 200 {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	if f.lastCmd != "order" {
		t.Fatalf("command %q", f.lastCmd)
	}
	if f.lastArgs["domain"] != "ecg" {
		t.Fatalf("args not forwarded: %v", f.lastArgs)
	}
}

func TestDomainErrorIs422WithMessage(t *testing.T) {
	f := &fakeEngine{errMsg: "no clinical question — looking, not diagnosis", err: errDomain}
	srv := server{engine: f}
	req := httptest.NewRequest(http.MethodPost, "/orders",
		strings.NewReader(`{"domain":"ecg"}`))
	w := httptest.NewRecorder()
	srv.routes().ServeHTTP(w, req)
	if w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "no clinical question") {
		t.Fatalf("engine message swallowed: %s", w.Body)
	}
}

func TestEngineDownIs502(t *testing.T) {
	f := &fakeEngine{err: errors.New("boom")}
	srv := server{engine: f}
	req := httptest.NewRequest(http.MethodGet, "/worklist?domain=ecg&reader_id=r1&day=0", nil)
	w := httptest.NewRecorder()
	srv.routes().ServeHTTP(w, req)
	if w.Code != http.StatusBadGateway {
		t.Fatalf("expected 502, got %d", w.Code)
	}
}

func TestQueryParamsFoldIntoArgs(t *testing.T) {
	f := &fakeEngine{result: `[]`}
	srv := server{engine: f}
	req := httptest.NewRequest(http.MethodGet, "/worklist?domain=ecg&reader_id=r1&day=4", nil)
	w := httptest.NewRecorder()
	srv.routes().ServeHTTP(w, req)
	if f.lastArgs["day"] != 4 || f.lastArgs["reader_id"] != "r1" {
		t.Fatalf("query not folded: %v", f.lastArgs)
	}
}

func TestHealthz(t *testing.T) {
	srv := server{engine: &fakeEngine{}}
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	w := httptest.NewRecorder()
	srv.routes().ServeHTTP(w, req)
	if w.Code != 200 || strings.TrimSpace(w.Body.String()) != "ok" {
		t.Fatalf("healthz broken: %d %s", w.Code, w.Body)
	}
}
