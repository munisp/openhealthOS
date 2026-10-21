"""evaluate.py — token-level P/R/F1 for the de-id NER pipeline.

Works against ANY detector that emits spans — the deterministic pattern
pack today (an honest baseline on the seed corpus), a fine-tuned model
later. Same metric, same corpus, comparable numbers.
"""
from prepare import tokenize


def span_to_bio(text, spans):
    toks = tokenize(text)
    tags = ["O"] * len(toks)
    for s in spans:
        first = True
        for i, (_, ts, te) in enumerate(toks):
            if ts >= s["start"] and te <= s["end"]:
                tags[i] = ("B-" if first else "I-") + s["label"]
                first = False
    return tags


def prf1(gold_records, detect_fn):
    """detect_fn(text) -> spans with start/end/label. Returns honest
    micro P/R/F1 over BIO tags, per-label breakdown, and counts."""
    tp = fp = fn = 0
    per_label = {}
    for r in gold_records:
        gold = span_to_bio(r["text"], r["spans"])
        pred = span_to_bio(r["text"], detect_fn(r["text"]))
        for g, p in zip(gold, pred):
            for lab in {g, p} - {"O"}:
                d = per_label.setdefault(lab, {"tp": 0, "fp": 0, "fn": 0})
                if g == p:
                    d["tp"] += 1
                elif g != "O" and p == "O":
                    d["fn"] += 1
                elif p != "O" and g == "O":
                    d["fp"] += 1
                else:
                    d["fp"] += 1  # wrong label on a real entity
                    d["fn"] += 1
            if g == p and g != "O":
                tp += 1
            elif g != "O" and p == "O":
                fn += 1
            elif p != "O" and g == "O":
                fp += 1
            elif g != "O" and p != "O":
                fp += 1
                fn += 1
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"micro": {"precision": round(p, 4), "recall": round(r, 4),
                      "f1": round(f, 4), "tp": tp, "fp": fp, "fn": fn},
            "per_label": per_label}
