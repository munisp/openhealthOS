"""Inpatient ADT (admission–discharge–transfer) and bed management.

Stdlib-only state machine. An Episode moves
    admitted -> transferred* -> discharged | absconded | died | referred_out
and every move touches a Bed, so occupancy can never drift from reality:
you cannot discharge a patient from a bed they do not occupy, and a bed
cannot be double-allocated. Beds have infection-control states
(available / occupied / cleaning / closed) so a ward can quarantine a
side-room without losing count of capacity.
"""

BED_STATES = {"available", "occupied", "cleaning", "closed"}
EPISODE_STATES = {"admitted", "discharged", "absconded", "died", "referred_out"}
MOVEMENTS = {"admit", "transfer", "discharge", "abscond", "die", "refer_out"}


class Error(Exception):
    pass


class Bed:
    def __init__(self, bed_id, ward, kind="general"):
        if kind not in {"general", "icu", "isolation", "maternity", "paediatric"}:
            raise Error(f"unknown bed kind: {kind}")
        self.id = bed_id
        self.ward = ward
        self.kind = kind
        self.state = "available"
        self.episode_id = None


class Episode:
    def __init__(self, episode_id, patient_id, facility_id, actor):
        if not actor:
            raise Error("every ADT event names an actor (ADR-0018 doctrine)")
        self.id = episode_id
        self.patient_id = patient_id
        self.facility_id = facility_id
        self.state = "admitted"
        self.bed_id = None
        self.movements = []  # audit trail: (movement, actor, note)

    def _log(self, movement, actor, note):
        self.movements.append({"movement": movement, "actor": actor, "note": note})


class Ward:
    def __init__(self, facility_id):
        self.facility_id = facility_id
        self.beds = {}
        self.episodes = {}
        self._seq = 0

    # ---- beds
    def add_bed(self, bed_id, ward, kind="general"):
        if bed_id in self.beds:
            raise Error(f"bed exists: {bed_id}")
        self.beds[bed_id] = Bed(bed_id, ward, kind)
        return self.beds[bed_id]

    def set_bed_state(self, bed_id, state):
        bed = self._bed(bed_id)
        if state == "occupied" or (bed.state == "occupied" and state != "cleaning"):
            raise Error("beds become occupied only via admit/transfer, "
                        "and leave occupied only to cleaning")
        if state not in BED_STATES:
            raise Error(f"unknown bed state: {state}")
        bed.state = state
        return bed

    def _bed(self, bed_id):
        if bed_id not in self.beds:
            raise Error(f"unknown bed: {bed_id}")
        return self.beds[bed_id]

    # ---- episodes
    def admit(self, patient_id, bed_id, actor, note=""):
        for ep in self.episodes.values():
            if ep.patient_id == patient_id and ep.state == "admitted":
                raise Error(f"{patient_id} already admitted as {ep.id}")
        bed = self._bed(bed_id)
        if bed.state != "available":
            raise Error(f"bed {bed_id} is {bed.state}, not available")
        self._seq += 1
        ep = Episode(f"EP-{self._seq:05d}", patient_id, self.facility_id, actor)
        ep.bed_id = bed_id
        ep._log("admit", actor, note)
        bed.state = "occupied"
        bed.episode_id = ep.id
        self.episodes[ep.id] = ep
        return ep

    def transfer(self, episode_id, to_bed_id, actor, note=""):
        ep = self._open(episode_id)
        src, dst = self._bed(ep.bed_id), self._bed(to_bed_id)
        if dst.state != "available":
            raise Error(f"bed {to_bed_id} is {dst.state}, not available")
        src.state, src.episode_id = "cleaning", None  # vacated beds need cleaning
        dst.state, dst.episode_id = "occupied", ep.id
        ep.bed_id = to_bed_id
        ep._log("transfer", actor, note)
        return ep

    def discharge(self, episode_id, actor, outcome="discharged", note=""):
        if outcome not in {"discharged", "absconded", "died", "referred_out"}:
            raise Error(f"unknown outcome: {outcome}")
        ep = self._open(episode_id)
        ep.state = outcome
        ep._log({"discharged": "discharge", "absconded": "abscond",
                 "died": "die", "referred_out": "refer_out"}[outcome], actor, note)
        bed = self._bed(ep.bed_id)
        bed.state, bed.episode_id = "cleaning", None
        ep.bed_id = None
        return ep

    def _open(self, episode_id):
        if episode_id not in self.episodes:
            raise Error(f"unknown episode: {episode_id}")
        ep = self.episodes[episode_id]
        if ep.state != "admitted":
            raise Error(f"episode {episode_id} is {ep.state}")
        return ep

    # ---- views
    def occupancy(self):
        total = len(self.beds)
        occupied = sum(1 for b in self.beds.values() if b.state == "occupied")
        available = sum(1 for b in self.beds.values() if b.state == "available")
        return {"beds": total, "occupied": occupied, "available": available,
                "cleaning": sum(1 for b in self.beds.values() if b.state == "cleaning"),
                "closed": sum(1 for b in self.beds.values() if b.state == "closed"),
                "occupancy_pct": round(100.0 * occupied / total, 1) if total else 0.0}

    def census(self, ward=None):
        return [{"episode": ep.id, "patient": ep.patient_id, "bed": ep.bed_id,
                 "ward": self.beds[ep.bed_id].ward}
                for ep in self.episodes.values()
                if ep.state == "admitted"
                and (ward is None or self.beds[ep.bed_id].ward == ward)]
