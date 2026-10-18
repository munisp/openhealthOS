# ADR-0022: Closing the remaining gaps — MCI mode, voice/IVR, blood bank, rota, CMMS, iOS shell, family grouping

Status: accepted (2026-10-19)

## Context

ADR-0021's gap analysis named the remaining holes for a national
healthcare OS. This wave closes them, and answers two product
questions: is mobile both iOS and Android (now yes), and how the
platform groups families (kinship layer, now built).

## Decisions

### 1. Mass-casualty mode — dispatch-service `mci.go`

A bus crash is thirty patients at one location, not one caller. MCI
incidents carry a **casualty list with START tags** (red immediate,
yellow delayed, green minor, black expectant), per-tag counts
including at-scene splits so command sees the shape of the scene, and
a **surge view** (`/mci/{id}/surge`) — facilities near the scene with
beds, nearest first — so transport distributes instead of overwhelming
the nearest hospital.

### 2. Voice/IVR seam — `POST /webhooks/voice`

Dispatch was WhatsApp/SMS/USSD/app; voice is the channel every basic
phone already has. A telecom partner's gateway POSTs call events;
DTMF **1** becomes an SOS on the `voice` channel with cell-tower
location (when the partner resolves it), the response is the sentence
the IVR plays back, repeat calls reassure instead of cloning (the
dedupe window does its job), and malformed events still answer 200 —
the same no-retry-loop doctrine as WhatsApp. One key, one job: in an
emergency that is all the menu there is.

### 3. Blood bank — `packages/bloodbank`

Unit-level inventory: group, product, collection/expiry, statuses
available→reserved→transfused (|expired|discarded, never deleted).
The compatibility matrix is deterministic medicine (O- universal
donor, AB+ universal recipient for red cells; plasma conservative
ABO-identical) enforced **twice** — at reservation and again at the
bedside, because the second check is the one that saves lives. FEFO
order, expiry worklists, automatic sweep, named actors everywhere.

### 4. Staff rota — `packages/rota`

Coverage policy ("two nurses and a CHEW per day shift") becomes
assignable shifts with three guarantees: gaps are visible per shift
per cadre, nobody double-books a window, weekly caps hold. Fairness
(night/weekend counts per person) is a report, and auto-fill is fair
greedy — least-loaded eligible person first, deterministic.

### 5. CMMS — `packages/cmms`

Oxygen plants, generators, fridges, autoclaves, vehicles as tracked
assets: preventive-maintenance intervals become work orders (overdue
surfaces as a worklist), a down **critical** asset auto-opens an
urgent repair, downtime is counted per asset and availability % is
visible to the LGA. ERPNext Assets remains the book of record for
depreciation/procurement; this is the facility's operational hot
path.

### 6. iOS shell — `apps/ios-shell`

Mobile is now **both**: Swift WKWebView shell mirroring the Android
contract exactly — live URL when reachable, bundled PWA snapshot
otherwise, service worker + IndexedDB guarantees intact, iOS 15+
(five-year-old-phone doctrine), location permission only for the
paramedic ping loop with a truthful usage string. The repo cannot
compile iOS (no macOS/Xcode in the sandbox) — that limit is stated in
the shell's README, not hidden. The PWA remains the single codebase;
both shells are boring vessels.

### 7. Family grouping — `packages/households/families.py`

Households group by **where** people live; families group by **who**
they are to each other. A family has one head, members with kinship
codes (spouse, child, parent, sibling, other-dependant), an optional
shared phone and scheme link. The rules that matter:

- dependants' reminders **route to the guardian**: a child's
  immunization SMS goes to the family phone, not a five-year-old;
- one patient, one family — a person cannot be double-counted across
  family units;
- CHW worklists group by family: "visit the Bello household — two
  children due, grandmother's BP check" is one trip, each entry naming
  who to contact;
- family→household and family→scheme links keep geography and payer
  enrolment connected without duplicating them.

## Consequences

- The gap analysis roadmap rows for MCI, IVR, blood bank, rota, CMMS,
  and iOS are now shipped rows; DHIS2 live transport stays a per-state
  deployment choice (exporter is code, transport is policy).
- Everything remains stdlib, offline-first, demo-proven; no new
  licences entered the tree.

## Honest limits

- Blood bank covers red-cell and conservative plasma rules;
  full serology (antibody screens, crossmatch gels) is lab-system
  territory — the adapter seam exists.
- Rota is single-week, single-facility; multi-week rotation templates
  are a natural next increment when a state asks.
- MCI casualty records are tags and descriptions, not clinical
  charts — each transported casualty's full record starts at the
  receiving facility's SHR.
