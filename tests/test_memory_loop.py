"""
The memory loop's first slice (DECISIONS.md D125): an answered self-test is set
against the model's chance of recalling it (beat the forecast), what it added to the
topic's memory is said in days (memory banked), and once every session planned today
is reported the day closes on "Done for today". What must hold: the chance is shown
only after the answer (D55's calibration must not be anchored), a recall the model
gave less than even odds is the one moment celebrated, forgetting is information with
the day the topic comes back, a second pass on the same day adds nothing, and the
day's close names the next session and asks for nothing more.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cps import service

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
START = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)
PARIS = ZoneInfo("Europe/Paris")


@pytest.fixture(scope="module")
def plan_and_test():
    """A plan, and its first self-test."""
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 28),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[service.TaskSpec("Stats report", "2026-10-16 18:00", 4.0, "Advanced Statistics")],
        preferences=service.DEFAULT_PREFERENCES,
        now=START,
    )
    plan = service.PlanReport.from_dict(sub.plan)
    test = next(s for s in plan.sessions if s.kind in ("review", "first review"))
    return sub, test


def _after(test, minutes=1):
    return datetime.fromisoformat(test.end) + timedelta(minutes=minutes)


def test_a_self_test_carries_the_chance_the_days_added_and_when_it_comes_back(plan_and_test):
    sub, test = plan_and_test
    plan = service.PlanReport.from_dict(sub.plan)
    gains = service.session_gains(plan)
    gain = gains[service.session_id(test)]
    assert set(gain) == {"without", "with", "chance", "same_day", "added", "holds", "next"}
    assert gain["chance"] % 5 == 0 and 0 <= gain["chance"] <= 100  # rounded to 5, as D24 rounds
    assert gain["added"] > 0 and not gain["same_day"]
    # It then holds 90 % until the day the plan brings it back: the planner's own rule.
    assert gain["holds"] == datetime.fromisoformat(gain["next"]).astimezone(PARIS).date()
    task = next(s for s in plan.sessions if s.kind == "task")
    assert service.session_id(task) not in gains  # a task has no memory to bank


def test_the_chance_shows_only_after_the_answer(plan_and_test):
    sub, test = plan_and_test
    sid = service.session_id(test)
    before = service.session_card(sub, sid, _after(test))
    assert before["memory"] is None and before["gain"]["chance"]  # known, not yet said
    answered = service.report_session(sub, sid, "most", now=_after(test))
    card = service.session_card(answered, sid, _after(test))
    assert card["memory"]["verdict"] == "forecast" and card["memory"]["chance"] == before["gain"]["chance"]
    assert card["memory"]["added"] >= 1 and card["memory"]["holds"] and card["memory"]["next"]


@pytest.mark.parametrize(
    ("answer", "verdict"),
    [("forgot", "forgot"), ("some", "forecast"), ("all", "forecast"), ("skipped", None)],
)
def test_each_answer_has_its_verdict(plan_and_test, answer, verdict):
    sub, test = plan_and_test
    sid = service.session_id(test)
    answered = service.report_session(sub, sid, answer, now=_after(test))
    memory = service.session_card(answered, sid, _after(test))["memory"]
    assert (memory or {}).get("verdict") == verdict
    if verdict == "forgot":
        assert memory["added"] == 0 and memory["next"]  # a loss is never counted on the card; it comes back


def test_the_verdicts_against_the_odds_and_on_the_same_day():
    now = datetime(2026, 10, 3, 9, 0, tzinfo=PARIS)
    gain = {
        "chance": 40,
        "same_day": False,
        "added": 11.6,
        "holds": date(2026, 10, 20),
        "next": "2026-10-20T08:00+02:00",
    }
    beaten = service._memory_words(gain, "most", PARIS, now)
    assert beaten == {
        "verdict": "beaten",
        "chance": 40,
        "added": 12,
        "holds": "Tue 20 Oct",
        "next": "Tue 20 Oct 08:00",
    }
    assert service._memory_words({**gain, "chance": 50}, "most", PARIS, now)["verdict"] == "forecast"
    same_day = service._memory_words({**gain, "same_day": True, "added": 0.3}, "all", PARIS, now)
    assert same_day["verdict"] == "same_day" and same_day["added"] == 0
    assert service._memory_words({**gain, "next": None}, "forgot", PARIS, now)["next"] is None
    assert service._memory_words(gain, None, PARIS, now) is None  # not answered yet
    assert service._memory_words(gain, "done", PARIS, now) is None  # not a self-test's answer
    assert service._memory_words({"without": 50, "with": 65}, "most", PARIS, now) is None


def test_the_day_closes_once_every_session_planned_today_is_reported(plan_and_test):
    sub, test = plan_and_test
    sid = service.session_id(test)
    open_day = service.today_view(sub, _after(test))
    assert open_day["done"] is None and open_day["banked"] is None and open_day["unreported"]
    answered = service.report_session(sub, sid, "most", now=_after(test))
    closed = service.today_view(answered, _after(test))
    assert closed["done"] == {"kept": 1, "of": 1, "added": closed["banked"]["memory"]["added"]}
    assert closed["banked"]["id"] == sid and closed["banked"]["memory"]["verdict"] == "forecast"
    assert closed["next"]  # the next session is named on the card
    # The result stays three hours, then Today is quiet again; the close stays.
    later = service.today_view(answered, _after(test, minutes=200))
    assert later["banked"] is None and later["done"] is not None
    skipped = service.today_view(service.report_session(sub, sid, "skipped", now=_after(test)), _after(test))
    assert skipped["done"] is None  # nothing kept: nothing to close on


def test_a_day_with_nothing_planned_has_no_close(plan_and_test):
    sub, _test = plan_and_test
    plan = service.PlanReport.from_dict(sub.plan)
    busy = {datetime.fromisoformat(s.start).astimezone(PARIS).date() for s in plan.sessions}
    free = next(d for n in range(60) if (d := date(2026, 9, 29) + timedelta(days=n)) not in busy)
    evening = datetime.combine(free, datetime.min.time(), tzinfo=PARIS).replace(hour=20)
    quiet = service.today_view(sub, evening)
    assert quiet["done"] is None


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
    """The settings form as a browser would submit it untouched (as test_web does)."""
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


def test_today_shows_the_answer_against_the_forecast_then_done_for_today(web, monkeypatch):
    files = {"file": ("t.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
    token = (
        web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        .headers["location"]
        .split("/")[2]
    )
    form = _form(web.get(f"/p/{token}/settings?new=1").text)
    assert web.post(f"/p/{token}/settings", data=form, follow_redirects=False).status_code == 303
    # A later day, not the first run (D107): what any day shows.
    sub = service.update_subscription(web.root, token, service.finish_first_run)
    plan = service.PlanReport.from_dict(sub.plan)
    test = next(s for s in plan.sessions if s.kind in ("review", "first review"))
    sid = service.session_id(test)
    day = datetime.fromisoformat(test.start).astimezone(PARIS).date()
    web.clock.now = _after(test)
    asked = unescape(web.get(f"/s/{token}/{sid}").text)
    assert "Answer first: the model's forecast shows after." in asked and "Recalled, as" not in asked
    web.post(f"/s/{token}/{sid}", data={"outcome": "most"}, follow_redirects=False)
    panel = unescape(web.get(f"/p/{token}/panel").text)
    assert "Just answered" in panel and "Recalled, as the model forecast (" in panel
    assert "memory day" in panel and "it now holds until" in panel and "A model's estimate." in panel
    shown = panel.split("Just answered")[1].split("</div>")[0]
    assert "data-celebrate" not in shown  # as forecast: no confetti
    own = unescape(web.get(f"/s/{token}/{sid}").text)
    assert "Recalled, as the model forecast (" in own and "Answer first" not in own
    # The day's other sessions reported too, after the last of them, each from Today's
    # own list (the plan is made again after each report, so ids change): the day closes.
    plan = service.PlanReport.from_dict(service.load_subscription(web.root, token).plan)
    begun = plan.history + plan.sessions  # a session that has started is already in the history
    todays = [s for s in begun if datetime.fromisoformat(s.start).astimezone(PARIS).date() == day]
    web.clock.now = _after(max(todays, key=lambda s: s.end))
    monkeypatch.setattr(service, "BEATEN_BELOW", 101)  # as if the model had given the next one no chance
    for _ in range(8):
        view = service.today_view(service.load_subscription(web.root, token), web.clock.now)
        if not view["unreported"]:
            break
        card = view["unreported"][0]
        word = "most" if card["self_test"] else "done"
        assert (
            web.post(f"/s/{token}/{card['id']}", data={"outcome": word}, follow_redirects=False).status_code
            == 303
        )
    closed = unescape(web.get(f"/p/{token}/panel").text)
    assert "Done for today" in closed and re.search(r"\d sessions? kept", closed) and "Next: " in closed
    assert "Up next" not in closed and "How did it go?" not in closed
    # The latest answer, against the odds: the one celebrated, once (D30).
    latest = closed.split("Just answered")[1]
    assert "Against the odds" in latest and "You recalled this at a" in latest
    assert re.search(r'data-celebrate="beaten-[0-9a-f]+"', latest)


def test_the_numbers_behind_the_line_a_second_pass_the_same_day_adds_almost_nothing():
    # DECISIONS.md D125: under FSRS-6 with the default weights, a second correct pass on
    # the same day multiplies how long a topic lasts by 1.10 at 12 hours, 1.05 at a day
    # and 1.00 from 2.12 days on; a review 3 days later of a topic lasting 3 days takes
    # it to 11.1 days.
    from cps import memory

    w = memory.DEFAULT_WEIGHTS_6
    same_day = {s: round(memory._short_term6(s, memory.Grade.GOOD, w) / s, 2) for s in (0.5, 1.0, 2.12, 7.0)}
    assert same_day == {0.5: 1.10, 1.0: 1.05, 2.12: 1.0, 7.0: 1.0}
    recall = memory.retrievability6(3.0, 3.0, w)
    assert round(recall, 2) == 0.9
    assert round(memory._recall6(3.0, 5.0, recall, memory.Grade.GOOD, w), 1) == 11.1
