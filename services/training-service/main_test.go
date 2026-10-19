package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// fakeLedger mirrors the Rust driver's op vocabulary in memory.
type fakeLedger struct {
	balances map[string]int
	posts    []string
}

func newFakeLedger() *fakeLedger { return &fakeLedger{balances: map[string]int{}} }

func (f *fakeLedger) Post(debit, credit, tx string, amount int) (string, error) {
	f.balances[debit] -= amount
	f.balances[credit] += amount
	f.posts = append(f.posts, tx)
	return `{"ok":true}`, nil
}

func (f *fakeLedger) Balance(id string) (int, error) { return f.balances[id], nil }

func testCourse() Course {
	return Course{
		ID: "im-still-standing", Title: "IMNCH Newborn Resuscitation",
		Version: "2025.1", GuidelineVersion: "WHO-HBB-2", CPDPoints: 4, Scheme: "NMCN",
		Modules: []Module{
			{ID: "vid", Title: "Helping Babies Breathe", Type: ModuleVideo, Ref: "blob://media/hbb.mp4"},
			{ID: "quiz", Title: "Knowledge check", Type: ModuleQuiz, PassMark: 70, Questions: []Question{
				{Prompt: "First action?", Options: []string{"Dry & stimulate", "Suction"}, Answer: 0},
				{Prompt: "Rate of BVM?", Options: []string{"40-60/min", "10/min"}, Answer: 0},
			}},
			{ID: "skills", Title: "Practical sign-off", Type: ModuleChecklist,
				Items: []string{"bag-mask seal", "chest rise observed"}},
		},
	}
}

func enrolFixture(t *testing.T) (*TrainingService, *fakeLedger, string) {
	t.Helper()
	led := newFakeLedger()
	svc := NewTrainingService(led)
	if err := svc.AddCourse(testCourse()); err != nil {
		t.Fatal(err)
	}
	e, err := svc.Enrol("im-still-standing", "nurse-42", "phc-karu")
	if err != nil {
		t.Fatal(err)
	}
	return svc, led, e.ID
}

func TestCourseValidation(t *testing.T) {
	led := newFakeLedger()
	svc := NewTrainingService(led)
	bad := testCourse()
	bad.Scheme = "BOGUS"
	if err := svc.AddCourse(bad); err == nil {
		t.Fatal("unknown scheme must be rejected")
	}
	bad = testCourse()
	bad.Modules[0].Ref = "s3://elsewhere"
	if err := svc.AddCourse(bad); err == nil {
		t.Fatal("non-blob media ref must be rejected")
	}
}

func TestFullJourneyToCertificate(t *testing.T) {
	svc, led, enrID := enrolFixture(t)

	if err := svc.MarkDone(enrID, "vid"); err != nil {
		t.Fatal(err)
	}
	// Quiz: first attempt fails (one wrong of two = 50 < 70).
	if _, err := svc.SubmitQuiz(enrID, "quiz", []int{0, 1}); err != ErrPassMarkNotMet {
		t.Fatalf("expected pass-mark failure, got %v", err)
	}
	score, err := svc.SubmitQuiz(enrID, "quiz", []int{0, 0})
	if err != nil || score != 100 {
		t.Fatalf("score=%d err=%v", score, err)
	}
	// Certificate too early: checklist unsigned.
	if _, err := svc.IssueCertificate(enrID); err == nil {
		t.Fatal("certificate before checklist completion must fail")
	}
	if err := svc.SignChecklist(enrID, "skills", "bag-mask seal", "assessor-ada"); err != nil {
		t.Fatal(err)
	}
	if err := svc.SignChecklist(enrID, "skills", "chest rise observed", "assessor-ada"); err != nil {
		t.Fatal(err)
	}

	cert, err := svc.IssueCertificate(enrID)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(cert.Serial, "OH-CPD-") {
		t.Fatal("bad serial shape")
	}
	if led.balances["cpd:nurse-42"] != 4 {
		t.Fatalf("ledger credit = %d, want 4", led.balances["cpd:nurse-42"])
	}
	if led.posts[0] != "cert:"+cert.Serial {
		t.Fatal("ledger tx must carry the certificate serial")
	}
	// Idempotent.
	if _, err := svc.IssueCertificate(enrID); err != ErrAlreadyCertified {
		t.Fatalf("duplicate issuance: %v", err)
	}

	v, err := svc.Verify(cert.Serial)
	if err != nil {
		t.Fatal(err)
	}
	if v["valid"] != true || v["cpd_balance"] != 4 {
		t.Fatalf("verify: %v", v)
	}

	// Revocation: counter-entry, balance back to zero.
	if _, err := svc.Revoke(cert.Serial, "", "fraud"); err == nil {
		t.Fatal("revoke needs actor")
	}
	if _, err := svc.Revoke(cert.Serial, "regulator", "fraudulent attestation"); err != nil {
		t.Fatal(err)
	}
	v, _ = svc.Verify(cert.Serial)
	if v["valid"] != false || v["cpd_balance"] != 0 {
		t.Fatalf("post-revocation verify: %v", v)
	}
	if led.posts[len(led.posts)-1] != "revoke:"+cert.Serial {
		t.Fatal("revocation must be a ledger counter-entry")
	}
}

