import os
import sys
import unittest

for pkg in ("teleread", "aigov"):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", pkg))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import ReaderRegistry, TeleReadError
from aigov import ModelRegistry, AIGovError
from drscreen import DRScreening


class TestDR(unittest.TestCase):
    def setUp(self):
        r = ReaderRegistry()
        r.register("gr-1", licence_id="G1", licence_expiry_day=400,
                   scopes=("dr-grader",))
        self.models = ModelRegistry()
        self.models.register("dr-ai", version="2.0", task="dr-grade",
                             licence_class="embed-clean",
                             evidence_ref="doi:10.x/dr")
        self.svc = DRScreening(r, self.models)

    def test_ai_draft_governed(self):
        with self.assertRaises(TeleReadError):
            DRScreening(ReaderRegistry()).screen(
                "p1", eye="both", image_ref="i1", facility="f",
                referrer="n1", day=0,
                ai_draft={"model_id": "dr-ai", "version": "2.0",
                          "output": {}})
        with self.assertRaises(AIGovError):
            self.svc.screen("p1", eye="both", image_ref="i1", facility="f",
                            referrer="n1", day=0,
                            ai_draft={"model_id": "ghost", "version": "9",
                                      "output": {}})

    def test_cascade_sequential_and_attrition(self):
        self.svc.screen("p1", eye="both", image_ref="i1", facility="f",
                        referrer="n1", day=0)
        with self.assertRaises(TeleReadError):
            self.svc.advance("p1", "treated", day=5)
        self.svc.record_grade("p1", grade="pdr", day=2)
        self.svc.advance("p1", "attended", day=10)
        self.svc.advance("p1", "treated", day=10)
        self.svc.screen("p2", eye="left", image_ref="i2", facility="f",
                        referrer="n1", day=0)
        self.svc.record_grade("p2", grade="moderate-npdr", day=2)
        rep = self.svc.cascade_report()
        self.assertEqual(rep["screened"], 2)
        self.assertEqual(rep["referred"], 2)
        self.assertEqual(rep["treated"], 1)  # attrition visible

    def test_no_image_no_screen(self):
        with self.assertRaises(TeleReadError):
            self.svc.screen("p9", eye="left", image_ref="", facility="f",
                            referrer="n1", day=0)


if __name__ == "__main__":
    unittest.main()
