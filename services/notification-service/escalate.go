package main

// escalate.go — clinical escalations reach the howUdeyChat Matrix rooms
// (ADR-0038 sovereign channel; same admin path as the WhatsApp gateway's
// danger-sign escalation). Referral SLA breaches and adherence lapses
// are clinical events: a referral nobody accepted is a patient quietly
// deteriorating at home. Fail-soft: HUC admin unset or unreachable logs
// and returns — the SMS/USSD rail has already fired by then.

import (
	"bytes"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"time"
)

// escalateToCareRoom posts an escalation into the patient's care room.
// reason is machine-readable ("referral_breached:REF-000012").
func escalateToCareRoom(patientPhone, roomID, reason string) {
	base := os.Getenv("HUC_ADMIN_URL")
	if base == "" || patientPhone == "" {
		return
	}
	body, _ := json.Marshal(map[string]string{
		"username": patientPhone, "room_id": roomID, "reason": reason,
	})
	req, err := http.NewRequest(http.MethodPost, base+"/v1/escalate", bytes.NewReader(body))
	if err != nil {
		return
	}
	if tok := os.Getenv("HUC_ADMIN_TOKEN"); tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	req.Header.Set("Content-Type", "application/json")
	cli := &http.Client{Timeout: 3 * time.Second}
	resp, err := cli.Do(req)
	if err != nil {
		log.Printf("care-room escalation failed (fail-soft): %v", err)
		return
	}
	resp.Body.Close()
}

// handleReferralBreach: the referral tracker (innovation 9) fired — the
// SLA passed with the patient still not accepted/completed. Route to:
//   1. the patient's care room (clinical team sees it in Matrix),
//   2. ops contacts for the referring facility's tenant.
func (s *Service) handleReferralBreach(ev DomainEvent) {
	pid, _ := ev.Payload["patient_id"].(string)
	refID, _ := ev.Payload["referral_id"].(string)
	roomID, _ := ev.Payload["room_id"].(string)
	if sub, err := s.store.Subscription(pid); err == nil && sub.Phone != "" {
		escalateToCareRoom(sub.Phone, roomID, "referral_breached:"+refID)
	}
	for _, c := range opsContactsFor("") {
		if err := s.provider.Send(c, "[FIRING] ReferralBreach (warning): referral "+refID+
			" for patient "+pid+" breached its SLA — patient not yet accepted."); err != nil {
			log.Printf("referral breach alert failed: %v", err)
		}
	}
}
