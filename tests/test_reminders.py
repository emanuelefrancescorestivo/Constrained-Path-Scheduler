"""
Reminders on the phone (DECISIONS.md D38): which push services are used, the
server's key, the devices, when a session is reminded (once), what the reminder
says, and that what reaches the push service can be read by the device alone.

No push service is called: `send` posts through a fake HTTP session that keeps the
request, and the test decrypts it with the device's private key, as the phone would.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import pytest

pytest.importorskip("pywebpush")

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from cps import reminders, service
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)  # a Monday, 09:00 in Paris
APPLE = "https://web.push.apple.com/QGuQyavXutnMCQBdj7NBqcv6"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Phone:
    """A browser's push subscription: its own key pair and secret, as
    `PushSubscription.toJSON()` gives them."""

    def __init__(self, endpoint: str = APPLE) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789abcdef"
        self.endpoint = endpoint

    def info(self) -> dict:
        point = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return {"endpoint": self.endpoint, "keys": {"p256dh": b64(point), "auth": b64(self.auth)}}

    def read(self, body: bytes) -> dict:
        import http_ece

        return json.loads(
            http_ece.decrypt(body, private_key=self.key, auth_secret=self.auth, version="aes128gcm")
        )


class PushService:
    """An HTTP session in place of `requests`: keeps each post, answers `status`."""

    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.posts: list[dict] = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append({"url": url, "data": data, "headers": dict(headers or {}), "timeout": timeout})

        class Answer:
            status_code = self.status
            reason = "fake"
            text = ""
            headers: ClassVar[dict] = {}

        return Answer()


def _plan(where, now: datetime = NOW, lang: str = "en") -> service.Subscription:
    sub = service.start_subscription(
        tz="Europe/Paris", ics=(EXAMPLES / "sample-semester.ics").read_bytes(), now=now, lang=lang
    )
    service.save_subscription(where, sub)
    return sub


def _first(sub: service.Subscription) -> service.SessionView:
    return service.PlanReport.from_dict(sub.plan).sessions[0]


# --------------------------------------------------------------------------- #
# Push services, keys, devices
# --------------------------------------------------------------------------- #


def test_only_known_push_services_are_used():
    for good in (
        "https://fcm.googleapis.com/fcm/send/abc",
        "https://updates.push.services.mozilla.com/wpush/v2/abc",
        "https://web.push.apple.com/abc",
        "https://wns2-par02p.notify.windows.com/w/?token=abc",
        "https://jmt17.google.com/fcm/send/abc",  # as Chromium gave it (AUDIT item 55)
    ):
        assert reminders.endpoint_allowed(good), good
    for bad in (
        "http://fcm.googleapis.com/fcm/send/abc",  # not https
        "https://fcm.googleapis.com.evil.example/abc",
        "https://evilnotify.windows.com/abc",
        "https://mail.google.com/mail/u/0/",  # a google.com host, not its push path
        "https://google.com.evil.example/fcm/send/abc",
        "https://user:pw@fcm.googleapis.com/abc",
        "https://fcm.googleapis.com:8443/abc",
        "https://127.0.0.1/abc",
        "https://localhost/abc",
        "file:///etc/passwd",
        "https://[::1/abc",
    ):
        assert not reminders.endpoint_allowed(bad), bad


def test_the_server_key_is_made_once_and_kept(tmp_path, monkeypatch):
    monkeypatch.delenv("CPS_VAPID_PRIVATE_KEY", raising=False)
    store = Store(tmp_path)
    first = reminders.public_key(reminders.vapid_key(store))
    assert first == reminders.public_key(reminders.vapid_key(Store(tmp_path)))
    assert len(unb64(first)) == 65 and unb64(first)[0] == 4
    given = ec.generate_private_key(ec.SECP256R1())
    raw = b64(given.private_numbers().private_value.to_bytes(32, "big"))
    pem = given.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    for text in (raw, pem):
        monkeypatch.setenv("CPS_VAPID_PRIVATE_KEY", text)
        assert reminders.public_key(reminders.vapid_key(store)) == reminders.public_key(given)


def test_a_device_is_kept_checked_and_forgotten_with_its_plan(tmp_path):
    sub = _plan(tmp_path)
    phone = Phone()
    ident = service.add_push_device(tmp_path, sub.token, phone.info(), user_agent="iPhone", now=NOW)
    view = service.reminders_view(tmp_path, sub)
    assert [d["device"] for d in view["devices"]] == ["iPhone"] and view["devices"][0]["id"] == ident
    assert view["lead"] == 10 and view["key"] == reminders.public_key(reminders.vapid_key(Store(tmp_path)))
    for spoiled in (
        {**phone.info(), "endpoint": "https://intranet.example/hook"},
        {**phone.info(), "keys": {"p256dh": "AAAA", "auth": phone.info()["keys"]["auth"]}},
        {**phone.info(), "keys": {"p256dh": phone.info()["keys"]["p256dh"], "auth": "c2hvcnQ"}},
        {"endpoint": APPLE},
    ):
        with pytest.raises(service.InvalidInput, match="cannot be used"):
            service.add_push_device(tmp_path, sub.token, spoiled, now=NOW)
    assert service.remove_push_device(tmp_path, sub.token, ident)
    service.add_push_device(tmp_path, sub.token, phone.info(), now=NOW)
    service.delete_subscription(tmp_path, sub.token)
    assert reminders.tokens_with_devices(Store(tmp_path)) == []


def test_the_lead_is_one_of_the_offered_times():
    sub = service.start_subscription(tz="Europe/Paris", draw=True, now=NOW)
    assert service.reminder_lead(sub) == 10
    assert service.reminder_lead(service.set_reminder_lead(sub, 0)) == 0
    assert service.reminder_lead(service.set_reminder_lead(sub, 30)) == 30
    with pytest.raises(service.InvalidInput):
        service.set_reminder_lead(sub, 7)


# --------------------------------------------------------------------------- #
# When, once, and what
# --------------------------------------------------------------------------- #


def test_a_session_is_reminded_in_its_window_only():
    start = NOW + timedelta(minutes=30)
    assert not reminders.due(start, NOW, 10)
    assert reminders.due(start, start - timedelta(minutes=10), 10)
    assert reminders.due(start, start - timedelta(seconds=1), 10)
    assert not reminders.due(start, start, 10)  # it has started: no late reminder
    assert not reminders.due(start, start - timedelta(minutes=5), 0)  # off


def test_each_session_is_reminded_once_to_every_device(tmp_path):
    sub = _plan(tmp_path)
    first = _first(sub)
    start = datetime.fromisoformat(first.start)
    phones = [Phone(), Phone("https://fcm.googleapis.com/fcm/send/xyz")]
    for phone in phones:
        service.add_push_device(tmp_path, sub.token, phone.info(), now=NOW)
    sent: list[tuple[str, dict, int]] = []

    def send(device, payload, key, *, subject, ttl):
        sent.append((device["endpoint"], payload, ttl))
        return 201

    early = start - timedelta(minutes=30)
    assert service.send_due_reminders(tmp_path, early, send=send) == 0
    at = start - timedelta(minutes=8)
    assert service.send_due_reminders(tmp_path, at, send=send) == 2
    assert service.send_due_reminders(tmp_path, at + timedelta(minutes=1), send=send) == 0  # once
    assert {e for e, _, _ in sent} == {p.endpoint for p in phones}
    payload, ttl = sent[0][1], sent[0][2]
    assert payload["url"] == f"/p/{sub.token}" and payload["tag"] == service.session_id(first)
    local = start.astimezone(service._zone("Europe/Paris"))
    assert payload["title"] == f"{first.subject} at {local:%H:%M}"
    assert payload["body"].startswith(service.translate(service.KIND_LABELS[first.kind]) + " · ")
    assert ttl == 8 * 60  # useless once the session has started


def test_a_device_its_service_forgot_is_forgotten_and_off_means_off(tmp_path):
    sub = _plan(tmp_path)
    service.add_push_device(tmp_path, sub.token, Phone().info(), now=NOW)
    at = datetime.fromisoformat(_first(sub).start) - timedelta(minutes=5)
    assert service.send_due_reminders(tmp_path, at, send=lambda *a, **k: 410) == 0
    assert service.reminders_view(tmp_path, sub)["devices"] == []
    service.add_push_device(tmp_path, sub.token, Phone().info(), now=NOW)
    service.update_subscription(tmp_path, sub.token, lambda s: service.set_reminder_lead(s, 0))
    calls: list = []
    later = datetime.fromisoformat(service.PlanReport.from_dict(sub.plan).sessions[1].start) - timedelta(
        minutes=5
    )
    assert service.send_due_reminders(tmp_path, later, send=lambda *a, **k: calls.append(a) or 201) == 0
    assert calls == []


def test_a_reminder_speaks_the_plans_language(tmp_path):
    sub = _plan(tmp_path, lang="it")
    service.add_push_device(tmp_path, sub.token, Phone().info(), now=NOW)
    got: list[dict] = []
    at = datetime.fromisoformat(_first(sub).start) - timedelta(minutes=5)
    service.send_due_reminders(tmp_path, at, send=lambda d, p, k, **_: got.append(p) or 201)
    assert " alle " in got[0]["title"]
    test: list[dict] = []
    assert service.send_test_reminder(tmp_path, sub, send=lambda d, p, k, **_: test.append(p) or 201) == 1
    assert test[0]["title"] == "I promemoria sono attivi"


# --------------------------------------------------------------------------- #
# What leaves the server
# --------------------------------------------------------------------------- #


def _verify_vapid(header: str, public: str, audience: str, subject: str) -> dict:
    """The VAPID header: a JWT signed with the server's key, for this push service."""
    assert header.startswith("vapid ")
    token = re.search(r"\bt=([^,\s]+)", header).group(1)
    key = re.search(r"\bk=([^,\s]+)", header).group(1)
    assert key == public
    head, claims, signature = token.split(".")
    raw = unb64(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    point = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64(public))
    point.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    body = json.loads(unb64(claims))
    assert body["aud"] == audience and body["sub"] == subject and body["exp"] > time.time()
    return body


