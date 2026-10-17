#!/usr/bin/env python3
"""Validate OpenHealth FHIR profiles against a running Medplum server.

This is the acceptance harness for ADR-0010: Medplum is the production
FHIR foundation, so the platform's StructureDefinitions
(fhir/profiles/openhealth-*.json) and example resources (fhir/examples/)
must behave correctly on a real Medplum instance — not just on our own
stdlib validator.

What it does, stdlib only:

  1. GET {base}/healthcheck — server must be up.
  2. If MEDPLUM_CLIENT_ID / MEDPLUM_CLIENT_SECRET are set, obtains a
     bearer token via OAuth2 client_credentials (POST /oauth2/token).
     A freshly seeded Medplum server also accepts the default
     admin@example.com / medplum_admin via password grant; set
     MEDPLUM_ADMIN_EMAIL / MEDPLUM_ADMIN_PASSWORD to use that instead.
  3. PUTs each StructureDefinition from fhir/profiles/ (upsert by id).
  4. For each fhir/examples/*.json, injects meta.profile with the
     matching canonical URL and POSTs to
     /fhir/R4/{resourceType}/$validate. Files named *-valid.json must
     produce an OperationOutcome with no error/fatal issues; files named
     *-invalid.json must produce at least one error/fatal issue.

Exit code 0 only if every expectation is met.

Usage:
  docker compose -f infra/compose/platform.yaml --profile medplum up -d
  MEDPLUM_BASE_URL=http://localhost:18103 python3 tools/medplum-validate/main.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = ROOT / "fhir" / "profiles"
EXAMPLES_DIR = ROOT / "fhir" / "examples"

BASE = os.environ.get("MEDPLUM_BASE_URL", "http://localhost:18103").rstrip("/")
TIMEOUT = 15.0

# Example file -> profile file whose canonical URL it must satisfy.
EXAMPLE_PROFILE = {
    "patient": "openhealth-patient.json",
    "encounter": "openhealth-encounter.json",
    "claim": "openhealth-claim.json",
    "consent": "openhealth-consent.json",
}


class Client:
    def __init__(self) -> None:
        self.token: str | None = None

    def request(self, method: str, path: str, body: dict | None = None,
                form: dict | None = None) -> tuple[int, dict]:
        url = BASE + path
        headers = {"Accept": "application/fhir+json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/fhir+json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            headers["Accept"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read() or b"{}"
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as e:
            raw = e.read() or b"{}"
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {"raw": raw.decode(errors="replace")[:500]}

    def authenticate(self) -> None:
        cid = os.environ.get("MEDPLUM_CLIENT_ID")
        secret = os.environ.get("MEDPLUM_CLIENT_SECRET")
        email = os.environ.get("MEDPLUM_ADMIN_EMAIL")
        password = os.environ.get("MEDPLUM_ADMIN_PASSWORD")
        if cid and secret:
            form = {"grant_type": "client_credentials",
                    "client_id": cid, "client_secret": secret}
        elif email and password:
            form = {"grant_type": "password",
                    "username": email, "password": password}
        else:
            print("note: no credentials set; trying unauthenticated "
                  "(set MEDPLUM_CLIENT_ID/SECRET or MEDPLUM_ADMIN_EMAIL/PASSWORD)")
            return
        status, out = self.request("POST", "/oauth2/token", form=form)
        if status != 200 or "access_token" not in out:
            raise SystemExit(f"FAIL: OAuth2 token request -> {status}: {out}")
        self.token = out["access_token"]
        print("auth: bearer token acquired")


def issues_of(out: dict) -> list[dict]:
    return [i for i in out.get("issue", [])
            if i.get("severity") in ("error", "fatal")]


def main() -> int:
    c = Client()

    status, out = c.request("GET", "/healthcheck")
    if status != 200 or not out.get("ok"):
        raise SystemExit(f"FAIL: healthcheck -> {status}: {out}")
    print(f"healthcheck: ok (version {out.get('version', '?')}, "
          f"postgres={out.get('postgres')}, redis={out.get('redis')})")

    c.authenticate()

    # 1. Upload the profiles.
    canonicals: dict[str, str] = {}
    for path in sorted(PROFILES_DIR.glob("openhealth-*.json")):
        sd = json.loads(path.read_text())
        if sd.get("resourceType") != "StructureDefinition":
            raise SystemExit(f"FAIL: {path.name} is not a StructureDefinition")
        sid = sd.get("id") or sd["url"].rsplit("/", 1)[-1]
        status, out = c.request("PUT", f"/fhir/R4/StructureDefinition/{sid}", body=sd)
        if status not in (200, 201):
            raise SystemExit(f"FAIL: upload {path.name} -> {status}: {out}")
        canonicals[path.name] = sd["url"]
        print(f"profile: {path.name} -> {status} ({sd['url']})")

    # 2. Validate every example against its profile.
    failures = 0
    for path in sorted(EXAMPLES_DIR.glob("*.json")):
        resource = json.loads(path.read_text())
        stem = path.name.replace("-valid.json", "").replace("-invalid.json", "")
        expect_valid = path.name.endswith("-valid.json")
        profile_file = EXAMPLE_PROFILE.get(stem)
        if not profile_file:
            print(f"example: {path.name} -> SKIP (no profile mapping)")
            continue
        canonical = canonicals.get(profile_file)
        if not canonical:
            raise SystemExit(f"FAIL: profile {profile_file} was not uploaded")
        resource.setdefault("meta", {})["profile"] = [canonical]
        rt = resource["resourceType"]
        status, out = c.request("POST", f"/fhir/R4/{rt}/$validate", body=resource)
        errs = issues_of(out)
        ok = (not errs) if expect_valid else bool(errs)
        mark = "PASS" if ok else "FAIL"
        print(f"example: {path.name} -> {rt} $validate {status}, "
              f"errors={len(errs)} [{mark}]")
        for i in errs:
            detail = (i.get("details") or {}).get("text", i.get("code", "?"))
            expr = ",".join(i.get("expression", []))
            print(f"         - {i.get('severity')}: {detail} {expr}")
        if not ok:
            failures += 1

    if failures:
        print(f"\n{failures} expectation(s) failed")
        return 1
    print("\nall profiles uploaded; all example expectations met")
    return 0


if __name__ == "__main__":
    sys.exit(main())
