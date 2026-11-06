"""vhgolive — staged go-live gates for a VHC scheme deployment (vhc-golive@1.0.0).

A scheme does not "launch". It passes gates, in order, each with named
checks, mandatory evidence, and four-eyes sign-off. A gate that flips
without green checks and two hands is a bribe with a receipt — so the
engine refuses it.

Doctrine carried from the runbook (docs/runbooks/vhc-scheme-deployment.md):
  - absence is not a value: every check needs an evidence_ref
  - the hand that proposes never approves
  - a cable not plugged in is not an interface: a rail marked "unplugged"
    must be honestly declared, not silently absent
  - rollback triggers are named before cutover, not invented during one
  - country-agnostic: the profile drives the checks, never an if-country
"""

from __future__ import annotations

GOLIVE_VERSION = "vhc-golive@1.0.0"

# Gate order is contractual — a scheme walks it, it does not skip it.
GATES = (
    "G0_foundation",      # profile versioned, buckets sum to 100, keys in custody
    "G1_money_rails",     # PSP contract test, settlement recon, refunds
    "G2_border_lanes",    # lane kit, verdict paths, offline cache, stale=red
    "G3_claims_ops",      # intake->GOP->dossier->adjudicate->pay->appeal dry run
    "G4_surveillance",    # BR-10 tabletop, backstop tabletop, registry cadence
    "G5_cutover",         # canary ramp with named halt conditions
)

# Each gate's checks: (check_id, description). Evidence is attached at
# completion time; the description is the contract, not the checkbox.
GATE_CHECKS = {
    "G0_foundation": (
        ("profile_versioned", "country profile entry reviewed and version pinned"),
        ("buckets_sum_100", "fund bucket split sums to 100 in the profile"),
        ("key_ceremony", "HSM key ceremony done, rotation window agreed"),
        ("cache_schedule", "offline cache distribution inside cache_max_age_days"),
    ),
    "G1_money_rails": (
        ("psp_contract", "charge, 3DS, decline, refund paths exercised"),
        ("settlement_recon", "settlement file reconciles within break tolerance"),
        ("duplicate_refunds", "duplicate-charge auto-refund path demonstrated"),
        ("rail_declared", "national rail integrated or honestly declared unplugged"),
    ),
    "G2_border_lanes": (
        ("verdict_paths", "green/amber/red paths spot-checked on real lane kit"),
        ("stale_cache_red", "stale cache produces RED, not a silent pass"),
        ("offline_mode", "72h offline operation demonstrated end-to-end"),
        ("desk_runbook", "amber desk procedure rehearsed with named officer roles"),
    ),
    "G3_claims_ops": (
        ("gop_clock", "GOP issued inside 2h in dry run"),
        ("full_cycle", "intake->dossier->adjudication->payment->remittance->appeal"),
        ("tariff_four_eyes", "tariff book proposal and approval by different hands"),
        ("glosa_register", "glosa codes versioned and lookup working"),
    ),
    "G4_surveillance": (
        ("br10_tabletop", "five synthetic GI incidents trigger and ack an alert"),
        ("export_checksum", "IHR/UKHSA/ECDC export pack checksum verified"),
        ("backstop_tabletop", "backstop activation with notice ref, deactivation with solvency ref"),
        ("k_floor", "k-anonymity floor suppression demonstrated on dashboard"),
    ),
    "G5_cutover": (
        ("halt_conditions", "named halt conditions recorded before first canary"),
        ("rollback_drill", "rollback to previous state rehearsed"),
        ("ops_clocks", "every ops clock has a named owner"),
        ("suite_green", "pytest packages green on the deployment tag"),
    ),
}

# Canary ramp for G5: each stage doubles exposure; any halt condition at
# any stage freezes the ramp (never silently rolls it back — that is a
# decision for two hands).
CANARY_STAGES = (("canary_5", 5), ("canary_25", 25), ("canary_50", 50), ("full", 100))


class GateError(Exception):
    """Refused: a gate rule was violated. Fail-closed, always."""


