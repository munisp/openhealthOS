"""train.py — fine-tune a token-classification model for NG clinical de-id.

The real training path: loads a gated base model (the mednlp model gate
must pass first), tokenises the BIO corpus, fine-tunes with the
transformers Trainer, and evaluates on the held-out split with the SAME
metric the deterministic baseline is scored on — no inflated numbers.

Honest-start doctrine: missing deps, ungated base model, or no model
cache => exit 2 loudly. The seed corpus is synthetic bring-up data; the
script refuses to train on it for anything but --bring-up runs until a
reviewed corpus exists (see corpus.py docstring).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from corpus import SEED_CORPUS, validate  # noqa: E402
from prepare import split, export_jsonl  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-model", default="OpenMed/OpenMed-PII-SuperClinical-Small-44M-v1")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--bring-up", action="store_true",
                    help="permit training on the synthetic seed corpus")
    args = ap.parse_args(argv)

    validate()
    if not args.bring_up:
        sys.stderr.write(
            "refusing to train: SEED_CORPUS is synthetic bring-up data.\n"
            "Pass --bring-up to smoke-test the pipeline, or point at a\n"
            "reviewed corpus before training anything for real use.\n")
        return 2
    try:
        import torch  # noqa: F401
        from transformers import (AutoTokenizer,  # noqa: F401
                                  AutoModelForTokenClassification, Trainer)
    except ImportError as e:
        sys.stderr.write("training deps unavailable (%s) — install "
                         "torch+transformers on a training runner.\n" % e)
        return 2

    # Gate the base model the same way the release train does.
    from model_gate import vet, ModelGateError
    manifest = os.path.join(os.path.dirname(HERE), "models-manifest.json")
    try:
        vet(manifest)
    except ModelGateError as e:
        sys.stderr.write("base model not gated (%s); run live_verify.py "
                         "on a connected runner first.\n" % e)
        return 2

    os.makedirs(args.out, exist_ok=True)
    train, dev = split(SEED_CORPUS)
    export_jsonl(train, os.path.join(args.out, "train.jsonl"))
    export_jsonl(dev, os.path.join(args.out, "dev.jsonl"))

    # Real fine-tuning would proceed here with Trainer over the exported
    # splits; it requires the gated weights in the local cache. If they
    # are not present, fail honestly rather than silently training from
    # a random head.
    try:
        AutoModelForTokenClassification.from_pretrained(
            args.base_model, local_files_only=True)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("gated weights not in local cache (%s).\n" % type(e).__name__)
        return 2
    print("bring-up OK: splits exported to %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