def test_only_the_device_can_read_what_the_push_service_carries(tmp_path, monkeypatch):
    monkeypatch.delenv("CPS_VAPID_PRIVATE_KEY", raising=False)
    phone = Phone()
    key = reminders.vapid_key(Store(tmp_path))
    pushes = PushService()
    payload = {"title": "Algebra 3 at 10:30", "body": "Self-test · 90 min", "url": "/p/x", "tag": "abc"}
    status = reminders.send(
        {"endpoint": phone.endpoint, **phone.info()["keys"]},
        payload,
        key,
        subject="mailto:owner@example.org",
        ttl=480,
        session=pushes,
    )
    assert status == 201
    (post,) = pushes.posts
    assert post["url"] == APPLE and post["headers"]["ttl"] == "480" and post["headers"]["urgency"] == "high"
    assert post["headers"]["content-encoding"] == "aes128gcm"
    assert b"Algebra" not in post["data"]  # encrypted
    assert phone.read(post["data"]) == payload  # and the device alone reads it
    _verify_vapid(
        post["headers"]["authorization"],
        reminders.public_key(key),
        "https://web.push.apple.com",
        "mailto:owner@example.org",
    )
    assert (
        reminders.send(
            {"endpoint": phone.endpoint, **phone.info()["keys"]},
            payload,
            key,
            subject="mailto:o@example.org",
            ttl=60,
            session=PushService(410),
        )
        == 410
    )


