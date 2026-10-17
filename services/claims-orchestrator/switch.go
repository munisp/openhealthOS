package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// HTTPSwitch implements the Mojaloop-pattern settlement seam:
// FSPIOP-shaped POST /quotes followed by POST /transfers carrying an
// ILP-style packet. Production binds these calls to a real switch
// deployment (hub + DFSP principals); development uses
// services/payment-switch-mock.
type HTTPSwitch struct {
	BaseURL string
	Client  *http.Client
}

func (s HTTPSwitch) httpClient() *http.Client {
	if s.Client != nil {
		return s.Client
	}
	return &http.Client{Timeout: 10 * time.Second}
}

func (s HTTPSwitch) post(path string, body map[string]any) (map[string]any, error) {
	raw, _ := json.Marshal(body)
	resp, err := s.httpClient().Post(s.BaseURL+path, "application/json", bytes.NewReader(raw))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var m map[string]any
	b, _ := io.ReadAll(resp.Body)
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, fmt.Errorf("switch %s: %s", path, b)
	}
	return m, nil
}

// Settle runs quote → transfer for the payable amount (NGN).
func (s HTTPSwitch) Settle(claimID, payee string, amount int) (map[string]any, error) {
	quote := buildQuote(claimID, payee, amount, time.Now())
	q, err := s.post("/quotes", quote)
	if err != nil {
		return nil, err
	}
	tr := buildTransfer(quote)
	if qid, ok := q["quoteId"].(string); ok {
		tr["quoteId"] = qid
	}
	return s.post("/transfers", tr)
}

// buildQuote shapes an FSPIOP-style quote request (Mojaloop pattern).
func buildQuote(claimID, payee string, amount int, now time.Time) map[string]any {
	return map[string]any{
		"quoteId":       fmt.Sprintf("Q-%s", claimID),
		"transactionId": fmt.Sprintf("TX-%s", claimID),
		"payee":         map[string]any{"partyIdType": "DEVICE", "partyIdentifier": payee},
		"amountType":    "SEND",
		"amount":        fmt.Sprintf("%d", amount),
		"currency":      "NGN",
		"expiration":    now.Add(2 * time.Minute).UTC().Format(time.RFC3339),
	}
}

// buildTransfer shapes the FSPIOP-style transfer against the quoted amount.
func buildTransfer(quote map[string]any) map[string]any {
	pkt := map[string]any{"quoteId": quote["quoteId"], "amount": quote["amount"], "currency": quote["currency"]}
	raw, _ := json.Marshal(pkt)
	return map[string]any{
		"transferId": fmt.Sprintf("TR-%s", quote["quoteId"]),
		"quoteId":    quote["quoteId"],
		"ilpPacket":  base64.RawURLEncoding.EncodeToString(raw),
		"condition":  "prototype-condition-hash",
	}
}
