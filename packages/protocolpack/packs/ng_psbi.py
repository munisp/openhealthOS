"""ng_psbi.py — seed PSBI pack: sick young infant 0-2 months.

IMNCI's classic algorithm starts at 2 months; the neonate with sepsis
sat in a protocol hole between the PNC danger signs and IMNCI. PSBI
(possible serious bacterial infection) screening closes it — clean-room
seed mapped to the publicly documented WHO young-infant structure:
danger signs, then the PSBI sign set, then feeding/jaundice.

SEED: requires clinical review (packreview) before deployment.
"""

PSBI_V1 = {
    "version": "psbi-young-infant-1.0.0-seed",
    "start": "danger",
    "steps": {
        "danger": {
            "text": "Any danger sign — convulsions, unable to feed at "
                    "all, no spontaneous movement, or lethargic/"
                    "unconscious?",
            "options": {
                "yes": {"outcome": "emergency-refer-newborn-prewrite-referral",
                        "escalate": True},
                "no": "psbi-signs"},
        },
        "psbi-signs": {
            "text": "Any PSBI sign — fast breathing (>=60/min), severe "
                    "chest indrawing, fever (>=37.5) or hypothermia "
                    "(<35.5), movement only when stimulated?",
            "options": {
                "yes": {"outcome": "psbi-refer-or-outpatient-where-referral-infeasible",
                        "escalate": True},
                "no": "feeding"},
        },
        "feeding": {
            "text": "Feeding poorly or not attaching well to the breast?",
            "options": {
                "poor-feeding": {"outcome": "feeding-support-review-2days"},
                "feeding-well": "jaundice"},
        },
        "jaundice": {
            "text": "Jaundice — in first 24h of life, or palms/soles "
                    "yellow at any time?",
            "options": {
                "severe-jaundice": {"outcome": "emergency-refer-jaundice",
                                    "escalate": True},
                "mild-jaundice": {"outcome": "jaundice-followup-phototherapy-assess"},
                "none": {"outcome": "routine-newborn-care-followup"}},
        },
    },
    "red_flags": {
        "danger": ["yes"],
        "psbi-signs": ["yes"],
        "jaundice": ["severe-jaundice"],
    },
    "actions": {
        "give-oral-amoxicillin-young-infant": {"min_cadre": "nurse"},
        "give-im-gentamicin-pre-referral": {"min_cadre": "nurse"},
        "teach-express-breastmilk-cup-feed": {"min_cadre": "chw"},
        "manage-psbi-inpatient": {"min_cadre": "doctor"},
    },
}
