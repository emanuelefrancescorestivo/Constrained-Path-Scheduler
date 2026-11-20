"""
The end of a session celebrated, and Community right under the next step on Today
(DECISIONS.md D177).

What must hold: every session timed with Focus ends on "Session complete!", as a lesson
ends in Duolingo: the minutes the timer measured, the streak with today counted (the
session counts once its log is saved, the button below), the length reached or the
whole time on the page when true, the points a session of the plan gives (D158), and a
key that makes the confetti fly once per session; a first run's first session keeps its
own words (D107); a session not timed has no celebration. On Today, Community comes
right after the next step and is on to start: who is studying and what classmates shared,
or, with nothing yet, the way in (choosing a handle, or finding classmates to follow).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service, social
from cps.store import Store
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)  # 18:00 in Paris, the plan's first day


def _plan(root: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(root, service.finish_first_run(sub))
    return service.load_subscription(root, sub.token)


def test_a_timed_session_ends_with_its_minutes_its_streak_and_its_points(tmp_path):
    sub = _plan(tmp_path)
    assert service.session_done_view(tmp_path, sub, NOW) is None  # nothing timed yet
    first = service.PlanReport.from_dict(sub.plan).sessions[0]
    running = service.start_focus(sub, "Algebra 3", sid=service.session_id(first), goal=25, now=NOW)
    for minute in range(1, 31):  # the page open the whole time: a beat a minute
        running = service.focus_beat(running, now=NOW + timedelta(minutes=minute))
    done = service.finish_focus(running, now=NOW + timedelta(minutes=30))
    view = service.session_done_view(tmp_path, done, NOW + timedelta(minutes=30))
    assert view is not None
    assert view["minutes"] == 30 and view["course"] == "Algebra 3"
    assert view["streak"] == 1 and view["streak_new"]  # today counted, once the log is saved
    assert view["goal"] == "25 min" and view["whole"]
    assert view["points"] == service.POINTS_DONE == 10 and view["proof"] == service.POINTS_PROOF
    assert view["key"] == f"session-{done.options['focus_draft']['started']}"
    # A session besides the plan earns no points of its own; a short one reaches no length.
    free = service.finish_focus(
        service.start_focus(sub, "Reading", goal=45, now=NOW), now=NOW + timedelta(minutes=10)
    )
    other = service.session_done_view(tmp_path, free, NOW + timedelta(minutes=10))
    assert other["points"] == 0 and other["goal"] == "" and not other["whole"]  # no beat: not checked


def test_the_log_page_celebrates_each_session_once_and_today_shows_community_next(tmp_path):
    sub = _plan(tmp_path)
    clock = [NOW]
    config = Config(store=tmp_path, clock=lambda: clock[0], background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        today = web.get(f"/p/{sub.token}").text
        sections = re.findall(r'<section class="w w-(\w+)', today)
        assert sections[:2] == ["next", "friends"]  # Community right under the next step
        assert "Study alongside your classmates" in unescape(today)  # no handle yet: the way in

        web.post(f"/p/{sub.token}/focus/start", data={"course": "Algebra 3", "goal": "25"})
        clock[0] = NOW + timedelta(minutes=26)
        finished = web.post(f"/p/{sub.token}/focus/finish", follow_redirects=False)
        assert finished.headers["location"] == f"/p/{sub.token}/log"
        page = unescape(web.get(f"/p/{sub.token}/log").text)
        assert "Session complete!" in page and "First session done!" not in page
        assert re.search(r'class="run-done" data-celebrate="session-[^"]+"', page)
        assert "1 day in a row" not in page and "day in a row" in page and "26" in page
        assert "You reached your length: 25 min." in page
        assert "How was it?" in page  # the diary's questions follow, as before

        # With a handle and someone followed who shared, Community shows them.
        service.save_profile(
            tmp_path, sub.token, {"handle": "sam_c", "university": "University of Bologna", "old_enough": "1"}
        )
        quiet = unescape(web.get(f"/p/{sub.token}").text)
        assert "Nobody you follow is studying right now" in quiet
        mate = _plan(tmp_path)
        service.save_profile(
            tmp_path,
            mate.token,
            {"handle": "lea_m", "university": "University of Bologna", "old_enough": "1"},
        )
        service.follow(tmp_path, sub.token, "lea_m")  # a request, which Lea accepts
        service.answer_follow(tmp_path, mate.token, "sam_c", True)
        social.create_post(
            Store(tmp_path),
            mate.token,
            "session",
            day="2026-09-29",
            visibility="everyone",
            data={"course": "Algebra 3", "effort": 5, "progress": 3, "minutes": 40},
        )
        lively = unescape(web.get(f"/p/{sub.token}").text)
        assert "@lea_m" in lively and "studied 40 min" in lively
        assert re.findall(r'<section class="w w-(\w+)', lively)[:2] == ["next", "friends"]


def test_a_session_not_timed_has_no_celebration(tmp_path):
    sub = _plan(tmp_path)
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        page = unescape(web.get(f"/p/{sub.token}/log").text)
    assert "Session complete!" not in page and "data-celebrate" not in page and "How was it?" in page
