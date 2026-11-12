"""
Where the app opens (DECISIONS.md D124, which replaces D102). What must hold: every
student opens on Today, the plan (the day's panel beside the calendar); every way in
goes through the same door (the home page signed in, signing in, the installed app, the
demo, the logo); Today is the first tab and the feed one tap away.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

SEMESTER = Path(__file__).resolve().parent.parent / "examples" / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


@pytest.fixture
def web(tmp_path):
    config = Config(
        store=tmp_path, clock=lambda: MORNING, background=False, sweep_every=None, remind_every=None
    )
    with TestClient(create_app(config)) as client:
        client.root = tmp_path
        yield client


def _start(client) -> str:
    files = {"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")}
    answer = client.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    return answer.headers["location"].split("/")[2]


def _join(client, token: str, handle: str = "ada") -> None:
    form = {"handle": handle, "university": "Université Lyon 1", "old_enough": "1"}
    service.save_profile(client.root, token, form)


def test_everyone_opens_on_today_with_or_without_a_handle(web):
    token = _start(web)
    door = web.get(f"/p/{token}/open", follow_redirects=False)
    assert door.status_code == 303 and door.headers["location"] == f"/p/{token}"
    _join(web, token)
    door = web.get(f"/p/{token}/open", follow_redirects=False)
    assert door.headers["location"] == f"/p/{token}"  # D124: a handle no longer opens the feed
    shown = web.get(f"/p/{token}/open?saved=demo", follow_redirects=False)
    assert shown.headers["location"] == f"/p/{token}?saved=demo"  # a notice goes along
    assert web.get("/p/" + "x" * 32 + "/open").status_code == 404
    plan = service.load_subscription(web.root, token)
    assert service.front_door(web.root, plan) == "today"
    assert service.front_door(web.root, replace(plan, plan=None)) == "today"


def test_every_way_in_goes_through_the_door_and_the_feed_is_one_tap_away(web):
    token = _start(web)
    _join(web, token)
    today = web.get(f"/p/{token}/open").text
    assert 'id="panel"' in today and 'class="feed"' not in today
    tabs = re.search(r'<nav class="tabbar".*?</nav>', today, re.S)[0]
    links = re.findall(r'<a href="(/p/[^"]+)"', tabs)
    assert links[0] == f"/p/{token}" and f"/p/{token}/community" in links  # Today first, the feed a tab
    assert f'<a class="brand" href="/p/{token}/open">' in today  # the logo is the door too
    app = web.get(f"/p/{token}/manifest.webmanifest").json()
    assert app["start_url"] == f"/p/{token}/open" and app["id"] == f"/p/{token}"
    sw = (Path(__file__).resolve().parent.parent / "src/cps/web/static/sw.js").read_text()
    assert "/open$/" in sw and "/community`" not in sw  # offline, the installed app opens on the kept Today


def test_the_feeds_first_line_does_not_ask_a_member_to_choose_a_handle(web):
    # AUDIT 82: Community's first-visit line asked every student to choose a handle;
    # a member reads what the feed is to them instead, once.
    member, newcomer = _start(web), _start(web)
    _join(web, member)
    first = unescape(web.get(f"/p/{member}/community").text)
    assert "What your classmates studied, explained and shared." in first
    assert "Choose a handle to join your classmates" not in first
    assert "What your classmates studied" not in unescape(web.get(f"/p/{member}/community").text)  # once
    assert "Choose a handle to join your classmates" in unescape(web.get(f"/p/{newcomer}/community").text)
