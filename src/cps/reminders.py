"""
Reminders on the phone (DECISIONS.md D38): a notification a few minutes before each
study session, on the devices where the student turned them on.

This is Web Push. A browser that allows notifications gives the page an address at
its push service (Google's for Chrome and Android, Apple's for Safari and installed
apps on an iPhone, Mozilla's for Firefox, Microsoft's for Edge) and two keys. The
server keeps them; at the right time it encrypts a short message for that device and
posts it to the address, signed with the server's own key (VAPID), and the push
service delivers it, without being able to read it. The encryption and the signature
are `pywebpush`'s (the `web` extra); this module keeps the keys and the devices,
refuses addresses that are not a known push service, and makes sure no session is
reminded twice.

Rejected: e-mail (no address is kept, D35), text messages (a paid gateway), a native
app (an app store account and review; later). Reminders need a server that is awake
at the time: on a host that puts the service to sleep when nobody visits (Render's
free plan), they arrive only while it is awake.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from .passkeys import b64url, unb64url
from .store import Store

LEADS = (5, 10, 15, 30, 60)  # minutes before a session a reminder may come; 0 is off
DEFAULT_LEAD = 10
MAX_DEVICES = 10  # per plan
SENT_KEPT = timedelta(days=3)
# The push services browsers use, as (host, or ".suffix" for any host under it; and
# the path an address must start with). The server posts to the address a browser
# gives it, so any other is refused: nobody can make the server call an address of
# their choosing (as `sources` refuses for calendar links). Chrome's addresses are
# under fcm.googleapis.com or, as Chromium gave in the browser run, a google.com
# host such as jmt17.google.com, always under /fcm/ (AUDIT item 55). A browser on
# another push service is told reminders are not available for it.
PUSH_HOSTS = (
    ("fcm.googleapis.com", "/"),
    ("android.googleapis.com", "/"),
    (".google.com", "/fcm/"),
    ("updates.push.services.mozilla.com", "/"),
    ("web.push.apple.com", "/"),
    (".notify.windows.com", "/"),
)


class DeviceRefused(ValueError):
    """A device's push address or keys that this server will not use."""


