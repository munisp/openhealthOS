package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// rig with media-service + homeserver + gateway bindings.
func artifactRig(t *testing.T, blob []byte) (*MatrixProvider, *map[string]string) {
	t.Helper()
	got := map[string]string{} // msgtype -> url

	media := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/blobs/") {
			w.Header().Set("Content-Type", "image/png")
			w.Write(blob)
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(media.Close)

	hs := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == "POST" && r.URL.Path == "/_matrix/media/v3/upload":
			io_, _ := io.ReadAll(r.Body)
			if len(io_) == 0 {
				http.Error(w, "empty", 400)
				return
			}
			json.NewEncoder(w).Encode(map[string]string{"content_uri": "mxc://hs/uploaded1"})
		case r.Method == "PUT" && strings.Contains(r.URL.Path, "/send/m.room.message/"):
			var b map[string]string
			json.NewDecoder(r.Body).Decode(&b)
			got[b["msgtype"]] = b["url"] + "|" + b["body"]
			json.NewEncoder(w).Encode(map[string]string{"event_id": "$a"})
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(hs.Close)

	gw := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/internal/matrix-room" {
			json.NewEncoder(w).Encode(map[string]string{"room_id": "!room1:hs"})
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(gw.Close)

	p := NewMatrixProvider(gw.URL, hs.URL, "tok")
	p.MediaURL = media.URL
	return p, &got
}

func TestMatrixArtifactDeliversInlineImage(t *testing.T) {
	png := []byte("\x89PNG-fake-xray")
	p, got := artifactRig(t, png)
	err := p.SendArtifact("08011112222", "OpenHealth: your X-ray result is ready", "blob://media/xray1", "image/png")
	if err != nil {
		t.Fatalf("SendArtifact: %v", err)
	}
	if !strings.HasPrefix((*got)["m.image"], "mxc://hs/uploaded1|") {
		t.Fatalf("no m.image with mxc url delivered: %+v", *got)
	}
	if !strings.Contains((*got)["m.image"], "X-ray") {
		t.Fatalf("caption missing: %+v", *got)
	}
}

func TestMatrixArtifactMissingMediaFails(t *testing.T) {
	p, _ := artifactRig(t, nil)
	p.MediaURL = ""
	if err := p.SendArtifact("08011112222", "x", "blob://media/x", "image/png"); err == nil {
		t.Fatal("no MEDIA_URL must fail the leg so the chain degrades to text")
	}
}

func TestChainArtifactDegradesToText(t *testing.T) {
	// a chain with no artifact-capable leg delivers the caption as text
	lp := &LogProvider{}
	c := NewChainProvider(1, time.Millisecond, lp)
	if err := c.SendArtifact("0801", "caption text", "blob://media/x", "image/png"); err != nil {
		t.Fatalf("degraded send must succeed: %v", err)
	}
	if len(lp.Sent) != 1 || lp.Sent[0][1] != "caption text" {
		t.Fatalf("expected caption via text leg: %+v", lp.Sent)
	}
}

func TestFhirDiagnosticReportMapsToImagingEvent(t *testing.T) {
	report := map[string]any{
		"resourceType": "DiagnosticReport",
		"code":         map[string]any{"text": "Chest X-ray"},
		"subject":      map[string]any{"reference": "Patient/OH-KD-0001"},
		"media": []any{map[string]any{
			"link": map[string]any{"reference": "Blob/media-xray1"},
		}},
	}
	ev, ok := fhirToEvent("DiagnosticReport", report)
	if !ok || ev.Type != "imaging_result" {
		t.Fatalf("DiagnosticReport must map to imaging_result: %+v", ev)
	}
	if ev.Payload["blob_ref"] != "blob://media/xray1" || ev.Payload["modality"] != "Chest X-ray" {
		t.Fatalf("payload missing artifact: %+v", ev.Payload)
	}
	// notification carries the blob ref through HandleEvent
	s := NewService(NewMemoryStore(), &LogProvider{})
	_ = s.store.PutSubscription(Subscription{PatientID: "OH-KD-0001", Phone: "0801"})
	n, err := s.HandleEvent(ev)
	if err != nil || n.BlobRef != "blob://media/xray1" {
		t.Fatalf("notification must carry blob ref: %+v err=%v", n, err)
	}
	if !strings.Contains(n.Text, "image is attached") {
		t.Fatalf("artifact wording expected: %s", n.Text)
	}
}
