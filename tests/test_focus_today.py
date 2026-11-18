"""
D144: focus and attention from Today (the owner's pick, 2026-11-18: "focus, attention").
What must hold: the week's attention is the minutes the timer logged each day, Monday
first, the longest day full height, minutes typed by hand not counted; the part of the day
the student focuses longest is named only once four timed sessions of the last four weeks
say when they started and two parts have two sessions each to compare; Today starts
anything besides the plan's next session in one tap, with the course last timed and the
shortest length chosen, offers no card while a session runs, and its tip on breaks cites
only checked sources.
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
from cps import tips as tips_module
from cps.store import Store
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)  # 18:00 in Paris, a Tuesday
PARIS = service._zone("Europe/Paris")


def _plan(root: Path) -> service.Subscription:
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
    return service.load_subscription(root, sub.token)


def _log(root: Path, sub: service.Subscription, day: str, minutes: int, *, at: str = "", **data) -> None:
    """A session saved to the diary; timed when `at` (the local hour the timer started)."""
    started = ""
    if at:
        hour, minute = (int(x) for x in at.split(":"))
        local = datetime.combine(date.fromisoformat(day), datetime.min.time(), PARIS).replace(
            hour=hour, minute=minute
        )
        started = local.astimezone(UTC).isoformat()
    social.create_post(
        Store(root),
        sub.token,
        "session",
        day=day,
        visibility="me",
        data={
            "course": data.pop("course", "Algebra 3"),
            "effort": 5,
            "progress": 3,
            "minutes": minutes,
            "timed": bool(at),
            "checked": bool(at),
            "started": started,
            **data,
        },
        now=NOW,
    )


def test_the_weeks_attention_is_the_timers_minutes_day_by_day(tmp_path):
    sub = _plan(tmp_path)
    _log(tmp_path, sub, "2026-09-28", 60, at="09:00")  # Monday
    _log(tmp_path, sub, "2026-09-29", 20, at="10:00")  # Tuesday, today
    _log(tmp_path, sub, "2026-09-29", 10, at="15:00")
    _log(tmp_path, sub, "2026-09-29", 90)  # typed by hand: not the timer's
    _log(tmp_path, sub, "2026-09-21", 120, at="09:00")  # last week
    focus = service.time_and_attention(tmp_path, sub, NOW)["focus"]
    days = focus["days"]
    assert len(days) == 7
    assert [d["minutes"] for d in days] == [60, 30, 0, 0, 0, 0, 0]
    assert [d["height"] for d in days] == [100, 50, 0, 0, 0, 0, 0]
    assert [d["today"] for d in days] == [False, True, False, False, False, False, False]
    assert [d["ahead"] for d in days] == [False, False, True, True, True, True, True]
    assert days[0]["time"] == service.duration_words(60) and days[2]["time"] == ""
    assert focus["minutes"] == 90 and focus["sessions"] == 3
    assert focus["days_said"] == f"{days[0]['name']} {days[0]['time']}, {days[1]['name']} {days[1]['time']}"
    # An empty week draws no column at all.
    empty = service.time_and_attention(_fresh(tmp_path), _plan(_fresh(tmp_path)), NOW)["focus"]
    assert all(d["height"] == 0 for d in empty["days"])


def _fresh(root: Path) -> Path:
    other = root / "other"
    other.mkdir(exist_ok=True)
    return other


def test_the_part_of_the_day_is_named_only_with_enough_to_compare(tmp_path):
    sub = _plan(tmp_path)
    best = lambda: service.time_and_attention(tmp_path, sub, NOW)["focus"]["best"]  # noqa: E731
    # Three mornings: one part, nothing to compare it with.
    for day in ("2026-09-22", "2026-09-23", "2026-09-24"):
        _log(tmp_path, sub, day, 50, at="09:30")
    assert best() is None
    # One evening: four sessions, but the evening has only one.
    _log(tmp_path, sub, "2026-09-25", 25, at="20:00")
    assert best() is None
    # A second evening: mornings average 50 minutes, evenings 25.
    _log(tmp_path, sub, "2026-09-26", 25, at="21:00")
    found = best()
    assert found is not None and (found["part"], found["sessions"]) == ("morning", 5)
    assert found["words"] == (
        f"You focus longest in the morning: {service.duration_words(50)} a session on average."
    )
    # Long evenings turn it; sessions older than four weeks, or typed without the timer,
    # never count.
    _log(tmp_path, sub, "2026-08-20", 300, at="08:00")
    _log(tmp_path, sub, "2026-09-27", 400)
    _log(tmp_path, sub, "2026-09-28", 180, at="19:00")
    assert best()["part"] == "evening"  # (25 + 25 + 180) / 3 against 50
    assert best()["sessions"] == 6


def test_focus_from_today_starts_in_one_tap_with_the_last_course_first(tmp_path):
    sub = _plan(tmp_path)
    quick = service.focus_quick(tmp_path, sub)
    assert quick is not None
    assert quick["courses"] == service.course_names(sub)[: service.FOCUS_QUICK_COURSES]
    assert [g["minutes"] for g in quick["goals"]] == [25, 45, 90] and quick["goal"] == 25
    # Every length offered is one the focus page accepts.
    assert all(service.focus_goal(g["minutes"]) == g["minutes"] for g in quick["goals"])
    # The course last timed comes first; one typed by hand does not move it.
    _log(tmp_path, sub, "2026-09-28", 30, at="09:00", course="advanced statistics")
    _log(tmp_path, sub, "2026-09-29", 30, course="Algebra 3")
    assert service.focus_quick(tmp_path, sub)["courses"][0] == "Advanced Statistics"
    # The tip on breaks, with its evidence a tap away, rests on checked sources only.
    tip = quick["tip"]
    assert tip["key"] == "breaks"
    breaks = next(t for t in tips_module.TIPS if t.key == "breaks")
    assert breaks.sources and all(k in tips_module.SOURCES for k in breaks.sources)
    references = (ROOT / "docs" / "REFERENCES.md").read_text(encoding="utf-8")
    assert all(f"### {k}\n" in references for k in breaks.sources)
    assert service.focus_quick(tmp_path, service.set_tips(sub, "off"))["tip"] is None
    # No card while a session runs: Today's banner leads back to it instead.
    running = service.start_focus(sub, "Algebra 3", goal=25, now=NOW)
    assert service.focus_quick(tmp_path, running) is None


def test_today_draws_the_focus_card_and_the_week_and_the_card_starts_the_timer(tmp_path):
    sub = _plan(tmp_path)
    _log(tmp_path, sub, "2026-09-28", 60, at="09:00")
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        page = client.get(f"/p/{sub.token}").text
        card = re.search(r'<section class="w w-focus".*?</section>', page, re.S)
        assert card is not None
        form = card.group(0)
        assert f'action="/p/{sub.token}/focus/start"' in form
        assert re.search(r'name="course" value="Algebra 3" checked', form)
        assert re.search(r'name="goal" value="25" checked', form)
        assert "#tip-breaks" in form
        # The card stands in for the link to the focus page under the next step.
        assert "Study something else" not in page
        # The week's attention: one column (Monday's), seven day letters.
        week = re.search(r'<svg class="focus-week".*?</svg>', page, re.S).group(0)
        assert week.count('class="fw-col') == 1 and week.count('class="fw-day') == 7
        assert "Time focused each day:" in unescape(week)
        started = client.post(
            f"/p/{sub.token}/focus/start", data={"course": "Algebra 3", "goal": "25"}, follow_redirects=False
        )
        assert started.status_code == 303
        running = service.load_subscription(tmp_path, sub.token).options["focus"]
        assert (running["course"], running["goal"]) == ("Algebra 3", 25)
        page = client.get(f"/p/{sub.token}").text
        assert 'class="w w-focus"' not in page and "running-focus" in page


def test_the_breaks_tip_is_where_the_card_links(tmp_path):
    sub = _plan(tmp_path)
    tip = service.focus_quick(tmp_path, sub)["tip"]
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        page = client.get(f"/p/{sub.token}/tips?topic={tip['topic']}").text
        assert f'id="tip-{tip["key"]}"' in page


def test_a_week_of_minutes_ends_on_sunday(tmp_path):
    sub = _plan(tmp_path)
    sunday = NOW + timedelta(days=5)
    days = service.time_and_attention(tmp_path, sub, sunday)["focus"]["days"]
    assert days[-1]["today"] and not any(d["ahead"] for d in days)
