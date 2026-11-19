"""
D161: Settings as a dashboard (the owner's choice of three, 2026-11-19: "A for the
settings"). What must hold: each of the six parts shows what it is set to, read from the
same values its own page shows, and follows a change; each card's head is still the link to
its part's page, with the old anchors (D103).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app
from cps.web import app as web_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 7, 0, tzinfo=UTC)


def _plan(root: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", date(2027, 1, 26), familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[service.TaskSpec("Statistics report", "2026-10-20 18:00", 6, "Advanced Statistics")],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(root, service.finish_first_run(sub))
    return service.load_subscription(root, sub.token)


def _values(overview: dict, part: str) -> dict[str, str]:
    return {row["label"]: row["value"] for row in overview[part]}


def test_each_part_says_what_it_is_set_to(tmp_path):
    sub = _plan(tmp_path)
    overview = service.settings_overview(tmp_path, sub, now=NOW)
    assert list(overview) == list(web_app.SETTINGS_PARTS)
    window = service.setup_view(sub)["study_window"]
    week = _values(overview, "week")
    assert week["Study hours"] == f"{int(window[0])}:00–{int(window[1])}:00"
    assert set(week) == {"Study hours", "At most", "Days off", "Busy times"}
    exams = _values(overview, "exams")
    assert exams["Algebra 3"] == "26 January" and exams["Deadlines"] == "1 to do"
    assert exams["Timetable"] == "from a file"
    assert _values(overview, "account") == {"Passkeys": "None", "Recovery code": "not yet"}
    assert _values(overview, "phone") == {"Reminders": "off", "Before a session": "10 min"}
    look = _values(overview, "look")
    assert look == {
        "Language": "English",
        "Appearance": "Automatic",
        "Themes": "Sky · Night sky",
        "Accent": "Automatic",
    }
    # A change shows on its card.
    changed = service.set_palette(service.set_theme(sub, "dark"), "accent", "teal")
    changed = service.set_palette(changed, "dark", "graphite")
    look = _values(service.settings_overview(tmp_path, changed, now=NOW), "look")
    assert look["Appearance"] == "Dark" and look["Themes"] == "Sky · Graphite" and look["Accent"] == "Teal"
    signed_in = service.settings_overview(
        tmp_path, sub, login={"email": "student@example.org", "confirmed": False}, now=NOW
    )
    assert _values(signed_in, "account")["E-mail"] == "student@example.org"
    assert _values(signed_in, "account")["Confirmed"] == "not yet"


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def test_the_page_is_six_cards_each_with_its_values_and_its_link(web, tmp_path):
    sub = _plan(tmp_path)
    page = unescape(web.get(f"/p/{sub.token}/settings").text)
    cards = re.findall(r'<section class="card set-card" aria-labelledby="set-([a-z]+)">', page)
    assert cards == list(web_app.SETTINGS_PARTS)
    assert page.count('<dl class="set-values">') == 6
    assert "<dt>Study hours</dt>" in page and "<dd>26 January</dd>" in page
    links = re.findall(r'<a href="/p/[^/]+/settings/([a-z]+)" data-anchors="[^"]*">', page)
    assert links == list(web_app.SETTINGS_PARTS)
    web.post(f"/p/{sub.token}/language", data={"lang": "it"})
    italian = unescape(web.get(f"/p/{sub.token}/settings").text)
    assert "<dt>Ore di studio</dt>" in italian and "Modifica" in italian
