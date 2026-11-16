"""
The semester recap (DECISIONS.md D131): once every exam is behind, the semester in
numbers the app already keeps; shared only by the student's choice, through their
device; kept when the next semester starts, which starts clean. What must hold: no
recap while an exam or a deadline is ahead; its best streak is the one Progress shows;
the text to share carries the numbers and nothing that names the plan or the courses;
starting the next semester keeps it, at most `RECAPS_KEPT`, latest first.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from cps import service

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
START = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)


def _answer_until(sub: service.Subscription, before: date) -> service.Subscription:
    """Each self-test planned before `before` answered "Most", a minute after it ends."""
    done: set[str] = set()
    while True:
        plan = service.PlanReport.from_dict(sub.plan)
        todo = sorted(
            (
                s
                for s in (*plan.history, *plan.sessions)
                if s.kind in ("review", "first review")
                and not s.grade
                and datetime.fromisoformat(s.start).date() < before
                and service.session_id(s) not in done
            ),
            key=lambda s: s.start,
        )
        if not todo:
            return sub
        done.add(service.session_id(todo[0]))
        end = datetime.fromisoformat(todo[0].end) + timedelta(minutes=1)
        sub = service.report_session(sub, service.session_id(todo[0]), "most", now=end)


def _semester(tmp_path: Path) -> tuple[service.Subscription, datetime]:
    """A semester whose first three weeks of self-tests were answered, the rest
    followed as planned, two days after its last exam."""
    sub = service.new_subscription(
        subjects=[
            service.SubjectSpec("Algebra 3", None, familiarity=3),
            service.SubjectSpec("Advanced Statistics", None, familiarity=3),
        ],
        start=date(2026, 9, 28),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=START,
    )
    sub = _answer_until(service.finish_first_run(sub), date(2026, 10, 19))
    plan = service.PlanReport.from_dict(sub.plan)
    after = max(datetime.fromisoformat(s.exam) for s in plan.subjects) + timedelta(days=2)
    sub = service.refresh_subscription(sub, now=after, reread=False)
    service.save_subscription(tmp_path, sub)
    return service.load_subscription(tmp_path, sub.token), after


def test_no_recap_while_an_exam_is_ahead(tmp_path):
    sub, after = _semester(tmp_path)
    plan = service.PlanReport.from_dict(sub.plan)
    before = min(datetime.fromisoformat(s.exam) for s in plan.subjects) - timedelta(days=1)
    assert service.recap_view(tmp_path, sub, before) is None
    assert service.recap_view(tmp_path, sub, after) is not None


def test_the_recap_is_the_numbers_progress_keeps(tmp_path):
    sub, after = _semester(tmp_path)
    recap = service.recap_view(tmp_path, sub, after)
    assert recap["sessions"] > 0 and recap["hours"] > 0 and 0 < recap["days"] <= recap["sessions"]
    assert (
        recap["best_streak"]
        == service.progress_view(sub, after, logged=service.logged_sessions(tmp_path, sub))["streak"]["best"]
    )
    assert recap["memory_days"] > 0
    assert 0 <= recap["month_topics"] <= recap["topics"]
    assert recap["beaten"] == 0  # a plan followed as planned is never answered against the odds
    assert recap["together"] == 0 and recap["exams"] == []
    assert recap["from"] == "28 September"


def test_an_exam_answered_is_in_the_recap(tmp_path):
    sub, after = _semester(tmp_path)
    course = service.exam_questions(sub, after)[0]["course"]
    sub = service.answer_exam(sub, course, {"result": "passed"}, now=after)
    recap = service.recap_view(tmp_path, sub, after)
    assert recap["exams"] == [{"course": course, "says": "Passed"}]


def test_the_text_to_share_has_the_numbers_and_nothing_private(tmp_path):
    sub, after = _semester(tmp_path)
    recap = service.recap_view(tmp_path, sub, after)
    text = service.recap_share_text(recap)
    assert text.startswith("My semester, 28 September to ")
    assert f"{recap['sessions']} study sessions" in text and f"{recap['hours']} hours" in text
    assert f"Best streak: {recap['best_streak']} day" in text
    for private in (sub.token, "Algebra", "Statistics", "http"):
        assert private not in text


def test_the_next_semester_keeps_the_recap_and_starts_clean(tmp_path):
    sub, after = _semester(tmp_path)
    recap = service.recap_view(tmp_path, sub, after)
    ics = (EXAMPLES / "sample-semester.ics").read_bytes()
    nxt = service.start_next_semester(sub, ics=ics, now=after, recap=recap)
    assert service.recaps_of(nxt) == [recap]
    assert service.recap_view(tmp_path, nxt, after) is None  # the new semester's exams are ahead
    many = nxt
    for n in range(service.RECAPS_KEPT + 2):
        many = service.start_next_semester(many, ics=ics, now=after, recap={**recap, "sessions": n})
    kept = service.recaps_of(many)
    assert len(kept) == service.RECAPS_KEPT and kept[0]["sessions"] == service.RECAPS_KEPT + 1


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    clock = {"now": START}
    config = Config(
        store=tmp_path, clock=lambda: clock["now"], background=False, sweep_every=None, remind_every=None
    )
    with TestClient(create_app(config)) as client:
        client.clock = clock
        yield client


def test_today_leads_to_the_recap_and_the_next_semester_keeps_it(web, tmp_path):
    sub, after = _semester(tmp_path)
    web.clock["now"] = after
    panel = unescape(web.get(f"/p/{sub.token}/panel").text)
    if "See your semester in numbers" not in panel:
        # A more pressing message (how the exam went, D56) comes first; answer it.
        assert "How did the" in panel
        for q in service.exam_questions(sub, after):
            web.post(f"/p/{sub.token}/exam", data={"course": q["course"], "skip": "1"})
        panel = unescape(web.get(f"/p/{sub.token}/panel").text)
    assert "See your semester in numbers" in panel and f"/p/{sub.token}/recap" in panel
    page = unescape(web.get(f"/p/{sub.token}/recap").text)
    recap = service.recap_view(tmp_path, service.load_subscription(tmp_path, sub.token), after)
    assert "Your semester" in page and "study sessions kept" in page
    assert service.recap_share_text(recap) in page  # the text to copy, without a script
    assert 'data-share-text="My semester' in page and "Start next semester" in page
    started = web.post(
        f"/p/{sub.token}/semester",
        files={"file": ("next.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")},
        follow_redirects=False,
    )
    assert started.status_code == 303
    page = unescape(web.get(f"/p/{sub.token}/recap").text)
    assert f"{recap['sessions']}" in page and "Start next semester" not in page  # the kept one, read only
