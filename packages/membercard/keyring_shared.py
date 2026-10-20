"""Shared import shim — qrtrust lives in a sibling package; keep imports
working whether run from the package dir (PYTHONPATH=../qrtrust) or the
repo tooling harness."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "qrtrust"))
from keyring import *  # noqa: F401,F403
from keyring import TrustError, RevokedError  # noqa: F401

PREFIX = "OHMEM1"
