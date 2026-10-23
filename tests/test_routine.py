"""
The questions about the week (DECISIONS.md D42): the trip to university, made from
each day's classes, and sport, a job or anything else every week as busy time, so
that nothing is planned then. Asked once while setting up, changed later in
Settings and on the calendar.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cps import service
from cps.calendar_io import BusyEvent

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SEMESTER = EXAMPLES / "sample-semester.ics"
PARIS = ZoneInfo("Europe/Paris")
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, the semester's first Tuesday


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=PARIS)


def test_the_trip_is_before_the_first_class_and_after_the_last():
    campus = [
        BusyEvent(_at(28, 9), _at(28, 11), "Algebra"),
        BusyEvent(_at(28, 14), _at(28, 16), "Physics"),  # the gap stays free: study on campus
        BusyEvent(_at(29, 10), _at(29, 12), "Algebra"),
        BusyEvent(_at(30, 0), _at(30, 0) + timedelta(days=1), "Holiday"),  # all day: not on campus
    ]
    trips = service.commute_events(campus, 45, PARIS, "Commute")
    assert [(t.start, t.end) for t in trips] == [
        (_at(28, 8, 15), _at(28, 9)),
        (_at(28, 16), _at(28, 16, 45)),
        (_at(29, 9, 15), _at(29, 10)),
        (_at(29, 12), _at(29, 12, 45)),
    ]
    assert service.commute_events(campus, 0, PARIS, "Commute") == []


def test_the_trip_is_busy_time_but_not_a_course_or_an_exam():
    ics = SEMESTER.read_bytes()
    without = service.analyse_calendar(ics, start=date(2026, 9, 28), tz="Europe/Paris", days=14)
    with_trip = service.analyse_calendar(
        ics, start=date(2026, 9, 28), tz="Europe/Paris", days=14, commute_minutes=30
    )
    class_days = {e.start[:10] for e in without.events}
    # Two trips of half an hour on each day with classes, none on the others.
    assert with_trip.busy_hours == pytest.approx(without.busy_hours + len(class_days))
    assert {e.start[:10] for e in with_trip.events if e.source == "commute"} == class_days
    assert with_trip.assessments == without.assessments
    assert service.course_colours(with_trip) == service.course_colours(without)
    week = service.calendar_week(with_trip, 0)
    trips = [i for i in week["items"] if i["kind"] == "commute"]
    assert trips and all(i["label"] == "Commute" and i["course"] is None for i in trips)


def test_classes_drawn_by_hand_have_a_trip_and_training_does_not():
    rows = [
        {"label": "Algebra", "kind": "class", "weekday": "Mon", "start": "09:00", "end": "11:00"},
        {"label": "Football", "kind": "training", "weekday": "Tue", "start": "18:00", "end": "20:00"},
    ]
    report = service.analyse_calendar(
        None, start=date(2026, 9, 28), tz="Europe/Paris", days=7, busy_rows=rows, commute_minutes=20
    )
    trips = [(e.start[11:16], e.end[11:16]) for e in report.events if e.source == "commute"]
    assert trips == [("08:40", "09:00"), ("11:00", "11:20")]  # Monday only


def test_a_trip_longer_than_three_hours_is_refused():
    with pytest.raises(service.InvalidInput, match="trip to university"):
        service.analyse_calendar(None, start=date(2026, 9, 28), commute_minutes=181)
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)
    with pytest.raises(service.InvalidInput, match="trip to university"):
        service.revise_subscription(sub, commute_minutes=-5, now=MORNING)


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(PARIS)


def test_answers_become_weekly_busy_times_and_the_plan_keeps_clear_of_them():
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)
    sub = service.answer_routine(
        sub,
        commute_minutes=30,
        answers=[
            {
                "kind": "training",
                "label": "Football",
                "days": ["Tue", "Thu"],
                "start": "18:00",
                "end": "20:00",
            },
            {"kind": "work", "label": "", "days": ["Sat"], "start": "10:00", "end": "16:00"},
            {"kind": "other", "label": "", "days": [], "start": "", "end": ""},  # not answered
        ],
        now=MORNING,
    )
    assert sub.options["commute_minutes"] == 30
    assert [(r["label"], r["kind"], r["weekday"]) for r in sub.busy_rows] == [
        ("Football", "training", 1),
        ("Football", "training", 3),
        ("Work", "work", 5),  # no name given: the kind's
    ]
    plan = service.PlanReport.from_dict(sub.plan)
    assert plan.sessions
    busy = [(_local(e.start), _local(e.end)) for e in plan.events if e.source in ("typed", "commute")]
    assert any(e.source == "commute" for e in plan.events)
    for s in plan.sessions:
        begin, end = _local(s.start), _local(s.end)
        assert not any(lo < end and begin < hi for lo, hi in busy), s.start


def test_half_an_answer_says_what_is_missing():
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)
    with pytest.raises(service.InvalidInput, match="Football: choose the days"):
        service.answer_routine(
            sub,
            commute_minutes=0,
            answers=[{"kind": "training", "label": "Football", "days": [], "start": "18:00", "end": "20:00"}],
            now=MORNING,
        )
    with pytest.raises(service.InvalidInput, match="Work: give the hours"):
        service.answer_routine(
            sub, commute_minutes=0, answers=[{"kind": "work", "days": ["Mon"], "start": "09:00"}], now=MORNING
        )


def test_a_plan_made_before_the_question_existed_still_changes():
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)
    options = dict(sub.options)
    options.pop("commute_minutes", None)
    old = service.Subscription.from_dict({**sub.to_dict(), "options": options})
    assert service.routine_view(old)["commute"] == 0
    fresh = service.revise_subscription(old, commute_minutes=15, now=MORNING)
    assert fresh.options["commute_minutes"] == 15 and fresh.error is None


def test_the_trip_and_the_training_stay_for_the_next_semester():
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)
    sub = service.answer_routine(
        sub,
        commute_minutes=25,
        answers=[{"kind": "training", "label": "Swim", "days": ["Wed"], "start": "07:00", "end": "08:00"}],
        now=MORNING,
    )
    later = service.start_next_semester(sub, draw=True, now=MORNING + timedelta(days=150))
    assert later.options["commute_minutes"] == 25
    assert [r["label"] for r in later.busy_rows] == ["Swim"]


# --------------------------------------------------------------------------- #
# The pages
# --------------------------------------------------------------------------- #

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cps.web import Config, create_app  # noqa: E402


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=Clock(MORNING), background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        client.path = tmp_path
        yield client


def _start(web, lang: str = "en") -> str:
    files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
    answer = web.post(
        "/start", data={"tz": "Europe/Paris", "lang": lang}, files=files, follow_redirects=False
    )
    return answer.headers["location"].split("/")[2]


def _names(html: str) -> set[str]:
    return set(re.findall(r'name="([^"]+)"', html))


def _form(html: str) -> dict:
    """A form as a browser would submit it untouched (as in test_web)."""
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


def test_the_questions_about_the_week_come_after_setting_up_then_show_it(web):
    # Not a step of setting up since D98: Today offers them the next day, and Spark's
    # sheet any day; answered, the week shows what changed.
    token = _start(web)
    page = unescape(web.get(f"/p/{token}/routine").text)
    assert "How long is your trip to university?" in page
    assert "Do you play a sport or train?" in page and "Do you have a job?" in page
    assert 'name="new" value="1"' not in page and 'class="steps"' not in page
    assert {"commute", "x0_kind", "x0_days", "x0_start", "x5_end"} <= _names(page)
    answer = web.post(
        f"/p/{token}/routine",
        data={
            "commute": "30",
            "x0_kind": "training",
            "x0_label": "Football",
            "x0_days": ["Tue", "Thu"],
            "x0_start": "18:00",
            "x0_end": "20:00",
            "x2_kind": "work",
            "x2_label": "",
            "x2_start": "",
            "x2_end": "",
        },
        follow_redirects=False,
    )
    assert answer.status_code == 303 and answer.headers["location"] == f"/p/{token}/week?saved=1"
    stored = service.load_subscription(web.path, token)
    assert stored is not None and stored.options["commute_minutes"] == 30
    assert [(r["label"], r["weekday"]) for r in stored.busy_rows] == [("Football", 1), ("Football", 3)]
    view = web.get(f"/p/{token}/calendar.json?start=2026-09-29&days=7").json()
    assert any(i["kind"] == "commute" for i in view["items"])
    assert {a["label"] for a in view["activities"]} == {"Football"}  # the trip is not a drawn block


def test_a_half_answer_is_shown_again_as_typed(web):
    token = _start(web)
    answer = web.post(
        f"/p/{token}/routine",
        data={"new": "1", "commute": "45", "x0_kind": "training", "x0_label": "Rugby", "x0_days": ["Wed"]},
    )
    assert answer.status_code == 400
    page = unescape(answer.text)
    assert "Rugby: give the hours" in page
    assert 'value="Rugby"' in page and 'value="Wed" checked' in page
    assert 'value="45" checked' in page
    stored = service.load_subscription(web.path, token)
    assert stored is not None and stored.busy_rows == () and not stored.options.get("commute_minutes")


def test_settings_change_the_trip_and_setting_up_leaves_it_to_the_questions(web):
    token = _start(web)
    assert 'name="commute"' not in web.get(f"/p/{token}/settings?new=1").text
    service.update_subscription(
        web.path, token, lambda s: service.revise_subscription(s, commute_minutes=20, now=MORNING)
    )
    page = web.get(f"/p/{token}/settings").text
    assert '<option value="20" selected>' in page
    assert f'href="/p/{token}/routine"' in page
    form = {**_form(page), "commute": "60"}
    saved = web.post(f"/p/{token}/settings", data=form, follow_redirects=False)
    assert saved.status_code == 303, saved.text
    stored = service.load_subscription(web.path, token)
    assert stored is not None and stored.options["commute_minutes"] == 60


def test_the_questions_are_asked_in_the_student_s_language(web):
    token = _start(web, lang="fr")
    page = unescape(web.get(f"/p/{token}/routine?new=1").text)
    assert "Combien de temps dure ton trajet vers la fac ?" in page
    assert "Tu fais du sport ou tu t'entraînes ?" in page
    token = _start(web, lang="it")
    page = unescape(web.get(f"/p/{token}/routine?new=1").text)
    assert "Quanto dura il tuo tragitto verso l'università?" in page
