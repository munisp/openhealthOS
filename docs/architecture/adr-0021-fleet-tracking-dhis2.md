# ADR-0021: Ambulance fleet with live uber-like tracking, and DHIS2 national reporting

Status: accepted (2026-10-19)

## Context

Two asks: (1) assess what is missing for this platform to be the
country's healthcare operating system and close the gaps
(`docs/gap-analysis.md` is that assessment); (2) make dispatch
*uber-like* — patient and facility watch the ambulance approach on a
map, on PWA and native mobile.

The top gaps the assessment found were the missing third point of the
emergency triangle (dispatch knew the patient and the facility, not
the ambulance) and national reporting interop (the ministry runs
DHIS2). Both land here.

## Decisions

### 1. The fleet — dispatch-service `fleet.go`

Ambulance **units** register once (callsign, kind — ambulance /
motorbike / riverine boat, home facility, crew). Their devices ping
GPS fixes (`POST /units/{id}/position`); the store keeps the latest
fix plus a 50-point trail for the map. Fleet state is **in-memory by
design**: positions are ephemeral hot state rebuilt from device pings
after any restart — persisting a stale fix would be worse than losing
it.

**Auto-dispatch**: a located SOS that reaches `dispatched` gets the
nearest *available* unit automatically — the uber default; a
dispatcher can override with a named unit (manual beats algorithm,
timeline says who). Unit status rides the incident lifecycle:
en_route → on_scene → transporting → available again on closure.

**Honest ETA**: straight-line haversine × 1.4 road factor ÷ 40km/h
conservative urban speed — and the assumptions print on every
response. A health system never shows a fake-precise countdown.

### 2. The map — `apps/ambulance-map.js`

One shared, dependency-free canvas tracker (no tiles, no CDN, no
WebGL — the same offline doctrine as the facility map) renders
patient (red), facility (green), unit (blue, greyed when its fix is
2+ minutes stale), and the unit's trail, projected equirectangularly
at city scale. `follow()` polls `/incidents/{id}/track` and repaints;
`startPinging()` is the paramedic side — the device GPS heartbeat via
`navigator.geolocation.watchPosition`.

Two surfaces ship it:
- **`apps/patient/track.html`** — the patient sends an SOS (location
  attached) and watches help approach with the ETA in words; the
  incident code is followable and remembered.
- **`apps/pwa/dispatch.html`** — the dispatcher board: open incidents,
  fleet roster, live map, one-tap dispatch and lifecycle transitions.
  Registering a unit from a device starts that device's ping loop —
  the paramedic's PWA *is* the tracker, so the Android shell inherits
  it with no native code.

Both service workers precache the map module and pages; cache
versions bumped. Pure parts (projection, ETA text, staleness) are
node-tested.

### 3. DHIS2 reporting — `packages/dhis2`

MeasureReports (ADR-0020) and k-anonymous surveillance aggregates
convert to DHIS2 **dataValueSets** with org-unit/data-element mapping
as JSON configuration a state edits, not code. Unmapped ids warn —
never silently drop. Suppressed surveillance cells never export: a
suppressed count IS a privacy decision. Payloads also append to a
JSONL spool for the offline monthly USB run — the sync tier's
existing pattern.

## Consequences

- The emergency triangle is closed: patient, facility, and ambulance
  are all visible, live, on the cheapest hardware, offline-tolerant.
- The platform now answers the ministry in its own format without
  running a second registry.
- `docs/gap-analysis.md` records what remains (blood bank, CMMS,
  staff rota, IVR, MCI mode) with honest roadmap placement.

## Honest limits

- The map is a schematic canvas, not street-level tiles — deliberate:
  no map provider survives an offline PHC. Turn-by-turn stays with the
  phone's maps app (geo: links).
- ETA assumes urban speed; rural states tune `UrbanSpeedKmh` per
  deployment, same pattern as the triage protocol file.
- Fleet store is memory-only; a restart loses trails (by design) but
  not unit registrations when a durable fleet registry is needed —
  roadmap if a state asks.
