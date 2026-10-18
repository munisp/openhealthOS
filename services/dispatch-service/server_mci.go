package main

// HTTP surface for MCI mode + the surge view + the voice/IVR seam
// (ADR-0022).

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"sync"
)

func (s server) mciRoutes(mux *http.ServeMux) {
	mux.HandleFunc("POST /mci", s.mciCreate)
	mux.HandleFunc("GET /mci", s.mciList)
	mux.HandleFunc("GET /mci/{id}", s.mciGet)
	mux.HandleFunc("POST /mci/{id}/casualties", s.mciAddCasualty)
	mux.HandleFunc("POST /mci/{id}/casualties/{cid}", s.mciUpdateCasualty)
	mux.HandleFunc("GET /mci/{id}/surge", s.mciSurge)
	mux.HandleFunc("POST /webhooks/voice", s.voiceInbound)
	mux.HandleFunc("POST /webhooks/voice/converse", s.voiceConverse)
}

func (s server) mciCreate(w http.ResponseWriter, r *http.Request) {
	var req struct {
		IncidentID string  `json:"incident_id"`
		Name       string  `json:"name"`
		Lat        float64 `json:"lat"`
		Lng        float64 `json:"lng"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		fail(w, fmt.Errorf("%w: bad json", ErrValidation))
		return
	}
	m, err := s.mci.Create(req.IncidentID, req.Name, req.Lat, req.Lng, s.ix.now())
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 201, m)
}

func (s server) mciList(w http.ResponseWriter, _ *http.Request) {
	out := []map[string]any{}
	for _, m := range s.mci.List() {
		out = append(out, map[string]any{
			"id": m.ID, "name": m.Name, "created_at": m.CreatedAt, "counts": m.Counts(),
		})
	}
	writeJSON(w, 200, map[string]any{"mcis": out})
}

func (s server) mciGet(w http.ResponseWriter, r *http.Request) {
	m, err := s.mci.Get(r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 200, map[string]any{"mci": m, "counts": m.Counts()})
}

func (s server) mciAddCasualty(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Tag         string `json:"tag"`
		AgeGroup    string `json:"age_group"`
		Description string `json:"description"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		fail(w, fmt.Errorf("%w: bad json", ErrValidation))
		return
	}
	m, err := s.mci.AddCasualty(r.PathValue("id"), req.Tag, req.AgeGroup, req.Description, s.ix.now())
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 201, m)
}

func (s server) mciUpdateCasualty(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Tag        string `json:"tag"`
		Status     string `json:"status"`
		FacilityID string `json:"facility_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		fail(w, fmt.Errorf("%w: bad json", ErrValidation))
		return
	}
	m, err := s.mci.UpdateCasualty(r.PathValue("id"), r.PathValue("cid"),
		req.Tag, req.Status, req.FacilityID, s.ix.now())
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 200, m)
}

// mciSurge answers the transport question: facilities near the scene
// with beds, sorted by distance — so the load spreads instead of
// overwhelming the nearest hospital. Registry /facilities/near carries
// beds since ADR-0014.
func (s server) mciSurge(w http.ResponseWriter, r *http.Request) {
	m, err := s.mci.Get(r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	u := fmt.Sprintf("%s/facilities/near?lat=%f&lng=%f&radius_km=%s&limit=20",
		s.ix.registryURL, m.Lat, m.Lng, url.QueryEscape(r.URL.Query().Get("radius_km")))
	if r.URL.Query().Get("radius_km") == "" {
		u = fmt.Sprintf("%s/facilities/near?lat=%f&lng=%f&radius_km=50&limit=20",
			s.ix.registryURL, m.Lat, m.Lng)
	}
	req, err := http.NewRequestWithContext(context.Background(), http.MethodGet, u, nil)
	if err != nil {
		fail(w, err)
		return
	}
	resp, err := s.ix.client.Do(req)
	if err != nil {
		fail(w, fmt.Errorf("registry unreachable: %w", err))
		return
	}
	defer resp.Body.Close()
	var out struct {
		Results []struct {
			FacilityMatch
			Beds int `json:"beds"`
		} `json:"results"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		fail(w, fmt.Errorf("registry answer unreadable: %w", err))
		return
	}
	sort.Slice(out.Results, func(a, b int) bool {
		return out.Results[a].DistanceKm < out.Results[b].DistanceKm
	})
	counts := m.Counts()
	writeJSON(w, 200, map[string]any{
		"mci": m.ID, "casualties_at_scene": counts["at_scene"],
		"red_at_scene": counts[TagRed+"_at_scene"], "facilities": out.Results,
		"guidance": "distribute red casualties across facilities with beds, nearest first",
	})
}

// ----------------------------------------------------------- voice / IVR

