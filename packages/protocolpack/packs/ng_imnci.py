"""ng_imnci.py — seed IMNCI protocol pack (under-5 integrated management).

Clean-room seed mapped to the publicly documented WHO IMNCI danger-sign
and classification structure: general danger signs first, then the big
three killers — pneumonia (fast breathing by age band), diarrhoea
(dehydration), fever (malaria in endemic Nigeria, rides the fever pack's
RDT doctrine). Doses live in formulary snapshots, never in the pack.

SEED for engine validation: requires clinical review (packreview) before
any deployment. Red flags on every danger-sign answer.
"""

IMNCI_V1 = {
    "version": "imnci-u5-1.0.0-seed",
    "start": "danger",
    "steps": {
        "danger": {
            "text": "Any general danger sign — convulsions, unable to "
                    "drink or breastfeed, vomits everything, lethargic or "
                    "unconscious?",
            "options": {
                "yes": {"outcome": "emergency-refer-pre-referral-treatment",
                        "escalate": True},
                "no": "breathing"},
        },
        "breathing": {
            "text": "Fast breathing for age (<2m: >=60/min, 2-12m: "
                    ">=50/min, 1-5y: >=40/min) or chest indrawing?",
            "options": {
                "chest-indrawing": {"outcome": "severe-pneumonia-refer",
                                    "escalate": True},
                "fast-breathing": {"outcome": "pneumonia-amoxicillin-dt-protocol"},
                "normal": "diarrhoea"},
        },
        "diarrhoea": {
            "text": "Diarrhoea present? Assess dehydration: lethargic/"
                    "sunken eyes/skin pinch very slow?",
            "options": {
                "severe-dehydration": {"outcome": "emergency-refer-iv-plan",
                                       "escalate": True},
                "some-dehydration": {"outcome": "ors-zinc-plan-b"},
                "none": {"outcome": "ors-home-plan-a", "next": "feeding"},
                "no-diarrhoea": "feeding"},
        },
        "feeding": {
            "text": "Feeding problem or MUAC < 12.5cm or bilateral "
                    "pitting oedema?",
            "options": {
                "oedema-or-muac<11.5": {"outcome": "severe-acute-malnutrition-refer",
                                        "escalate": True},
                "muac-11.5-12.4": {"outcome": "supplementary-feeding-program"},
                "feeding-problem": {"outcome": "feeding-counselling-followup"},
                "well": {"outcome": "routine-prevention-visit"}},
        },
    },
    "red_flags": {
        "danger": ["yes"],
        "breathing": ["chest-indrawing"],
        "diarrhoea": ["severe-dehydration"],
        "feeding": ["oedema-or-muac<11.5"],
    },
    "actions": {
        "give-oral-amoxicillin-dt": {"min_cadre": "chw"},
        "give-ors-zinc": {"min_cadre": "chw"},
        "give-pre-referral-antibiotic": {"min_cadre": "nurse"},
        "start-iv-fluids": {"min_cadre": "nurse"},
        "manage-severe-pneumonia": {"min_cadre": "doctor"},
    },
}
