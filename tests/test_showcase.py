"""
`cps demo` (cps.showcase, DECISIONS.md D31): the hosted app on a made-up class, on
one's own computer (@alex) or public (a fresh student for each visitor). Fast checks
of the moved timetable and the drawn pages; slow runs of both commands, then the app
on the store each made.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from html import unescape
from zoneinfo import ZoneInfo

import pytest

from cps import calendar_io, service, showcase, social
from cps.cli import main

PARIS = ZoneInfo("Europe/Paris")


def test_today_falls_in_the_eighth_week_of_the_moved_semester():
    for today in (date(2026, 11, 18), date(2026, 10, 9), date(2027, 3, 1), date(2031, 1, 5)):
        weeks = showcase.weeks_for(today)
        eighth = showcase.EIGHTH + timedelta(weeks=weeks)
        assert eighth <= today < eighth + timedelta(days=7)
    assert showcase.weeks_for(date(2026, 11, 22)) == 0  # the semester as written
    assert (showcase.EIGHTH - showcase.FIRST).days == 7 * 7 and showcase.FIRST.weekday() == 0


SMALL = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//test//EN\r
BEGIN:VEVENT\r
UID:lecture@test\r
SUMMARY:Lineare Algebra Vorlesung\r
DTSTART;TZID=Europe/Paris:20261005T101500\r
DTEND;TZID=Europe/Paris:20261005T114500\r
RRULE:FREQ=WEEKLY;UNTIL=20261102T235959Z\r
EXDATE;TZID=Europe/Paris:20261019T101500,20261026T101500\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:exam@test\r
SUMMARY:Examen final - Algebra\r
DTSTART;TZID=Europe/Paris:20270118T090000\r
DTEND;TZID=Europe/Paris:20270118T120000\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:utc@test\r
SUMMARY:Seminar\r
DTSTART:20261007T130000Z\r
DTEND:20261007T140000Z\r
END:VEVENT\r
END:VCALENDAR\r
"""


def _occurrences(ics: bytes, weeks: int = 0) -> list[tuple[datetime, datetime, str]]:
    start = datetime(2026, 9, 1, tzinfo=PARIS) + timedelta(weeks=weeks)
    found = calendar_io.expand_events(calendar_io.decode_ics(ics), start, start + timedelta(days=200), PARIS)
    return sorted((e.start, e.end, e.summary) for e in found)


@pytest.mark.parametrize("weeks", [3, -2, 60])
def test_moving_a_calendar_keeps_weekdays_times_repetitions_and_exams(weeks):
    moved = showcase.shifted(SMALL, weeks)
    before, after = _occurrences(SMALL), _occurrences(moved, weeks)
    # Five Mondays, two of them excepted; the exam; the seminar.
    assert [t for _s, _e, t in after].count("Lineare Algebra Vorlesung") == 3
    assert len(before) == len(after) == 5
    for (s0, e0, t0), (s1, e1, t1) in zip(before, after, strict=True):
        assert t1 == t0 and s1.date() == s0.date() + timedelta(weeks=weeks)
        if t0 != "Seminar":  # local wall-clock times stay; a UTC time stays in UTC
            assert (s1.time(), e1.time()) == (s0.time(), e0.time())
        else:
            assert s1.astimezone(UTC).time() == s0.astimezone(UTC).time()
    exams = calendar_io.find_deadlines([calendar_io.BusyEvent(s, e, t) for s, e, t in after])
    assert [d.when.date() for d in exams] == [date(2027, 1, 18) + timedelta(weeks=weeks)]


def test_the_drawn_pages_of_notes_are_images_the_network_keeps():
    page = showcase.page_png(random.Random(1))
    kept, kind = social.clean_photo(page)
    assert kind == "png" and kept.startswith(b"\x89PNG") and len(page) < social.MAX_PHOTO_BYTES
    assert showcase.page_png(random.Random(1)) == page  # deterministic


def _app(store, admin, demo=None):
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    config = Config(store=store, background=False, sweep_every=None, admin_token=admin, demo=demo)
    return TestClient(create_app(config))


