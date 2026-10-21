"""prepare.py — span corpus -> BIO token datasets for NER fine-tuning.

Tokenisation is whitespace+punctuation splitting with offsets preserved,
so BIO tags always map back to character spans (the contract the rest of
the de-id pipeline speaks). Deterministic split: hash of record id
decides train/dev, so goldens are stable across runs.
"""
import hashlib
import json
import re

TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.U)


def tokenize(text):
    """[(token, start, end)] — offsets preserved for BIO alignment."""
    return [(m.group(0), m.start(), m.end()) for m in TOKEN_RE.finditer(text)]


def bio_encode(record):
    """One corpus record -> {"tokens": [...], "tags": [...], "id", "lang"}."""
    toks = tokenize(record["text"])
    tags = ["O"] * len(toks)
    for s in record["spans"]:
        first = True
        for i, (_, ts, te) in enumerate(toks):
            if ts >= s["start"] and te <= s["end"]:
                tags[i] = ("B-" if first else "I-") + s["label"]
                first = False
    return {"id": record["id"], "lang": record["lang"],
            "tokens": [t for t, _, _ in toks], "tags": tags}


def split(records, dev_fraction=0.25):
    """Deterministic train/dev split keyed on record id."""
    train, dev = [], []
    for r in records:
        h = int(hashlib.sha256(r["id"].encode()).hexdigest(), 16)
        (dev if h % 100 < dev_fraction * 100 else train).append(r)
    return train, dev


def export_jsonl(records, path):
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(bio_encode(r), ensure_ascii=False) + "\n")
    return len(records)
