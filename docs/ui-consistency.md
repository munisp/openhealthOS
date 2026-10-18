# UI consistency across surfaces

The platform ships four front-ends. This document is the audit of how
they stay one product, and which differences are deliberate.

## The rule

**Care-facing surfaces share one grammar. Operations surfaces are
allowed to look like instruments.**

| Surface | Audience | Theme | Verdict |
|---|---|---|---|
| `apps/patient` (patient app, 5 languages) | patients | daylight green, big type, one column | shared grammar |
| `apps/pwa` (clinician core) | clinicians at the facility | clinical sage, dense but calm | shared grammar |
| `apps/android-shell` | patients (wrapper) | bundles the patient app | inherits patient |
| `apps/dashboards` | LGA/state situation room | dark ops (deliberate) | exempt — see below |

## Shared tokens

`apps/design-tokens.css` is the single source for the care-facing
grammar (Medplum-inspired): white cards (`--oh-card-bg`), one soft
shadow scale (`--oh-shadow-sm/md`), pill badges (`--oh-radius-pill`),
hairline borders, indigo interactive accent (`--oh-accent`). Both
care apps link it before their own stylesheet; their service workers
precache it, so the grammar works offline like everything else.

Each app keeps its own **palette** — a patient app and a clinician
console may differ in hue — but **shape** (radii, elevation, badge
grammar) is no longer per-app: `.card` in the patient app and `.card`
in the PWA resolve to the same radius and shadow tokens.

## Why the dashboards stay dark

A situation room watches a wall of screens for hours; dark themes with
monospace tabular numerals and glow accents reduce fatigue and make
threshold breaches pop. That is a functional requirement, not drift —
the dashboards' two themes (ops/daylight) are documented in their own
stylesheet header and both pass the same contrast gate.

## Gates that keep this honest

- `tools/test_a11y_contrast.py` — WCAG AA contrast on every app's CSS;
  palette changes cannot sneak below 4.5:1.
- The shared token file is the only place care-surface radii and
  shadows are defined; app stylesheets reference the variables, never
  literals (enforced by review, cheap to grep: `grep -n "border-radius: [0-9]" apps/patient apps/pwa`).
