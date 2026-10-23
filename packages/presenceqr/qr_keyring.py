"""Shared import shim — see membercard/keyring_shared.py."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "qrtrust"))
from keyring import *  # noqa: F401,F403
from keyring import TrustError, RevokedError  # noqa: F401

PREFIX = "OHPRS1"
