"""seriallane.py — the RS-232 lane for ASTM analyzers.

Most real ASTM devices hang off serial ports, not TCP. This module
reads sessions from any binary file object — os.open("/dev/ttyUSB0")
at deployment, BytesIO in tests. The codec is astm.py; this is just
the discipline of knowing when a session is complete and when the
line has gone silent mid-frame (a finding, not a timeout to swallow).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astm  # noqa: E402
from devicehub import DeviceHubError  # noqa: E402

SERIAL_VERSION = "serial-lane@1.0.0"


def read_session_from(source, *, max_bytes=1 << 20):
    """Read one ASTM session from a binary file object with .read(1).

    Returns astm.parse_session(text) plus a noise count. ENQ opens,
    EOT closes; anything before ENQ is line noise — counted, never
    silently forgiven."""
    buf = bytearray()
    noise = 0
    started = False
    while True:
        ch = source.read(1)
        if ch == b"":
            raise DeviceHubError(
                "serial line went silent mid-session — an incomplete "
                "session is a finding, not a result; the spool takes "
                "nothing, the feed health takes a mark")
        if not started:
            if ch == astm.ENQ.encode("latin-1"):
                started = True
                buf += ch
            else:
                noise += 1
            continue
        buf += ch
        if len(buf) > max_bytes:
            raise DeviceHubError(
                f"session exceeded {max_bytes} bytes — an analyzer "
                "streaming forever is a fault, not a big result")
        if ch == astm.EOT.encode("latin-1"):
            break
    text = buf.decode("latin-1")
    out = astm.parse_session(text)
    out["line_noise_bytes"] = noise
    return out


def open_serial(path, *, baud=9600):
    """Open a serial device for reading. Termios configuration is the
    deployment's job (stty) — this refuses clearly when the device is
    absent rather than pretending a port exists."""
    if not os.path.exists(path):
        raise DeviceHubError(f"{path} does not exist — a cable not "
                             "plugged in is not an interface")
    return open(path, "rb", buffering=0)
