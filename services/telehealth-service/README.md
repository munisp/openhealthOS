# telehealth-service

Nurse-first telemedicine — spec module 8.

Every consult starts with a nurse assessment against a triage protocol.
The nurse closes routine cases; anything beyond protocol escalates to a
remote doctor, and may produce a referral into the physical network.
Prescriptions route to the facility pharmacy or a designated retail
pharmacy.

The **nurse-first gate is structural**: there is no code path to the
doctor queue, closure, or a prescription without a recorded nurse
assessment. Specialist time is the scarcest resource in the system; the
gate keeps it spendable on patients who need it.

## Flow

```
open → assess → close (routine, under protocol)
              → escalate → refer (routine | urgent | emergency)
                              ↓ accept → complete (loop closed)
              → prescribe (nurse under protocol, or doctor after escalation)
                              ↓ routed → dispensed
```

## API

| Route | Purpose |
| --- | --- |
| `POST /consults` | Open a consult (nurse-first) |
| `POST /consults/{id}/assess` | Record protocol assessment |
| `POST /consults/{id}/close` | Close routine case |
| `POST /consults/{id}/escalate` | Hand to remote doctor queue |
| `POST /consults/{id}/refer` | Refer into physical network |
| `POST /referrals/{id}/accept` / `complete` | Close the referral loop |
| `POST /prescriptions` | Route a prescription to a pharmacy |
| `POST /prescriptions/{id}/dispense` | Mark dispensed |
| `GET /healthz` | Liveness |

```bash
LISTEN=:8087 go run .          # serve
go test .                      # test
```
