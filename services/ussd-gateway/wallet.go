package main

// Citizen health wallet over USSD (ADR-0030): a feature phone checks
// balances and pays a facility short-code without data. The gateway
// never holds money — it queries wallet-service, which enforces the
// healthcare allow-list before any posting.

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// WalletReader answers balance questions; WalletPayer moves money.
// wallet-service implements both over HTTP.
type WalletReader interface {
	Balance(nin string) (totalKobo int, envelopes map[string]int, err error)
}

type WalletPayer interface {
	Pay(nin, envelope, destination string, amountKobo int, ref string) error
}

// HTTPWallet binds the wallet-service citizen endpoints.
type HTTPWallet struct {
	BaseURL string
	Client  *http.Client
}

func (h HTTPWallet) http() *http.Client {
	if h.Client != nil {
		return h.Client
	}
	return &http.Client{Timeout: 8 * time.Second}
}

func (h HTTPWallet) Balance(nin string) (int, map[string]int, error) {
	resp, err := h.http().Get(strings.TrimRight(h.BaseURL, "/") +
		"/citizens/" + url.PathEscape(nin) + "/balance")
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return 0, nil, fmt.Errorf("wallet: HTTP %d", resp.StatusCode)
	}
	var body struct {
		TotalKobo int            `json:"total_kobo"`
		Envelopes map[string]int `json:"envelopes"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return 0, nil, err
	}
	return body.TotalKobo, body.Envelopes, nil
}

func (h HTTPWallet) Pay(nin, envelope, destination string, amountKobo int, ref string) error {
	payload := fmt.Sprintf(
		`{"nin":%q,"envelope":%q,"destination":%q,"amount_kobo":%d,"ref":%q}`,
		nin, envelope, destination, amountKobo, ref)
	req, err := http.NewRequest("POST", strings.TrimRight(h.BaseURL, "/")+"/citizens/pay",
		strings.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Actor", "ussd:"+nin)
	resp, err := h.http().Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		msg, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("wallet refused (%d): %s", resp.StatusCode, strings.TrimSpace(string(msg)))
	}
	return nil
}
