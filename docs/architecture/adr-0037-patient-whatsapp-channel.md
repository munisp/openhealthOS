# ADR-0037: Patient WhatsApp channel — the chat thread as a front door

Status: accepted (2026-10-19)

## Context

The patient channels were the PWA (smartphone), USSD (feature phone),
and SMS (outbound only). The largest interactive channel remained
unclaimed: WhatsApp is the application Nigerians actually open, and
"message the clinic" is a more natural act than "install the hospital
app". Dispatch already proves the WhatsApp webhook pattern for
emergencies (ADR-0019/0022); this ADR extends it to the everyday
patient↔provider conversation.

## Decisions

### 1. A thin channel service — `services/whatsapp-gateway` (:8099)

The bot contains **no domain logic**. Every action is a call into an
existing rail with its own gates:

- **Book an appointment** — portal rail `/portal/facilities` →
  `/portal/slots` → `POST /portal/appointments` (facility → day →
  time → confirm; full slots are never shown)
- **My appointments** — list and cancel (`/portal/appointments`,
  `/portal/appointments/cancel` with its ownership check)
- **My coverage** — `/portal/coverage` (honest answers: eligible /
  suspended / not-enrolled)
- **Clinic updates** — `/portal/feed` (the notification rail's
  patient feed)
- **Ask the clinic** — `/portal/messages` opens a triaged consult;
  the nurse replies into the same thread
- **Log a health reading** — steps / weight / blood pressure into the
  wellness rail (ADR-0035, `source: manual`), which lands in the
  medical record and shapes the prevention plan

### 2. Identity is WhatsApp-authenticated MSISDN → exact phone match

WhatsApp authenticates the sender's number; the gateway searches the
registry and binds the session only on an **exact normalized phone
equality** (prefix search alone would risk binding one patient's
channel to another's). Every domain call then passes the portal's
patient_id + phone gate, which 404s any mismatch indistinguishably.
A number that matches no patient gets enrolment guidance — never a
hint about who exists.

### 3. Numbered text menus, not interactive payloads

Interactive list messages are a Cloud-API-specific feature; plain
numbered text is the floor every gateway and every phone supports, and
it survives a provider swap unchanged. `menu` / `hi` / `0` reset from
any state — the escape hatch is one keystroke.

### 4. Webhook hygiene, same doctrine as dispatch

GET verification handshake; inbound dedupe by message id (a retried
"1" must not book twice); X-Hub-Signature-256 HMAC enforced when
`WHATSAPP_APP_SECRET` is set; malformed payloads ack 200 — a webhook
that retries poison payloads forever is a self-inflicted outage.

### 5. Five languages, patient-switchable

The channel speaks the platform's five languages — English, Nigerian
Pidgin, Hausa, Yoruba, Igbo — the same set as the PWA and notification
rail. Every user-facing string is keyed (`i18n.go`); the session
carries the language; menu option 7 opens the picker, and the words
"language" / "harshe" / "èdè" / "asụsụ" work from anywhere in any
state. A missing translation falls back to English, so adding a
language never silences the bot. Register is chat-short, not brochure.

### 6. Nothing proprietary embedded

The Cloud API is one HTTPS POST behind a `Sender` seam. Default
senders are a local log or a JSONL outbox (`WA_OUTBOX`) so the whole
channel develops and demos offline; the graph sender activates on
`WHATSAPP_TOKEN` + `WHATSAPP_PHONE_ID`. A different aggregator (or a
national WhatsApp Business provider) is a one-interface change.

## Consequences

- The platform now meets the patient in three graded channels:
  WhatsApp (interactive, zero install), PWA (rich), USSD (universal).
- Sessions are in-memory with a 30-minute TTL by design — chat context
  is ephemeral; all durable state lives in the domain services.
- Gateway route `/api/whatsapp`, compose + k8s entries, and the
  production-plane smoke lists are updated in the same commit; the
  route-coverage regression test now guards this service too.
