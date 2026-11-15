"""
Repair and pause (DECISIONS.md D126): a missed day is repaired by a make-up session
of focus within two days, timed and at least 25 minutes long; a pause the student
sets (today, until Monday, a week, until a day, at most 90 days) keeps the streak
as it is and leaves its days free of sessions. What must hold: a repaired or paused
day never breaks the streak, a studied day stays studied, the offer comes only for
an unforgiven miss of the last two days and never twice on a day the student said
"Not now", minutes typed in repair nothing, and one pause at a time.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cps import service
from cps.progress import Session, grid, pause_days, repairable, streak

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
MON = date(2026, 10, 5)  # a Monday
PARIS = ZoneInfo("Europe/Paris")
START = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)


def _s(day: date, report: str | None = None) -> Session:
    return Session(datetime(day.year, day.month, day.day, 10), 90, "review", "Algebra", report)


def _states(result):
    return [d.state for d in result.days]


def test_a_paused_day_passes_the_streak_on_and_a_studied_one_stays_studied():
    days = [MON + timedelta(days=n) for n in range(5)]
    sessions = [_s(days[0], "done"), _s(days[1]), _s(days[2]), _s(days[3], "done"), _s(days[4], "done")]
    paused = pause_days([(days[1], days[3])])
    result = streak(sessions, MON, days[4], paused=paused)
    assert _states(result) == ["studied", "paused", "paused", "studied", "studied"]
    assert result.current == 3  # neither of the two paused misses broke it
    without = streak(sessions, MON, days[4])
    assert _states(without) == ["studied", "forgiven", "missed", "studied", "studied"]
    assert without.current == 2
    # A pause set ahead shows in the grid as paused, not "to come".
    rows = grid(sessions, MON, days[0], paused=pause_days([(days[2], days[3])]))
    assert [d.state for d in rows[0][:5]] == ["studied", "future", "paused", "paused", "future"]


def test_a_repaired_day_passes_the_streak_on_and_keeps_the_weeks_forgiveness():
    days = [MON + timedelta(days=n) for n in range(5)]
    sessions = [_s(days[0], "done"), _s(days[1]), _s(days[2], "done"), _s(days[3]), _s(days[4], "done")]
    plain = streak(sessions, MON, days[4])
    assert _states(plain) == ["studied", "forgiven", "studied", "missed", "studied"] and plain.current == 1
    repaired = streak(sessions, MON, days[4], repaired={days[3]})
    assert _states(repaired) == ["studied", "forgiven", "studied", "repaired", "studied"]
    assert repaired.current == 3
    # Repairing the first miss leaves the week's forgiveness for the second.
    both = streak(sessions, MON, days[4], repaired={days[1]})
    assert _states(both) == ["studied", "repaired", "studied", "forgiven", "studied"] and both.current == 3


def test_only_an_unforgiven_miss_of_the_last_two_days_can_be_repaired():
    days = [MON + timedelta(days=n) for n in range(6)]
    sessions = [_s(days[0], "done"), _s(days[1]), _s(days[2]), _s(days[3]), _s(days[4]), _s(days[5])]
    # Day 1 is forgiven, days 2 to 4 missed; on day 5 the latest within two days is day 4.
    assert repairable(streak(sessions, MON, days[5]), days[5]) == days[4]
    # Day 4 repaired: day 3 is still within reach.
    assert repairable(streak(sessions, MON, days[5], repaired={days[4]}), days[5]) == days[3]
    # Only the forgiven day behind: nothing to repair.
    assert repairable(streak([*sessions[:2], _s(days[2])], MON, days[2]), days[2]) is None
    # Three days later, a miss stands.
    old = [_s(days[0], "done"), _s(days[1]), _s(days[2])]
    assert repairable(streak([*old, _s(days[5])], MON, days[5]), days[5]) is None


@pytest.fixture(scope="module")
def sub() -> service.Subscription:
    return service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 28),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[service.TaskSpec("Stats report", "2026-10-16 18:00", 4.0, "Advanced Statistics")],
        preferences=service.DEFAULT_PREFERENCES,
        now=START,
    )


def _planned_days(subscription) -> list[date]:
    plan = service.PlanReport.from_dict(subscription.plan)
    begun = plan.history + plan.sessions
    return sorted({datetime.fromisoformat(s.start).astimezone(PARIS).date() for s in begun})


def _at(day: date, hour: int) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=PARIS).replace(hour=hour)


def test_a_pause_leaves_its_days_free_keeps_the_streak_and_ends_on_resume(sub):
    days = _planned_days(sub)
    today = days[1]
    paused = service.set_pause(sub, "week", now=_at(today, 7))
    assert paused.options["preferences"]["pauses"] == [
        {"from": today.isoformat(), "to": (today + timedelta(days=6)).isoformat()}
    ]
    assert all(not today <= d <= today + timedelta(days=6) for d in _planned_days(paused))  # nothing planned
    view = service.progress_view(paused, _at(today, 7))
    assert view["streak"]["pause"]["to"] == (today + timedelta(days=6)).isoformat()
    assert view["streak"]["line"].startswith("Paused until")
    states = {d["date"]: d["state"] for row in view["grid"] for d in row}
    assert states[today.isoformat()] == "paused"
    assert states[(today + timedelta(days=3)).isoformat()] == "paused"
    # Resume two days in: the pause ends yesterday, and the days after are planned again.
    resumed = service.end_pause(paused, now=_at(today + timedelta(days=2), 9))
    assert resumed.options["preferences"]["pauses"] == [
        {"from": today.isoformat(), "to": (today + timedelta(days=1)).isoformat()}
    ]
    assert any(d > today + timedelta(days=1) for d in _planned_days(resumed))
    # "Until Monday" runs through Sunday; a day in the past or 90 days on is refused.
    monday = service.set_pause(sub, "monday", now=_at(today, 7))
    last = date.fromisoformat(monday.options["preferences"]["pauses"][-1]["to"])
    assert last.weekday() == 6 and last >= today
    assert service.pause_until("today", today) == today
    with pytest.raises(service.InvalidInput):
        service.pause_until((today - timedelta(days=1)).isoformat(), today)
    with pytest.raises(service.InvalidInput):
        service.pause_until((today + timedelta(days=90)).isoformat(), today)
    with pytest.raises(service.InvalidInput):
        service.pause_until("someday", today)
    # One pause at a time: a new one replaces the one ahead.
    twice = service.set_pause(paused, "today", now=_at(today, 8))
    assert twice.options["preferences"]["pauses"] == [{"from": today.isoformat(), "to": today.isoformat()}]


def _two_misses(sub):
    """The first two planned days skipped: the first forgiven, the second missed."""
    days = _planned_days(sub)
    skipped = sub
    for day in days[:2]:
        plan = service.PlanReport.from_dict(skipped.plan)
        for s in plan.history + plan.sessions:
            if datetime.fromisoformat(s.start).astimezone(PARIS).date() == day:
                skipped = service.report_session(skipped, service.session_id(s), "skipped", now=_at(day, 23))
    return skipped, days[1]


def test_a_missed_day_is_repaired_by_a_timed_make_up_session(sub, tmp_path):
    skipped, missed = _two_misses(sub)
    morning = _at(missed + timedelta(days=1), 9)
    offer = service.repair_offer(tmp_path, skipped, morning)
    assert offer == {
        "day": missed.isoformat(),
        "yesterday": True,
        "label": service.format_day(missed, "short"),
        "minutes": 25,
        "course": offer["course"],
    }
    assert offer["course"]
    running = service.start_focus(skipped, offer["course"], goal=25, repair=offer["day"], now=morning)
    for sec in range(0, 26 * 60, 30):
        running = service.focus_beat(running, now=morning + timedelta(seconds=sec))
    finished = service.finish_focus(running, now=morning + timedelta(minutes=26))
    assert finished.options["focus_draft"]["repair"] == missed.isoformat()
    form = {"effort": "5", "progress": "3", "visibility": "me"}
    logged, _post = service.log_session(tmp_path, finished, form, now=morning + timedelta(minutes=26))
    assert logged.options["repairs"] == {missed.isoformat(): (missed + timedelta(days=1)).isoformat()}
    later = morning + timedelta(minutes=27)
    view = service.progress_view(logged, later, logged=service.logged_sessions(tmp_path, logged))
    states = {d["date"]: d["state"] for row in view["grid"] for d in row}
    assert states[missed.isoformat()] == "repaired" and view["streak"]["current"] == 1
    assert service.repair_offer(tmp_path, logged, later) is None  # repaired: no offer
    # Too short, or minutes typed in: nothing repaired.
    short = service.start_focus(skipped, offer["course"], goal=25, repair=offer["day"], now=morning)
    short = service.finish_focus(short, now=morning + timedelta(minutes=10))
    brief, _post = service.log_session(tmp_path, short, form, now=morning + timedelta(minutes=10))
    assert "repairs" not in brief.options
    typed = service.finish_focus(running, now=morning + timedelta(minutes=26))
    later_on = morning + timedelta(minutes=26)
    typed, _post = service.log_session(tmp_path, typed, {**form, "minutes": "40"}, now=later_on)
    assert "repairs" not in typed.options
    # "Not now" holds for the day; a day too far back is refused.
    assert service.repair_offer(tmp_path, service.repair_later(skipped, now=morning), morning) is None
    with pytest.raises(service.InvalidInput):
        far = (missed - timedelta(days=5)).isoformat()
        service.start_focus(skipped, "Algebra 3", repair=far, now=morning)
    with pytest.raises(service.InvalidInput):
        service.start_focus(skipped, "Algebra 3", repair="not-a-day", now=morning)


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    class Clock:
        now = START

    clock = Clock()
    config = Config(
        store=tmp_path, clock=lambda: clock.now, background=False, sweep_every=None, remind_every=None
    )
    with TestClient(create_app(config)) as client:
        client.root, client.clock = tmp_path, clock
        yield client


def _form(html: str) -> dict:
    form: dict = {}
    for tag in re.findall(r"<input[^>]*>", html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        if 'type="checkbox"' in tag:
            if " checked" in tag:
                form.setdefault(name.group(1), []).append(unescape(value.group(1)))
            continue
        form[name.group(1)] = unescape(value.group(1)) if value else ""
    for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        chosen = re.search(r'<option(?: value="([^"]*)")? selected>([^<]*)', body)
        form[name] = (chosen.group(2) if chosen.group(1) is None else chosen.group(1)) if chosen else ""
    return form


def test_today_offers_the_repair_and_progress_holds_the_pause(web):
    files = {"file": ("t.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
    token = (
        web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        .headers["location"]
        .split("/")[2]
    )
    form = _form(web.get(f"/p/{token}/settings?new=1").text)
    assert web.post(f"/p/{token}/settings", data=form, follow_redirects=False).status_code == 303
    service.update_subscription(web.root, token, service.finish_first_run)
    sub = service.load_subscription(web.root, token)
    skipped, missed = _two_misses(sub)
    service.update_subscription(web.root, token, lambda _current: skipped)
    web.clock.now = _at(missed + timedelta(days=1), 9)
    today = unescape(web.get(f"/p/{token}").text)
    assert "Repair your streak" in today
    assert "Yesterday was missed. 25 minutes of focus today repair it." in today
    assert f'name="repair" value="{missed.isoformat()}"' in today
    # "Not now" puts it away for the day.
    assert web.post(f"/p/{token}/repair/later", follow_redirects=False).status_code == 303
    assert "Repair your streak" not in unescape(web.get(f"/p/{token}").text)
    # Starting the make-up session from the card carries the day to the timer.
    service.update_subscription(web.root, token, lambda _current: skipped)
    started = web.post(
        f"/p/{token}/focus/start",
        data={"course": "Algebra 3", "goal": "25", "repair": missed.isoformat()},
        follow_redirects=False,
    )
    assert started.status_code == 303
    assert service.load_subscription(web.root, token).options["focus"]["repair"] == missed.isoformat()
    # Progress: the pause form; a week's pause, then the day grid and the strip say so.
    progress = unescape(web.get(f"/p/{token}/progress").text)
    assert "Pause the streak" in progress and 'name="until" value="week"' in progress
    paused = web.post(f"/p/{token}/pause", data={"until": "week"}, follow_redirects=False)
    assert paused.status_code == 303 and paused.headers["location"].endswith("/progress?saved=pause")
    progress = unescape(web.get(f"/p/{token}/progress").text)
    assert "Paused until" in progress
    assert "Resume" in progress
    assert 'class="d d-paused' in unescape(web.get(f"/p/{token}/progress?more=1").text)  # the day grid
    assert "Paused until" in unescape(web.get(f"/p/{token}/panel").text)  # the strip on Today
    bad = web.post(f"/p/{token}/pause", data={"until": "someday"})
    assert bad.status_code == 400
    resumed = web.post(f"/p/{token}/pause", data={"end": "1"}, follow_redirects=False)
    assert resumed.status_code == 303
    assert "Pause the streak" in unescape(web.get(f"/p/{token}/progress").text)