# --------------------------------------------------------------------------- #
# The pages
# --------------------------------------------------------------------------- #

MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/605.1.15 Safari/605.1.15"


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    pushed: list[dict] = []

    def send(device, payload, key, *, subject, ttl):
        pushed.append({"device": device, "payload": payload, "subject": subject})
        return 201

    config = Config(
        store=tmp_path,
        clock=lambda: NOW,
        background=False,
        sweep_every=None,
        remind_every=None,
        push_send=send,
        contact="owner@example.org",
    )
    with TestClient(create_app(config), headers={"user-agent": MAC}) as client:
        client.store_path = tmp_path
        client.pushed = pushed
        yield client


@pytest.fixture
def token(web) -> str:
    files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
    answer = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    return answer.headers["location"].split("/")[2]


def test_reminders_are_turned_on_tested_timed_and_off_in_settings(web, token):
    page = web.get(f"/p/{token}/settings/phone").text  # its own page (D103)
    assert 'id="reminders"' in page and "/static/push.js" in page and "data-key=" in page
    assert "Send a test reminder" not in page  # no device yet
    refused = web.post(f"/p/{token}/push", json={**Phone().info(), "endpoint": "https://evil.example/x"})
    assert refused.status_code == 400
    answer = web.post(f"/p/{token}/push", json=Phone().info())
    assert answer.json() == {"go": f"/p/{token}/settings/phone?saved=reminders"}
    page = web.get(f"/p/{token}/settings/phone?saved=reminders").text
    assert (
        "<b>Mac</b>" in page
        and "Reminders are on for this device." in page
        and "Send a test reminder" in page
    )
    tested = web.post(f"/p/{token}/push/test", follow_redirects=False)
    assert tested.headers["location"] == f"/p/{token}/settings/phone?saved=test_sent"
    assert web.pushed[0]["payload"]["title"] == "Reminders are on"
    assert web.pushed[0]["subject"] == "mailto:owner@example.org"
    assert web.post(f"/p/{token}/reminders", data={"lead": "30"}, follow_redirects=False).status_code == 303
    assert service.reminder_lead(service.load_subscription(web.store_path, token)) == 30
    (device,) = service.reminders_view(web.store_path, service.load_subscription(web.store_path, token))[
        "devices"
    ]
    web.post(f"/p/{token}/push/remove", data={"device": device["id"]})
    assert (
        service.reminders_view(web.store_path, service.load_subscription(web.store_path, token))["devices"]
        == []
    )
    none = web.post(f"/p/{token}/push/test", follow_redirects=False)
    assert none.headers["location"] == f"/p/{token}/settings/phone?saved=test_none"


