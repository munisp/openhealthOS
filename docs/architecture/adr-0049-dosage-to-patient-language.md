# ADR-0049: Dosage-to-patient-language rendering — insight from FDS, not the library

Status: accepted (2026-10-20)
Source: jy95/fds (FHIR Dosage Support, Apache-2.0). **Library rejected,
contract adopted clean-room.**

## Context

FDS turns structured FHIR Dosage elements into human-readable
instructions ("Take 2 tablets twice daily") via ICU4J i18n. Two reasons
it cannot be embedded: it is **Java 17** (nothing in this platform runs
a JVM — stdlib Go/Rust/Python doctrine, ADR-0005), and its locale set
(en/fr/nl/de/es/it/pt) covers **none** of the languages we commit to —
English, Nigerian Pidgin, Hausa, Yorùbá, Igbo.

The insight stands on its own: a prescription reaches the patient as
JSON unless someone renders it, and an adherence nudge that says "time
for your medication" is weaker than one that says *how* to take it.

## Decision

`packages/dosagetext` (stdlib Python): renders the 80% shape of real
prescriptions (doseQuantity + timing frequency/period/periodUnit + route
+ as-needed) into imperative SMS-length instructions in all five
platform languages. Anything else — tapers, every-6-hours, bounds —
returns the **safe fallback** ("take exactly as the prescriber
explained"): a wrong instruction is worse than a plain one, the same
bias-to-safety doctrine as danger-sign detection.

Wired in: the notification-service adherence engine (innovation 2)
carries `dosage_text` from the prescription event into the plan, and
the daily nudge **replaces** the generic text with the instruction when
present. Pharmacy dispense messages and the portal medication view can
adopt the same renderer without new infrastructure.

## Consequences

- Translation table lives in one reviewed file; adding a language is a
  phrase-table edit with tests, not a dependency.
- The fallback path means partial FHIR data degrades to safe behaviour
  rather than failing the notification rail.
- If a JVM ever enters the stack (e.g. via an adopted HAPI component),
  FDS becomes re-evaluable — noted here so the decision is cheap to
  revisit.
