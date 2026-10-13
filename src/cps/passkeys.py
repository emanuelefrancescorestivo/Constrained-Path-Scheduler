"""
Passkeys (WebAuthn), checked on the server (DECISIONS.md D35).

A passkey is a key pair the student's device makes for this site and keeps behind
their face, fingerprint or screen lock. The site keeps only the public half. To
sign in, the device signs a fresh random challenge; this module checks that
signature, and that it was made for this site, from this site's pages, with the
person present. No password, no e-mail, no third party.

What is checked, following the WebAuthn Level 2 specification:

* registration (`verify_registration`): the client data says `webauthn.create`,
  carries our challenge and our origin; the authenticator data is for our relying
  party (the SHA-256 of the host name), with the user-present flag, and holds a
  credential with a public key in a form we can use (ES256, RS256 or Ed25519).
  Attestation is not asked for ("none"): the site does not need to know which
  device made the key, only that the same device signs later.
* sign-in (`verify_assertion`): the same checks with `webauthn.get`, then the
  signature over the authenticator data and the hash of the client data, with the
  public key kept at registration; a signature counter that goes backwards (a
  cloned key) is refused. Most passkeys that sync between devices keep it at zero,
  which is allowed.

The few bytes of CBOR WebAuthn uses are read here (`_cbor`), so that the only
dependency is `cryptography`, for the signatures. It is part of the `web` extra and
imported only when a key is read or a signature checked: the planner, the command
line and the workbench import `service`, which imports this module, without it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
from dataclasses import dataclass
from typing import Any

ES256, RS256, EDDSA = -7, -257, -8
ALGORITHMS = (ES256, EDDSA, RS256)  # in the order a browser is asked to prefer them

_USER_PRESENT = 0x01
_USER_VERIFIED = 0x04
_ATTESTED = 0x40


class PasskeyError(ValueError):
    """A registration or a sign-in that does not check out; the message says why,
    for the log and the tests (the student is told only that it did not work)."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as error:
        raise PasskeyError("not base64url") from error


# --------------------------------------------------------------------------- #
# CBOR, as much as WebAuthn uses
# --------------------------------------------------------------------------- #


def _cbor(data: bytes, at: int = 0) -> tuple[Any, int]:
    """One CBOR item from `data` at `at`: (value, position after it)."""
    if at >= len(data):
        raise PasskeyError("CBOR ends early")
    head = data[at]
    major, info = head >> 5, head & 0x1F
    at += 1
    if info < 24:
        arg = info
    elif info in (24, 25, 26, 27):
        size = 1 << (info - 24)
        if at + size > len(data):
            raise PasskeyError("CBOR ends early")
        arg = int.from_bytes(data[at : at + size], "big")
        at += size
    else:
        raise PasskeyError("CBOR with indefinite lengths is not used by WebAuthn")
    if major == 0:
        return arg, at
    if major == 1:
        return -1 - arg, at
    if major in (2, 3):
        if at + arg > len(data):
            raise PasskeyError("CBOR ends early")
        chunk = data[at : at + arg]
        return (chunk if major == 2 else chunk.decode("utf-8")), at + arg
    if major == 4:
        items = []
        for _ in range(arg):
            item, at = _cbor(data, at)
            items.append(item)
        return items, at
    if major == 5:
        mapping = {}
        for _ in range(arg):
            key, at = _cbor(data, at)
            value, at = _cbor(data, at)
            mapping[key] = value
        return mapping, at
    if major == 7 and info in (20, 21, 22):
        return {20: False, 21: True, 22: None}[info], at
    raise PasskeyError("CBOR item WebAuthn does not use")


def cbor_decode(data: bytes) -> Any:
    value, end = _cbor(data)
    if end != len(data):
        raise PasskeyError("trailing bytes after CBOR")
    return value


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #


def _public_key(cose: dict) -> tuple[int, Any]:
    """The algorithm and the public key of a COSE key (RFC 9053)."""
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

    alg = cose.get(3)
    kty = cose.get(1)
    if alg == ES256 and kty == 2 and cose.get(-1) == 1:
        x, y = cose.get(-2), cose.get(-3)
        if not (isinstance(x, bytes) and isinstance(y, bytes) and len(x) == len(y) == 32):
            raise PasskeyError("bad P-256 key")
        return alg, ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + x + y)
    if alg == RS256 and kty == 3:
        n, e = cose.get(-1), cose.get(-2)
        if not (isinstance(n, bytes) and isinstance(e, bytes)):
            raise PasskeyError("bad RSA key")
        return alg, rsa.RSAPublicNumbers(int.from_bytes(e, "big"), int.from_bytes(n, "big")).public_key()
    if alg == EDDSA and kty == 1 and cose.get(-1) == 6:
        x = cose.get(-2)
        if not (isinstance(x, bytes) and len(x) == 32):
            raise PasskeyError("bad Ed25519 key")
        return alg, ed25519.Ed25519PublicKey.from_public_bytes(x)
    raise PasskeyError(f"unsupported key (alg {alg}, kty {kty})")


