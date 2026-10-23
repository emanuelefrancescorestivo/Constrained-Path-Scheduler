"""
Accounts: an e-mail and a password (DECISIONS.md D94), and a plan's passkeys and its
recovery code (D35).

Since D94 a student makes an account before the plan: an e-mail address and a
password, kept only as its scrypt digest. The plan made while signed in belongs to
that account and opens only for it, on the devices signed in to it (a session: a
random value in a cookie, kept here as its SHA-256). Links sent by e-mail confirm the
address or choose a new password; each works once. A plan made before D94, or a demo
student, is still its private address, as a calendar's secret link is.

D35 gave two ways back to a plan's address when it is lost (a new phone, a cleared
browser), which stay as ways to sign in:

* **Passkeys.** The device makes a key pair for this site and keeps it behind the
  student's face, fingerprint or screen lock; the site keeps the public half
  (`passkeys.py` checks the signatures). Signing in finds the plan from the passkey
  alone (a discoverable credential), so nothing has to be typed.
* **A recovery code**, for when every device is gone: twenty characters from an
  alphabet without look-alikes (about 100 bits), shown once, kept only as its
  SHA-256 digest. It works once; using it makes a new one, shown at once.

D35 rejected e-mail links and passwords before launch; the owner asked for e-mail
accounts at launch (D94). "Sign in with Google" stays rejected (a third party told of
every sign-in).

Challenges are kept here, not in a cookie: the browser
sends back the client data, which carries the challenge, and the challenge is
found, checked and deleted in one transaction, so it is used once.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
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


# --------------------------------------------------------------------------- #
# E-mail accounts (D94)
# --------------------------------------------------------------------------- #

# OWASP Application Security Verification Standard 4.0.3, V2.1.1 and V2.1.2: at least
# 12 characters once runs of spaces count as one, at least 64 allowed, more than 128
# refused; no rule about which kinds of characters.
PASSWORD_MIN = 12
PASSWORD_MAX = 128
# scrypt as OWASP's Password Storage Cheat Sheet sets it: one of its five settings of
# equal strength, the one that needs 16 MiB a hash rather than 128 (N=2^17, p=1), so a
# small server signs several students in at once.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 5
SCRYPT_MAXMEM = 64 * 1024 * 1024
SESSION_LIFE = timedelta(days=90)  # a device stays signed in this long after its last use
SESSION_TOUCH = timedelta(days=1)  # how often a session's last use is written down
CONFIRM_LIFE = timedelta(days=7)
RESET_LIFE = timedelta(hours=2)
UNFINISHED_LIFE = timedelta(days=30)  # an account that never made a plan
# Wrong passwords in a row before an account waits: at most 40 tries an hour, where
# ASVS V2.2.1 allows 100. Counted per account, so it holds whatever addresses try.
FAILURES = 10
LOCKED_FOR = timedelta(minutes=15)
EMAIL_MAX = 254
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)*\.[^@\s.]{2,}")
# Obvious choices, refused whole (NIST SP 800-63B asks for a blocklist of commonly
# used values, compared with the whole password). Written by hand from the patterns
# common lists show (digit runs, keyboard rows, "password" and its French and Italian
# words with digits), not taken from a measured list; with a password made of two
# characters at most. Not a check against leaked passwords, which needs an outside
# service told of each one (or of part of its hash).
COMMON = frozenset(
    [
        "123456789012",
        "1234567890123",
        "password1234",
        "passwordpassword",
        "qwertyuiopas",
        "azertyuiopqs",
        "motdepasse123",
        "motdepasse1234",
        "iloveyou1234",
        "aaaaaaaaaaaa",
        "111111111111",
        "000000000000",
        "123123123123",
        "abcdefghijkl",
        "abc123abc123",
        "password123!",
        "qwerty123456",
        "azerty123456",
        "passwordazerty",
        "welcome12345",
        "bonjour12345",
        "ciaociao1234",
        "passwordpassw",
        "1q2w3e4r5t6y",
        "soleil123456",
        "juventus1234",
        "napoli123456",
        "football1234",
        "password0000",
    ]
)


class AccountError(Exception):
    """Why an e-mail account could not be made or opened: `reason` is one of
    "email", "short", "long", "common", "same", "taken", "wrong", "locked", "bound"."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Account:
    id: str
    email: str
    token: str | None  # the plan it owns, once made
    created: str
    confirmed: str | None


def normal_email(text: str) -> str | None:
    """An address as kept: trimmed and in lower case, if it has an address's shape
    (something, @, a domain with a dot); None otherwise. Whether it receives mail is
    only known when the confirmation link is opened."""
    email = text.strip().lower()
    if len(email) > EMAIL_MAX or not _EMAIL.fullmatch(email):
        return None
    return email