class GoLivePlan:
    """One deployment's walk through the gates.

    plan = GoLivePlan(profile="ng-vhc@1.0.0")
    plan.complete_check("G0_foundation", "profile_versioned",
                        evidence_ref="ev/profile-review-2026-11.pdf", by="alice")
    plan.sign_off("G0_foundation", role="proposer", by="alice")
    plan.sign_off("G0_foundation", role="approver", by="bob")   # != alice
    plan.flip("G0_foundation")
    """

    def __init__(self, *, profile: str):
        if not profile or "@" not in profile:
            raise GateError("profile must be a pinned version, e.g. ng-vhc@1.0.0")
        self.profile = profile
        self._checks = {g: {} for g in GATES}          # gate -> check_id -> {evidence, by}
        self._signoffs = {g: {} for g in GATES}        # gate -> role -> hand
        self._flipped = []                             # ordered list of flipped gates
        self._halt_conditions = []                     # named before cutover
        self._rollback_drills = []                     # {day, by, note}
        self._canary_stage = None                      # None until G5 flipped
        self._ramp_frozen = False

    # ---- checks --------------------------------------------------------
    def complete_check(self, gate: str, check_id: str, *,
                       evidence_ref: str, by: str):
        self._require_gate(gate)
        known = {c for c, _ in GATE_CHECKS[gate]}
        if check_id not in known:
            raise GateError(f"unknown check {check_id!r} for {gate}")
        if not evidence_ref:
            raise GateError("absence is not a value: evidence_ref is mandatory")
        if not by:
            raise GateError("a check without a hand is a rumor")
        if gate in self._flipped:
            raise GateError(f"{gate} already flipped; reopen it deliberately, not quietly")
        self._checks[gate][check_id] = {"evidence_ref": evidence_ref, "by": by}

    def check_state(self, gate: str) -> dict:
        self._require_gate(gate)
        done = self._checks[gate]
        return {
            cid: {"desc": desc, "done": cid in done,
                  **({"evidence_ref": done[cid]["evidence_ref"], "by": done[cid]["by"]}
                     if cid in done else {})}
            for cid, desc in GATE_CHECKS[gate]
        }

    # ---- four-eyes -----------------------------------------------------
    def sign_off(self, gate: str, *, role: str, by: str):
        self._require_gate(gate)
        if role not in ("proposer", "approver"):
            raise GateError("role must be proposer or approver")
        other = "approver" if role == "proposer" else "proposer"
        if self._signoffs[gate].get(other) == by:
            raise GateError("the hand that proposes never approves")
        self._signoffs[gate][role] = by

    # ---- gate flip -----------------------------------------------------
    def flip(self, gate: str):
        self._require_gate(gate)
        if gate in self._flipped:
            raise GateError(f"{gate} already green")
        idx = GATES.index(gate)
        if idx > 0 and GATES[idx - 1] not in self._flipped:
            raise GateError(f"gates walk in order: {GATES[idx - 1]} is not green")
        missing = [cid for cid, _ in GATE_CHECKS[gate] if cid not in self._checks[gate]]
        if missing:
            raise GateError(f"{gate} has unproven checks: {', '.join(missing)}")
        so = self._signoffs[gate]
        if "proposer" not in so or "approver" not in so:
            raise GateError(f"{gate} needs both hands before it flips")
        self._flipped.append(gate)

    # ---- cutover --------------------------------------------------------
    def declare_halt_conditions(self, conditions, *, by: str):
        """Name the halt conditions BEFORE the first canary. A halt
        condition invented mid-incident is a excuse, not a control."""
        if self._flipped and "G5_cutover" in self._flipped and self._canary_stage:
            raise GateError("halt conditions are named before cutover, not during it")
        conds = [c for c in conditions if c and c.strip()]
        if not conds:
            raise GateError("an empty halt list is a promise to never stop")
        self._halt_conditions = conds
        self._halt_by = by

    def record_rollback_drill(self, *, day: int, by: str, note: str):
        if not note:
            raise GateError("a drill without a note did not happen")
        self._rollback_drills.append({"day": day, "by": by, "note": note})

    def start_canary(self):
        if "G5_cutover" not in self._flipped:
            raise GateError("G5 must be green before exposure ramps")
        if not self._halt_conditions:
            raise GateError("no halt conditions declared; refusing to ramp")
        if not self._rollback_drills:
            raise GateError("no rollback drill on record; refusing to ramp")
        self._canary_stage = CANARY_STAGES[0][0]
        self._ramp_frozen = False

    def advance_canary(self):
        if self._canary_stage is None:
            raise GateError("canary not started")
        if self._ramp_frozen:
            raise GateError("ramp frozen by a halt condition; two hands must unfreeze")
        idx = [s for s, _ in CANARY_STAGES].index(self._canary_stage)
        if idx + 1 < len(CANARY_STAGES):
            self._canary_stage = CANARY_STAGES[idx + 1][0]

    def halt_ramp(self, *, condition: str, by: str):
        if condition not in self._halt_conditions:
            raise GateError(f"{condition!r} is not a declared halt condition")
        self._ramp_frozen = True
        self._halt_event = {"condition": condition, "by": by}

    def unfreeze_ramp(self, *, proposer: str, approver: str, note: str):
        if proposer == approver:
            raise GateError("the hand that proposes never approves")
        if not note:
            raise GateError("unfreezing without a note is amnesia")
        self._ramp_frozen = False

    # ---- reporting ------------------------------------------------------
    def readiness(self) -> dict:
        """Denominators visible, always: n green gates out of 6, and per
        gate the checks done out of the checks that exist."""
        per_gate = {}
        total_done = total_checks = 0
        for g in GATES:
            checks = GATE_CHECKS[g]
            done = sum(1 for cid, _ in checks if cid in self._checks[g])
            per_gate[g] = {
                "checks_done": done, "checks_total": len(checks),
                "signed": sorted(self._signoffs[g]),
                "green": g in self._flipped,
            }
            total_done += done
            total_checks += len(checks)
        return {
            "version": GOLIVE_VERSION,
            "profile": self.profile,
            "gates_green": len(self._flipped),
            "gates_total": len(GATES),
            "checks_done": total_done,
            "checks_total": total_checks,
            "per_gate": per_gate,
            "halt_conditions": list(self._halt_conditions),
            "rollback_drills": len(self._rollback_drills),
            "canary_stage": self._canary_stage,
            "ramp_frozen": self._ramp_frozen,
            "live": self._canary_stage == "full" and not self._ramp_frozen,
        }

    def _require_gate(self, gate: str):
        if gate not in GATES:
            raise GateError(f"unknown gate {gate!r}; gates are {GATES}")
