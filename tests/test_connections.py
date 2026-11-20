"""
Connections, and the Moodle calendar link kept as a secret (DECISIONS.md D175).

Moodle's calendar export address holds the student's `authtoken`: whoever has it reads
their calendar. What must hold: the link is kept sealed (never in clear in the store,
the plan's copy or a page), read in clear only to fetch the deadlines; the page says it
is linked, and where, without showing it; an empty field keeps it and Unlink removes
it; a link kept in clear before is still read, and sealed at its next read; a key lost
says to paste the link again. The Connections page shows every link with its state,
and asks the student's Moodle how it signs in once a month, a public question.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from cps import sealed, service, sources

pytest.importorskip("fastapi")
pytest.importorskip("cryptography")
from fastapi.testclient import TestClient

from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
SEMESTER = ROOT / "examples" / "sample-semester.ics"
NOW = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
TOKEN = "s3cr3tAuthToken0123456789abcdef"
EXPORT = (
    "https://moodle.example.edu/calendar/export_execute.php?userid=42"
    f"&authtoken={TOKEN}&preset_what=all&preset_time=recentupcoming"
)
MOODLE_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Moodle//NONSGML//EN
BEGIN:VEVENT
UID:1@moodle.example.edu
SUMMARY:Problem sheet 3 is due
DTSTART:20261020T215900Z
DTEND:20261020T215900Z
CATEGORIES:Analysis 3
END:VEVENT
END:VCALENDAR
"""
SIGN_IN = {
    "typeoflogin": 3,
    "enablewebservices": 1,
    "enablemobilewebservice": 1,
    "identityproviders": [{"name": "University federation (Shibboleth)"}, {"name": "OpenID Connect"}],
}
ASKED: list[str] = []


def _probe(site: str) -> dict:
    ASKED.append(site)
    return SIGN_IN


@pytest.fixture
def web(tmp_path):
    ASKED.clear()
    config = Config(
        store=tmp_path,
        clock=lambda: NOW,
        background=False,
        sweep_every=None,
        fetch=lambda url: MOODLE_ICS,
        moodle_probe=_probe,
    )
    with TestClient(create_app(config)) as client:
        client.root = tmp_path
        yield client


def _plan(web) -> str:
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=NOW)
    service.save_subscription(web.root, sub)
    return sub.token


def _stored(root: Path) -> str:
    return "".join(
        p.read_text(encoding="utf-8", errors="ignore") for p in Path(root).rglob("*") if p.is_file()
    )


def _link(web, token, **form) -> object:
    """Settings' deadlines part saved, as its page sends it (D103)."""
    return web.post(f"/p/{token}/settings", data={"part": "deadlines", **form}, follow_redirects=False)


def test_a_sealed_secret_opens_only_with_its_key():
    value = sealed.seal(EXPORT)
    assert value.startswith(sealed.PREFIX) and TOKEN not in value
    assert sealed.unseal(value) == EXPORT
    assert sealed.unseal(EXPORT) == EXPORT  # kept in clear before D175: read as it is
    kid, _, rest = value[len(sealed.PREFIX) :].partition(":")
    assert sealed.unseal(f"{sealed.PREFIX}00000000:{rest}") is None  # a key the server does not have
    assert sealed.unseal(f"{sealed.PREFIX}{kid}:{rest[:-4]}abcd") is None  # tampered
    assert sealed.unseal(None) is None and sealed.unseal("") is None


def test_the_store_keeps_one_key_and_the_deployment_can_give_its_own(tmp_path, monkeypatch):
    monkeypatch.delenv("CPS_SEAL_KEY", raising=False)
    service.seal_with(tmp_path)
    first = sealed.seal("x")
    service.seal_with(tmp_path)  # the same store: the same key
    assert sealed.seal("x").split(":")[2] == first.split(":")[2] and sealed.unseal(first) == "x"
    given = "q6Jr9e0e7nZ0y3dKk9x5aJk0hX0m7rC1VtS1p4dJb2I="
    monkeypatch.setenv("CPS_SEAL_KEY", given)
    service.seal_with(tmp_path)
    assert sealed.seal("y").split(":")[2] != first.split(":")[2]
    assert sealed.unseal(first) == "x"  # the store's key is still known to this process