// voiceInbound is the IVR seam (ADR-0022): a telecom partner's voice
// gateway POSTs call events here. DTMF "1" = emergency → an SOS intake
// on the voice channel with cell-tower location (the partner resolves
// the number to a rough fix when it can). The response tells the IVR
// what to play back. Menu structure is deliberately minimal: in an
// emergency, one key is all a caller should need.
func (s server) voiceInbound(w http.ResponseWriter, r *http.Request) {
	var req struct {
		From   string  `json:"from"` // caller number, E.164
		DTMF   string  `json:"dtmf"` // key pressed, if any
		CallID string  `json:"call_id"`
		Lat    float64 `json:"lat"` // cell-tower fix when the partner provides one
		Lng    float64 `json:"lng"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, 200, map[string]any{"received": false}) // same no-retry doctrine as WhatsApp
		return
	}
	if req.From == "" {
		writeJSON(w, 200, map[string]any{"received": false, "play": "We could not identify your number. Please try again."})
		return
	}
	switch req.DTMF {
	case "":
		// New call: greet and menu. One key, one job.
		writeJSON(w, 200, map[string]any{
			"received":    true,
			"play":        "OpenHealth emergency line. Press 1 now if this is a medical emergency.",
			"expect_dtmf": true, "call_id": req.CallID,
		})
	case "1":
		inc, created, err := s.intake("voice", req.From, "", req.Lat, req.Lng,
			"cell-tower", "voice emergency call — DTMF 1")
		if err != nil {
			writeJSON(w, 200, map[string]any{"received": false, "play": "Please hold on and try again."})
			return
		}
		msg := "Your emergency has been registered. Help is being arranged. Keep this phone on."
		if !created {
			msg = "We already have your emergency open. Help is being arranged. Keep this phone on."
		}
		writeJSON(w, 200, map[string]any{
			"received": true, "play": msg,
			"incident_id": inc.ID, "triage": inc.Triage, "call_id": req.CallID,
		})
	default:
		writeJSON(w, 200, map[string]any{
			"received":    true,
			"play":        "Press 1 if this is a medical emergency.",
			"expect_dtmf": true, "call_id": req.CallID,
		})
	}
}

// ------------------------------------------------- conversational voice (AVA seam, ADR-0024)

// voiceDialogues holds per-call conversational state. Voice calls are
// ephemeral — minutes, not days — so memory is the right store; a restart
// mid-call degrades gracefully to a fresh dialogue (the caller simply
// repeats the complaint).
type voiceDialogues struct {
	mu    sync.Mutex
	calls map[string]*voiceDialogue
}

type voiceDialogue struct {
	complaints []string
	triage     string
	confirmed  bool
	done       bool
}

func newVoiceDialogues() *voiceDialogues {
	return &voiceDialogues{calls: map[string]*voiceDialogue{}}
}

func (v *voiceDialogues) get(callID string) *voiceDialogue {
	v.mu.Lock()
	defer v.mu.Unlock()
	d, ok := v.calls[callID]
	if !ok {
		d = &voiceDialogue{}
		v.calls[callID] = d
	}
	return d
}

// voiceConverse is the conversational half of the voice seam (ADR-0024).
// An on-prem Asterisk + AVA stack (MIT-licensed AVA Core) handles STT/TTS
// in the caller's language and POSTs each transcribed utterance here. The
// platform side stays deterministic — the same keyword triage protocol as
// every other channel — because an emergency line must be auditable and
// must work with zero cloud dependencies. The LLM lives at the edge (in
// AVA) where it only translates speech to text and back; triage decisions
// never leave deterministic code.
func (s server) voiceConverse(w http.ResponseWriter, r *http.Request) {
	var req struct {
		From      string  `json:"from"`
		CallID    string  `json:"call_id"`
		Utterance string  `json:"utterance"`
		Lat       float64 `json:"lat"`
		Lng       float64 `json:"lng"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.CallID == "" {
		writeJSON(w, 200, map[string]any{"received": false, "say": "Please say that again."})
		return
	}
	if req.From == "" {
		writeJSON(w, 200, map[string]any{"received": false, "say": "I could not identify your number. Please call again."})
		return
	}
	d := s.voice.get(req.CallID)
	utt := strings.ToLower(strings.TrimSpace(req.Utterance))

	switch {
	case d.done:
		writeJSON(w, 200, map[string]any{"received": true, "done": true,
			"say": "Your emergency is already registered. Help is being arranged. Keep this phone on."})
	case len(d.complaints) == 0 && utt != "":
		d.complaints = append(d.complaints, utt)
		d.triage, _ = triage(defaultTriageProtocol, utt)
		writeJSON(w, 200, map[string]any{"received": true, "expect_more": true, "triage": d.triage,
			"say": "I understand. Is there anything else happening — for example bleeding, unconsciousness, or trouble breathing?"})
	case !d.confirmed:
		if utt != "" && utt != "no" && utt != "nothing" && utt != "no nothing else" {
			d.complaints = append(d.complaints, utt)
		}
		whole := strings.Join(d.complaints, ". ")
		d.triage, _ = triage(defaultTriageProtocol, whole)
		d.confirmed = true
		writeJSON(w, 200, map[string]any{"received": true, "expect_more": true, "triage": d.triage,
			"say": "I am sending help now. Please confirm: is the patient with you? Say yes or no."})
	case !d.done:
		inc, created, err := s.intake("voice", req.From, "", req.Lat, req.Lng,
			"cell-tower", "voice conversation: "+strings.Join(d.complaints, ". "))
		if err != nil {
			writeJSON(w, 200, map[string]any{"received": false, "say": "Please hold on and try again."})
			return
		}
		d.done = true
		msg := "Help is on the way. Keep the patient still and keep this phone on. We will call you back."
		if !created {
			msg = "We already have your emergency open. Help is being arranged. Keep this phone on."
		}
		writeJSON(w, 200, map[string]any{"received": true, "done": true,
			"incident_id": inc.ID, "triage": inc.Triage, "say": msg})
	}
}
