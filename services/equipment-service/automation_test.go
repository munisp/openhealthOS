package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func registerFridge(t *testing.T, svc *Service) string {
	t.Helper()
	e, err := svc.Register(Equipment{Facility: "F-1", Type: "cold-chain-fridge", Model: "TCW-2000"})
	if err != nil {
		t.Fatal(err)
	}
	return e.ID
}

func TestColdChainBreachOpensCriticalTicket(t *testing.T) {
	svc := NewService()
	id := registerFridge(t, svc)
	res, err := EvaluateReading(svc, Reading{EquipmentID: id, Metric: "cold_chain_temp", Value: 9.4})
	if err != nil || res.Action != "opened" {
		t.Fatalf("breach must open: %v %v", res, err)
	}
	if res.Ticket.Priority != "P1" {
		t.Fatalf("cold-chain breach is P1, got %s", res.Ticket.Priority)
	}
	eq, _ := svc.GetEquipment(id)
	if eq.Status != "down" {
		t.Fatalf("breached equipment must go down, got %s", eq.Status)
	}
}

func TestDuplicateBreachSuppressed(t *testing.T) {
	svc := NewService()
	id := registerFridge(t, svc)
	EvaluateReading(svc, Reading{EquipmentID: id, Metric: "cold_chain_temp", Value: 9.0})
	res, _ := EvaluateReading(svc, Reading{EquipmentID: id, Metric: "cold_chain_temp", Value: 11.0})
	if res.Action != "none" {
		t.Fatalf("second breach must not open a duplicate: %v", res.Action)
	}
	if n := len(svc.ListTickets("open")); n != 1 {
		t.Fatalf("open tickets: %d", n)
	}
}

func TestRecoveryAutoResolvesWithHysteresis(t *testing.T) {
	svc := NewService()
	id := registerFridge(t, svc)
	EvaluateReading(svc, Reading{EquipmentID: id, Metric: "cold_chain_temp", Value: 9.0})
	// 7.5°C: out of breach but inside hysteresis band — ticket stays open
	res, _ := EvaluateReading(svc, Reading{EquipmentID: id, Metric: "cold_chain_temp", Value: 7.5})
	if res.Action != "none" {
		t.Fatalf("hysteresis band must not resolve: %v", res.Action)
	}
	res, _ = EvaluateReading(svc, Reading{EquipmentID: id, Metric: "cold_chain_temp", Value: 5.0})
	if res.Action != "resolved" {
		t.Fatalf("genuine recovery must resolve: %v", res.Action)
	}
	if n := len(svc.ListTickets("open")); n != 0 {
		t.Fatalf("open tickets after recovery: %d", n)
	}
}

func TestUnknownMetricIgnored(t *testing.T) {
	svc := NewService()
	id := registerFridge(t, svc)
	res, err := EvaluateReading(svc, Reading{EquipmentID: id, Metric: "humidity", Value: 99})
	if err != nil || res.Action != "ignored" {
		t.Fatalf("unknown metric: %v %v", res, err)
	}
}

func TestUnknownEquipment404ViaHTTP(t *testing.T) {
	s := server{svc: NewService()}
	srv := httptest.NewServer(s.routes())
	defer srv.Close()
	body, _ := json.Marshal(Reading{EquipmentID: "EQ-NOPE", Metric: "cold_chain_temp", Value: 9})
	resp, err := http.Post(srv.URL+"/telemetry/reading", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("want 404, got %s", resp.Status)
	}
}

func TestHTTPBreachEndToEnd(t *testing.T) {
	svc := NewService()
	id := registerFridge(t, svc)
	s := server{svc: svc}
	srv := httptest.NewServer(s.routes())
	defer srv.Close()
	body, _ := json.Marshal(Reading{EquipmentID: id, Metric: "power_soc", Value: 12})
	resp, err := http.Post(srv.URL+"/telemetry/reading", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var res AutomationResult
	json.NewDecoder(resp.Body).Decode(&res)
	if res.Action != "opened" || res.Ticket.Priority != "P2" {
		t.Fatalf("power breach: %v", res)
	}
}
