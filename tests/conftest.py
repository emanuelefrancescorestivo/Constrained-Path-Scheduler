"""Shared fixtures: a local web server that plays a university timetable's link."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class _Timetable(BaseHTTPRequestHandler):
    """`/<file>.ics` from examples/, plus the ways a link goes wrong."""

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/web-page":
            self._send(200, b"<html><body>Log in to see your timetable</body></html>", "text/html")
        elif path == "/missing":
            self._send(404, b"not found", "text/plain")
        elif path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/sample-timetable.ics")
            self.end_headers()
        elif path == "/moved-inside":
            self.send_response(302)
            self.send_header("Location", "http://10.0.0.7/sample-timetable.ics")
            self.end_headers()
        elif (EXAMPLES / path.lstrip("/")).is_file() and path.endswith(".ics"):
            self._send(200, (EXAMPLES / path.lstrip("/")).read_bytes(), "text/calendar")
        else:
            self._send(404, b"not found", "text/plain")

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(scope="session")
def timetable_server() -> Iterator[str]:
    """Base URL of a local server with the example calendars and some bad links."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Timetable)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


# --------------------------------------------------------------------------- #
# A passkey device, simulated: what a phone or a password manager does (D35)
# --------------------------------------------------------------------------- #


def cbor(value: object) -> bytes:
    """CBOR, as much as an authenticator writes (the server only reads it)."""

    def head(major: int, n: int) -> bytes:
        if n < 24:
            return bytes([major << 5 | n])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | info]) + n.to_bytes(size, "big")
        raise ValueError("too large")

    if isinstance(value, bool):
        return bytes([0xF5 if value else 0xF4])
    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return head(3, len(raw)) + raw
    if isinstance(value, list):
        return head(4, len(value)) + b"".join(cbor(v) for v in value)
    if isinstance(value, dict):
        return head(5, len(value)) + b"".join(cbor(k) + cbor(v) for k, v in value.items())
    raise TypeError(type(value))


class PasskeyDevice:
    """Makes key pairs and signs as an authenticator does: the attestation object
    with its authenticator data and COSE key, the client data, the assertion. Each
    field can be spoiled, to show the server refuses it."""

    def __init__(self, alg: int = -7, origin: str = "http://testserver") -> None:
        self.alg = alg
        self.origin = origin
        self.count = 0
        self.keys: dict[str, tuple[object, str]] = {}  # credential id -> (private key, user handle)

    @staticmethod
    def b64(data: bytes) -> str:
        import base64

        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    def _key(self):
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

        if self.alg == -7:
            return ec.generate_private_key(ec.SECP256R1())
        if self.alg == -8:
            return ed25519.Ed25519PrivateKey.generate()
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def _cose(self, key) -> dict:
        from cryptography.hazmat.primitives import serialization

        public = key.public_key()
        if self.alg == -7:
            n = public.public_numbers()
            return {1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}
        if self.alg == -8:
            raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            return {1: 1, 3: -8, -1: 6, -2: raw}
        n = public.public_numbers()
        return {1: 3, 3: -257, -1: n.n.to_bytes(256, "big"), -2: n.e.to_bytes(3, "big")}

    def _sign(self, key, data: bytes) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, padding

        if self.alg == -7:
            return key.sign(data, ec.ECDSA(hashes.SHA256()))
        if self.alg == -8:
            return key.sign(data)
        return key.sign(data, padding.PKCS1v15(), hashes.SHA256())

    def _client(self, kind: str, challenge: str, origin: str | None) -> bytes:
        import json

        body = {"type": kind, "challenge": challenge, "origin": origin or self.origin, "crossOrigin": False}
        return json.dumps(body).encode("utf-8")

    def create(
        self,
        options: dict,
        *,
        origin: str | None = None,
        rp_id: str | None = None,
        flags: int = 0x45,
        kind: str = "webauthn.create",
        challenge: str | None = None,
        credential: bytes | None = None,
    ) -> dict:
        """`navigator.credentials.create`'s answer to the server's options."""
        import hashlib
        import os
        import struct

        key = self._key()
        credential = credential or os.urandom(16)
        self.keys[self.b64(credential)] = (key, options["user"]["id"])
        client = self._client(kind, challenge or options["challenge"], origin)
        auth = (
            hashlib.sha256((rp_id or options["rp"]["id"]).encode()).digest()
            + bytes([flags])
            + struct.pack(">I", self.count)
            + bytes(16)
            + struct.pack(">H", len(credential))
            + credential
            + cbor(self._cose(key))
        )
        attestation = cbor({"fmt": "none", "attStmt": {}, "authData": auth})
        return {
            "id": self.b64(credential),
            "type": "public-key",
            "response": {"clientDataJSON": self.b64(client), "attestationObject": self.b64(attestation)},
        }

    def get(
        self,
        options: dict,
        *,
        credential: str | None = None,
        origin: str | None = None,
        rp_id: str | None = None,
        flags: int = 0x05,
        step: int = 1,
        kind: str = "webauthn.get",
        handle: str | None = None,
        spoil: bool = False,
    ) -> dict:
        """`navigator.credentials.get`'s answer: a signature with the passkey."""
        import hashlib
        import struct

        credential = credential or next(iter(self.keys))
        key, user = self.keys[credential]
        self.count += step
        client = self._client(kind, options["challenge"], origin)
        auth = (
            hashlib.sha256((rp_id or options["rpId"]).encode()).digest()
            + bytes([flags])
            + struct.pack(">I", self.count)
        )
        signature = self._sign(key, auth + hashlib.sha256(client).digest())
        if spoil:
            signature = signature[:-1] + bytes([signature[-1] ^ 1])
        return {
            "id": credential,
            "type": "public-key",
            "response": {
                "clientDataJSON": self.b64(client),
                "authenticatorData": self.b64(auth),
                "signature": self.b64(signature),
                "userHandle": handle if handle is not None else user,
            },
        }


@pytest.fixture
def device() -> PasskeyDevice:
    pytest.importorskip("cryptography")
    return PasskeyDevice()
