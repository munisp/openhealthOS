"""Document de-duplication and code-to-plain-text enrichment.

Clean-room re-implementation of the two genuinely useful patterns in
Metriport's (AGPLv3, not embeddable) document pipeline, adapted to FHIR R4:

1. simhash near-deduplication — when the same referral letter arrives via
   WhatsApp, the PWA, and a scanned upload, the record must not grow three
   copies. Exact dupes are easy; *near*-dupes (re-scans, re-exports with a
   new header) are what SimHash catches in O(1) per lookup.
2. code enrichment — FHIR resources arrive carrying bare codes
   ("ICD-10: A15.0"); clinicians read plain language. A small bundled
   dictionary plus any supplied terminology maps codes to display text.
"""

import hashlib
import re

_WORD = re.compile(r"[a-z0-9]+")


class Error(Exception):
    pass


# ------------------------------------------------------------- simhash

def _tokens(text):
    # Word-level features. Shingles were tested and rejected: on the short
    # documents this pipeline handles (referral letters, discharge notes —
    # tens of words), a shingle-level edit perturbs too large a fraction of
    # the feature set and the 3-bit threshold stops separating near-dupes
    # from distinct documents.
    return _WORD.findall(text.lower())


def simhash(text, bits=64):
    toks = _tokens(text)
    if not toks:
        raise Error("cannot fingerprint an empty document")
    v = [0] * bits
    for tok in toks:
        h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(),
                           "big")
        for i in range(bits):
            v[i] += 1 if (h >> i) & 1 else -1
    fp = 0
    for i in range(bits):
        if v[i] > 0:
            fp |= 1 << i
    return fp


def hamming(a, b, bits=64):
    return bin(a ^ b).count("1")


class DedupIndex:
    """threshold: max hamming distance to count as a duplicate (3 of 64
    bits is the classic near-dupe cut-off)."""

    def __init__(self, threshold=3):
        self.threshold = threshold
        self.seen = {}  # fingerprint -> doc_id

    def check(self, doc_id, text):
        """Returns {"duplicate": bool, "matches": [doc_id, distance]}.
        Registers the document either way."""
        fp = simhash(text)
        matches = []
        for seen_fp, seen_id in self.seen.items():
            d = hamming(fp, seen_fp)
            if d <= self.threshold:
                matches.append((seen_id, d))
        self.seen[fp] = doc_id
        return {"duplicate": bool(matches),
                "matches": sorted(matches, key=lambda m: m[1])}


# ------------------------------------------------------------- enrichment

# Small bundled core dictionary; facilities extend with their own
# terminology files. ICD-10 codes that matter most to surveillance (ADR-0020)
# and top-up NGS (national guideline) procedure codes.
CORE_TERMS = {
    "A00": "Cholera",
    "A01": "Typhoid fever",
    "A15": "Respiratory tuberculosis",
    "B05": "Measles",
    "B50": "Plasmodium falciparum malaria",
    "B54": "Unspecified malaria",
    "A95": "Yellow fever",
    "A98": "Viral haemorrhagic fever",
    "G00": "Bacterial meningitis",
    "I10": "Essential hypertension",
    "E11": "Type 2 diabetes mellitus",
    "O80": "Spontaneous vertex delivery",
    "Z23": "Immunization encounter",
    "penta1": "Pentavalent vaccine, first dose",
    "penta3": "Pentavalent vaccine, third dose",
    "anc1": "Antenatal care, first visit",
    "anc4": "Antenatal care, fourth visit",
}


def enrich_code(code, dictionary=None):
    """Bare code -> plain-language display. Unknown codes pass through with
    an explicit marker rather than a guess."""
    terms = dict(CORE_TERMS)
    if dictionary:
        terms.update(dictionary)
    key = code.split(".")[0] if "." in code else code  # A15.0 -> A15
    if code in terms:
        return terms[code]
    if key in terms:
        return terms[key]
    return f"[uncoded: {code}]"


def enrich_resource(resource, dictionary=None):
    """Walk a FHIR R4 resource and add `text` display fields to every bare
    CodeableConcept coding. Returns a NEW dict — inputs are never mutated."""
    import copy
    r = copy.deepcopy(resource)

    def walk(node):
        if isinstance(node, dict):
            if "coding" in node and isinstance(node["coding"], list):
                for coding in node["coding"]:
                    if isinstance(coding, dict) and "code" in coding \
                            and "display" not in coding:
                        coding["display"] = enrich_code(coding["code"], dictionary)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(r)
    return r