def _stamp(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds")


def endpoint_allowed(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443) or not host:
        return False
    return any(
        (host == h or (h.startswith(".") and host.endswith(h))) and parts.path.startswith(path)
        for h, path in PUSH_HOSTS
    )


def device_id(endpoint: str) -> str:
    """A short name for a device's push address, for the pages (the address itself
    is long and only the push service's business)."""
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# The server's key pair (VAPID)
# --------------------------------------------------------------------------- #


def _load_key(text: str) -> Any:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    text = text.strip()
    if text.startswith("-----BEGIN"):
        key = serialization.load_pem_private_key(text.encode("ascii"), password=None)
    else:
        raw = unb64url(text)
        if len(raw) == 32:
            key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
        else:
            key = serialization.load_der_private_key(raw, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("a VAPID key is a P-256 private key")
    return key


def vapid_key(store: Store, now: datetime | None = None) -> Any:
    """The private key that signs every push: `CPS_VAPID_PRIVATE_KEY` (PEM, or the 32
    bytes in base64url) when the deployment gives one, else one made on first use and
    kept in the store. Changing it orphans every device: each must turn reminders on
    again."""
    given = os.environ.get("CPS_VAPID_PRIVATE_KEY")
    if given:
        return _load_key(given)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    with store.connection() as db:
        row = db.execute("SELECT value FROM server_keys WHERE name = 'vapid'").fetchone()
        if row is None:
            pem = (
                ec.generate_private_key(ec.SECP256R1())
                .private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
                .decode("ascii")
            )
            # Two threads making one at once: the first kept wins, both read it back.
            db.execute(
                "INSERT OR IGNORE INTO server_keys (name, value, created) VALUES ('vapid', ?, ?)",
                (pem, _stamp(now or datetime.now(UTC))),
            )
            row = db.execute("SELECT value FROM server_keys WHERE name = 'vapid'").fetchone()
    return _load_key(row[0])


def public_key(key: Any) -> str:
    """The key a browser needs to subscribe (`applicationServerKey`): the public
    point, uncompressed, in base64url."""
    from cryptography.hazmat.primitives import serialization

    return b64url(
        key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )


# --------------------------------------------------------------------------- #
# Devices
# --------------------------------------------------------------------------- #


def add_device(store: Store, token: str, info: Mapping, *, device: str, now: datetime) -> str:
    """Keep the push subscription a browser gave (`PushSubscription.toJSON()`); its id."""
    endpoint = str(info.get("endpoint") or "")
    found = info.get("keys")
    keys: Mapping = found if isinstance(found, Mapping) else {}
    p256dh, auth = str(keys.get("p256dh") or ""), str(keys.get("auth") or "")
    if len(endpoint) > 2048 or not endpoint_allowed(endpoint):
        raise DeviceRefused("not a push service this server knows")
    try:
        point, secret = unb64url(p256dh), unb64url(auth)
    except ValueError as error:
        raise DeviceRefused("keys are not base64url") from error
    if len(point) != 65 or point[0] != 4 or len(secret) != 16:
        raise DeviceRefused("not a P-256 key and a 16-byte secret")
    with store.connection() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM push_devices WHERE token = ? AND endpoint != ?", (token, endpoint)
        ).fetchone()[0]
        if count >= MAX_DEVICES:
            raise DeviceRefused(f"at most {MAX_DEVICES} devices per plan")
        # A browser has one push address per site: if it was another plan's (two plans
        # in one browser), it now reminds the plan that asked last.
        db.execute(
            """
            INSERT INTO push_devices (endpoint, token, p256dh, auth, device, created)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (endpoint) DO UPDATE SET token = excluded.token, p256dh = excluded.p256dh,
                auth = excluded.auth, device = excluded.device, created = excluded.created
            """,
            (endpoint, token, p256dh, auth, device[:40], _stamp(now)),
        )
    return device_id(endpoint)


def devices(store: Store, token: str) -> list[dict]:
    with store.connection() as db:
        rows = db.execute(
            "SELECT endpoint, p256dh, auth, device, created FROM push_devices "
            "WHERE token = ? ORDER BY created",
            (token,),
        ).fetchall()
    return [
        {"id": device_id(e), "endpoint": e, "p256dh": p, "auth": a, "device": d, "created": c}
        for e, p, a, d, c in rows
    ]


def remove_device(store: Store, token: str, ident: str) -> bool:
    """Forget a device, named by its id or its push address."""
    for d in devices(store, token):
        if ident in (d["id"], d["endpoint"]):
            with store.connection() as db:
                db.execute(
                    "DELETE FROM push_devices WHERE endpoint = ? AND token = ?", (d["endpoint"], token)
                )
            return True
    return False


def tokens_with_devices(store: Store) -> list[str]:
    with store.connection() as db:
        return [t for (t,) in db.execute("SELECT DISTINCT token FROM push_devices ORDER BY token")]


# --------------------------------------------------------------------------- #
# Once per session
# --------------------------------------------------------------------------- #


def due(start: datetime, now: datetime, lead: int) -> bool:
    """Whether a session starting at `start` is reminded now: from `lead` minutes
    before it until it starts. A server that was asleep through that window does
    not remind late."""
    return bool(lead) and start - timedelta(minutes=lead) <= now < start


def claim(store: Store, token: str, sid: str, now: datetime) -> bool:
    """True the first time a session is claimed for a reminder, False after: a
    reminder is sent at most once, even if two senders run at once."""
    with store.connection() as db:
        return bool(
            db.execute(
                "INSERT OR IGNORE INTO push_sent (token, sid, at) VALUES (?, ?, ?)", (token, sid, _stamp(now))
            ).rowcount
        )


def prune(store: Store, now: datetime) -> None:
    with store.connection() as db:
        db.execute("DELETE FROM push_sent WHERE at < ?", (_stamp(now - SENT_KEPT),))


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #

Sender = Callable[..., int]


def send(device: Mapping, payload: Mapping, key: Any, *, subject: str, ttl: int, session: Any = None) -> int:
    """Encrypt `payload` (JSON) for one device and post it to its push service,
    signed with `key`; the push service's status (201 sent, 404 or 410 gone, 0 when
    it could not be reached). `session` replaces the HTTP client (tests)."""
    import requests
    from py_vapid import Vapid02  # type: ignore[import-untyped]
    from pywebpush import WebPushException, webpush  # type: ignore[import-untyped]

    try:
        response = webpush(
            {"endpoint": device["endpoint"], "keys": {"p256dh": device["p256dh"], "auth": device["auth"]}},
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=Vapid02(key),
            vapid_claims={"sub": subject},
            ttl=max(0, int(ttl)),
            timeout=10,
            headers={"Urgency": "high"},
            requests_session=session,
        )
    except WebPushException as error:
        return int(error.response.status_code) if error.response is not None else 0
    except requests.RequestException:
        return 0
    return int(getattr(response, "status_code", 0))
