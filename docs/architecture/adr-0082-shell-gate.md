# ADR-0082: The shell gate — the offline guarantee is only as strong as the cache manifest

- Status: accepted
- Date: 2026-11-11
- Supersedes: nothing. Extends the wave discipline ("every wave adds the
  sw pair") with the gate that enforces it.

## Context

The estate's offline story rests on two service workers: `sw.js` (the
app shell) and `sw-vhc.js` (the boards shell). Every demo wave added its
board pair to `sw-vhc.js` by hand — discipline without a gate.

Wave-21 recon censused both shells against the tree and found three
defect classes, all silent:

1. **An install-killer.** `sw-vhc.js` cached `./design-tokens.css`;
   the file lives one level up at `apps/design-tokens.css`.
   `cache.addAll` rejects if ANY URL 404s, so the entire 59-URL vhc
   shell never installed. Every board page's offline guarantee was
   absent while every online demo stayed green — the failure mode of a
   service worker install is silence.
2. **Coverage gaps.** Ten registered payloads (appmarket, charting,
   inpatient/specialty NG+CV, vhc-br10, vhc-fund, vhc-growth,
   vhc-helpdesk) were in NO shell — including the data behind four
   cached boards, which would render "loading…" forever offline.
3. **Unregistered pages.** emergency.html — the break-glass page that
   must work offline above all others — plus radiology.html and
   viewer.html registered no service worker at all.

## Decision

1. **The defects are fixed:** the path corrected, the ten payloads
   added to the vhc shell, the three pages added to the app shell and
   given `sw.js` registration. `probe.html` is a stated exclusion
   (diagnostic probe, run online by hand) — exclusions are data with
   reasons, never silence.

2. **`packages/shellproof/shellproof.py@1.0.0` is the gate.**
   `audit(root, *, payloads, exists/listdir/read INJECTED)` parses both
   shells' URL lists, resolves every entry against disk (relative to
   the shell's own directory, `../` escapes included), and checks every
   registered payload and every board page against the union of all
   shells. Verdicts are named: install-killers (red), uncached targets
   (red), stated exclusions (amber).

3. **The meta gate runs it against the live tree**
   (`TestRealTree`), with the proofindex registry injected as the
   payload list — a registered payload that no shell caches fails the
   suite.

4. **The demo audits the real tree at export time** (statuses and
   counts only). If the estate drifts — a board added without its
   shell pair — the committed payload goes stale and the freshness
   gate (ADR-0080) names it. The refresh rail (ADR-0081) repairs it.

## Consequences

- The offline guarantee is now a gated property, not a discipline.
- Adding a board without its shell entry fails the suite twice: the
  shell gate names the coverage gap; the freshness gate names the stale
  shellproof payload.
- Stated exclusions carry reasons and are enumerable in one place.

## Alternatives considered

- **Fix the path and move on.** Rejected: the same silence would
  return with the next hand-edited manifest.
- **Generate the shell lists from the tree.** Rejected: the shells
  would stop being reviewable declarations; the gate keeps them
  hand-written but HONEST.
- **Exclude the emergency page's registration fix to a later wave.**
  Rejected: break-glass offline access is the highest-stakes page in
  the estate; the fix is one script tag.
