"""
D154: setting up shows the plan's defaults as one sentence (the owner's choice 1-B,
2026-11-19: "very easy to fill in so students do not have much friction to start with").
What must hold: each value of the sentence is a choice with the plan's own value chosen;
going on untouched keeps the defaults; each choice changes what it says; a busy time added
in a tap is planned around, one with no day is none, and one without its hours says so;
the trip is a choice; Moodle's link is found by Moodle's own words, in the student's
language, and shows what it brings before anything is saved.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
SEMESTER = ROOT / "examples" / "sample-semester.ics"
MOODLE = (ROOT / "examples" / "sample-moodle.ics").read_bytes()
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
LINK = "https://moodle.example.org/calendar/export_execute.php?userid=1&authtoken=x&preset_what=all"


def _fetch(url: str) -> bytes:
    if "moodle" in url:
        return MOODLE
    if "page" in url:
        return b"<html>not a calendar</html>"
    return SEMESTER.read_bytes()


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    config = Config(
        store=tmp_path_factory.mktemp("sentence"),
        clock=lambda: NOW,
        background=False,
        sweep_every=None,
        fetch=_fetch,
    )
    with TestClient(create_app(config)) as client:
        client.root = config.store
        yield client


def _start(web, lang: str = "en") -> str:
    files = {"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")}
    form = {"tz": "Europe/Paris", "lang": lang}
    answer = web.post("/start", data=form, files=files, follow_redirects=False)
    return answer.headers["location"].split("/")[2]


def _form(html: str) -> dict:
    """The setting-up form as a browser sends it untouched: ticked boxes and the chosen
    radio only."""
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


def _save(web, token: str, **changes) -> service.Subscription:
    form = {**_form(web.get(f"/p/{token}/settings?new=1").text), **changes}
    answer = web.post(f"/p/{token}/settings", data=form, follow_redirects=False)
    assert answer.status_code == 303, re.findall(r'class="(?:error|alert)[^"]*"[^>]*>([^<]*)', answer.text)
    stored = service.load_subscription(web.root, token)
    assert stored is not None
    return stored


def test_the_defaults_are_one_sentence_each_value_a_choice(web):
    token = _start(web)
    page = web.get(f"/p/{token}/settings?new=1").text
    sentence = page.split('<section class="card sentence"', 1)[1].split("</section>", 1)[0]
    text = " ".join(re.sub(r"<[^>]+>", " ", unescape(sentence)).split())
    assert "You get up at" in text and "go to bed at" in text and "and have lunch at" in text
    for name, value in (
        ("wake", "07:30"),
        ("bed", "22:30"),
        ("lunch_pick", "12:30"),
        ("blocks_per_day", "2"),
        ("block_minutes", "90"),
    ):
        assert f'name="{name}" value="{value}" checked' in sentence, name
        assert 'class="is-saved">' in sentence
    assert sentence.count('<details class="pick" name="sentence">') == 5
    assert "Outside your classes, you are free all the time." in text and "Your deadlines: none yet." in text
    # The fold of D98 is gone, and its busy-time line that promised a step no one took.
    templates = ROOT / "src" / "cps" / "web" / "templates"
    for path in templates.glob("*.html"):
        body = path.read_text(encoding="utf-8")
        assert "setup-more" not in body and "The next step asks about them" not in body, path.name


def test_going_on_untouched_keeps_the_defaults_and_each_choice_says_what_it_changes(web):
    token = _start(web)
    kept = _save(web, token)
    assert (kept.options["wake"], kept.options["bed"]) == ("07:30", "22:30")
    assert kept.options["lunch"] == {"start": "12:30", "end": "13:30", "free": True}
    assert (kept.options["blocks_per_day"], kept.options["block_minutes"]) == (2, 90)
    assert kept.busy_rows == ()
    defaults = service.DEFAULT_PREFERENCES
    assert {k: kept.options["preferences"][k] for k in defaults} == defaults
    token = _start(web)
    chosen = _save(
        web, token, wake="08:00", bed="23:30", lunch_pick="13:00", blocks_per_day="3", block_minutes="60"
    )
    assert (chosen.options["wake"], chosen.options["bed"]) == ("08:00", "23:30")
    assert chosen.options["lunch"] == {"start": "13:00", "end": "14:00", "free": True}  # as long as before
    assert (chosen.options["blocks_per_day"], chosen.options["block_minutes"]) == (3, 60)
    studying = _save(web, token, lunch_pick="study")
    assert studying.options["lunch"]["free"] is False
    # Shown again, the sentence says what was chosen.
    page = web.get(f"/p/{token}/settings?new=1").text
    assert 'name="wake" value="08:00" checked' in page and 'name="lunch_pick" value="study" checked' in page


def test_a_busy_time_added_in_a_tap_is_planned_around(web):
    token = _start(web)
    page = web.get(f"/p/{token}/settings?new=1").text
    assert 'name="q0_kind" value="training"' in page
    assert 'name="q0_start" type="time" step="900" value="18:00"' in page
    # Its hours come filled in: with no day chosen it is no answer.
    assert _save(web, token).busy_rows == ()
    saved = _save(web, token, q0_days=["Tue", "Thu"], q0_label="Football", q1_days=["Sat"])
    rows = [(r["label"], r["kind"], r["weekday"], r["start"], r["end"]) for r in saved.busy_rows]
    assert ("Football", "training", 1, "18:00", "20:00") in rows
    assert ("Football", "training", 3, "18:00", "20:00") in rows
    assert ("Work", "work", 5, "09:00", "13:00") in rows and len(rows) == 3
    # Shown again: the busy times kept, the sentence names them; going on keeps them once.
    page = unescape(web.get(f"/p/{token}/settings?new=1").text)
    assert "Outside your classes, you are busy:" in page and "Football · Tue, Thu · 18:00–20:00" in page
    assert len(_save(web, token).busy_rows) == 3
    # Days without hours say what is missing, and nothing is saved.
    form = {**_form(web.get(f"/p/{token}/settings?new=1").text), "q2_days": ["Mon"], "q2_start": ""}
    answer = web.post(f"/p/{token}/settings", data=form)
    assert answer.status_code == 400 and "Busy: give the hours" in unescape(answer.text)
    assert len(service.load_subscription(web.root, token).busy_rows) == 3


def test_the_trip_is_a_choice(web):
    token = _start(web)
    saved = _save(web, token, commute="30", study_on_the_way="1")
    assert saved.options["commute_minutes"] == 30 and saved.options["study_on_the_way"] is True
    assert "And 30 minutes to get to university." in unescape(web.get(f"/p/{token}/settings?new=1").text)


def test_moodle_in_three_steps_in_its_own_words(web):
    english = web.get(f"/p/{_start(web)}/settings?new=1").text
    steps = unescape(english.split('class="add-panel moodle-steps"', 1)[1].split("</ol>", 1)[0])
    for words in ("Calendar", "Import or export calendars", "Export calendar", "All events"):
        assert f"<b>{words}</b>" in steps, words
    assert "<b>Recent and next 60 days</b>" in steps and "<b>Get calendar URL</b>" in steps
    french = unescape(web.get(f"/p/{_start(web, 'fr')}/settings?new=1").text)
    assert "<b>Tous les événements</b>" in french and "<b>URL du calendrier</b>" in french
    assert "<b>Importer ou exporter des calendriers</b>" in french
    italian = unescape(web.get(f"/p/{_start(web, 'it')}/settings?new=1").text)
    assert "<b>Recenti e dei prossimi 60 giorni</b>" in italian and "<b>Esporta calendario</b>" in italian
    # Once the link is known, its export page is a tap away.
    token = _start(web)
    saved = _save(web, token, deadlines_url=LINK)
    names = [t["name"] for t in saved.options["tasks"]]
    assert names == ["Week 4 quiz", "Problem sheet 3", "Essay on fairness"]
    page = web.get(f"/p/{token}/settings?new=1").text
    assert 'href="https://moodle.example.org/calendar/export.php" target="_blank"' in page


def test_a_pasted_link_shows_what_it_brings_before_it_is_saved(web):
    token = _start(web)
    preview = unescape(web.post(f"/p/{token}/deadlines/preview", data={"url": LINK}).text)
    assert "3 deadlines found" in preview and "Problem sheet 3" in preview and "STAT301" in preview
    assert "Problem sheet 1" not in preview  # already past
    stored = service.load_subscription(web.root, token)
    assert not stored.options.get("deadlines_url") and not stored.options.get("tasks")  # nothing kept
    wrong = unescape(web.post(f"/p/{token}/deadlines/preview", data={"url": "https://x.org/page"}).text)
    assert "Nothing could be read from this link" in wrong
    assert web.post(f"/p/{token}/deadlines/preview", data={"url": ""}).text.strip() == ""
    assert web.post("/p/nope/deadlines/preview", data={"url": LINK}).status_code == 404


def test_a_busy_time_holds_shifts_another_job_and_a_night(web):
    """D172 (the owner: "one may have more jobs and shifts so it needs to be more
    flexible"): each busy time holds up to four shifts, each its own days and hours, each
    folded under the one before so no script is needed; a later shift may name another
    job; one that ends earlier than it starts ends the next morning; one that ends when it
    starts is a mistake, said."""
    token = _start(web)
    page = web.get(f"/p/{token}/settings?new=1").text
    kinds = len(service.QUICK_BUSY)
    assert page.count('<details class="more-shift">') == kinds * (service.QUICK_SHIFTS - 1)
    assert 'name="q1_3_days"' in page and 'name="q1_4_days"' not in page
    saved = _save(
        web,
        token,
        q1_label="Bar",
        q1_days=["Mon", "Wed"],
        q1_start="18:00",
        q1_end="22:00",
        q1_1_label="Library",
        q1_1_days=["Sat"],
        q1_1_start="10:00",
        q1_1_end="16:00",
        q1_2_days=["Fri"],
        q1_2_start="22:00",
        q1_2_end="06:00",
    )
    rows = {(r["label"], r["kind"], r["weekday"], r["start"], r["end"]) for r in saved.busy_rows}
    assert rows == {
        ("Bar", "work", 0, "18:00", "22:00"),
        ("Bar", "work", 2, "18:00", "22:00"),
        ("Library", "work", 5, "10:00", "16:00"),
        ("Bar", "work", 4, "22:00", "06:00"),
    }
    # The night shift ends the next morning: Saturday until 06:00 is busy.
    from datetime import date, time

    from cps.calendar_io import busy_from_table

    night = [r for r in saved.busy_rows if r["start"] == "22:00"]
    events = busy_from_table(night, date(2026, 9, 28), 7, "Europe/Paris")
    assert [(e.start.strftime("%a %H:%M"), e.end.strftime("%a %H:%M")) for e in events] == [
        ("Fri 22:00", "Sat 06:00")
    ]
    assert events[0].end.time() == time(6, 0)
    page = unescape(web.get(f"/p/{token}/settings?new=1").text)
    assert "Bar · Mon, Wed · 18:00–22:00" in page and "Bar · Fri · 22:00–06:00 (the next day)" in page
    # Ending when it starts would be a whole day taken: said, and nothing saved.
    form = {**_form(web.get(f"/p/{token}/settings?new=1").text), "q0_days": ["Mon"], "q0_end": "18:00"}
    answer = web.post(f"/p/{token}/settings", data=form)
    assert answer.status_code == 400 and "Training: it ends when it starts" in unescape(answer.text)
    assert len(service.load_subscription(web.root, token).busy_rows) == 4


def test_every_time_of_day_gets_the_apps_own_clock():
    """D172: the system's grey list of times gave way to the app's clock on a computer;
    every time-of-day field is marked for it, and a phone keeps its wheel."""
    templates = ROOT / "src" / "cps" / "web" / "templates"
    fields = [
        tag
        for path in templates.glob("*.html")
        for tag in re.findall(r'<input[^>]*type="time"[^>]*>', path.read_text(encoding="utf-8"))
    ]
    assert len(fields) >= 10 and all("data-clock" in tag for tag in fields)
    ui = (ROOT / "src" / "cps" / "web" / "static" / "ui.js").read_text(encoding="utf-8")
    assert 'querySelectorAll("input[data-clock]")' in ui and '"(pointer: fine)"' in ui
    assert 'for (const mm of ["00", "15", "30", "45"])' in ui
