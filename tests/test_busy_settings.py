"""
Busy times in Settings as in setting up (DECISIONS.md D173), the owner: "do the busy times in
Settings too" after D172. What must hold: each weekly busy time is one card with its days as
boxes and its hours, the days of the same name, kind and hours together; a day can be added or
taken off a card, a card removed; a night shift is kept and marked; one that ends when it starts
is refused; new ones are added with their shifts, another job named on its own; a day on a date
stays a row of its own.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest

from cps import service

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
SEMESTER = ROOT / "examples" / "sample-semester.ics"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
ROWS = [
    {"label": "Bar", "kind": "work", "weekday": "Wed", "start": "18:00", "end": "22:00"},
    {"label": "Bar", "kind": "work", "weekday": "Mon", "start": "18:00", "end": "22:00"},
    {"label": "Football", "kind": "training", "weekday": "Thu", "start": "19:00", "end": "20:30"},
    {"label": "Dentist", "kind": "other", "date": "2026-10-14", "start": "14:00", "end": "15:00"},
]


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        client.root = tmp_path
        yield client


def _plan(web) -> str:
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=NOW)
    sub = service.revise_subscription(sub, busy_rows=ROWS, now=NOW)
    service.save_subscription(web.root, sub)
    return sub.token


def _form(html: str) -> dict:
    """The week's form as a browser sends it untouched: ticked boxes only, every value."""
    form: dict = {}
    for tag in re.findall(r"<input[^>]*>", html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        if 'type="checkbox"' in tag or 'type="radio"' in tag:
            if " checked" in tag:
                if 'type="checkbox"' in tag:
                    form.setdefault(name.group(1), []).append(unescape(value.group(1)))
                else:
                    form[name.group(1)] = unescape(value.group(1))
            continue
        form[name.group(1)] = unescape(value.group(1)) if value else ""
    for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        chosen = re.search(r'<option(?: value="([^"]*)")? selected>([^<]*)', body)
        form[name] = (chosen.group(2) if chosen.group(1) is None else chosen.group(1)) if chosen else ""
    return form


def _rows(web, token) -> set[tuple]:
    sub = service.load_subscription(web.root, token)
    return {
        (r["label"], r["kind"], r.get("weekday"), r.get("date"), r["start"], r["end"]) for r in sub.busy_rows
    }


def _save(web, token, **changes):
    page = web.get(f"/p/{token}/settings/week").text
    form = {**_form(page), **changes}
    return web.post(f"/p/{token}/settings", data=form, follow_redirects=False)


def test_the_weekly_busy_times_are_grouped_by_name_kind_and_hours():
    split = service.busy_groups(
        service.setup_view(
            service.revise_subscription(
                service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=NOW),
                busy_rows=ROWS,
                now=NOW,
            )
        )["activities"]
    )
    assert [(g["label"], g["days"], g["overnight"]) for g in split["weekly"]] == [
        ("Bar", ["Mon", "Wed"], False),
        ("Football", ["Thu"], False),
    ]
    assert [a["label"] for a in split["dated"]] == ["Dentist"]
    night = service.busy_groups(
        [{"label": "Bar", "kind": "work", "weekday": "Fri", "start": "22:00", "end": "06:00"}]
    )
    assert night["weekly"][0]["overnight"]


def test_settings_show_a_card_per_busy_time_and_save_its_days(web):
    token = _plan(web)
    page = unescape(web.get(f"/p/{token}/settings/week").text)
    assert page.count('<details class="busy-card">') == 2 and page.count('class="busy-card busy-dated"') == 1
    # Each one a row that says it, opened to change it.
    assert '<summary><b>Bar</b><span class="muted">Mon, Wed · 18:00–22:00</span></summary>' in page
    assert re.findall(r'name="b0_days" value="(\w+)" checked', page) == ["Mon", "Wed"]
    assert 'name="b0_start" type="time" step="900" value="18:00" data-clock' in page
    assert "Type them instead" not in page
    # Thursday added to the bar, Monday taken off; football removed; nothing else changes.
    answer = _save(web, token, b0_days=["Wed", "Thu"], b1_remove="1")
    assert answer.status_code == 303, re.findall(r'role="alert">([^<]*)', answer.text)
    assert _rows(web, token) == {
        ("Bar", "work", 2, None, "18:00", "22:00"),
        ("Bar", "work", 3, None, "18:00", "22:00"),
        ("Dentist", "other", None, "2026-10-14", "14:00", "15:00"),
    }


def test_a_night_shift_is_kept_and_one_that_ends_as_it_starts_is_refused(web):
    token = _plan(web)
    assert _save(web, token, b0_start="22:00", b0_end="06:00").status_code == 303
    assert ("Bar", "work", 0, None, "22:00", "06:00") in _rows(web, token)
    page = unescape(web.get(f"/p/{token}/settings/week").text)
    assert '<span class="muted">Mon, Wed · 22:00–06:00 (the next day)</span>' in page
    refused = _save(web, token, b1_start="19:00", b1_end="19:00")
    assert refused.status_code == 400 and "Football: it ends when it starts" in unescape(refused.text)
    assert ("Football", "training", 3, None, "19:00", "20:30") in _rows(web, token)


def test_new_busy_times_are_added_with_their_shifts_and_a_day_on_a_date(web):
    token = _plan(web)
    page = web.get(f"/p/{token}/settings/week").text
    assert page.count('<details class="more-shift">') == len(service.QUICK_BUSY) * (service.QUICK_SHIFTS - 1)
    answer = _save(
        web,
        token,
        q1_label="Library",
        q1_days=["Sat"],
        q1_start="10:00",
        q1_end="16:00",
        q1_1_label="Shop",
        q1_1_days=["Sun"],
        q1_1_start="09:00",
        q1_1_end="13:00",
        a1_label="Concert",
        a1_date="2026-10-20",
        a1_start="20:00",
        a1_end="23:00",
    )
    assert answer.status_code == 303, re.findall(r'role="alert">([^<]*)', answer.text)
    rows = _rows(web, token)
    assert ("Library", "work", 5, None, "10:00", "16:00") in rows
    assert ("Shop", "work", 6, None, "09:00", "13:00") in rows
    assert ("Concert", "other", None, "2026-10-20", "20:00", "23:00") in rows
    assert len(rows) == 4 + 3  # the four kept, three added
