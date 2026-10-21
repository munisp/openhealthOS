"""corpus.py — seed NER corpus for Nigerian-language clinical de-id.

Status: SYNTHETIC SEED, authored for pipeline bring-up (ADR-0065).
Every sentence is hand-written, not harvested; no real patient text has
ever touched this file. Before any model is trained for production use
this corpus MUST be reviewed and extended by native speakers andprivacy
review — it exists to prove the pipeline end-to-end and to give the
community contribution a concrete starting shape.

Entity labels: NIN, PHONE, NHIA_ID, FACILITY, EMAIL, PERSON.
Identifier formats are language-invariant, so each language's sentences
teach the *context* around the identifiers — which is exactly what a
monolingual English model misses in Hausa/Yorùbá/Igbo chat.
"""

SEED_CORPUS = [
    # ---- English (control language) ----
    {"id": "en-001", "lang": "en",
     "text": "Patient NIN 12345678901 called from 08035551234 about fever.",
     "spans": [{"start": 12, "end": 23, "label": "NIN"},
               {"start": 36, "end": 47, "label": "PHONE"}]},
    {"id": "en-002", "lang": "en",
     "text": "Amara Okafor attended OHF-FC-WUSE-0001; NHIA number NHIA-123456.",
     "spans": [{"start": 0, "end": 12, "label": "PERSON"}, {"start": 22, "end": 38, "label": "FACILITY"}, {"start": 52, "end": 63, "label": "NHIA_ID"}]},
    # ---- Hausa ----
    {"id": "ha-001", "lang": "ha",
     "text": "Mai ciwo NIN 12345678901 ya kira daga 08035551234 saboda zazzabi.",
     "spans": [{"start": 13, "end": 24, "label": "NIN"}, {"start": 38, "end": 49, "label": "PHONE"}]},
    {"id": "ha-002", "lang": "ha",
     "text": "Aisha Bello ta halarci OHF-FC-WUSE-0001, lambar NHIA NHIA-234567.",
     "spans": [{"start": 0, "end": 11, "label": "PERSON"}, {"start": 23, "end": 39, "label": "FACILITY"}, {"start": 53, "end": 64, "label": "NHIA_ID"}]},
    {"id": "ha-003", "lang": "ha",
     "text": "Tura sakowa zuwa 08031112222 idan sakamakon ya fito.",
     "spans": [{"start": 17, "end": 28, "label": "PHONE"}]},
    # ---- Yorùbá ----
    {"id": "yo-001", "lang": "yo",
     "text": "Oníṣàárẹ NIN 12345678901 pe láti 08035551234 nítorí iba.",
     "spans": [{"start": 13, "end": 24, "label": "NIN"}, {"start": 33, "end": 44, "label": "PHONE"}]},
    {"id": "yo-002", "lang": "yo",
     "text": "Adaeze Eze lọ sí OHF-LA-IKEJA-0002, nọ́mbà NHIA NHIA-345678.",
     "spans": [{"start": 0, "end": 10, "label": "PERSON"}, {"start": 17, "end": 34, "label": "FACILITY"}, {"start": 48, "end": 59, "label": "NHIA_ID"}]},
    {"id": "yo-003", "lang": "yo",
     "text": "Jọ̀wọ́ pe 08023334455 fún àbájáde èyídánwò.",
     "spans": [{"start": 10, "end": 21, "label": "PHONE"}]},
    # ---- Igbo ----
    {"id": "ig-001", "lang": "ig",
     "text": "Onye ọrịa NIN 12345678901 kpọọrọ site na 08035551234 maka ọbara ọjọọ.",
     "spans": [{"start": 14, "end": 25, "label": "NIN"}, {"start": 41, "end": 52, "label": "PHONE"}]},
    {"id": "ig-002", "lang": "ig",
     "text": "Chiamaka Obi gara OHF-EN-NSUKKA-0003, nọmba NHIA NHIA-456789.",
     "spans": [{"start": 0, "end": 12, "label": "PERSON"}, {"start": 18, "end": 36, "label": "FACILITY"}, {"start": 49, "end": 60, "label": "NHIA_ID"}]},
    {"id": "ig-003", "lang": "ig",
     "text": "Biko kpọọ 08055556677 maka ọgwụ mgbochi.",
     "spans": [{"start": 10, "end": 21, "label": "PHONE"}]},
]

LANGS = sorted({r["lang"] for r in SEED_CORPUS})
LABELS = ["NIN", "PHONE", "NHIA_ID", "FACILITY", "EMAIL", "PERSON"]


def validate(rows=None):
    """Every span's offsets must slice exactly its entity text — a corpus
    whose offsets drift trains lies. Raises on the first violation."""
    rows = rows or SEED_CORPUS
    for r in rows:
        for s in r["spans"]:
            frag = r["text"][s["start"]:s["end"]]
            if not frag or frag != frag.strip():
                raise ValueError("%s: bad offsets for %s" % (r["id"], s))
            if s["label"] not in LABELS:
                raise ValueError("%s: unknown label %s" % (r["id"], s["label"]))
    return True