def test_the_link_is_sealed_said_and_never_shown(web):
    token = _plan(web)
    answer = _link(web, token, deadlines_url=EXPORT)
    assert answer.status_code == 303, answer.text[:300]
    sub = service.load_subscription(web.root, token)
    assert (
        sealed.is_sealed(sub.options["deadlines_url"])
        and sub.options["deadlines_site"] == "https://moodle.example.edu"
    )
    assert any(t["name"].startswith("Problem sheet 3") for t in sub.options["tasks"])
    assert TOKEN not in _stored(web.root)  # nowhere in the store in clear
    page = unescape(web.get(f"/p/{token}/settings/exams").text)
    assert "Linked to moodle.example.edu" in page and TOKEN not in page
    assert TOKEN not in json.dumps(service.export_data(web.root, token, NOW))  # nor in the plan's copy
    # The form sent again with its field empty keeps the link; Unlink removes it.
    assert _link(web, token, deadlines_url="").status_code == 303
    assert service.load_subscription(web.root, token).options["deadlines_url"]
    assert _link(web, token, deadlines_url="", deadlines_remove="1").status_code == 303
    assert not service.load_subscription(web.root, token).options["deadlines_url"]


def test_a_link_kept_in_clear_is_read_then_sealed_and_a_lost_key_says_so(tmp_path):
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=NOW)
    old = service.replace(sub, options={**sub.options, "deadlines_url": EXPORT})
    read = service.sync_deadlines(old, now=NOW, fetch=lambda url: MOODLE_ICS)
    assert (
        sealed.is_sealed(read.options["deadlines_url"])
        and read.options["deadlines_host"] == "moodle.example.edu"
    )
    assert service.moodle_site(read) == "https://moodle.example.edu"
    lost = service.replace(read, options={**read.options, "deadlines_url": f"{sealed.PREFIX}00000000:xyz"})
    again = service.sync_deadlines(lost, now=NOW, fetch=lambda url: MOODLE_ICS)
    assert again.options["deadlines_error"] == "the link was lost on the server: paste it again"


def test_connections_show_each_link_and_ask_moodle_how_it_signs_in_once(web):
    token = _plan(web)
    page = unescape(web.get(f"/p/{token}/connections").text)
    assert "Not linked yet" in page and ASKED == []  # no Moodle known: nothing asked
    _link(web, token, deadlines_url=EXPORT)
    page = unescape(web.get(f"/p/{token}/connections").text)
    assert ASKED == ["https://moodle.example.edu"]
    assert "signs in through University federation (Shibboleth), OpenID Connect" in page
    assert "Linked to moodle.example.edu" in page and "1 deadline brought" in page and TOKEN not in page
    assert "0 of 6 linked to their page" in page and "From a file" in page
    web.get(f"/p/{token}/connections")
    assert len(ASKED) == 1  # kept for a month
    view = service.connections_view(web.root, service.load_subscription(web.root, token), NOW)
    assert view["attention"] == 0 and view["moodle"]["access"]["kind"] == "sso"


def test_a_moodle_that_does_not_answer_is_asked_again_a_day_later():
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=NOW)
    sub = service.replace(sub, options={**sub.options, "moodle_site": "https://lms.example.org"})

    def silent(site):
        raise sources.SourceError("the link could not be read (timed out)")

    found = service.ask_moodle_access("https://lms.example.org", silent, NOW)
    sub = service.keep_moodle_access(sub, found)
    assert found["ok"] is False
    assert service.moodle_access_due(sub, NOW + timedelta(hours=2)) is None
    assert service.moodle_access_due(sub, NOW + timedelta(days=2)) == "https://lms.example.org"
    form = {"typeoflogin": 1, "enablewebservices": 1, "enablemobilewebservice": 1}
    own = service.keep_moodle_access(
        sub, service.ask_moodle_access("https://lms.example.org", lambda s: form, NOW)
    )
    assert service.moodle_access_due(own, NOW + timedelta(days=29)) is None
    assert service.moodle_access_due(own, NOW + timedelta(days=31)) == "https://lms.example.org"


class _Moodle(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        asked = json.loads(self.rfile.read(length))
        if self.path.startswith("/lib/ajax/service-nologin.php") and asked[0]["methodname"] == (
            "tool_mobile_get_public_config"
        ):
            body = json.dumps([{"error": False, "data": SIGN_IN}]).encode()
        else:
            body = json.dumps([{"error": True, "exception": {"message": "no"}}]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_moodles_public_answer_is_read_without_any_sign_in():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Moodle)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        site = f"http://127.0.0.1:{server.server_address[1]}"
        assert sources.moodle_public_config(site, allow_private=True)["typeoflogin"] == 3
        with pytest.raises(sources.SourceError):
            sources.moodle_public_config(site)  # a private address is refused, as a calendar's is
    finally:
        server.shutdown()
