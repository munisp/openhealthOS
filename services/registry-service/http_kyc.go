package main

// KYC HTTP surface (ADR-0012):
//
//	POST /patients/{id}/verify           — NIN authority verification (NIMC seam)
//	POST /patients/{id}/phone/challenge  — issue a phone-binding OTP
//	POST /patients/{id}/phone/verify     — prove the handset, lift level to "phone"
//	GET  /kyc/signals                    — fraud signals for ops (shared phones,
//	                                       level distribution, unverified NINs)
//
// The OTP challenge uses the same generic SMS webhook seam as the
// notification rail; with DEV_OTP_ECHO=1 the code is returned in the
// response so demos and tests run without any SMS provider.

import (
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)

const otpTTL = 10 * time.Minute

type kycHandler struct {
	patients PatientStore
	verifier Verifier
	sender   OTPSender
	echoDev  bool
}

func registerKYCRoutes(mux *http.ServeMux, ps PatientStore, v Verifier, sender OTPSender) {
	h := kycHandler{patients: ps, verifier: v, sender: sender, echoDev: os.Getenv("DEV_OTP_ECHO") == "1"}

	mux.HandleFunc("POST /patients/{id}/verify", func(w http.ResponseWriter, r *http.Request) {
		id := r.PathValue("id")
		var body struct {
			NIN string `json:"nin"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.NIN == "" {
			http.Error(w, `{"error":"nin required"}`, http.StatusBadRequest)
			return
		}
		p, err := ps.GetPatient(id)
		if errors.Is(err, ErrNotFound) {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		vr := h.verifier.Verify(body.NIN, p.DOB)
		switch vr.Status {
		case "invalid_format":
			http.Error(w, `{"error":"`+ErrNINBadFormat.Error()+`"}`, http.StatusBadRequest)
		case "no_match":
			// Record the failed attempt — a registrar trying random NINs
			// against a record is itself a signal.
			_, _ = ps.SetKYC(id, p.KYCLevel, "verification failed: "+vr.Detail)
			http.Error(w, `{"error":"`+ErrNINNoMatch.Error()+`"}`, http.StatusConflict)
		case "deferred":
			// Care never blocks: record stays at nin-format, pending authority.
			p, _ = ps.SetKYC(id, KYCNINFormat, vr.Detail)
			writeJSON(w, http.StatusAccepted, map[string]any{"status": "deferred", "patient": p})
		default: // verified
			if p.NIN == "" {
				if _, err := ps.LinkNIN(id, body.NIN); err != nil {
					http.Error(w, err.Error(), http.StatusConflict)
					return
				}
			} else if p.NIN != body.NIN {
				http.Error(w, `{"error":"record bound to a different nin"}`, http.StatusConflict)
				return
			}
			detail := "NIMC-verified"
			if vr.Name != "" {
				detail = "NIMC-verified as " + vr.Name
			}
			p, _ = ps.SetKYC(id, KYCNINVerified, detail)
			writeJSON(w, http.StatusOK, p)
		}
	})

	mux.HandleFunc("POST /patients/{id}/phone/challenge", func(w http.ResponseWriter, r *http.Request) {
		id := r.PathValue("id")
		p, err := ps.GetPatient(id)
		if errors.Is(err, ErrNotFound) {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		if p.Phone == "" {
			http.Error(w, `{"error":"patient has no phone on record"}`, http.StatusBadRequest)
			return
		}
		code, err := newOTP()
		if err != nil {
			http.Error(w, "otp generation failed", http.StatusInternalServerError)
			return
		}
		if err := ps.SaveOTP(id, otpHash(code), time.Now().Add(otpTTL)); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		if err := h.sender.Send(p.Phone, "OpenHealth: your verification code is "+code+" (valid 10 min)"); err != nil {
			log.Printf("otp send failed for %s: %v", id, err)
			http.Error(w, `{"error":"sms send failed — retry"}`, http.StatusBadGateway)
			return
		}
		resp := map[string]string{"status": "sent", "expires_in": "600"}
		if h.echoDev {
			resp["dev_code"] = code
		}
		writeJSON(w, http.StatusAccepted, resp)
	})

	mux.HandleFunc("POST /patients/{id}/phone/verify", func(w http.ResponseWriter, r *http.Request) {
		id := r.PathValue("id")
		var body struct {
			Code string `json:"code"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Code == "" {
			http.Error(w, `{"error":"code required"}`, http.StatusBadRequest)
			return
		}
		err := h.patients.CheckOTP(id, otpHash(strings.TrimSpace(body.Code)), time.Now())
		switch {
		case errors.Is(err, ErrNotFound):
			http.Error(w, `{"error":"no pending challenge"}`, http.StatusNotFound)
		case errors.Is(err, ErrOTPExpired):
			http.Error(w, `{"error":"code expired — request a new one"}`, http.StatusGone)
		case errors.Is(err, ErrOTPLocked):
			http.Error(w, `{"error":"too many attempts — request a new code"}`, http.StatusTooManyRequests)
		case errors.Is(err, ErrOTPMismatch):
			http.Error(w, `{"error":"wrong code"}`, http.StatusUnauthorized)
		case err != nil:
			http.Error(w, err.Error(), http.StatusInternalServerError)
		default:
			p, _ := h.patients.SetKYC(id, KYCPhone, "phone OTP verified")
			writeJSON(w, http.StatusOK, p)
		}
	})

	mux.HandleFunc("GET /kyc/signals", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, ComputeSignals(ps.ListPatients()))
	})
}
