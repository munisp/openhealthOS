import unittest

from inpatient import Ward, Error


def ward():
    w = Ward("FAC-1")
    w.add_bed("B1", "male-medical")
    w.add_bed("B2", "male-medical")
    w.add_bed("ISO-1", "male-medical", kind="isolation")
    w.add_bed("ICU-1", "icu", kind="icu")
    return w


class TestADT(unittest.TestCase):
    def test_admit_occupies_bed(self):
        w = ward()
        ep = w.admit("PAT-1", "B1", "nurse-7")
        self.assertEqual(ep.id, "EP-00001")
        self.assertEqual(w.beds["B1"].state, "occupied")
        self.assertEqual(w.occupancy()["occupied"], 1)

    def test_no_double_allocation(self):
        w = ward()
        w.admit("PAT-1", "B1", "nurse-7")
        with self.assertRaises(Error):
            w.admit("PAT-2", "B1", "nurse-7")

    def test_one_open_episode_per_patient(self):
        w = ward()
        w.admit("PAT-1", "B1", "nurse-7")
        with self.assertRaises(Error):
            w.admit("PAT-1", "B2", "nurse-7")

    def test_transfer_cleans_vacated_bed(self):
        w = ward()
        ep = w.admit("PAT-1", "B1", "nurse-7")
        w.transfer(ep.id, "ICU-1", "nurse-7", "deteriorating")
        self.assertEqual(w.beds["B1"].state, "cleaning")
        self.assertEqual(w.beds["ICU-1"].state, "occupied")
        self.assertEqual(ep.bed_id, "ICU-1")

    def test_transfer_rejects_occupied_target(self):
        w = ward()
        e1 = w.admit("PAT-1", "B1", "nurse-7")
        w.admit("PAT-2", "B2", "nurse-7")
        with self.assertRaises(Error):
            w.transfer(e1.id, "B2", "nurse-7")

    def test_discharge_frees_bed_to_cleaning_then_available(self):
        w = ward()
        ep = w.admit("PAT-1", "B1", "nurse-7")
        w.discharge(ep.id, "doctor-3")
        self.assertEqual(ep.state, "discharged")
        self.assertEqual(w.beds["B1"].state, "cleaning")
        w.set_bed_state("B1", "available")
        ep2 = w.admit("PAT-1", "B1", "nurse-7")  # readmission allowed
        self.assertNotEqual(ep.id, ep2.id)

    def test_discharge_outcomes(self):
        w = ward()
        for i, outcome in enumerate(["discharged", "absconded", "died", "referred_out"]):
            ep = w.admit(f"PAT-{i}", ["B1", "B2", "ISO-1", "ICU-1"][i], "nurse-7")
            w.discharge(ep.id, "doctor-3", outcome=outcome)
            self.assertEqual(ep.state, outcome)
        with self.assertRaises(Error):
            w.discharge("EP-00001", "doctor-3")  # already closed

    def test_actor_required(self):
        w = ward()
        with self.assertRaises(Error):
            w.admit("PAT-1", "B1", "")

    def test_bed_state_guards(self):
        w = ward()
        with self.assertRaises(Error):
            w.set_bed_state("B1", "occupied")  # only via admit
        w.set_bed_state("ISO-1", "closed")     # infection-control closure
        with self.assertRaises(Error):
            w.admit("PAT-9", "ISO-1", "nurse-7")
        w.set_bed_state("ISO-1", "available")

    def test_census_and_occupancy(self):
        w = ward()
        w.admit("PAT-1", "B1", "nurse-7")
        w.admit("PAT-2", "ICU-1", "nurse-7")
        self.assertEqual(len(w.census()), 2)
        self.assertEqual(len(w.census("icu")), 1)
        occ = w.occupancy()
        self.assertEqual((occ["beds"], occ["occupied"], occ["available"]), (4, 2, 2))

    def test_audit_trail(self):
        w = ward()
        ep = w.admit("PAT-1", "B1", "nurse-7", "via emergency")
        w.discharge(ep.id, "doctor-3", note="fit for discharge")
        self.assertEqual([m["movement"] for m in ep.movements], ["admit", "discharge"])
        self.assertEqual(ep.movements[0]["actor"], "nurse-7")


if __name__ == "__main__":
    unittest.main()
