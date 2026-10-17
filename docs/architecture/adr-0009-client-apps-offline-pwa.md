# ADR-0009: Client apps — offline-first PWA core, thin native shell, one origin

**Status**: accepted · **Date**: 2026-10-18

## Context

The spec's primary users are nurses and CHWs on five-year-old Android
phones, on 2G where connectivity exists at all (ADR-0001). The client
decision determines whether the platform works in that reality:

- A React/Flutter native app means megabytes of JS/APK, an app-store
  dependency, and a second codebase — all hostile to ₦1,800-data-plan
  Nigeria and to a small open-source team.
- A pure web app fails the moment the network drops.
- A pure native app cannot be updated on a facility's schedule; OTA
  discipline (tools/ota) matters more than framework choice.

## Decision

**One PWA core, three vessels.**

1. **The app is a dependency-free PWA** (`apps/pwa`): hand-written ES
   modules, precached app shell via service worker, IndexedDB outbox,
   HLC-stamped CRDT replica (`apps/pwa/js/crdt.js` mirrors
   `shared/crdt` semantics), opportunistic background sync to
   `/api/sync`. No framework, no build step, no CDN — the entire shell
   is ~90 KB and renders on Android 7 WebViews. UI is task-typed and
   protocol-guided (ANC schedule, NPI immunisation, danger signs),
   in English, Pidgin, Hausa, Yorùbá and Igbo, with 48px touch targets
   and sunlight contrast.
2. **Operations dashboards** (`apps/dashboards`) are the same discipline:
   facility board (daylight theme, sunlight-readable) and state situation
   room (dark ops theme) reading through the gateway; every board renders
   from IndexedDB last-known data when offline and labels itself DEMO
   when neither network nor cache exists. Boards never fake liveness.
3. **Native is a thin WebView shell** (`apps/android-shell`, minSdk 24)
   with a bundled PWA snapshot for zero-connectivity first launch; TWA is
   the Play-distribution variant. All clinical logic stays in the PWA —
   the shell must stay boring.
4. **One origin**: the api-gateway serves the apps (`/app`, `/dash`) next
   to `/api/*`, so clients inherit OIDC, never fight CORS, and a state
   deployment exposes exactly one port.

## Consequences

- App updates ride the same signed-OTA channel as edge images; a facility
  never needs Play Store access.
- The CRDT semantics exist in two languages (JS client, Go server) by
  design — the merge rules are the contract, pinned by tests on both
  sides; a drift between them is a bug the conformance tests catch.
- Dashboard gate math mirrors `packages/analytics/reporter.py` — the
  board a commissioner sees shows the same numbers the go-live runbook
  enforces.
- Native shell changes are rare and low-risk; UX iteration happens in the
  PWA where it can be tested in a browser.
