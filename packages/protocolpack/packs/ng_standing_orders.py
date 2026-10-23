"""ng_standing_orders.py — seed protocol pack, National Standing Orders shape.

Clinical content here is a SEED for engine validation, mapped to
publicly documented Nigerian PHC standing-order patterns (fever/malaria,
hypertension follow-up). Every pack MUST pass clinical review before
deployment — the version field exists so that review is pinned and
auditable. Doses live in the formulary snapshots, not in the engine.
"""

FEVER_V1 = {
    "version": "ngso-fever-1.0.0-seed",
    "start": "duration",
    "steps": {
        "duration": {
            "text": "How many days has the fever lasted?",
            "options": {"<2 days": "danger", "2-7 days": "danger",
                        ">7 days": {"outcome": "refer", "escalate": True}},
        },
        "danger": {
            "text": "Any danger signs — convulsions, unable to drink, "
                    "repeated vomiting, lethargy, stiff neck?",
            "options": {"yes": {"outcome": "emergency-refer", "escalate": True},
                        "no": "test"},
        },
        "test": {
            "text": "Malaria RDT result?",
            "options": {"positive": {"outcome": "chw-treat-protocol-act"},
                        "negative": {"outcome": "refer-for-review", "escalate": True}},
        },
    },
    "red_flags": {"duration": [">7 days"], "danger": ["yes"]},
    "actions": {
        "give-act": {"min_cadre": "chw"},        # standing order allows
        "give-iv-artesunate": {"min_cadre": "nurse"},
        "adjust-antihypertensive": {"min_cadre": "doctor"},
    },
}

HTN_FOLLOWUP_V1 = {
    "version": "ngso-htn-1.0.0-seed",
    "start": "bp",
    "steps": {
        "bp": {
            "text": "Blood pressure today?",
            "options": {"<140/90": {"outcome": "continue-plan"},
                        "140-179/90-109": "adherence",
                        ">=180/110": {"outcome": "emergency-refer", "escalate": True}},
        },
        "adherence": {
            "text": "Missed any doses since last visit?",
            "options": {"no": {"outcome": "review-at-clinic", "escalate": True},
                        "yes": {"outcome": "adherence-support-plan"}},
        },
    },
    "red_flags": {"bp": [">=180/110"]},
    "actions": {
        "counsel-adherence": {"min_cadre": "chw"},
        "adjust-antihypertensive": {"min_cadre": "doctor"},
    },
}

PACKS = {"fever": FEVER_V1, "hypertension-followup": HTN_FOLLOWUP_V1}