def _der(key: Any) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def _verify(alg: int, der: bytes, signature: bytes, signed: bytes) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

    key = serialization.load_der_public_key(der)
    try:
        if alg == ES256 and isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        elif alg == RS256 and isinstance(key, rsa.RSAPublicKey):
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        elif alg == EDDSA and isinstance(key, ed25519.Ed25519PublicKey):
            key.verify(signature, signed)
        else:
            raise PasskeyError("the key does not match its algorithm")
    except InvalidSignature as error:
        raise PasskeyError("the signature does not match") from error


# --------------------------------------------------------------------------- #
# The two ceremonies
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AuthenticatorData:
    rp_id_hash: bytes
    flags: int
    sign_count: int
    credential_id: bytes | None = None
    cose_key: dict | None = None


def parse_authenticator_data(data: bytes) -> AuthenticatorData:
    if len(data) < 37:
        raise PasskeyError("authenticator data too short")
    rp_id_hash, flags, count = data[:32], data[32], struct.unpack(">I", data[33:37])[0]
    if not flags & _ATTESTED:
        return AuthenticatorData(rp_id_hash, flags, count)
    if len(data) < 55:
        raise PasskeyError("credential data too short")
    length = struct.unpack(">H", data[53:55])[0]
    credential_id = data[55 : 55 + length]
    if len(credential_id) != length or not length:
        raise PasskeyError("credential id cut short")
    cose, _end = _cbor(data, 55 + length)
    if not isinstance(cose, dict):
        raise PasskeyError("credential key is not a map")
    return AuthenticatorData(rp_id_hash, flags, count, credential_id, cose)


def _client_data(raw: bytes, kind: str, challenge: bytes, origin: str) -> None:
    try:
        client = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PasskeyError("client data is not JSON") from error
    if client.get("type") != kind:
        raise PasskeyError(f"client data is not {kind}")
    if unb64url(str(client.get("challenge", ""))) != challenge:
        raise PasskeyError("not our challenge")
    if client.get("origin") != origin:
        raise PasskeyError(f"made on {client.get('origin')!r}, not {origin!r}")


def _authenticator(auth: AuthenticatorData, rp_id: str) -> None:
    if auth.rp_id_hash != hashlib.sha256(rp_id.encode("utf-8")).digest():
        raise PasskeyError("made for another site")
    if not auth.flags & _USER_PRESENT:
        raise PasskeyError("the person was not present")


@dataclass(frozen=True)
class Credential:
    """What the site keeps of a passkey: its id, its public key (DER), the
    algorithm and the signature counter."""

    id: str
    public_key: bytes
    alg: int
    sign_count: int
    verified: bool  # the device checked the person (face, fingerprint, PIN)


def verify_registration(
    *, attestation_object: str, client_data: str, challenge: bytes, origin: str, rp_id: str
) -> Credential:
    """The credential a new passkey registers, if everything checks out."""
    raw_client = unb64url(client_data)
    _client_data(raw_client, "webauthn.create", challenge, origin)
    attestation = cbor_decode(unb64url(attestation_object))
    if not isinstance(attestation, dict) or not isinstance(attestation.get("authData"), bytes):
        raise PasskeyError("attestation object without authenticator data")
    auth = parse_authenticator_data(attestation["authData"])
    _authenticator(auth, rp_id)
    if auth.credential_id is None or auth.cose_key is None:
        raise PasskeyError("no credential in the registration")
    alg, key = _public_key(auth.cose_key)
    return Credential(
        b64url(auth.credential_id), _der(key), alg, auth.sign_count, bool(auth.flags & _USER_VERIFIED)
    )


def verify_assertion(
    *,
    authenticator_data: str,
    client_data: str,
    signature: str,
    challenge: bytes,
    origin: str,
    rp_id: str,
    public_key: bytes,
    alg: int,
    sign_count: int,
) -> int:
    """The new signature counter, if this sign-in checks out with the kept key."""
    raw_client = unb64url(client_data)
    _client_data(raw_client, "webauthn.get", challenge, origin)
    raw_auth = unb64url(authenticator_data)
    auth = parse_authenticator_data(raw_auth)
    _authenticator(auth, rp_id)
    _verify(alg, public_key, unb64url(signature), raw_auth + hashlib.sha256(raw_client).digest())
    if auth.sign_count and sign_count and auth.sign_count <= sign_count:
        raise PasskeyError("the signature counter went backwards: a copied key?")
    return auth.sign_count
