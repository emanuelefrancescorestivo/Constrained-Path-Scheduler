"""
Holidays (DECISIONS.md D184): a week or more without classes between two days of
classes is asked about once on Today, from two weeks before it begins: rest those
days, or keep studying. Found on the first-year timetable (D182), whose plan had
sessions every day from 19 December to 5 January, Christmas Day included.

What must hold: the holidays are the timetable's (All Saints' and Christmas on the
first year's, All Saints' alone on the second year's, whose gym goes on through it);
the question comes two weeks ahead and not before, once, and not after the holiday;
"Rest" leaves the holiday's days free and the streak waiting, as a pause does (D126),
from today when it is under way; a pause set or ended before it leaves it as it is;
"Study then" plans its days again; Today asks it before counting use, and the page
answers it in one tap.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cps import service

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
PARIS = ZoneInfo("Europe/Paris")
CHRISTMAS = (date(2026, 12, 19), date(2027, 1, 5))


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=PARIS)


@pytest.fixture(scope="module")
def sub() -> service.Subscription:
    return service.new_subscription(
        subjects=[
            service.SubjectSpec("Algebra 1", None, familiarity=2),
            service.SubjectSpec("Analysis 1", None, familiarity=2),
        ],
        start=date(2026, 9, 7),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-year1.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=datetime(2026, 9, 7, 6, 0, tzinfo=UTC),
    )


def _days(subscription: service.Subscription) -> set[date]:
    plan = service.PlanReport.from_dict(subscription.plan)
    return {datetime.fromisoformat(s.start).astimezone(PARIS).date() for s in plan.sessions}


def _in(days: set[date], holiday: tuple[date, date]) -> set[date]:
    return {d for d in days if holiday[0] <= d <= holiday[1]}


def test_a_timetable_s_holidays_are_its_weeks_without_classes(sub):
    plan = service.PlanReport.from_dict(sub.plan)
    assert plan.settings.preferences["holidays"] == [
        {"from": "2026-10-24", "to": "2026-11-01"},
        {"from": CHRISTMAS[0].isoformat(), "to": CHRISTMAS[1].isoformat()},
    ]
    # The second year's: All Saints' only, though its gym goes on that week; the weeks
    # between the last class and the exams are revision, not a holiday.
    second = service.analyse_calendar(
        (EXAMPLES / "sample-semester.ics").read_bytes(), start=date(2026, 9, 28), tz="Europe/Paris"
    )
    assert any(x.course == "Gym" and x.start.startswith("2026-10-29") for x in second.lectures)
    assert service.holidays_in(second, ["Algebra 3"]) == [(date(2026, 10, 24), date(2026, 11, 1))]


def test_christmas_is_asked_four_weeks_ahead_and_rest_leaves_it_free(sub):
    assert _in(_days(sub), CHRISTMAS)  # sessions planned through Christmas until asked
    assert service.holiday_ask(sub, _at(date(2026, 12, 4))) is None  # 15 days ahead: not yet
    ask = service.holiday_ask(sub, _at(date(2026, 12, 5)))
    assert ask == {
        "from": "2026-12-19",
        "to": "2027-01-05",
        "first": service.format_date(CHRISTMAS[0], "day_short"),
        "last": service.format_date(CHRISTMAS[1], "day_short"),
        "days": 18,
        "under_way": False,
    }
    now = _at(date(2026, 12, 7))
    rested = service.answer_holiday(sub, "2026-12-19", "rest", now=now)
    assert rested.options["holidays"] == {"2026-12-19": "rest"}
    assert rested.options["preferences"]["pauses"] == [
        {"from": "2026-12-19", "to": "2027-01-05", "holiday": "2026-12-19"}
    ]
    assert not _in(_days(rested), CHRISTMAS)
    assert _days(rested)  # the work goes on around it
    assert service.holiday_ask(rested, now) is None  # asked once
    ahead = service.progress_view(rested, now)["streak"]["ahead"]
    assert ahead["holiday"] and ahead["from"] == "2026-12-19"
    # During it, the streak waits, said as a holiday.
    during = service.progress_view(rested, _at(date(2026, 12, 24)))["streak"]
    assert during["pause"]["holiday"]
    until = service.format_date(CHRISTMAS[1], "short")
    assert during["line"] == f"Holidays until {until}: your streak waits."
    # A pause set and ended before it leaves the holiday as it is.
    paused = service.set_pause(rested, "today", now=now)
    assert {"from": "2026-12-19", "to": "2027-01-05", "holiday": "2026-12-19"} in paused.options[
        "preferences"
    ]["pauses"]
    resumed = service.end_pause(paused, now=now + timedelta(hours=1))
    assert resumed.options["preferences"]["pauses"] == rested.options["preferences"]["pauses"]
    # "Study then": its days are planned again, and nothing more is asked.
    studied = service.answer_holiday(resumed, "2026-12-19", "study", now=now)
    assert studied.options["preferences"]["pauses"] == []
    assert _in(_days(studied), CHRISTMAS)
    assert service.holiday_ask(studied, now) is None


def test_a_holiday_under_way_rests_from_today_and_none_is_asked_after_it(sub):
    today = date(2026, 12, 28)
    ask = service.holiday_ask(sub, _at(today))
    assert ask is not None and ask["under_way"]
    rested = service.answer_holiday(sub, "2026-12-19", "rest", now=_at(today))
    assert rested.options["preferences"]["pauses"] == [
        {"from": today.isoformat(), "to": "2027-01-05", "holiday": "2026-12-19"}
    ]
    # Resumed on 2 January: the rest ends the day before, and the days after are planned.
    back = service.end_pause(rested, now=_at(date(2027, 1, 2)))
    assert back.options["preferences"]["pauses"] == [
        {"from": today.isoformat(), "to": "2027-01-01", "holiday": "2026-12-19"}
    ]
    assert _in(_days(back), (date(2027, 1, 2), CHRISTMAS[1]))
    assert service.holiday_ask(sub, _at(date(2027, 1, 6))) is None  # over
    with pytest.raises(service.InvalidInput):
        service.answer_holiday(sub, "2026-12-20", "rest", now=_at(today))  # no holiday begins then
    with pytest.raises(service.InvalidInput):
        service.answer_holiday(sub, "2026-12-19", "sleep", now=_at(today))


def test_today_asks_before_counting_use():
    assert service.today_message(holiday={"from": "x"}, usage=True, fit=True) == "holiday"
    assert service.today_message(semester=True, holiday={"from": "x"}) == "semester"


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    class Clock:
        now = _at(date(2026, 12, 7))

    config = Config(
        store=tmp_path, clock=lambda: Clock.now, background=False, sweep_every=None, remind_every=None
    )
    with TestClient(create_app(config)) as client:
        client.root = tmp_path
        yield client


def test_today_s_card_rests_the_holiday_in_one_tap_and_progress_undoes_it(web, sub):
    service.save_subscription(web.root, sub)
    token = sub.token
    page = unescape(web.get(f"/p/{token}").text)
    assert 'action="/p/' + token + '/holiday"' in page
    assert "No classes from" in page and "Rest, or keep studying?" in page
    assert (
        web.post(
            f"/p/{token}/holiday", data={"from": "2026-12-19", "answer": "rest"}, follow_redirects=False
        ).headers["location"]
        == f"/p/{token}?saved=holiday-rest"
    )
    after = unescape(web.get(f"/p/{token}?saved=holiday-rest").text)
    assert "Rest well: nothing is planned on those days" in after
    assert "Rest, or keep studying?" not in after
    progress = unescape(web.get(f"/p/{token}/progress").text)
    assert "Holidays from" in progress and "Study then" in progress
    assert "Pause the streak" in progress  # a pause can still be set before it
    answer = {"from": "2026-12-19", "answer": "study", "back": "progress"}
    assert web.post(f"/p/{token}/holiday", data=answer, follow_redirects=False).status_code == 303
    assert "Holidays from" not in unescape(web.get(f"/p/{token}/progress").text)
    assert web.post(f"/p/{token}/holiday", data={"from": "2026-01-01", "answer": "rest"}).status_code == 400
