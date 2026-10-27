import unittest
from medevac import EvacuationRegister, MedevacError


def _accepted_case(reg, **kw):
    c = reg.request(patient_id="p1", from_facility="phc-1",
                    reason="ruptured-ectopic", urgency="emergency",
                    requested_day=100, **kw)
    reg.accept(c["id"], day=100, receiving_facility="hosp-x",
               receiving_clinician="dr-q", bed_confirmation_id="BED-7")
    return c


class TestMedevac(unittest.TestCase):
    def setUp(self):
        self.r = EvacuationRegister()

    def test_domestic_ground_flow(self):
        c = _accepted_case(self.r)
        cid = c["id"]
        self.r.dispatch(cid, day=100, transport_kind="ground-ambulance",
                        escort="nurse-j")
        self.r.log_en_route(cid, ts="10:30", entry="bp falling",
                            vitals={"bp": "80/50"}, intervention="iv-fluids")
        h = self.r.handover(cid, day=100, receiving_clinician="dr-q",
                            handover_document_ref="doc-99")
        self.assertEqual(h["state"], "handed-over")
        self.assertEqual(h["handover"]["transport_log_entries"], 1)

    def test_no_bed_confirmation_no_acceptance(self):
        c = self.r.request(patient_id="p1", from_facility="phc-1",
                           reason="x", urgency="emergency", requested_day=1)
        with self.assertRaises(MedevacError):
            self.r.accept(c["id"], day=1, receiving_facility="h",
                          receiving_clinician="d", bed_confirmation_id="")

    def test_flight_requires_clearance(self):
        c = _accepted_case(self.r, flight_required=True)
        with self.assertRaises(MedevacError):
            self.r.dispatch(c["id"], day=100, transport_kind="fixed-wing",
                            escort="medic")
        self.r.clear_fit_to_fly(c["id"], day=100, clinician="dr-q",
                                constraints=["oxygen-4lpm", "stretcher"])
        out = self.r.dispatch(c["id"], day=100, transport_kind="fixed-wing",
                              escort="medic")
        self.assertEqual(out["state"], "in-transit")

    def test_cross_border_gated_on_release_and_ips(self):
        c = _accepted_case(self.r, destination_country="GH",
                           to_facility="korle-bu", flight_required=True)
        self.r.clear_fit_to_fly(c["id"], day=100, clinician="dr-q")
        with self.assertRaises(MedevacError):
            self.r.dispatch(c["id"], day=100, transport_kind="fixed-wing",
                            escort="medic")  # no release_id
        with self.assertRaises(MedevacError):
            self.r.dispatch(c["id"], day=100, transport_kind="fixed-wing",
                            escort="medic", release_id="REL-1")  # no hash
        out = self.r.dispatch(c["id"], day=100, transport_kind="fixed-wing",
                              escort="medic", release_id="REL-1",
                              ips_bundle_hash="sha256:abc")
        self.assertEqual(out["transport"]["release_id"], "REL-1")

    def test_flight_required_refuses_ground(self):
        c = _accepted_case(self.r, flight_required=True)
        self.r.clear_fit_to_fly(c["id"], day=100, clinician="dr-q")
        with self.assertRaises(MedevacError):
            self.r.dispatch(c["id"], day=100,
                            transport_kind="ground-ambulance", escort="x")

    def test_handover_requires_document(self):
        c = _accepted_case(self.r)
        self.r.dispatch(c["id"], day=100, transport_kind="ground-ambulance",
                        escort="n")
        with self.assertRaises(MedevacError):
            self.r.handover(c["id"], day=100, receiving_clinician="dr-q",
                            handover_document_ref="")

    def test_en_route_log_append_only_and_sequenced(self):
        c = _accepted_case(self.r)
        with self.assertRaises(MedevacError):
            self.r.log_en_route(c["id"], ts="t", entry="too early")
        self.r.dispatch(c["id"], day=100, transport_kind="ground-ambulance",
                        escort="n")
        with self.assertRaises(MedevacError):
            self.r.log_en_route(c["id"], ts="t", entry="")

    def test_awaiting_acceptance_and_stats(self):
        c = self.r.request(patient_id="p1", from_facility="phc-1",
                           reason="x", urgency="emergency", requested_day=100)
        self.r.refuse(c["id"], day=101, reason="no-neurosurgeon")
        aw = self.r.awaiting_acceptance(as_of=105)
        self.assertEqual(len(aw), 1)
        self.assertEqual(aw[0]["days_waiting"], 5)
        s = self.r.stats(as_of=105)
        self.assertEqual(s["by_state"]["refused"], 1)


if __name__ == "__main__":
    unittest.main()
