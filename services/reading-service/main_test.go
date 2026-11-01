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
	srv := server{engine: f, reportkit: f, viewlink: f}
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
	srv := server{engine: f, reportkit: f, viewlink: f}
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
	srv := server{engine: f, reportkit: f, viewlink: f}
	req := httptest.NewRequest(http.MethodGet, "/worklist?domain=ecg&reader_id=r1&day=0", nil)
	w := httptest.NewRecorder()
	srv.routes().ServeHTTP(w, req)
	if w.Code != http.StatusBadGateway {
		t.Fatalf("expected 502, got %d", w.Code)
	}
}

func TestQueryParamsFoldIntoArgs(t *testing.T) {
	f := &fakeEngine{result: `[]`}
	srv := server{engine: f, reportkit: f, viewlink: f}
	req := httptest.NewRequest(http.MethodGet, "/worklist?domain=ecg&reader_id=r1&day=4", nil)
	w := httptest.NewRecorder()
	srv.routes().ServeHTTP(w, req)
	if f.lastArgs["day"] != 4 || f.lastArgs["reader_id"] != "r1" {
		t.Fatalf("query not folded: %v", f.lastArgs)
	}
}

func TestHealthz(t *testing.T) {
	srv := server{engine: &fakeEngine{}, reportkit: &fakeEngine{}, viewlink: &fakeEngine{}}
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	w := httptest.NewRecorder()
	srv.routes().ServeHTTP(w, req)
	if w.Code != 200 || strings.TrimSpace(w.Body.String()) != "ok" {
		t.Fatalf("healthz broken: %d %s", w.Code, w.Body)
	}
}

func TestViewerLaunchRouteUsesViewlinkEngine(t *testing.T) {
	tl := &fakeEngine{err: errors.New("teleread should not be called")}
	vl := &fakeEngine{result: `{"url":"https://v/viewer?StudyInstanceUIDs=1.2"}`}
	srv := server{engine: tl, reportkit: tl, viewlink: vl}
	req := httptest.NewRequest(http.MethodGet,
		"/viewer/launch?kind=dicom-ohif&artifact_ref=1.2&reader_id=r1&session_id=s1&case_id=c1&day=3", nil)
	w := httptest.NewRecorder()
	srv.routes().ServeHTTP(w, req)
	if w.Code != 200 {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	if vl.lastCmd != "launch" || vl.lastArgs["artifact_ref"] != "1.2" || vl.lastArgs["day"] != 3 {
		t.Fatalf("bad forward: %s %v", vl.lastCmd, vl.lastArgs)
	}
}

func TestStructuredReportRoutesUseReportkitEngine(t *testing.T) {
	rk := &fakeEngine{result: `{"id":"R1","status":"draft"}`}
	srv := server{engine: &fakeEngine{}, reportkit: rk, viewlink: &fakeEngine{}}
	req := httptest.NewRequest(http.MethodPost, "/reports/structured",
		strings.NewReader(`{"report_id":"R1","template_id":"birads-mammo@1.0.0","answers":{},"case_id":"c","author_id":"a","day":1}`))
	w := httptest.NewRecorder()
	srv.routes().ServeHTTP(w, req)
	if w.Code != 200 || rk.lastCmd != "draft" {
		t.Fatalf("structured draft broken: %d %s", w.Code, rk.lastCmd)
	}
	req = httptest.NewRequest(http.MethodGet, "/reports/export?id=R1&format=html", nil)
	w = httptest.NewRecorder()
	srv.routes().ServeHTTP(w, req)
	if rk.lastCmd != "export-html" {
		t.Fatalf("format=html must route to export-html, got %s", rk.lastCmd)
	}
}

func TestViewerLaunchDomainErrorIs422(t *testing.T) {
	vl := &fakeEngine{errMsg: "viewer 'ecg-canvas' is not configured for this deployment", err: errDomain}
	srv := server{engine: &fakeEngine{}, reportkit: &fakeEngine{}, viewlink: vl}
	req := httptest.NewRequest(http.MethodGet,
		"/viewer/launch?kind=ecg-canvas&artifact_ref=t1&reader_id=r1&session_id=s1&case_id=c1&day=1", nil)
	w := httptest.NewRecorder()
	srv.routes().ServeHTTP(w, req)
	if w.Code != http.StatusUnprocessableEntity {
		t.Fatalf("expected 422, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "not configured") {
		t.Fatalf("honest refusal swallowed: %s", w.Body)
	}
}
