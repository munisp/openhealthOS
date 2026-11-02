"""Convergence: cpoe's formulary and the drugkb knowledge base must
speak ONE class vocabulary.

The failure mode this guards against is silent: cpoe said 'acei',
drugkb said 'ace-inhibitor', the interaction table matched nothing,
and the missed pair presented as clearance. These tests pin the two
registries to each other so the vocabularies cannot drift apart again
without a red build.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for pkg in ("cpoe", "drugkb"):
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", pkg)))

from drugkb import (DRUG_CLASSES, CLASS_ALIASES, canonical_class,
                    classes_of)
from formulary import Formulary

_CPOE_DRUGS = Formulary().drugs


def _norm(drug):
    return drug.lower().replace(" ", "-")


class TestCanonicalClass(unittest.TestCase):
    def test_aliases_resolve(self):
        self.assertEqual(canonical_class("acei"), "ace-inhibitor")
        self.assertEqual(canonical_class("nsaids"), "nsaid")
        self.assertEqual(canonical_class("quinolone"), "fluoroquinolone")

    def test_canonical_names_pass_through(self):
        for cls in DRUG_CLASSES:
            self.assertEqual(canonical_class(cls), cls)

    def test_unknown_names_pass_through_unchanged(self):
        # convergence fixes known synonyms; it does not pretend to
        # know classes outside the registry
        self.assertEqual(canonical_class("ccb"), "ccb")

    def test_no_alias_points_nowhere(self):
        for alias, canonical in CLASS_ALIASES.items():
            self.assertIn(canonical, DRUG_CLASSES,
                          f"alias {alias} -> {canonical}, which is not "
                          "a class the registry knows")


class TestRegistriesAgree(unittest.TestCase):
    def test_every_cpoe_class_is_known_or_deliberately_local(self):
        """Every class cpoe asserts must either canonicalize to a
        drugkb class or be on an explicit local-only list that a
        reviewer has to justify extending."""
        local_only = {  # classes drugkb does not (yet) model
            "antimalarial", "electrolyte", "supplement", "analgesic",
            "biguanide", "insulin", "beta2-agonist", "uterotonic",
            "anticonvulsant", "anticoagulant", "ccb", "tetracycline",
            "penicillin",
        }
        for d in _CPOE_DRUGS.values():
            cls = canonical_class(d["class"])
            self.assertTrue(
                cls in DRUG_CLASSES or cls in local_only,
                f"{d['code']} ({d['name']}) carries class "
                f"'{d['class']}' which is neither a drugkb class nor a "
                f"reviewed local-only class — vocabularies must not "
                f"drift")

    def test_overlapping_drugs_agree_on_class(self):
        """Where BOTH registries know a drug, cpoe's (canonicalized)
        class must be one drugkb also asserts."""
        for d in _CPOE_DRUGS.values():
            name = _norm(d["name"])
            kb_classes = classes_of(name)
            if not kb_classes:
                continue  # drugkb does not know this drug; nothing to check
            self.assertIn(canonical_class(d["class"]), kb_classes,
                          f"{d['name']}: cpoe says {d['class']!r}, "
                          f"drugkb says {sorted(kb_classes)}")

    def test_interaction_pairs_use_real_classes(self):
        for a, b, sev, msg in Formulary().class_interactions:
            for cls in (a, b):
                self.assertTrue(
                    canonical_class(cls) in DRUG_CLASSES
                    or canonical_class(cls)
                    in {canonical_class(d["class"])
                        for d in _CPOE_DRUGS.values()},
                    f"interaction pair ({a}, {b}) names a class no "
                    f"drug carries — the rule can never fire")


class TestInteractionFiresAcrossVocabularies(unittest.TestCase):
    def test_acei_nsaid_pair_fires(self):
        """The pair that motivated convergence: lisinopril (cpoe
        'acei') + ibuprofen must surface the ACE-inhibitor/NSAID
        warning even though drugkb spells the class differently."""
        f = Formulary()
        findings = f.check_interactions(["NG-LISIN10", "NG-IBU400"])
        self.assertTrue(any("NSAID" in x["message"] or "nsaid"
                            in x["message"].lower()
                            for x in findings),
                        f"acei/nsaid interaction lost to vocabulary "
                        f"drift: {findings}")


if __name__ == "__main__":
    unittest.main()
