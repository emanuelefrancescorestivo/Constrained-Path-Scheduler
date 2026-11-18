"""
D140: Today as the app's home, Calendar the calendar alone. What must hold: Today draws
its widgets, the most urgent first, and no calendar; Calendar draws the calendar and no
panel; the day's list holds today's classes, exams, busy times and sessions in time order,
each over, going on or to come, a session with its report, the trips and lunch left to the
calendar; the classmates' window shows who of the people followed is studying now and their
newest posts, never the student's own, and nothing for a student without a handle; the page
redraws the same widgets after a change; a report from Today needs no calendar.
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

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)  # 18:00 in Paris, a Tuesday


def _plan(root: Path, handle: str | None = None) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec(n, None, familiarity=3) for n in ("Algebra 3", "Advanced Statistics")],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(root, service.finish_first_run(sub))
    if handle:
        form = {"handle": handle, "university": "Université Lyon 1", "programme": "L2", "old_enough": "1"}
        service.save_profile(root, sub.token, form)
    return service.load_subscription(root, sub.token)


def _friends(root: Path, a: service.Subscription, b: service.Subscription, a_handle: str, b_handle: str):
    """`a` follows `b`, accepted."""
    service.follow(root, a.token, b_handle)
    service.answer_follow(root, b.token, a_handle, accept=True)


def test_the_days_list_holds_today_in_order_with_where_each_stands(tmp_path):
    sub = _plan(tmp_path)
    day = service.day_agenda(sub, NOW)
    assert day == sorted(day, key=lambda d: (d["start"], d["end"]))
    # The same day as the week's list (the calendar without a script), less the trips and lunch.
    week = service.agenda(sub, 0, NOW)["days"][0]["entries"]
    expected = sorted((e["start"], e["end"]) for e in week if e["kind"] not in service.DAY_LEFT_OUT)
    assert sorted((d["start"], d["end"]) for d in day) == expected and day
    for d in day:
        assert d["state"] == ("past" if d["end"] <= "18:00" else "now" if d["start"] <= "18:00" else "later")
        assert d["kind"] in {"class", "exam", "typed", "skipped", "study", *service.TEST_KINDS}
    # On the day of the plan's first session, just after it: over, and once reported, said so.
    first = service.PlanReport.from_dict(sub.plan).sessions[0]
    after = datetime.fromisoformat(first.end) + timedelta(minutes=10)
    studied = [d for d in service.day_agenda(sub, after) if d["kind"] == "study"]
    assert studied and studied[0]["state"] == "past" and studied[0]["reported"] is None
    reported = service.report_session(sub, service.session_id(first), "done", now=after)
    assert next(d["reported"] for d in service.day_agenda(reported, after) if d["kind"] == "study") == "done"
    assert service.day_agenda(_plan(tmp_path), NOW - timedelta(days=400)) == []  # no day of the plan then


def test_classmates_are_the_people_followed_never_oneself(tmp_path):
    ada = _plan(tmp_path, "ada")
    ben = _plan(tmp_path, "ben")
    assert service.classmates_glance(tmp_path, _plan(tmp_path), NOW) is None  # no handle
    assert service.classmates_glance(tmp_path, ben, NOW) is None  # nobody followed: no empty window
    _friends(tmp_path, ben, ada, "ben", "ada")
    fresh, _post = service.log_session(
        tmp_path,
        ada,
        {"course": "Algebra 3", "minutes": "30", "effort": "5", "progress": "3", "visibility": "followers"},
        [],
        now=NOW,
    )
    service.save_subscription(tmp_path, fresh)
    service.post_explanation(
        tmp_path,
        ada,
        {
            "concept": "Eigenvalues",
            "course": "Algebra 3",
            "text": "Directions a matrix only stretches.",
            "visibility": "everyone",
        },
        now=NOW,
    )
    seen = service.classmates_glance(tmp_path, ben, NOW)
    assert seen is not None and seen["now"] == []
    assert [(p["author"]["handle"], p["course"]) for p in seen["posts"]] == [("ada", "Algebra 3")] * 2
    says = {p["says"] for p in seen["posts"]}
    assert "explained Eigenvalues" in says and any(s.startswith("studied ") for s in says)
    assert service.classmates_glance(tmp_path, ada, NOW) is None  # her own posts are not her classmates'
    # Studying now, with her yes (D108): she is in his window, with a Join.
    service.set_studying_now(tmp_path, ada.token, True)
    focused = service.update_subscription(
        tmp_path, ada.token, lambda current: service.start_focus(current, "Algebra 3", goal=25, now=NOW)
    )
    service.sync_presence(tmp_path, focused, NOW)
    seen = service.classmates_glance(tmp_path, ben, NOW + timedelta(minutes=5))
    assert seen is not None and [(s["handle"], s["join"]) for s in seen["now"]] == [("ada", "Algebra 3")]
    assert seen["join_minutes"] == service.JOIN_MINUTES


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def test_today_is_the_widgets_and_calendar_the_calendar(web, tmp_path):
    ada = _plan(tmp_path, "ada")
    ben = _plan(tmp_path, "ben")
    _friends(tmp_path, ben, ada, "ben", "ada")
    service.post_explanation(
        tmp_path,
        ada,
        {
            "concept": "Eigenvalues",
            "course": "Algebra 3",
            "text": "Directions a matrix only stretches.",
            "visibility": "everyone",
        },
        now=NOW,
    )
    today = unescape(web.get(f"/p/{ben.token}").text)
    assert (
        '<div class="hub">' in today
        and '<section class="board"' not in today
        and 'id="calendar"' not in today
    )
    order = [today.index(f'class="w w-{name}') for name in ("next", "day", "time", "tips", "week", "tasks", "friends")]
    assert order == sorted(order)  # the most urgent first, as a phone reads it
    for section in ("/week", "/progress", "/tasks", "/community", "/focus"):
        assert f'class="w-link" href="/p/{ben.token}{section}"' in today, section
    day = today[today.index('class="w w-day"') :]
    day = day[: day.index("</section>")]
    assert day.count('class="day-item') == len(service.day_agenda(ben, NOW)) and 'class="day-now"' in day
    assert "@ada" in today and "explained Eigenvalues" in today
    # The page's redraw is the same widgets.
    assert '<div class="hub">' in web.get(f"/p/{ben.token}/panel").text
    calendar = web.get(f"/p/{ben.token}/week").text
    assert 'id="calendar"' in calendar and 'id="panel"' not in calendar and 'class="hub"' not in calendar


def test_a_report_from_today_needs_no_calendar():
    script = (ROOT / "src" / "cps" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "const calendar = board && new Calendar(board" in script
    assert (
        "await change(`sessions/${sid}/report`" in script and "if (calendar) return calendar.change" in script
    )
    assert re.search(r"if \(calendar\) calendar\.load\(\);\s*$", script)


def test_the_days_hours_add_up_and_attention_counts_what_the_timer_logged(tmp_path):
    sub = _plan(tmp_path)
    seen = service.time_and_attention(tmp_path, sub, NOW)
    assert seen is not None and (seen["wake"], seen["bed"]) == (
        service.day_view(sub)["wake"],
        service.day_view(sub)["bed"],
    )
    day = service.day_agenda(sub, NOW)
    classes = sum(service._span(d["start"], d["end"]) for d in day if d["kind"] == "class")
    assert seen["hours"]["classes"] == service.format_number(round(classes * 2) / 2)
    widths = [b["width"] for b in seen["bar"]]
    assert all(w > 0 for w in widths) and 99.5 <= sum(widths) <= 100.5
    assert [b["part"] for b in seen["bar"]] == [
        p for p in service.TIME_PARTS if p in {b["part"] for b in seen["bar"]}
    ]
    assert seen["focus"] == {"minutes": 0, "time": "", "sessions": 0, "checked": 0, "whole": 0}

    # Two sessions timed this week, one checked without leaving the app, one left twice;
    # one logged by hand, and one timed last week: neither counts.
    def log(day, **data):
        social.create_post(
            Store(tmp_path),
            sub.token,
            "session",
            day=day,
            visibility="me",
            data={"course": "Algebra 3", "effort": 5, "progress": 3, **data},
            now=NOW,
        )

    log("2026-09-29", minutes=50, timed=True, checked=True)
    log("2026-09-29", minutes=25, timed=True, checked=True, interruptions=2, away_minutes=3)
    log("2026-09-29", minutes=40)
    log("2026-09-22", minutes=90, timed=True, checked=True)
    focus = service.time_and_attention(tmp_path, sub, NOW)["focus"]
    assert focus == {
        "minutes": 75,
        "time": service.duration_words(75),
        "sessions": 2,
        "checked": 2,
        "whole": 1,
    }


def test_a_tip_to_study_and_a_tip_to_live_well_each_day(tmp_path):
    sub = _plan(tmp_path)
    tips = service.tips_for_today(sub, NOW)
    assert [t["kind"] for t in tips] == ["study", "live"]
    assert tips[0]["topic"] in service.STUDY_TOPICS and tips[1]["topic"] in service.LIVE_TOPICS
    week = {tuple(t["key"] for t in service.tips_for_today(sub, NOW + timedelta(days=d))) for d in range(7)}
    assert len(week) == 7  # a different pair each day
    other = service.tips_for_today(sub, NOW, besides=tips[0]["key"])
    assert tips[0]["key"] not in {t["key"] for t in other}
    assert service.tips_for_today(service.set_tips(sub, "off"), NOW) == []
