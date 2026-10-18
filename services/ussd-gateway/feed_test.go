package main

// USSD patient read channel tests (ADR-0013): menu flow, telco-phone
// binding, no-enumeration, screen-length discipline.

import (
	"strings"
	"testing"
)

type fakeReader struct {
	items    []FeedItem
	err      error
	gotID    string
	gotPhone string
}

func (f *fakeReader) Feed(patientID, phone string) ([]FeedItem, error) {
	f.gotID, f.gotPhone = patientID, phone
	return f.items, f.err
}

func TestUSSDPatientFeed(t *testing.T) {
	fr := &fakeReader{items: []FeedItem{
		{Kind: "lab_ready", Text: "OpenHealth: your test result is ready at PHC-Kawo. Please come with your card."},
		{Kind: "appointment", Text: "OpenHealth: reminder — your appointment at PHC-Kawo is tomorrow 9am."},
	}}
	g := NewGatewayWithFeed(staticChecker{}, fr)

	// Open menu with the telco-supplied number.
	resp := g.HandleWithPhone("sess-1", "+2348031234567", "")
	if !strings.Contains(resp, "3. My clinic updates") {
		t.Fatalf("menu missing updates option: %q", resp)
	}
	// Choose 3 → prompted for patient code.
	resp = g.HandleWithPhone("sess-1", "+2348031234567", "3")
	if !strings.Contains(resp, "patient code") {
		t.Fatalf("expected code prompt, got %q", resp)
	}
	// Enter the code → feed rendered, phone taken from the telco, not typed.
	resp = g.HandleWithPhone("sess-1", "+2348031234567", "3*PT-1001")
	if fr.gotID != "PT-1001" || fr.gotPhone != "+2348031234567" {
		t.Fatalf("feed lookup: id=%q phone=%q", fr.gotID, fr.gotPhone)
	}
	if !strings.HasPrefix(resp, "END Clinic updates (2):") || !strings.Contains(resp, "test result is ready") {
		t.Fatalf("feed render: %q", resp)
	}
	if len(resp) > 182 {
		t.Fatalf("USSD screen too long (%d chars): %q", len(resp), resp)
	}
}

func TestUSSDFeedNoEnumeration(t *testing.T) {
	fr := &fakeReader{err: errUnknown{}} // upstream 404 (wrong code/phone)
	g := NewGatewayWithFeed(staticChecker{}, fr)
	g.HandleWithPhone("sess-2", "+2348000000000", "")
	g.HandleWithPhone("sess-2", "+2348000000000", "3")
	resp := g.HandleWithPhone("sess-2", "+2348000000000", "3*PT-WRONG")
	if !strings.Contains(resp, "No clinic updates found") {
		t.Fatalf("mismatch must read like an empty feed, got %q", resp)
	}
}

func TestUSSDFeedNoPhoneFromTelco(t *testing.T) {
	g := NewGatewayWithFeed(staticChecker{}, &fakeReader{})
	g.Handle("sess-3", "")
	g.Handle("sess-3", "3")
	resp := g.Handle("sess-3", "3*PT-1001")
	if !strings.Contains(resp, "did not send your number") {
		t.Fatalf("missing msisdn must be explained, got %q", resp)
	}
}

func TestUSSDFeedEmpty(t *testing.T) {
	g := NewGatewayWithFeed(staticChecker{}, &fakeReader{})
	g.HandleWithPhone("sess-4", "+2348012345678", "")
	g.HandleWithPhone("sess-4", "+2348012345678", "3")
	resp := g.HandleWithPhone("sess-4", "+2348012345678", "3*PT-1001")
	if !strings.Contains(resp, "No clinic updates yet") {
		t.Fatalf("empty feed: %q", resp)
	}
}

func TestUSSDFeedTrimming(t *testing.T) {
	long := "OpenHealth: " + strings.Repeat("a very long notification body. ", 10)
	fr := &fakeReader{items: []FeedItem{
		{Kind: "a", Text: long}, {Kind: "b", Text: long}, {Kind: "c", Text: long},
	}}
	g := NewGatewayWithFeed(staticChecker{}, fr)
	g.HandleWithPhone("sess-5", "+2348012345678", "")
	g.HandleWithPhone("sess-5", "+2348012345678", "3")
	resp := g.HandleWithPhone("sess-5", "+2348012345678", "3*PT-1001")
	if len(resp) > 182 {
		t.Fatalf("screen exceeds aggregator cap (%d)", len(resp))
	}
}