def password_problem(password: str, email: str = "") -> str | None:
    """What is wrong with a new password, as an `AccountError` reason; None if fine."""
    length = len(re.sub(r" {2,}", " ", password))
    if length < PASSWORD_MIN:
        return "short"
    if len(password) > PASSWORD_MAX:
        return "long"
    whole = password.strip().lower()
    if whole in COMMON or len(set(whole)) <= 2:
        return "common"
    if email and email.lower() in whole:
        return "same"
    return None


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    """The digest kept for a password: scrypt with a random salt, its settings
    written beside it so they can be raised later without locking anyone out."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=SCRYPT_MAXMEM,
        dklen=32,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def password_matches(stored: str, password: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(digest)
        found = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=SCRYPT_MAXMEM,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(found, expected)


@functools.cache
def _nobody() -> str:
    """Checked when an address has no account, so that a wrong address takes as long
    to refuse as a wrong password, and the time does not tell which addresses exist."""
    return hash_password(secrets.token_urlsafe(18))


def _secret_digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii", "replace")).hexdigest()


def _account(row: tuple | None) -> Account | None:
    return Account(*row) if row else None


_COLUMNS = "id, email, token, created, confirmed"


def create_account(store: Store, email: str, password: str, now: datetime) -> Account:
    """A new account; `email` must be normal (`normal_email`) and `password` fine."""
    reason = password_problem(password, email)
    if reason:
        raise AccountError(reason)
    account = Account(secrets.token_urlsafe(12), email, None, _stamp(now), None)
    try:
        with store.connection() as db:
            db.execute(
                "INSERT INTO accounts (id, email, password, created) VALUES (?, ?, ?, ?)",
                (account.id, email, hash_password(password), account.created),
            )
    except sqlite3.IntegrityError as error:
        raise AccountError("taken") from error
    return account


def check_password(store: Store, email: str, password: str, now: datetime) -> Account:
    """The account an address and a password open; `AccountError("wrong")` for either
    wrong (the same answer, so it does not tell which), "locked" after `FAILURES`
    wrong passwords in a row, for `LOCKED_FOR`."""
    with store.connection() as db:
        row = db.execute(
            f"SELECT {_COLUMNS}, password, failures, failed FROM accounts WHERE email = ?", (email,)
        ).fetchone()
    if row is None:
        password_matches(_nobody(), password)
        raise AccountError("wrong")
    account, stored, failures, failed = _account(row[:5]), row[5], row[6], row[7]
    assert account is not None
    if failures >= FAILURES and failed and datetime.fromisoformat(failed) + LOCKED_FOR > now:
        raise AccountError("locked")
    if not password_matches(stored, password):
        with store.connection() as db:
            db.execute(
                "UPDATE accounts SET failures = CASE WHEN failures >= ? THEN 1 ELSE failures + 1 END, "
                "failed = ? WHERE id = ?",
                (FAILURES, _stamp(now), account.id),
            )
        raise AccountError("wrong")
    if failures:
        with store.connection() as db:
            db.execute("UPDATE accounts SET failures = 0, failed = NULL WHERE id = ?", (account.id,))
    return account


def get_account(store: Store, account_id: str) -> Account | None:
    with store.connection() as db:
        return _account(db.execute(f"SELECT {_COLUMNS} FROM accounts WHERE id = ?", (account_id,)).fetchone())


def account_by_email(store: Store, email: str) -> Account | None:
    with store.connection() as db:
        return _account(db.execute(f"SELECT {_COLUMNS} FROM accounts WHERE email = ?", (email,)).fetchone())


def owner_of(store: Store, token: str) -> str | None:
    """The account a plan belongs to, if it belongs to one."""
    with store.connection() as db:
        row = db.execute("SELECT id FROM accounts WHERE token = ?", (token,)).fetchone()
    return row[0] if row else None


def give_plan(store: Store, account_id: str, token: str) -> None:
    """The plan becomes the account's; an account owns one plan, kept across semesters
    (D36), so a second is refused."""
    with store.connection() as db:
        done = db.execute(
            "UPDATE accounts SET token = ? WHERE id = ? AND token IS NULL", (token, account_id)
        ).rowcount
    if not done:
        raise AccountError("bound")


def set_password(store: Store, account: Account, password: str) -> None:
    reason = password_problem(password, account.email)
    if reason:
        raise AccountError(reason)
    with store.connection() as db:
        db.execute(
            "UPDATE accounts SET password = ?, failures = 0, failed = NULL WHERE id = ?",
            (hash_password(password), account.id),
        )


def confirm(store: Store, account_id: str, now: datetime) -> None:
    with store.connection() as db:
        db.execute(
            "UPDATE accounts SET confirmed = COALESCE(confirmed, ?) WHERE id = ?", (_stamp(now), account_id)
        )


# -- devices signed in ------------------------------------------------------


def open_session(store: Store, account_id: str, now: datetime, device: str = "") -> str:
    """A device signed in: the random value for its cookie (kept here as a digest)."""
    value = secrets.token_urlsafe(32)
    stamp = _stamp(now)
    with store.connection() as db:
        db.execute(
            "INSERT INTO sessions (digest, account, created, seen, device) VALUES (?, ?, ?, ?, ?)",
            (_secret_digest(value), account_id, stamp, stamp, device[:40]),
        )
    return value


def session_account(store: Store, value: str, now: datetime) -> tuple[Account, bool] | None:
    """The account a cookie's value is signed in to, and whether its cookie should be
    sent again (its last use was written down now, at most once a day); None for an
    unknown value or a session unused for `SESSION_LIFE`."""
    if not value or len(value) > 100:
        return None
    digest = _secret_digest(value)
    with store.connection() as db:
        row = db.execute("SELECT account, seen FROM sessions WHERE digest = ?", (digest,)).fetchone()
        if row is None:
            return None
        seen = datetime.fromisoformat(row[1])
        if seen + SESSION_LIFE < now:
            db.execute("DELETE FROM sessions WHERE digest = ?", (digest,))
            return None
        touched = seen + SESSION_TOUCH < now
        if touched:
            db.execute("UPDATE sessions SET seen = ? WHERE digest = ?", (_stamp(now), digest))
        account = _account(db.execute(f"SELECT {_COLUMNS} FROM accounts WHERE id = ?", (row[0],)).fetchone())
    return (account, touched) if account else None


def close_session(store: Store, value: str) -> None:
    with store.connection() as db:
        db.execute("DELETE FROM sessions WHERE digest = ?", (_secret_digest(value),))


def close_sessions(store: Store, account_id: str, keep: str | None = None) -> int:
    """Sign the account out everywhere, but for the session `keep` (this device)."""
    with store.connection() as db:
        return db.execute(
            "DELETE FROM sessions WHERE account = ? AND digest != ?",
            (account_id, _secret_digest(keep) if keep else ""),
        ).rowcount


def sessions_of(store: Store, account_id: str) -> list[dict]:
    with store.connection() as db:
        rows = db.execute(
            "SELECT device, created, seen FROM sessions WHERE account = ? ORDER BY seen DESC", (account_id,)
        ).fetchall()
    return [{"device": d, "created": c, "seen": s} for d, c, s in rows]


# -- links sent by e-mail ---------------------------------------------------

LINK_LIFE = {"confirm": CONFIRM_LIFE, "reset": RESET_LIFE}


def new_link(store: Store, account_id: str, purpose: str, now: datetime) -> str:
    """The random part of a link to send by e-mail; a newer link of the same kind
    replaces the older one."""
    value = secrets.token_urlsafe(24)
    with store.connection() as db:
        db.execute("DELETE FROM mail_links WHERE account = ? AND purpose = ?", (account_id, purpose))
        db.execute(
            "INSERT INTO mail_links (digest, account, purpose, expires) VALUES (?, ?, ?, ?)",
            (_secret_digest(value), account_id, purpose, _stamp(now + LINK_LIFE[purpose])),
        )
    return value


def link_account(store: Store, value: str, purpose: str, now: datetime, *, use: bool) -> Account | None:
    """The account a link from an e-mail is for, if it is still good; `use` spends it."""
    if not value or len(value) > 100:
        return None
    digest = _secret_digest(value)
    with store.connection() as db:
        row = db.execute(
            "SELECT account, purpose, expires FROM mail_links WHERE digest = ?", (digest,)
        ).fetchone()
        if row is None or row[1] != purpose or datetime.fromisoformat(row[2]) < now:
            return None
        if use:
            db.execute("DELETE FROM mail_links WHERE digest = ?", (digest,))
        return _account(db.execute(f"SELECT {_COLUMNS} FROM accounts WHERE id = ?", (row[0],)).fetchone())


def sweep_accounts(store: Store, now: datetime) -> int:
    """Sessions unused for `SESSION_LIFE`, links past their time, and accounts that
    never made a plan, `UNFINISHED_LIFE` after they were made (with what they had)."""
    with store.connection() as db:
        db.execute("DELETE FROM sessions WHERE seen < ?", (_stamp(now - SESSION_LIFE),))
        db.execute("DELETE FROM mail_links WHERE expires < ?", (_stamp(now),))
        stale = [
            a
            for (a,) in db.execute(
                "SELECT id FROM accounts WHERE token IS NULL AND created < ?",
                (_stamp(now - UNFINISHED_LIFE),),
            )
        ]
        for account_id in stale:
            db.execute("DELETE FROM sessions WHERE account = ?", (account_id,))
            db.execute("DELETE FROM mail_links WHERE account = ?", (account_id,))
            db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    return len(stale)