func TestSignChecklistRequiresAssessorAndKnownItem(t *testing.T) {
	svc, _, enrID := enrolFixture(t)
	if err := svc.SignChecklist(enrID, "skills", "bag-mask seal", ""); err == nil {
		t.Fatal("assessor is mandatory")
	}
	if err := svc.SignChecklist(enrID, "skills", "not-a-step", "assessor"); err == nil {
		t.Fatal("unknown item must be rejected")
	}
}

func TestGuidelineBumpAndAttestationGap(t *testing.T) {
	svc, _, enrID := enrolFixture(t)
	svc.Enrol("im-still-standing", "nurse-77", "phc-karu")
	svc.Enrol("im-still-standing", "nurse-88", "phc-nyanya")

	flagged, err := svc.BumpGuideline("im-still-standing", "WHO-HBB-3", "cmoh")
	if err != nil || flagged != 3 {
		t.Fatalf("flagged=%d err=%v", flagged, err)
	}
	gaps := svc.AttestationGap("phc-karu")
	if len(gaps) != 2 {
		t.Fatalf("karu gaps = %d, want 2", len(gaps))
	}
	if gaps[0]["enrolment"] != enrID && gaps[1]["enrolment"] != enrID {
		t.Fatal("original enrolment must appear in the gap list")
	}
	if len(svc.AttestationGap("phc-nyanya")) != 1 {
		t.Fatal("nyanya gap count wrong")
	}
}

func TestCPDSummary(t *testing.T) {
	svc, _, enrID := enrolFixture(t)
	svc.MarkDone(enrID, "vid")
	svc.SubmitQuiz(enrID, "quiz", []int{0, 0})
	svc.SignChecklist(enrID, "skills", "bag-mask seal", "a")
	svc.SignChecklist(enrID, "skills", "chest rise observed", "a")
	svc.IssueCertificate(enrID)

	sum := svc.CPDSummary("nurse-42", "NMCN")
	if sum["points"] != 4 {
		t.Fatalf("points = %v", sum["points"])
	}
	if svc.CPDSummary("nurse-42", "MDCN")["points"] != 0 {
		t.Fatal("scheme filter must exclude other schemes")
	}
}

func TestIVRQuizShape(t *testing.T) {
	svc, _, enrID := enrolFixture(t)
	q, err := svc.IVRQuiz(enrID, "quiz")
	if err != nil {
		t.Fatal(err)
	}
	if q["questions"] != 2 {
		t.Fatalf("ivr quiz questions = %v", q["questions"])
	}
	if _, err := svc.IVRQuiz(enrID, "vid"); err == nil {
		t.Fatal("IVR on a non-quiz module must fail")
	}
}

func TestHTTPJourney(t *testing.T) {
	led := newFakeLedger()
	srv := &server{svc: NewTrainingService(led)}
	ts := httptest.NewServer(srv.routes())
	defer ts.Close()

	post := func(path, body, actor string) (int, map[string]any) {
		req, _ := http.NewRequest("POST", ts.URL+path, strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-Actor", actor)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		var out map[string]any
		json.NewDecoder(resp.Body).Decode(&out)
		return resp.StatusCode, out
	}

	code, out := post("/courses", `{"id":"c1","title":"T","version":"1","guideline_version":"G1","cpd_points":2,"scheme":"MDCN","modules":[{"id":"q","title":"Q","type":"quiz","pass_mark":50,"questions":[{"prompt":"p","options":["a","b"],"answer":1}]}]}`, "")
	if code != 201 {
		t.Fatalf("add course: %d %v", code, out)
	}
	code, out = post("/enrolments", `{"course_id":"c1","practitioner_id":"dr-1","facility_id":"f1"}`, "")
	if code != 201 {
		t.Fatalf("enrol: %d", code)
	}
	enr := out["id"].(string)
	code, out = post(fmt.Sprintf("/enrolments/%s/modules/q/quiz", enr), `{"answers":[1]}`, "")
	if code != 200 || out["score"].(float64) != 100 {
		t.Fatalf("quiz: %d %v", code, out)
	}
	code, out = post(fmt.Sprintf("/enrolments/%s/certificate", enr), `{}`, "")
	if code != 201 {
		t.Fatalf("certify: %d %v", code, out)
	}
	serial := out["serial"].(string)

	resp, _ := http.Get(ts.URL + "/certificates/" + serial + "/verify")
	var v map[string]any
	json.NewDecoder(resp.Body).Decode(&v)
	resp.Body.Close()
	if v["valid"] != true {
		t.Fatalf("verify: %v", v)
	}

	code, out = post("/certificates/"+serial+"/revoke", `{"reason":"audit"}`, "regulator")
	if code != 200 || out["revoked"] != true {
		t.Fatalf("revoke: %d %v", code, out)
	}
}