def _student_pages(web, token):
    for path in ("", "/progress", "/trends", "/community", "/notes", "/groups", "/cards/review", "/focus"):
        assert web.get(f"/p/{token}{path}").status_code == 200, path


@pytest.mark.slow
def test_cps_demo_makes_a_store_to_look_around_and_serves_it(tmp_path, capsys):
    store = tmp_path / "demo"
    assert main(["demo", "--store", str(store), "--no-serve"]) == 0
    said = capsys.readouterr().out
    found = showcase.Showcase.load(store)
    assert found is not None and f"/p/{found.you}" in said and "@lena" in said and found.admin in said
    assert "everyone in it is made up" in said
    # Run again: the same store, not a new one.
    assert main(["demo", "--store", str(store), "--no-serve"]) == 0
    assert showcase.Showcase.load(store) == found
    # Never a directory it did not make.
    (tmp_path / "mine").mkdir()
    (tmp_path / "mine" / "thesis.tex").write_text("keep me")
    assert main(["demo", "--store", str(tmp_path / "mine"), "--no-serve"]) == 2
    assert (tmp_path / "mine" / "thesis.tex").exists()

    you = service.load_subscription(store, found.you)
    assert you is not None and you.outcomes  # the semester so far, reported
    now = found.clock()
    view = service.progress_view(you, now, logged=service.logged_sessions(store, you))
    assert view["streak"]["current"] > 0 and view["week"]["planned"] > 0
    assert service.cards_due(store, you, now) > 0
    assert len(service.library_view(store, you, now=now)["notes"]) == 4
    assert len(service.groups_view(store, you, now)["invitations"]) == 1
    assert service.group_view(store, you, found.group, now)["size"] == 4

    with _app(store, found.admin) as web:
        _student_pages(web, found.you)
        assert "@alex" in unescape(web.get(f"/p/{found.people['marco']}/u/alex").text)
        assert web.get(f"/admin/{found.admin}").status_code == 200
        home = web.get("/").text
        assert 'action="/demo"' not in home and web.post("/demo").status_code == 404


@pytest.mark.slow
def test_the_public_demo_gives_each_visitor_a_student_of_their_own(tmp_path, capsys):
    store = tmp_path / "public"
    assert main(["demo", "--public", "--store", str(store), "--no-serve"]) == 0
    said = capsys.readouterr().out
    world = showcase.Showcase.load(store)
    assert world is not None and world.you is None and "Each visitor" in said
    template = store / showcase.TEMPLATE
    made = template.read_text(encoding="utf-8")

    # Each student is a copy of the semester made once, with a diary, a group and
    # cards of their own; the model is not made again for the next one.
    one, two = showcase.add_student(store, world), showcase.add_student(store, world)
    assert template.read_text(encoding="utf-8") == made
    assert one.token != two.token and one.handle != two.handle and one.group != two.group
    a, b = service.load_subscription(store, one.token), service.load_subscription(store, two.token)
    assert a is not None and b is not None and a.plan == b.plan and a.outcomes == b.outcomes
    assert len(a.outcomes) > 20
    now = datetime.now(UTC)
    for student, sub in ((one, a), (two, b)):
        assert service.group_view(store, sub, student.group, now)["size"] == 4
        assert service.cards_due(store, sub, now) > 0

    with _app(store, world.admin, demo=lambda: showcase.add_student(store, world).token) as web:
        home = web.get("/").text
        assert 'action="/demo"' in home and "Start the demo" in home
        started = web.post("/demo", follow_redirects=False)
        assert started.status_code == 303 and started.headers["location"].endswith("?saved=demo")
        token = started.headers["location"].split("/p/")[1].split("?")[0]
        page = unescape(web.get(started.headers["location"]).text)
        assert "This student is yours" in page
        assert "everything starts again when it restarts" in web.get(f"/p/{token}/progress").text
        _student_pages(web, token)
        # After a restart, an old link says what happened instead of a bare 404.
        gone = web.get("/p/" + "x" * 32)
        assert gone.status_code == 404 and "starts again from scratch" in unescape(gone.text)
