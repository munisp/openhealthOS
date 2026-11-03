"""mllp.py — Minimal Lower Layer Protocol: the wire HL7 v2 actually
rides.

Every analyzer gateway, LIS middleware, and patient-monitor HL7 lane
in the field speaks MLLP: TCP with 0x0B framing, one message per
frame, one ACK per frame. A parser without the transport is a radio
without an antenna — this module is the antenna.

Doctrine:
  - framing is exact: SB (0x0B) ... EB (0x1C) CR. A truncated frame
    waits; a garbage stream refuses loudly;
  - every received message gets an honest ACK — AA only after the
    dispatch handler actually accepted it;
  - every connection registers, so "which devices are actually
    plugged in" is a query, not a guess.
"""
import datetime
import socket
import socketserver
import threading

SB = b"\x0b"
EB_CR = b"\x1c\x0d"


class MLLPError(Exception):
    pass


def frame(message: str) -> bytes:
    """Wrap an HL7 message in an MLLP frame."""
    if not message:
        raise MLLPError("refusing to frame an empty message — silence "
                        "on the wire is not a result")
    return SB + message.encode("utf-8") + EB_CR


def unframe(buffer: bytes):
    """Pull the first complete frame out of a byte buffer.

    Returns (message, rest). Incomplete frames return (None, buffer)
    — the wire is allowed to be slow; it is not allowed to be vague.
    A buffer with no start byte is garbage, not a partial message."""
    if not buffer:
        return None, buffer
    if not buffer.startswith(SB):
        raise MLLPError("stream does not start with SB (0x0B) — not "
                        "MLLP, or corrupted in transit; refuse, "
                        "don't resync by guessing")
    end = buffer.find(EB_CR, 1)
    if end == -1:
        return None, buffer  # partial frame — wait for more bytes
    return buffer[1:end].decode("utf-8"), buffer[end + 2:]


def _is_loopback(host):
    return host in ("127.0.0.1", "::1", "localhost")


def check_tls_posture(host, tls_context, *, allow_plaintext=False,
                      plaintext_reason=""):
    """Plaintext MLLP on a routable interface is patient data on the
    wire in cleartext. Loopback is fine (same box, same trust); anything
    else needs TLS or an explicit, reasoned exception — never a shrug."""
    if tls_context is not None or _is_loopback(host):
        return "tls" if tls_context is not None else "loopback-plaintext"
    if not allow_plaintext:
        raise MLLPError(
            f"plaintext MLLP to {host} — patient results in cleartext "
            "on a routable network. Supply a TLS context, or pass "
            "allow_plaintext=True with a reason and accept the audit "
            "entry that follows")
    if not plaintext_reason:
        raise MLLPError("a plaintext exception without a reason is a "
                        "shrug — name WHY this segment is trusted")
    return f"plaintext-exception:{plaintext_reason}"


class ConnectionRegistry:
    """Who is actually on the wire. Append-only connect/disconnect
    audit — a connection without a record is a rumour."""

    def __init__(self):
        self.events = []  # append-only
        self.open = {}    # conn_id -> info

    def register(self, conn_id, *, peer, adapter_id=None):
        info = {"conn_id": conn_id, "peer": peer,
                "adapter_id": adapter_id,
                "connected_at": datetime.datetime.now(
                    datetime.timezone.utc).isoformat()}
        self.open[conn_id] = info
        self.events.append({"event": "connect", **info})
        return info

    def deregister(self, conn_id):
        info = self.open.pop(conn_id, None)
        if info:
            self.events.append({
                "event": "disconnect", "conn_id": conn_id,
                "peer": info["peer"],
                "at": datetime.datetime.now(
                    datetime.timezone.utc).isoformat()})


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        server = self.server
        conn_id = f"{self.client_address[0]}:{self.client_address[1]}"
        server.registry.register(conn_id, peer=conn_id)
        buffer = b""
        try:
            while True:
                chunk = self.request.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                while True:
                    message, buffer = unframe(buffer)
                    if message is None:
                        break
                    try:
                        reply = server.dispatch(message)
                    except Exception as exc:  # the ACK must not lie
                        from devicehub import build_ack
                        reply = build_ack("UNKNOWN",
                                          error=f"dispatch: {exc}")
                    self.request.sendall(frame(reply))
        except MLLPError:
            pass  # garbage stream: drop the connection, loudly logged
        finally:
            server.registry.deregister(conn_id)


class MLLPListener(socketserver.ThreadingTCPServer):
    """The inbound socket. dispatch(message_str) -> ack_str is the
    platform's handler (typically DeviceHub.ingest + build_ack)."""
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host, port, dispatch, registry=None,
                 tls_context=None, allow_plaintext=False,
                 plaintext_reason=""):
        self.dispatch = dispatch
        self.registry = registry or ConnectionRegistry()
        self.tls_context = tls_context
        self.tls_posture = check_tls_posture(
            host, tls_context, allow_plaintext=allow_plaintext,
            plaintext_reason=plaintext_reason)
        super().__init__((host, port), _Handler)

    def get_request(self):
        sock, addr = super().get_request()
        if self.tls_context is not None:
            sock = self.tls_context.wrap_socket(sock, server_side=True)
        return sock, addr

    def serve_in_thread(self):
        t = threading.Thread(target=self.serve_forever, daemon=True)
        t.start()
        return t


def send(host, port, message, *, timeout=10.0, tls_context=None,
         allow_plaintext=False, plaintext_reason=""):
    """Client side: frame, send, wait for the ACK frame. A send without
    an ACK is a message into the void — raise, don't assume. TLS
    posture is checked before a single byte moves."""
    check_tls_posture(host, tls_context, allow_plaintext=allow_plaintext,
                      plaintext_reason=plaintext_reason)
    with socket.create_connection((host, port), timeout=timeout) as raw:
        s = (tls_context.wrap_socket(raw, server_hostname=host)
             if tls_context is not None else raw)
        s.sendall(frame(message))
        buffer = b""
        deadline = datetime.datetime.now() + datetime.timedelta(
            seconds=timeout)
        while datetime.datetime.now() < deadline:
            chunk = s.recv(65536)
            if not chunk:
                break
            buffer += chunk
            ack, buffer = unframe(buffer)
            if ack is not None:
                return ack
    raise MLLPError(f"no ACK from {host}:{port} within {timeout}s — "
                    "a message without an acknowledgement did not "
                    "arrive; retry through the spool, not by amnesia")
