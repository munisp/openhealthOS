#!/usr/bin/env python3
"""Medplum FHIR-subscription conformance gate (ADR-0013).

Proves the platform's FHIR intake contract against a REAL Medplum server:
when a laboratory Observation or a MedicationRequest lands in the FHIR
server, the notification rail fires — event-driven, no polling of Medplum.

The gate:
  G0  subscribe a patient on the notification rail
  G1  authenticate to Medplum (seeded admin, headless login flow)
  G2  create rest-hook Subscriptions (Observation?category=laboratory,
      MedicationRequest) pointing at the platform's /fhir/inbox
  G3  create a laboratory Observation → lab_ready notification appears
  G4  create a vital-signs Observation → NO notification (narrow mapping:
      vitals must not spam the patient)
  G5  create a MedicationRequest → pickup notification appears

Run it two ways:
  CI / staging  : --medplum http://localhost:8103 --openhealth URL
                  [--inbox-endpoint URL seen from the Medplum container]
  Offline proof : --selftest --openhealth URL   (in-process mock Medplum
                  implementing the same login + subscription + rest-hook
                  contract; validates the gate harness itself)

stdlib only; exits non-zero with the failing step named.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []

PATIENT = "gate-pt-1"
PHONE = "+2348001112222"
LAB_CATEGORY = {
    "coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                "code": "laboratory"}]
}
VITAL_CATEGORY = {
    "coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                "code": "vital-signs"}]
}


def check(step: str, name: str, ok: bool, detail: str = "") -> bool:
    results.append((step, name, PASS if ok else FAIL))
    print(f"  [{PASS if ok else FAIL}] {step}: {name}" + (f" — {detail}" if detail and not ok else ""))
    return ok


def req(method: str, url: str, body: dict | None = None, headers: dict | None = None,
        form: dict | None = None) -> tuple[int, dict]:
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    elif body is not None:
        data = json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type",
                 "application/x-www-form-urlencoded" if form is not None else "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except urllib.error.URLError as e:
        return 0, {"error": str(e)}


# ---------------------------------------------------------------- mock Medplum

class MockMedplum(BaseHTTPRequestHandler):
    """Just enough of the Medplum contract to validate this gate offline:
    headless login, token exchange, Subscription create, and rest-hook
    delivery of created Observations / MedicationRequests."""

    subscriptions: list[dict] = []

    def log_message(self, *_):  # silence
        pass

    def _body(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        try:
            return json.loads(raw or b"{}")
        except Exception:
            return dict(urllib.parse.parse_qsl(raw.decode()))

    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthcheck":
            self._send(200, {"ok": True, "version": "mock", "postgres": True, "redis": True})
        else:
            self._send(404, {})

    def do_POST(self):
        b = self._body()
        if self.path == "/auth/login":
            self._send(200, {"login": "MOCK-LOGIN-1"})
        elif self.path == "/auth/code":
            self._send(200, {"code": "MOCK-CODE-1"})
        elif self.path == "/oauth2/token":
            self._send(200, {"access_token": "MOCK-TOKEN", "token_type": "Bearer"})
        elif self.path == "/fhir/R4/Subscription":
            sub = dict(b, id=f"mock-sub-{len(self.subscriptions) + 1}")
            self.subscriptions.append(sub)
            self._send(201, sub)
        elif self.path in ("/fhir/R4/Observation", "/fhir/R4/MedicationRequest"):
            self._fire(b)
            self._send(201, dict(b, id="mock-res-1"))
        else:
            self._send(404, {})

    def _fire(self, resource: dict) -> None:
        """Deliver rest-hook, as Medplum does, off-thread."""
        rt = resource.get("resourceType", "")
        for sub in self.subscriptions:
            if sub.get("status") != "active":
                continue
            if sub.get("criteria", "").split("?")[0] != rt:
                continue
            if "category=laboratory" in sub.get("criteria", ""):
                codes = [c.get("code")
                         for cat in resource.get("category", [])
                         for c in cat.get("coding", [])]
                if "laboratory" not in codes:
                    continue
            endpoint = sub.get("channel", {}).get("endpoint")
            if endpoint:
                threading.Thread(
                    target=req, args=("POST", endpoint, resource), daemon=True).start()


# ------------------------------------------------------------------ gate steps

def medplum_login(base: str, email: str, password: str) -> str | None:
    """Headless login: /auth/login → /auth/code → /oauth2/token. Tolerates
    servers that return the code directly from /auth/login."""
    st, r = req("POST", f"{base}/auth/login", {"email": email, "password": password})
    if st != 200:
        check("G1", "medplum login", False, f"auth/login got {st} {r}")
        return None
    code = r.get("code")
    if not code:
        st, r2 = req("POST", f"{base}/auth/code", {"login": r.get("login")})
        if st != 200:
            check("G1", "medplum login", False, f"auth/code got {st} {r2}")
            return None
        code = r2.get("code")
    st, tok = req("POST", f"{base}/oauth2/token",
                  form={"grant_type": "authorization_code", "code": code})
    if st != 200 or not tok.get("access_token"):
        check("G1", "medplum login", False, f"oauth2/token got {st} {tok}")
        return None
    check("G1", "medplum login (headless)", True)
    return tok["access_token"]


def create_subscription(base: str, token: str, criteria: str, endpoint: str) -> bool:
    sub = {
        "resourceType": "Subscription", "status": "active",
        "reason": "openhealth notification rail (conformance gate)",
        "criteria": criteria,
        "channel": {"type": "rest-hook", "endpoint": endpoint},
    }
    st, r = req("POST", f"{base}/fhir/R4/Subscription", sub,
                {"Authorization": f"Bearer {token}"})
    return check("G2", f"subscription {criteria}", st in (200, 201), f"got {st} {r}")


def notifications(openhealth: str) -> list[dict]:
    st, r = req("GET", f"{openhealth}/notifications?patient={PATIENT}")
    return r if isinstance(r, list) else []


def wait_for_kind(openhealth: str, kind: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(n.get("kind") == kind for n in notifications(openhealth)):
            return True
        time.sleep(1.0)
    return False


def run_gate(medplum: str, openhealth: str, inbox_endpoint: str,
             email: str, password: str, wait: float) -> None:
    st, _ = req("POST", f"{openhealth}/subscriptions",
                {"patient_id": PATIENT, "phone": PHONE})
    check("G0", "patient subscribed on notification rail", st in (200, 201), f"got {st}")

    st, hc = req("GET", f"{medplum}/healthcheck")
    if not check("G1", "medplum healthcheck", st == 200 and hc.get("ok") is True,
                 f"got {st} {hc}"):
        return
    token = medplum_login(medplum, email, password)
    if not token:
        return

    if not create_subscription(medplum, token, "Observation?category=laboratory", inbox_endpoint):
        return
    if not create_subscription(medplum, token, "MedicationRequest", inbox_endpoint):
        return

    auth = {"Authorization": f"Bearer {token}"}
    lab = {
        "resourceType": "Observation", "status": "final",
        "category": [LAB_CATEGORY],
        "code": {"text": "Malaria RDT"},
        "subject": {"reference": f"Patient/{PATIENT}"},
    }
    st, _ = req("POST", f"{medplum}/fhir/R4/Observation", lab, auth)
    if check("G3", "laboratory Observation created", st in (200, 201), f"got {st}"):
        check("G3", "lab_ready notification delivered (rest-hook → inbox)",
              wait_for_kind(openhealth, "lab_ready", wait), f"no notification within {wait:.0f}s")

    before = len(notifications(openhealth))
    vital = dict(lab, category=[VITAL_CATEGORY], code={"text": "Blood pressure"})
    st, _ = req("POST", f"{medplum}/fhir/R4/Observation", vital, auth)
    if check("G4", "vital-signs Observation created", st in (200, 201), f"got {st}"):
        time.sleep(min(8.0, wait / 3))
        check("G4", "vital-signs Observation did NOT notify (narrow mapping)",
              len(notifications(openhealth)) == before)

    rx = {
        "resourceType": "MedicationRequest", "status": "active", "intent": "order",
        "medicationCodeableConcept": {"text": "Amoxicillin 500mg"},
        "subject": {"reference": f"Patient/{PATIENT}"},
    }
    st, _ = req("POST", f"{medplum}/fhir/R4/MedicationRequest", rx, auth)
    if check("G5", "MedicationRequest created", st in (200, 201), f"got {st}"):
        check("G5", "pickup notification delivered",
              wait_for_kind(openhealth, "pickup", wait), f"no notification within {wait:.0f}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--medplum", default="http://localhost:8103")
    ap.add_argument("--openhealth", default="http://127.0.0.1:18090",
                    help="notification-service base URL")
    ap.add_argument("--inbox-endpoint", default="",
                    help="/fhir/inbox URL as reachable FROM the Medplum server "
                         "(default: {openhealth}/fhir/inbox; in CI use the "
                         "host-gateway address)")
    ap.add_argument("--email", default="admin@example.com")
    ap.add_argument("--password", default="medplum_admin")
    ap.add_argument("--wait", type=float, default=45.0,
                    help="seconds to wait for rest-hook delivery")
    ap.add_argument("--selftest", action="store_true",
                    help="run against an in-process mock Medplum (offline)")
    args = ap.parse_args()

    medplum = args.medplum
    mock = None
    if args.selftest:
        mock = ThreadingHTTPServer(("127.0.0.1", 0), MockMedplum)
        threading.Thread(target=mock.serve_forever, daemon=True).start()
        medplum = f"http://127.0.0.1:{mock.server_port}"
        args.wait = min(args.wait, 15.0)
        print(f"selftest: mock Medplum on {medplum}")

    inbox = args.inbox_endpoint or f"{args.openhealth}/fhir/inbox"
    print(f"OpenHealth OS — Medplum FHIR-subscription gate\n"
          f"  medplum={medplum} openhealth={args.openhealth} inbox={inbox}")
    run_gate(medplum, args.openhealth, inbox, args.email, args.password, args.wait)

    if mock:
        mock.shutdown()
    failed = [r for r in results if r[2] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("MEDPLUM GATE: FAIL")
        return 1
    print("MEDPLUM GATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
