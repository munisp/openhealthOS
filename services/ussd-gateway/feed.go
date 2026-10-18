package main

// Patient read channel over USSD (ADR-0013): the patient PWA's feed,
// available on a ₦5,000 feature phone. The telco supplies the caller's
// phone number (msisdn) with every session; the patient enters their
// patient code — the same two factors as the web portal, so the USSD
// channel inherits its no-enumeration property for free.
//
// FeedReader is a seam: the HTTP implementation calls the notification
// service's /portal/feed; the default is a polite empty feed.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// FeedItem is one patient-facing notification, already clinical-detail-free
// (the notification rail never sends clinical detail — USSD inherits that).
type FeedItem struct {
	Kind   string `json:"kind"`
	Text   string `json:"text"`
	SentAt string `json:"sent_at"`
}

// FeedReader fetches a patient's notification feed. phone comes from the
// telco (msisdn) — the patient never types it, and a wrong code simply
// finds no feed (404 upstream → friendly empty message).
type FeedReader interface {
	Feed(patientID, phone string) ([]FeedItem, error)
}

// EmptyFeedReader is the default when NOTIFY_URL is unset.
type EmptyFeedReader struct{}

func (EmptyFeedReader) Feed(_, _ string) ([]FeedItem, error) { return nil, nil }

// HTTPFeedReader reads the notification service's portal feed.
type HTTPFeedReader struct {
	BaseURL string // e.g. http://notification-service:8089
	Client  *http.Client
}

func (h HTTPFeedReader) Feed(patientID, phone string) ([]FeedItem, error) {
	c := h.Client
	if c == nil {
		c = &http.Client{Timeout: 5 * time.Second}
	}
	u := fmt.Sprintf("%s/portal/feed?patient_id=%s&phone=%s",
		strings.TrimRight(h.BaseURL, "/"), url.QueryEscape(patientID), url.QueryEscape(phone))
	resp, err := c.Get(u)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		// 404 = no match (never say which factor failed); 5xx = transient.
		return nil, fmt.Errorf("feed: status %d", resp.StatusCode)
	}
	var out struct {
		Notifications []FeedItem `json:"notifications"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	return out.Notifications, nil
}

// ussdFeed renders the newest items as one USSD screen. USSD messages cap
// at ~182 chars on most aggregators — lead with count, then items newest
// first, trimmed hard.
func ussdFeed(items []FeedItem) string {
	if len(items) == 0 {
		return "END No clinic updates yet. We will send an SMS when there is news."
	}
	var b strings.Builder
	fmt.Fprintf(&b, "END Clinic updates (%d):\n", len(items))
	shown := 0
	for i := len(items) - 1; i >= 0 && shown < 3; i-- {
		line := strings.TrimPrefix(items[i].Text, "OpenHealth: ")
		if b.Len()+len(line) > 170 {
			break
		}
		fmt.Fprintf(&b, "- %s\n", line)
		shown++
	}
	return strings.TrimRight(b.String(), "\n")
}
