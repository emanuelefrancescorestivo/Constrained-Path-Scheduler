"""D196: a web page cannot block other apps; the focus page shows the phone's own way, for the
device it is opened on, and on an iPhone opens the student's shortcut in one tap."""

from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest

from cps import service

SEMESTER = Path(__file__).resolve().parent.parent / "examples" / "sample-semester.ics"
NOW = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"
ANDROID = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36"


@pytest.mark.parametrize(
    "agent, device",
    [
        (IPHONE, "iphone"),
        ("Mozilla/5.0 (iPad; CPU OS 18_1 like Mac OS X)", "iphone"),
        (ANDROID, "android"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7)", "mac"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "windows"),
        ("", "other"),
    ],
)
def test_the_device_is_guessed_from_the_browser(agent, device):
    assert service.device_of(agent) == device


def test_the_steps_come_for_the_device_first_and_the_shortcut_only_on_an_iphone():
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=NOW)
    android = service.lock_view(sub, "android")
    assert [g["device"] for g in android["steps"]] == ["android", "iphone", "mac", "windows"]
    assert all(app in android["steps"][0]["lines"][0] for app in service.LOCK_APPS)
    assert android["link"] == "" and android["shortcut"] == ""
    named = service.set_focus_shortcut(sub, "  Study   time ")
    assert service.lock_view(named, "iphone")["link"] == "shortcuts://run-shortcut?name=Study%20time"
    assert service.lock_view(named, "android")["link"] == ""  # a shortcut is an iPhone's
    assert service.lock_view(service.set_focus_shortcut(named, ""), "iphone")["link"] == ""
    for wrong in ("x" * 41, "Study<script>", "a/b"):
        with pytest.raises(service.InvalidInput):
            service.set_focus_shortcut(sub, wrong)


def test_the_focus_page_shows_the_way_and_the_button_once_set(tmp_path):
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    web = TestClient(
        create_app(Config(store=tmp_path, background=False, sweep_every=None, remind_every=None))
    )
    files = {"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")}
    token = (
        web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        .headers["location"]
        .split("/")[2]
    )
    page = unescape(web.get(f"/p/{token}/focus", headers={"user-agent": ANDROID}).text)
    assert 'id="lock"' in page and "Focus mode" in page and "A website cannot block other apps" in page
    assert "shortcuts://" not in page and "lock-go" not in page
    saved = web.post(
        f"/p/{token}/focus/lock",
        data={"shortcut": "Study"},
        headers={"user-agent": IPHONE},
        follow_redirects=False,
    )
    assert saved.status_code == 303 and saved.headers["location"] == f"/p/{token}/focus#lock"
    page = web.get(f"/p/{token}/focus", headers={"user-agent": IPHONE}).text
    assert 'class="btn btn-tinted btn-block lock-go" href="shortcuts://run-shortcut?name=Study"' in page
    # A computer gets no button: it has no Shortcuts to open.
    laptop = web.get(f"/p/{token}/focus", headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0)"}).text
    assert "lock-go" not in laptop
    assert web.post(f"/p/{token}/focus/lock", data={"shortcut": "<b>"}).status_code == 400
