"""
Changing a class of the timetable (DECISIONS.md D60): skipping it once or every week
frees its time for the plan, while it stays on the calendar to be restored;
skipping it to study it on one's own adds a catch-up task as long as the class, due
before the course's next one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from cps import service

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SEMESTER = EXAMPLES / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, the semester's first Tuesday
# The sample semester's classes this test changes (Paris time):
STATS = ("2026-09-29T09:00+02:00", "Advanced Statistics, Grp: CM ., Salle: Salle 3")  # Tue 09:00-10:30
ALGEBRA = ("2026-10-01T16:00+02:00", "Algebra 3, Grp: TD ., Salle: Salle 2")  # Thu 16:00-17:30


@pytest.fixture(scope="module")
def plan_now():
    return service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)


def _sources(sub: service.Subscription, summary: str) -> dict[str, str]:
    plan = service.PlanReport.from_dict(sub.plan)
    return {e.start: e.source for e in plan.events if e.summary == summary}


def test_a_class_skipped_once_frees_its_time_and_stays_on_the_calendar(plan_now):
    at, summary = STATS
    week = dict(start=MORNING.date(), tz="Europe/Paris", days=7)
    before = service.analyse_calendar(SEMESTER.read_bytes(), **week)
    change = {"at": at, "summary": summary, "action": "skip"}
    after = service.analyse_calendar(SEMESTER.read_bytes(), **week, lecture_changes=[change])
    # The class was inside the study window (08:00 to 22:00) and overlapped nothing.
    assert before.busy_hours - after.busy_hours == pytest.approx(1.5)
    assert [e.source for e in after.events if e.start == at and e.summary == summary] == ["skipped"]

    skipped = service.change_lecture(plan_now, at, summary, "skip", now=MORNING)
    sources = _sources(skipped, summary)
    assert sources[at] == "skipped"
    assert all(source == "calendar" for start, source in sources.items() if start != at)
    view = service.calendar_view(skipped, MORNING.date(), 1, MORNING)
    item = next(i for i in view["items"] if i.get("at") == at)
    assert item["kind"] == "skipped" and not item["weekly"] and item["catch_up"] is None
    assert item["color"] is not None  # the course keeps its colour
    # It no longer stands in the way of a session moved onto its time.
    session = next(s for s in service.PlanReport.from_dict(skipped.plan).sessions if s.kind != "task")
    moved = service.move_session(skipped, service.session_id(session), "2026-09-29T09:00", now=MORNING)
    assert any(
        s.start.startswith("2026-09-29T09:00") for s in service.PlanReport.from_dict(moved.plan).sessions
    )
    # Restored, it is busy again; and the change outlives a new reading of the timetable.
    assert (
        _sources(service.change_lecture(skipped, at, summary, "restore", now=MORNING), summary)[at]
        == "calendar"
    )
    again = service.refresh_subscription(skipped, now=MORNING + timedelta(hours=1), reread=False)
    assert _sources(again, summary)[at] == "skipped"


def test_a_class_skipped_every_week_is_skipped_from_that_week_on(plan_now):
    at, summary = ALGEBRA
    later = MORNING + timedelta(days=1)
    first = service.change_lecture(plan_now, at, summary, "skip", now=MORNING)
    with pytest.raises(service.InvalidInput, match="already skipped"):
        service.change_lecture(first, at, summary, "skip", every_week=True, now=MORNING)
    weekly = service.change_lecture(plan_now, at, summary, "skip", every_week=True, now=later)
    sources = _sources(weekly, summary)
    thursdays = [start for start in sources if datetime.fromisoformat(start).weekday() == 3]
    assert len(thursdays) > 5 and all(sources[s] == "skipped" for s in thursdays if s >= at)
    assert weekly.options["lecture_changes"] == [
        {"weekday": 3, "time": "16:00", "from": "2026-10-01", "summary": summary, "action": "skip"}
    ]
    agenda = service.agenda(weekly, 0, later)
    entry = next(e for d in agenda["days"] for e in d["entries"] if e.get("at") == at)
    assert entry["kind"] == "skipped" and entry["weekly"]
    # Restoring one of them restores the rule, every week.
    one = next(s for s in thursdays if s > at)
    restored = service.change_lecture(weekly, one, summary, "restore", now=later)
    assert set(_sources(restored, summary).values()) == {"calendar"}
    with pytest.raises(service.InvalidInput, match="not caught up by itself"):
        service.change_lecture(plan_now, at, summary, "self", every_week=True, now=later)


def test_studying_it_on_ones_own_adds_a_catch_up_task_due_before_the_next_class(plan_now):
    at, summary = STATS
    caught = service.change_lecture(plan_now, at, summary, "self", now=MORNING)
    [task] = [t for t in caught.options["tasks"] if t["name"].startswith("Catch up")]
    assert task["name"] == "Catch up: Advanced Statistics, class of Tue 29 Sep"
    assert task["hours"] == 1.5 and task["course"] == "Advanced Statistics"
    assert task["due"] == "2026-10-01 11:00"  # the course's next class, Thursday's TD
    [change] = caught.options["lecture_changes"]
    assert change == {"at": at, "summary": summary, "action": "self", "task": task["name"]}
    plan = service.PlanReport.from_dict(caught.plan)
    # Planned at once; here into the class's own time, which the skip freed.
    [catch_up] = [s for s in plan.sessions if s.kind == "task" and s.title == task["name"]]
    assert catch_up.start == "2026-09-29T09:30+02:00"
    view = service.calendar_view(caught, MORNING.date(), 1, MORNING)
    assert next(i for i in view["items"] if i.get("at") == at)["catch_up"] == task["name"]
    # Restored, the catch-up goes with it, unless it was done.
    restored = service.change_lecture(caught, at, summary, "restore", now=MORNING)
    assert not [t for t in restored.options.get("tasks", ()) if t["name"] == task["name"]]
    done = service.set_task_done(caught, task["name"], now=MORNING)
    kept = service.change_lecture(done, at, summary, "restore", now=MORNING)
    assert [t["name"] for t in kept.options["tasks"]] == [task["name"]]


def test_a_day_whose_classes_are_skipped_has_no_trip_and_no_warm_up(plan_now):
    """Tuesday 29 September has two classes; skipped, the day has no trip to
    campus (D42) and Today offers no warm-up before them (D51)."""
    tuesday = [
        {"at": "2026-09-29T09:00+02:00", "summary": STATS[1], "action": "skip"},
        {
            "at": "2026-09-29T14:00+02:00",
            "summary": "Analysis 3, Grp: CM ., Salle: Salle 4",
            "action": "skip",
        },
    ]
    week = dict(start=MORNING.date(), tz="Europe/Paris", days=7, commute_minutes=30)
    trips = [
        e for e in service.analyse_calendar(SEMESTER.read_bytes(), **week).events if e.source == "commute"
    ]
    kept = service.analyse_calendar(SEMESTER.read_bytes(), **week, lecture_changes=tuesday).events
    assert any(e.start.startswith("2026-09-29") for e in trips)
    assert not [e for e in kept if e.source == "commute" and e.start.startswith("2026-09-29")]
    warm = service.revise_subscription(plan_now, warm_up=True, now=MORNING)
    assert [w["course"] for w in service.warm_ups(warm, MORNING)] == ["Advanced Statistics", "Analysis 3"]
    skipped = service.change_lecture(warm, STATS[0], STATS[1], "skip", now=MORNING)
    assert [w["course"] for w in service.warm_ups(skipped, MORNING)] == ["Analysis 3"]


def test_what_cannot_be_changed_says_why(plan_now):
    at, summary = STATS
    for args, words in (
        ((at, summary, "cancel"), "choose to skip the class"),
        ((at, "Advanced Statistics", "skip"), "not in your timetable"),
        (("2026-09-29T09:15+02:00", summary, "skip"), "not in your timetable"),
        (("nonsense", summary, "skip"), "not in your timetable"),
        ((at, summary, "restore"), "not skipped"),
    ):
        with pytest.raises(service.InvalidInput, match=words):
            service.change_lecture(plan_now, *args, now=MORNING)
    with pytest.raises(service.InvalidInput, match="already begun"):
        service.change_lecture(plan_now, at, summary, "skip", now=MORNING + timedelta(hours=2))
    exam = service.PlanReport.from_dict(plan_now.plan).subjects[0]
    event = next(e for e in service.PlanReport.from_dict(plan_now.plan).events if e.start == exam.exam)
    with pytest.raises(service.InvalidInput, match="an exam is not skipped here"):
        service.change_lecture(plan_now, event.start, event.summary, "skip", now=MORNING)


pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cps.web import Config, create_app  # noqa: E402


@pytest.mark.parametrize(
    ("lang", "words"), [("en", "Can't go?"), ("fr", "Tu ne peux pas y aller"), ("it", "Non puoi andarci")]
)
def test_the_calendar_and_the_weeks_list_skip_a_class(tmp_path, lang, words):
    config = Config(store=tmp_path, clock=lambda: MORNING, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
        start = web.post(
            "/start", data={"tz": "Europe/Paris", "lang": lang}, files=files, follow_redirects=False
        )
        token = start.headers["location"].split("/")[2]
        at, summary = STATS
        ref = service.lecture_ref(summary)  # pages name a class without its raw title
        # From the calendar: the answer is the calendar after the change.
        answer = web.post(
            f"/p/{token}/lecture.json?start=2026-09-29&days=1",
            json={"at": at, "ref": ref, "action": "skip", "every_week": False},
        )
        assert answer.status_code == 200
        assert next(i for i in answer.json()["items"] if i.get("at") == at)["kind"] == "skipped"
        assert "Grp:" not in answer.text
        refused = web.post(f"/p/{token}/lecture.json", json={"at": at, "ref": ref, "action": "skip"})
        assert refused.status_code == 400
        # From the week's list, without the calendar's script.
        page = unescape(web.get(f"/p/{token}/agenda").text)
        assert words in page and 'value="skip-weekly"' in page and 'value="restore"' in page
        assert "Grp:" not in page and f'name="ref" value="{ref}"' in page
        back = web.post(
            f"/p/{token}/lecture",
            data={"at": at, "ref": ref, "action": "restore", "w": "0"},
            follow_redirects=False,
        )
        assert back.status_code == 303 and back.headers["location"] == f"/p/{token}/agenda?w=0"
        weekly = web.post(
            f"/p/{token}/lecture",
            data={
                "at": ALGEBRA[0],
                "ref": service.lecture_ref(ALGEBRA[1]),
                "action": "skip-weekly",
                "w": "0",
            },
            follow_redirects=False,
        )
        assert weekly.status_code == 303
        stored = service.load_subscription(tmp_path, token)
        assert [c.get("weekday") for c in stored.options["lecture_changes"]] == [3]
        assert web.post(f"/p/{token}/lecture", data={"at": at, "ref": ref, "action": "x"}).status_code == 400


# --------------------------------------------------------------------------- #
# A class that is a test (D61): a pop-up quiz, a mock exam, a partial exam
# --------------------------------------------------------------------------- #

TEST_CLASS = ("2026-11-05T16:00+01:00", "Algebra 3, Grp: TD ., Salle: Salle 2")  # Thu 16:00-17:30


def _practice(sub: service.Subscription) -> dict[str, list[str]]:
    plan = service.PlanReport.from_dict(sub.plan)
    out: dict[str, list[str]] = {}
    for s in plan.sessions:
        if s.kind == "practice" and s.title.startswith("Exam practice: Algebra 3"):
            out.setdefault(s.title, []).append(s.start)
    return out


def test_a_quiz_stays_busy_and_brings_an_hour_to_prepare(plan_now):
    at, summary = TEST_CLASS
    week = dict(start=date(2026, 11, 2), tz="Europe/Paris", days=7)
    before = service.analyse_calendar(SEMESTER.read_bytes(), **week)
    change = {"at": at, "summary": summary, "action": "quiz"}
    after = service.analyse_calendar(SEMESTER.read_bytes(), **week, lecture_changes=[change])
    assert after.busy_hours == before.busy_hours  # the class still happens
    assert [e.source for e in after.events if e.start == at] == ["quiz"]

    quiz = service.change_lecture(plan_now, at, summary, "quiz", now=MORNING)
    [task] = [t for t in quiz.options["tasks"] if t["name"].startswith("Prepare")]
    assert task == {
        "name": "Prepare for the quiz: Algebra 3, Thu 5 Nov",
        "due": "2026-11-05 16:00",
        "hours": 1.0,
        "course": "Algebra 3",
    }
    view = service.calendar_view(quiz, date(2026, 11, 5), 1, MORNING)
    item = next(i for i in view["items"] if i.get("at") == at)
    assert item["kind"] == "quiz" and item["label"] == "Quiz: Algebra 3" and item["catch_up"] == task["name"]
    with pytest.raises(service.InvalidInput, match="already marked as a test"):
        service.change_lecture(quiz, at, summary, "mock", now=MORNING)
    with pytest.raises(service.InvalidInput, match="a test is one class"):
        service.change_lecture(plan_now, at, summary, "quiz", every_week=True, now=MORNING)
    restored = service.change_lecture(quiz, at, summary, "restore", now=MORNING)
    assert not [t for t in restored.options.get("tasks", ()) if t["name"] == task["name"]]
    assert {e.source for e in service.PlanReport.from_dict(restored.plan).events if e.start == at} == {
        "calendar"
    }


def test_a_mock_exam_in_class_counts_as_exam_practice(plan_now):
    at, summary = TEST_CLASS
    before = _practice(plan_now)["Exam practice: Algebra 3"]
    mock = service.change_lecture(plan_now, at, summary, "mock", now=MORNING)
    after = _practice(mock)["Exam practice: Algebra 3"]
    assert len(after) == len(before) - 1  # the mock's 90 minutes are one block of practice
    assert service.PlanReport.from_dict(mock.plan).settings.preferences["mocks"] == [
        {"course": "Algebra 3", "day": pytest.approx(37.67, abs=0.01), "hours": 1.5}
    ]
    view = service.calendar_view(mock, date(2026, 11, 5), 1, MORNING)
    assert next(i for i in view["items"] if i.get("at") == at)["label"] == "Mock exam: Algebra 3"


def test_a_partial_exam_is_practised_for_and_may_take_its_material_off_the_final(plan_now):
    at, summary = TEST_CLASS
    key = "Algebra 3, partial exam of Thu 5 Nov"

    def reviews(sub: service.Subscription, topic: str) -> list[str]:
        plan = service.PlanReport.from_dict(sub.plan)
        return [
            s.start[:10] for s in plan.sessions if s.kind in ("review", "first review") and s.title == topic
        ]

    kept = service.change_lecture(plan_now, at, summary, "partial", now=MORNING)
    taken = service.change_lecture(plan_now, at, summary, "partial", exempt=True, now=MORNING)
    for sub in (kept, taken):
        practice = _practice(sub)
        assert practice[f"Exam practice: {key}"] and all(
            day < at for day in practice[f"Exam practice: {key}"]
        )
        assert practice["Exam practice: Algebra 3"]  # and the final still is
    # Taken off the final, the weeks taught before it are examined there and no
    # longer reviewed after it; kept for the final, they still are.
    exams = {
        s["topic"]: s["exam"][:10]
        for s in service.PlanReport.from_dict(taken.plan).specs
        if s.get("subject") == "Algebra 3"
    }
    # (The partial itself teaches nothing: the week it falls in is examined there.)
    assert exams["taught before 29 Sep"] == exams["week of 28 Sep"] == exams["week of 02 Nov"] == "2026-11-05"
    assert exams["week of 09 Nov"] == exams["week of 14 Dec"] == "2027-01-26"
    first_week = "Algebra 3 · week of 28 Sep"
    assert max(reviews(taken, first_week)) < "2026-11-05" < max(reviews(kept, first_week))
    view = service.calendar_view(taken, date(2026, 11, 5), 1, MORNING)
    item = next(i for i in view["items"] if i.get("at") == at)
    assert item["kind"] == "partial" and item["exempt"] and item["label"] == "Partial exam: Algebra 3"
    # Its practice wears the course's colour.
    week = service.calendar_view(taken, date(2026, 10, 26), 7, MORNING)
    algebra = next(i["color"] for i in week["items"] if i.get("course") == "Algebra 3")
    assert {i["color"] for i in week["items"] if i.get("course") == key} == {algebra}


def test_the_calendar_and_the_weeks_list_mark_a_test(tmp_path):
    config = Config(store=tmp_path, clock=lambda: MORNING, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
        start = web.post(
            "/start", data={"tz": "Europe/Paris", "lang": "it"}, files=files, follow_redirects=False
        )
        token = start.headers["location"].split("/")[2]
        at, summary = TEST_CLASS
        ref = service.lecture_ref(summary)
        page = unescape(web.get(f"/p/{token}/agenda?w=5").text)
        assert "È una prova?" in page and 'value="partial"' in page and 'name="exempt"' in page
        marked = web.post(
            f"/p/{token}/lecture",
            data={"at": at, "ref": ref, "action": "partial", "exempt": "1", "w": "5"},
            follow_redirects=False,
        )
        assert marked.status_code == 303
        stored = service.load_subscription(tmp_path, token)
        assert stored.options["lecture_changes"] == [
            {"at": at, "summary": summary, "action": "partial", "exempt": True}
        ]
        page = unescape(web.get(f"/p/{token}/agenda?w=5").text)
        assert "Prova intermedia: Algebra 3" in page and "È una lezione normale" in page
        back = web.post(f"/p/{token}/lecture", data={"at": at, "ref": ref, "action": "restore", "w": "5"})
        assert back.status_code == 200  # followed to the list
        quiz = web.post(
            f"/p/{token}/lecture.json?start=2026-11-05&days=1", json={"at": at, "ref": ref, "action": "quiz"}
        )
        item = next(i for i in quiz.json()["items"] if i.get("at") == at)
        assert item["kind"] == "quiz" and item["label"] == "Quiz: Algebra 3"
