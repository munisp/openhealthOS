// Webhook SMS provider — the generic aggregator seam. POSTs
// {"to": phone, "text": text} with an optional bearer token to any
// SMS aggregator that can front a JSON webhook (Africa's Talking,
// Termii, Twilio-shaped gateways). Bounded timeout: a dead aggregator
// slows the drain loop by seconds, never hangs it.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

type WebhookProvider struct {
	url    string
	token  string
	client *http.Client
}

func NewWebhookProvider(url, token string) *WebhookProvider {
	return &WebhookProvider{
		url:    url,
		token:  token,
		client: &http.Client{Timeout: 8 * time.Second},
	}
}

func (p *WebhookProvider) Name() string { return "webhook" }

func (p *WebhookProvider) Send(phone, text string) error {
	body, _ := json.Marshal(map[string]string{"to": phone, "text": text})
	req, err := http.NewRequest(http.MethodPost, p.url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	if p.token != "" {
		req.Header.Set("Authorization", "Bearer "+p.token)
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return fmt.Errorf("sms webhook: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("sms webhook: status %d", resp.StatusCode)
	}
	return nil
}