def test_a_device_turns_itself_off_by_its_address(web, token):
    phone = Phone()
    web.post(f"/p/{token}/push", json=phone.info())
    gone = web.post(f"/p/{token}/push/remove", json={"endpoint": phone.endpoint})
    assert gone.json()["go"].endswith("/settings/phone?saved=reminders_off")
    assert reminders.tokens_with_devices(Store(web.store_path)) == []


def test_the_running_app_sends_the_reminders_due(tmp_path):
    """The web app sends what is due every `remind_every`, in its own thread."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    sub = _plan(tmp_path)
    service.add_push_device(tmp_path, sub.token, Phone().info(), now=NOW)
    at = datetime.fromisoformat(_first(sub).start) - timedelta(minutes=4)
    pushed: list[dict] = []
    config = Config(
        store=tmp_path,
        clock=lambda: at,
        background=False,
        sweep_every=None,
        remind_every=timedelta(seconds=0.05),
        push_send=lambda d, p, k, **_: pushed.append(p) or 201,
    )
    with TestClient(create_app(config)):
        deadline = time.monotonic() + 5
        while not pushed and time.monotonic() < deadline:
            time.sleep(0.02)
    assert len(pushed) == 1 and pushed[0]["tag"] == service.session_id(_first(sub))


def test_the_privacy_page_names_the_push_services(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    with TestClient(create_app(Config(store=tmp_path, sweep_every=None, remind_every=None))) as client:
        page = client.get("/privacy").text
    assert "push service" in page and "encrypted for your device" in page


def test_payload_text_for_a_task_names_the_task(tmp_path):
    sub = _plan(tmp_path)
    task = replace(_first(sub), kind="task", topic="Stats report")
    payload = service._reminder_payload(sub, task)
    assert payload["title"].startswith("Stats report at ")
