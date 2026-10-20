package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func rigTags(t *testing.T) (*Service, *http.ServeMux, Equipment) {
	t.Helper()
	t.Setenv("OH_ASSET_SECRET", "test-secret-key-123456")
	svc := NewService()
	svc.now = func() time.Time { return time.Unix(1761000000, 0) }
	eq, err := svc.Register(Equipment{Facility: "KDH-01", Type: "mri", Model: "Siemens 1.5T"})
	if err != nil {
		t.Fatal(err)
	}
	s := server{svc: svc}
	return svc, s.routes(), eq
}

func TestTagMintAndScanRoundTrip(t *testing.T) {
	svc, mux, eq := rigTags(t)

	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("POST", "/equipment/"+eq.ID+"/tag", nil))
	if rec.Code != 201 {
		t.Fatalf("tag: %d %s", rec.Code, rec.Body)
	}
	var tagOut struct{ Tag string `json:"tag"` }
	json.NewDecoder(rec.Body).Decode(&tagOut)

	body, _ := json.Marshal(map[string]string{
		"tag": tagOut.Tag, "action": "transfer", "actor": "nurse-7", "location": "Ward B"})
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("POST", "/scan", bytes.NewReader(body)))
	if rec.Code != 200 {
		t.Fatalf("scan: %d %s", rec.Code, rec.Body)
	}
	got, _ := svc.GetEquipment(eq.ID)
	if got.Location != "Ward B" {
		t.Fatalf("location must move on transfer: %+v", got)
	}
	log := svc.CustodyLog(eq.ID)
	if len(log) != 1 || log[0].Actor != "nurse-7" {
		t.Fatalf("custody spine: %+v", log)
	}
}

func TestScanTamperedTagRejected(t *testing.T) {
	svc, mux, eq := rigTags(t)
	tag, _ := MintTag(eq.Facility, eq.ID, svc.now())
	evil := tag[:len(tag)-2] + "00"
	body, _ := json.Marshal(map[string]string{"tag": evil, "action": "transfer", "actor": "mallory"})
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("POST", "/scan", bytes.NewReader(body)))
	if rec.Code != 400 {
		t.Fatalf("tampered tag must be rejected, got %d", rec.Code)
	}
	if len(svc.CustodyLog(eq.ID)) != 0 {
		t.Fatal("rejected scan must not reach the custody spine")
	}
}

func TestMaintenanceFlagChangesStatus(t *testing.T) {
	svc, _, eq := rigTags(t)
	tag, _ := MintTag(eq.Facility, eq.ID, svc.now())
	if _, err := svc.Scan(tag, "maintenance_flag", "tech-1", "", "strange noise"); err != nil {
		t.Fatal(err)
	}
	got, _ := svc.GetEquipment(eq.ID)
	if got.Status != "in-maintenance" {
		t.Fatalf("maintenance flag must change status: %+v", got)
	}
}

func TestTaggingDisabledWithoutSecret(t *testing.T) {
	t.Setenv("OH_ASSET_SECRET", "")
	svc := NewService()
	eq, _ := svc.Register(Equipment{Facility: "F", Type: "fridge"})
	if _, err := MintTag(eq.Facility, eq.ID, svc.now()); err == nil {
		t.Fatal("mint must fail closed without the secret")
	}
}

func TestScanUnknownAssetRejected(t *testing.T) {
	svc, _, _ := rigTags(t)
	tag, _ := MintTag("KDH-01", "GHOST-1", svc.now())
	if _, err := svc.Scan(tag, "locate", "a", "", ""); err != ErrNotFound {
		t.Fatalf("unknown asset: %v", err)
	}
}
