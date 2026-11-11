"""refresh.py — the refresh rail (ADR-0081). The freshness gate names
which committed payloads no longer match their machinery; this rail
regenerates ALL of them in one run, from the same recipe book
(recipes.py) the gate reads. A gate and its fix never drift apart.

    python3 packages/proofindex/refresh.py          # regenerate all
    python3 packages/proofindex/refresh.py --list   # show recipes

The executor is INJECTED into run_recipes: the CLI passes a subprocess
executor; demos and tests pass fixture executors. Stdlib only
(ADR-0005).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import recipes as _recipes  # noqa: E402

REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))


def _digest(root, path):
    full = os.path.join(root, path)
    if not os.path.exists(full):
        return None
    with open(full, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run_recipes(recipes_list, *, root, executor):
    """Run every recipe's write snippet and report per-payload results.

    executor(package, snippet) -> None; a raise is a runner-error
    verdict, never a crash. Results carry before/after digests per
    path so the caller can see exactly what moved.

    Statuses: regenerated (bytes changed), already-fresh (unchanged),
    runner-error (executor raised), absent-after-write (the recipe ran
    but its payload is not on disk — a recipe that does not produce
    its payload is a defect, named).
    """
    results = []
    for r in recipes_list:
        paths = r["paths"]
        before = {p: _digest(root, p) for p in paths}
        try:
            executor(r["package"], r["write"])
        except Exception as exc:
            results.append({
                "name": r["name"], "status": "runner-error",
                "detail": "%s: %s" % (type(exc).__name__, exc),
                "paths": paths})
            continue
        after = {p: _digest(root, p) for p in paths}
        missing = [p for p in paths if after[p] is None]
        if missing:
            results.append({
                "name": r["name"], "status": "absent-after-write",
                "detail": "recipe ran but produced nothing at %s"
                          % ", ".join(missing),
                "paths": paths})
            continue
        changed = [p for p in paths if before[p] != after[p]]
        results.append({
            "name": r["name"],
            "status": "regenerated" if changed else "already-fresh",
            "changed": changed,
            "digests": {p: after[p][:12] for p in paths},
            "paths": paths})
    summary = {
        "total": len(results),
        "regenerated": sum(1 for x in results
                           if x["status"] == "regenerated"),
        "already_fresh": sum(1 for x in results
                             if x["status"] == "already-fresh"),
        "runner_error": sum(1 for x in results
                            if x["status"] == "runner-error"),
        "absent_after_write": sum(1 for x in results
                                  if x["status"] == "absent-after-write"),
        "ok": all(x["status"] in ("regenerated", "already-fresh")
                  for x in results),
    }
    return {"results": results, "summary": summary}


def _subprocess_executor(root):
    def execute(package, snippet):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(root, package)
        r = subprocess.run(
            [sys.executable, "-c", snippet],
            cwd=os.path.join(root, package),
            env=env, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError("exit %s: %s" % (r.returncode,
                                                r.stderr[-400:]))
    return execute


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--list" in argv:
        for r in _recipes.RECIPES:
            print("%-18s %-5s %s" % (r["name"], r["kind"],
                                     ", ".join(r["paths"])))
        return 0
    report = run_recipes(_recipes.RECIPES, root=REPO_ROOT,
                         executor=_subprocess_executor(REPO_ROOT))
    for x in report["results"]:
        if x["status"] == "regenerated":
            print("REGENERATED  %-18s %s" % (x["name"],
                                             ", ".join(x["changed"])))
        elif x["status"] == "already-fresh":
            print("fresh        %-18s" % x["name"])
        else:
            print("ERROR        %-18s %s" % (x["name"], x["detail"]))
    s = report["summary"]
    print("refresh: %d recipes — %d regenerated, %d already fresh, "
          "%d runner-error, %d absent-after-write"
          % (s["total"], s["regenerated"], s["already_fresh"],
             s["runner_error"], s["absent_after_write"]))
    return 0 if s["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
