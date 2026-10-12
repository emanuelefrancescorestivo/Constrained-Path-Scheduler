"""
Accounts that last (DECISIONS.md D35): a plan's passkeys and its recovery code.

A plan is still its private address, as a calendar's secret link is: there is no
name, no e-mail and no password. What this module adds are two ways back to that
address when it is lost (a new phone, a cleared browser):

* **Passkeys.** The device makes a key pair for this site and keeps it behind the
  student's face, fingerprint or screen lock; the site keeps the public half
  (`passkeys.py` checks the signatures). Signing in finds the plan from the passkey
  alone (a discoverable credential), so nothing has to be typed.
* **A recovery code**, for when every device is gone: twenty characters from an
  alphabet without look-alikes (about 100 bits), shown once, kept only as its
  SHA-256 digest. It works once; using it makes a new one, shown at once.

Rejected: e-mail links (an address to keep, mail to send, a provider to pay for),
passwords (to be hashed, leaked, reset), "Sign in with Google" (a third party told
of every sign-in). A plan without a passkey or a code cannot be found again if its
address is lost; the pages say so.

Challenges are kept here, not in a cookie (the service sets none): the browser
sends back the client data, which carries the challenge, and the challenge is
found, checked and deleted in one transaction, so it is used once.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from . import passkeys as _pk
from .passkeys import PasskeyError
from .store import Store

CHALLENGE_LIFE = timedelta(minutes=5)
SITE_NAME = "Study plan"
MAX_PASSKEYS = 10  # per plan: a phone, a laptop, a tablet and spares
# Crockford's base 32: no I, L, O or U, so a code copied by hand is read right.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 20  # 100 bits


class UnknownPasskey(PasskeyError):
    """A passkey this site does not know (any more): its plan was deleted, or it
    was made on another copy of the service."""


def _stamp(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds")


def user_id(token: str) -> bytes:
    """The user handle a passkey keeps for this plan: derived from the token, one
    way, so it is stable (a device replaces its passkey instead of adding a second)
    and gives nothing away if a passkey provider's copy is read."""
    return hashlib.sha256(b"cps passkey user\0" + token.encode("ascii")).digest()[:16]


# --------------------------------------------------------------------------- #
# Challenges
# --------------------------------------------------------------------------- #


def _challenge(db: sqlite3.Connection, purpose: str, token: str, now: datetime) -> str:
    value = _pk.b64url(secrets.token_bytes(32))
    db.execute("DELETE FROM challenges WHERE expires < ?", (_stamp(now),))
    db.execute(
        "INSERT INTO challenges (value, purpose, token, expires) VALUES (?, ?, ?, ?)",
        (value, purpose, token, _stamp(now + CHALLENGE_LIFE)),
    )
    return value


def _take_challenge(store: Store, client_data: str, purpose: str, token: str, now: datetime) -> bytes:
    """The challenge a ceremony answers, deleted first so that it cannot answer
    twice, even when this answer is refused."""
    try:
        sent = str(json.loads(_pk.unb64url(client_data).decode("utf-8")).get("challenge", ""))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as error:
        raise PasskeyError("client data is not JSON") from error
    with store.connection() as db:
        row = db.execute("SELECT purpose, token, expires FROM challenges WHERE value = ?", (sent,)).fetchone()
        db.execute("DELETE FROM challenges WHERE value = ?", (sent,))
    if row is None:
        raise PasskeyError("unknown or used challenge")
    if row[0] != purpose or row[1] != token:
        raise PasskeyError("a challenge made for something else")
    if row[2] < _stamp(now):
        raise PasskeyError("the challenge has expired")
    return _pk.unb64url(sent)


# --------------------------------------------------------------------------- #
# Passkeys
# --------------------------------------------------------------------------- #


def _ids(db: sqlite3.Connection, token: str) -> list[str]:
    return [i for (i,) in db.execute("SELECT id FROM passkeys WHERE token = ? ORDER BY created", (token,))]


def registration_options(store: Store, token: str, *, rp_id: str, user_name: str, now: datetime) -> dict:
    """What the browser's `navigator.credentials.create` is given, as JSON (binary
    values in base64url): a discoverable passkey, any of the three algorithms, no
    attestation, and the plan's existing passkeys so a device does not add one twice."""
    with store.connection() as db:
        challenge = _challenge(db, "register", token, now)
        existing = _ids(db, token)
    return {
        "challenge": challenge,
        "rp": {"id": rp_id, "name": SITE_NAME},
        "user": {"id": _pk.b64url(user_id(token)), "name": user_name, "displayName": user_name},
        "pubKeyCredParams": [{"type": "public-key", "alg": alg} for alg in _pk.ALGORITHMS],
        "timeout": int(CHALLENGE_LIFE.total_seconds() * 1000),
        "attestation": "none",
        "authenticatorSelection": {
            "residentKey": "required",
            "requireResidentKey": True,
            "userVerification": "preferred",
        },
        "excludeCredentials": [{"type": "public-key", "id": i} for i in existing],
    }


