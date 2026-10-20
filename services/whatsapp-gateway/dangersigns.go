// dangersigns.go — maternal & emergency danger-sign escalation
// (innovation 6).
//
// A patient typing "I'm bleeding heavily" into the booking chat must never
// get a slot picker back. Danger-sign phrases — in all five platform
// languages — short-circuit the dialogue from ANY state: the patient gets
// urgent guidance immediately, and the message fires an escalation into
// the howUdeyChat staff queue (admin /v1/escalate) so a nurse sees it
// within seconds.
//
// Keyword sets are clinician-reviewed STARTER sets, deliberately narrow:
// false negatives are handled by "ask the clinic" (menu 5, human triage);
// false positives waste a nurse's minute. Tune per deployment with the
// state medical officer, never silently.
package main

import (
	"bytes"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)

// dangerSigns maps a canonical sign to trigger phrases per language.
// Matching is substring on the lowercased message — chat register, not
// clinical NLP.
var dangerSigns = map[string][]string{
	"heavy_bleeding": {
		// en + pcm
		"bleeding heavy", "bleeding heavily", "heavy bleeding", "blood dey rush",
		"blood dey flow", "i dey bleed", "am bleeding", "soaking the pad",
		// ha
		"zub da jini", "jini yana zuba", "ina zubar jini",
		// yo
		"ẹjẹ ń sàn", "ẹjẹ jinlẹ", "mo ń jẹjẹ",
		// ig
		"ọbara na-asị", "ọbara m na-asị", "na-asị ọbara",
	},
	"baby_not_moving": {
		"baby not moving", "baby is not moving", "baby no dey move", "baby stop to move", "no movement",
		"baby not dey kick",
		"jariri baya motsi", "baby baya motsawa",
		"ọmọ kò ń rìn", "ọmọ mi kò rìnrìn",
		"nwa m anaghị akpụ", "nwa anaghị akpụ",
	},
	"convulsion": {
		"convulsion", "convulse", "seizure", "fit", "shaking uncontrollably", "body dey shake",
		"taurewa", "mai taurewa", "jiki yana girgiza",
		"ṣíṣe bíi ẹrù", "ara ń mí ẹrù",
		"ọgbụgbọ", "ahụ na-akpị",
	},
	"severe_headache": {
		"severe headache", "worst headache", "head dey burst", "headache no gree stop",
		"ciwon kai matuƙa", "kai yana ciwo sosai",
		"orí ń gbònà gan-an", "orí ìrora gidigidi",
		"isi na-awọ m otu", "isi ọjọọ",
	},
	"high_fever_pregnancy": {
		"high fever", "fever no dey come down", "body dey hot well well",
		"zazzabi mai ƙarfi", "jiki yana da zafi sosai",
		"iba gan-an", "ara gbònà gidigidi",
		"ahụ ọkụ sie ike", "ahụ m na-ekpo ọkụ",
	},
	"labour_before_time": {
		"water don break", "water break", "labour too early", "contractions started",
		"ruwa ya karya", "cikuwar ruwa",
		"omi ẹyọ ti san", "omi ara ti san",
		"mmiri mmiri ebijala", "mmiri ụmụnaa",
	},
}

// detectDanger returns the canonical sign when the message contains a
// danger phrase. Longest phrase first so "bleeding heavily" beats "bleed".
func detectDanger(text string) (string, bool) {
	low := strings.ToLower(text)
	var signs []string
	for sign := range dangerSigns {
		signs = append(signs, sign)
	}
	// deterministic order for tests
	for i := 0; i < len(signs); i++ {
		for j := i + 1; j < len(signs); j++ {
			if signs[j] < signs[i] {
				signs[i], signs[j] = signs[j], signs[i]
			}
		}
	}
	for _, sign := range signs {
		for _, phrase := range dangerSigns[sign] {
			if strings.Contains(low, phrase) {
				return sign, true
			}
		}
	}
	return "", false
}

// escalateDanger fires the staff escalation (howUdeyChat admin, ADR-0011
// equivalent on the OpenHealth side). Fail-soft: a dead admin service must
// never delay the patient's urgent guidance — the reply already told them
// what to do.
func (s *service) escalateDanger(ss *Session, sign, text string) {
	base := os.Getenv("HUC_ADMIN_URL") // e.g. http://huc-admin:8083
	if base == "" {
		log.Printf("danger-sign %s from %s — HUC_ADMIN_URL unset, escalation skipped", sign, ss.Phone)
		return
	}
	body, _ := json.Marshal(map[string]string{
		"username": ss.Phone,
		"room_id":  ss.Data["room_id"], // matrix channel carries the room; whatsapp leaves it empty
		"reason":   "danger_sign:" + sign,
	})
	req, err := http.NewRequest(http.MethodPost, strings.TrimRight(base, "/")+"/v1/escalate", bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if tok := os.Getenv("HUC_ADMIN_TOKEN"); tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		log.Printf("danger-sign escalation failed: %v", err)
		return
	}
	resp.Body.Close()
	log.Printf("danger-sign %s escalated for %s (staff queue)", sign, ss.Phone)
}
