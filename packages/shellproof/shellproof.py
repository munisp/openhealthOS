"""shellproof.py — the shell gate (ADR-0082). A service worker's offline
guarantee is only as strong as its cache manifest, and nothing pinned
it. Two failure classes, both named:

  * install-killer — cache.addAll rejects if ANY shell URL 404s; one
    bad path silently uninstalls the ENTIRE offline guarantee;
  * coverage gap — a board or payload never listed in any shell is
    invisible offline, however green the online demo was.

The audit resolves every entry of every declared shell against disk
(relative to the shell's own directory — "../" escapes included) and
checks every registered payload and every board page against the union
of all shells. Exclusions are stated data, never silence.

Stdlib only (ADR-0005). The tree reader and the payload list are
INJECTED into audit() like clocks: the gate test passes the live
proofindex registry; demos and unit tests pass fixtures.
"""

from __future__ import annotations

import os
import re

SHELL_FILES = ("apps/pwa/sw.js", "apps/pwa/sw-vhc.js")

# Stated, with reasons — everything else on disk must be cached.
STATED_EXCLUSIONS = {
    "apps/pwa/probe.html": ("IndexedDB/module diagnostic probe — run "
                            "online, by hand, during support; caching it "
                            "would pretend it is a product page"),
    "apps/pwa/sw.js": ("the installer itself — the browser fetches the "
                       "service worker directly; no cache can hold its "
                       "own installer"),
    "apps/pwa/sw-vhc.js": ("the installer itself — the browser fetches "
                           "the service worker directly; no cache can "
                           "hold its own installer"),
}

_URL_RE = re.compile(r'"(\.{1,2}/[^"]+)"')


def parse_shell(text):
    """Extract the shell URL list from a service worker source."""
    return _URL_RE.findall(text)


def resolve(shell_file, url):
    """Resolve a shell entry against the shell's own directory."""
    return os.path.normpath(os.path.join(os.path.dirname(shell_file),
                                         url))


def audit(root, *, shell_files=SHELL_FILES, payloads, board_ext=".html",
          exclusions=STATED_EXCLUSIONS, listdir=None, exists=None,
          read=None):
    """Audit every shell's entries and the estate's coverage.

    payloads: iterable of repo-relative registered payload paths.
    listdir/exists/read: INJECTED tree readers (defaults read the real
    tree under root). Returns named verdicts, never bare booleans:

      shells[].entries[].verdict: cached | missing (install-killer)
      coverage.uncached: payloads/boards in NO shell, named
      summary: install_killers, uncached, excluded, all_installable,
               full_coverage
    """
    exists = exists or (lambda p: os.path.exists(os.path.join(root, p)))
    read = read or (lambda p: open(
        os.path.join(root, p), encoding="utf-8").read())

    def _walk():
        if listdir is not None:
            return listdir()
        out = []
        for r, dirs, files in os.walk(os.path.join(root, "apps", "pwa")):
            dirs[:] = [d for d in dirs
                       if d not in ("node_modules", "tests")]
            for f in files:
                if f.endswith(board_ext):
                    out.append(os.path.relpath(
                        os.path.join(r, f), root))
        return sorted(out)

    shells = []
    all_cached = set()
    install_killers = []
    for sf in shell_files:
        if not exists(sf):
            shells.append({"file": sf, "verdict": "absent",
                           "entries": []})
            install_killers.append(sf)
            continue
        urls = parse_shell(read(sf))
        entries = []
        for u in urls:
            rel = resolve(sf, u)
            ok = exists(rel)
            all_cached.add(rel)
            if not ok:
                install_killers.append(rel)
            entries.append({"url": u, "path": rel,
                            "verdict": "cached" if ok else "missing"})
        shells.append({"file": sf, "verdict": "parsed",
                       "entries": entries})

    targets = set(payloads) | set(_walk())
    excluded = sorted(t for t in targets if t in exclusions)
    uncached = sorted(t for t in targets
                      if t not in all_cached and t not in exclusions)
    summary = {
        "shells": len(shells),
        "shell_entries": sum(len(s["entries"]) for s in shells),
        "targets": len(targets),
        "cached_targets": len([t for t in targets
                               if t in all_cached]),
        "install_killers": len(install_killers),
        "uncached": len(uncached),
        "excluded": len(excluded),
        "all_installable": not install_killers,
        "full_coverage": not uncached,
    }
    return {
        "shells": shells,
        "coverage": {
            "uncached": uncached,
            "excluded": [{"path": e, "reason": exclusions[e]}
                         for e in excluded],
        },
        "install_killers": sorted(set(install_killers)),
        "summary": summary,
    }


VERSION = "shellproof@1.0.0"