def register(
    store: Store,
    token: str,
    answer: Mapping,
    *,
    origin: str,
    rp_id: str,
    device: str,
    now: datetime,
) -> str:
    """Keep the passkey the browser made (`answer`: its `id` and `response`), if it
    checks out; its id."""
    response = answer.get("response")
    if not isinstance(response, Mapping):
        raise PasskeyError("no response")
    client_data = str(response.get("clientDataJSON", ""))
    challenge = _take_challenge(store, client_data, "register", token, now)
    credential = _pk.verify_registration(
        attestation_object=str(response.get("attestationObject", "")),
        client_data=client_data,
        challenge=challenge,
        origin=origin,
        rp_id=rp_id,
    )
    with store.connection() as db:
        if len(_ids(db, token)) >= MAX_PASSKEYS:
            raise PasskeyError(f"at most {MAX_PASSKEYS} passkeys per plan")
        owner = db.execute("SELECT token FROM passkeys WHERE id = ?", (credential.id,)).fetchone()
        if owner is not None and owner[0] != token:
            raise PasskeyError("this passkey belongs to another plan")
        db.execute(
            """
            INSERT INTO passkeys (id, token, public_key, alg, sign_count, device, created)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET public_key = excluded.public_key, alg = excluded.alg,
                sign_count = excluded.sign_count
            """,
            (
                credential.id,
                token,
                credential.public_key,
                credential.alg,
                credential.sign_count,
                device[:40],
                _stamp(now),
            ),
        )
    return credential.id


def signin_options(store: Store, *, rp_id: str, now: datetime) -> dict:
    """What `navigator.credentials.get` is given: no list of passkeys, so that the
    device offers the ones it has for this site, and nobody learns which exist."""
    with store.connection() as db:
        challenge = _challenge(db, "signin", "", now)
    return {
        "challenge": challenge,
        "rpId": rp_id,
        "timeout": int(CHALLENGE_LIFE.total_seconds() * 1000),
        "userVerification": "preferred",
        "allowCredentials": [],
    }


def sign_in(store: Store, answer: Mapping, *, origin: str, rp_id: str, now: datetime) -> str:
    """The token of the plan a passkey opens, if its signature checks out."""
    response = answer.get("response")
    if not isinstance(response, Mapping):
        raise PasskeyError("no response")
    client_data = str(response.get("clientDataJSON", ""))
    challenge = _take_challenge(store, client_data, "signin", "", now)
    with store.connection() as db:
        row = db.execute(
            "SELECT token, public_key, alg, sign_count FROM passkeys WHERE id = ?",
            (str(answer.get("id", "")),),
        ).fetchone()
    if row is None:
        raise UnknownPasskey("no plan has this passkey")
    token, public_key, alg, count = row
    handle = response.get("userHandle")
    if handle and _pk.unb64url(str(handle)) != user_id(token):
        raise PasskeyError("the passkey names another plan")
    count = _pk.verify_assertion(
        authenticator_data=str(response.get("authenticatorData", "")),
        client_data=client_data,
        signature=str(response.get("signature", "")),
        challenge=challenge,
        origin=origin,
        rp_id=rp_id,
        public_key=public_key,
        alg=alg,
        sign_count=count,
    )
    with store.connection() as db:
        db.execute(
            "UPDATE passkeys SET sign_count = ?, used = ? WHERE id = ?",
            (count, _stamp(now), str(answer.get("id", ""))),
        )
    return token


def passkey_list(store: Store, token: str) -> list[dict]:
    with store.connection() as db:
        rows = db.execute(
            "SELECT id, device, created, used FROM passkeys WHERE token = ? ORDER BY created", (token,)
        ).fetchall()
    return [
        {"id": i, "device": device, "created": created, "used": used} for i, device, created, used in rows
    ]


def remove_passkey(store: Store, token: str, passkey: str) -> bool:
    with store.connection() as db:
        return bool(db.execute("DELETE FROM passkeys WHERE id = ? AND token = ?", (passkey, token)).rowcount)


# --------------------------------------------------------------------------- #
# The recovery code
# --------------------------------------------------------------------------- #


def _normal(code: str) -> str:
    """A code as typed, as stored: upper case, without spaces or dashes, with the
    letters people confuse read as the digits they look like."""
    text = "".join(c for c in code.upper() if c.isalnum())
    return text.translate(str.maketrans("OIL", "011"))


def _digest(code: str) -> str:
    return hashlib.sha256(_normal(code).encode("ascii", "replace")).hexdigest()


def new_recovery_code(store: Store, token: str, now: datetime) -> str:
    """A new code for the plan, replacing the old one; shown once, kept as a digest."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    with store.connection() as db:
        db.execute(
            """
            INSERT INTO recovery_codes (token, digest, created) VALUES (?, ?, ?)
            ON CONFLICT (token) DO UPDATE SET digest = excluded.digest, created = excluded.created
            """,
            (token, _digest(raw), _stamp(now)),
        )
    return "-".join(raw[i : i + 5] for i in range(0, CODE_LENGTH, 5))


def recovery_made(store: Store, token: str) -> str | None:
    """When the plan's recovery code was made, if it has one."""
    with store.connection() as db:
        row = db.execute("SELECT created FROM recovery_codes WHERE token = ?", (token,)).fetchone()
    return row[0] if row else None


def use_recovery_code(store: Store, code: str) -> str | None:
    """The token a recovery code opens, and the code used up; None if no plan has it."""
    if len(_normal(code)) != CODE_LENGTH:
        return None
    with store.connection() as db:
        row = db.execute("SELECT token FROM recovery_codes WHERE digest = ?", (_digest(code),)).fetchone()
        if row is None:
            return None
        db.execute("DELETE FROM recovery_codes WHERE token = ?", (row[0],))
    return str(row[0])
