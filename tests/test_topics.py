"""D81: a course's syllabus pasted as text, read by rules into topics placed on the
course's weeks of lectures, shown on the sessions that review them, and shared,
without a name, with the classmates who take the course."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service, syllabus
from cps.store import Store
from cps.syllabus import MAX_TITLE, MAX_TOPICS, Topic, place, read, to_text
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, a Tuesday
TWELVE = "\n".join(f"Week {n}: Topic {n}" for n in range(1, 13))


# -- the reader ------------------------------------------------------------------------


def _read(text: str) -> list[tuple[int | None, int | None, str]]:
    return [(t.week, t.lecture, t.title) for t in read(text)]


def test_a_week_holds_the_lines_under_it_until_the_next():
    text = """Course outline
Week 1: Vectors and matrices
Week 2 - Linear maps
  - kernel and image
  - rank-nullity theorem
Week 3 (12/10): Eigenvalues; diagonalisation
Slides: https://moodle.example.org/course/42
"""
    assert _read(text) == [
        (1, None, "Vectors and matrices"),
        (2, None, "Linear maps"),
        (2, None, "kernel and image"),
        (2, None, "rank-nullity theorem"),
        (3, None, "Eigenvalues; diagonalisation"),
    ]


def test_numbering_orders_a_list_but_is_not_a_week():
    assert _read("1. Limits\n2) Derivatives\n1.3 Integrals\n3 laws of motion") == [
        (None, None, "Limits"),
        (None, None, "Derivatives"),
        (None, None, "Integrals"),
        (None, None, "3 laws of motion"),  # a bare number is part of the title
    ]
    # A one-letter abbreviation counts only before a colon or a dash.
    assert _read("L1 norm and L2 norm\nL3: Banach spaces\nW4 - Hilbert spaces") == [
        (None, None, "L1 norm and L2 norm"),
        (None, 3, "Banach spaces"),
        (4, None, "Hilbert spaces"),
    ]


def test_a_pasted_table_and_bullets_run_together():
    assert _read("Week\tTopic\tReading\nW1\tSets\tch. 1\nW2\tRelations\tch. 2") == [
        (1, None, "Sets · ch. 1"),
        (2, None, "Relations · ch. 2"),
    ]
    bullets = _read("• Groups • Rings • Fields")
    assert bullets == [(None, None, "Groups"), (None, None, "Rings"), (None, None, "Fields")]


def test_the_list_is_kept_short_and_each_topic_too():
    topics = read("\n".join(f"Topic number {n}" for n in range(100)))
    assert len(topics) == MAX_TOPICS
    long = read("Week 1: " + "word " * 60)[0]
    assert len(long.title) <= MAX_TITLE and long.title.endswith("…")


@pytest.mark.parametrize(
    ("week", "lecture"), [("Semaine", "Cours"), ("Settimana", "Lezione"), ("Week", "Lecture")]
)
def test_saved_topics_read_back_the_same_in_each_language(week, lecture):
    topics = [Topic("Séries", week=2), Topic("Intégrales", lecture=5), Topic("Révisions")]
    assert read(to_text(topics, week, lecture)) == [
        Topic("Séries", week=2),
        Topic("Intégrales", lecture=5),
        Topic("Révisions", lecture=5),  # a line under a lecture belongs to it
    ]


def test_topics_are_placed_on_the_teaching_weeks():
    weeks = [Topic("a", week=1), Topic("b", week=3), Topic("c", week=9), Topic("intro")]
    assert place(weeks, [3, 3, 3], [1, 1, 2, 2, 3, 3]) == [1, 3, None, None]  # week 9 is not taught
    lectures = [Topic("a", lecture=1), Topic("b", lecture=4), Topic("c", lecture=7)]
    assert place(lectures, [3, 3, 3], [1, 1, 2, 2, 3, 3]) == [1, 2, None]
    plain = [Topic(x) for x in "abcd"]
    assert place(plain, [2, 2, 2, 2], [1, 2, 3, 4]) == [1, 2, 3, 4]
    assert place(plain, [3, 1, 3, 1], [1, 1, 2, 3, 3, 4]) == [1, 1, 3, 3]  # more hours, more topics
    assert place(plain, [], []) == [None] * 4  # no lectures in the timetable


# -- a whole course page (D83) -----------------------------------------------------------

PAGE = """MATH 2040 Linear Algebra
Exam: 15 January
Credits: 6 ECTS
Course content
Week 1: Vectors and matrices
Week 2: Linear maps
- Evaluation: kernel and image
Assessment methods and criteria
Written exam 70 % and coursework 30 %
Attendance is recorded.
Reading
Strang, Introduction to Linear Algebra
"""


def test_a_course_page_is_cut_at_its_headings_and_the_longest_of_each_kind_kept():
    found = syllabus.sections(PAGE)
    assert list(found) == ["assessment", "content", "reading"]
    assert found["assessment"].startswith("Written exam")  # not "15 January"
    # A bulleted line is an item of the list, even when it starts with a heading's word.
    assert [t.title for t in read(syllabus.course_text(PAGE))] == [
        "Vectors and matrices",
        "Linear maps",
        "Evaluation: kernel and image",
    ]
    assert syllabus.sections("Week 1: Sets\nWeek 2: Maps") == {}
    assert syllabus.course_text("Week 1: Sets") == "Week 1: Sets"


def test_an_assessment_is_kept_only_when_its_weights_add_up_to_a_hundred():
    parts = [(p.label, p.percent) for p in syllabus.assessment(PAGE)]
    assert parts == [("Written exam", 70), ("Coursework", 30)]
    assert syllabus.assessment("Assessment\nExam 70 %\nAttendance at least 75 %") == []
    assert syllabus.assessment("Exam 60 %, project 40 %") == []  # no assessment heading


@pytest.mark.parametrize(
    ("text", "credit"),
    [("6 ECTS", 6.0), ("ECTS: 7,5", 7.5), ("Crediti: 12 CFU", 12.0), ("120 ECTS in all", None), ("", None)],
)
def test_credits_are_read_between_one_and_thirty(text, credit):
    assert syllabus.credits(text) == credit


def test_a_credit_is_twenty_five_to_thirty_hours_of_work():
    assert syllabus.workload(6) == (150, 180) and syllabus.workload(7.5) == (188, 225)


# -- through the service ---------------------------------------------------------------


def _plan(tmp_path: Path, university: str | None, handle: str) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(tmp_path, sub)
    if university is not None:
        service.save_profile(
            tmp_path, sub.token, {"handle": handle, "university": university, "old_enough": "1"}
        )
    return sub


def _save(tmp_path: Path, sub: service.Subscription, text: str, share: bool) -> service.Subscription:
    fresh = service.update_subscription(
        tmp_path, sub.token, lambda now: service.save_course_topics(tmp_path, now, 0, text, share=share)
    )
    assert fresh is not None
    return fresh


def _take(tmp_path: Path, sub: service.Subscription, list_id: str) -> service.Subscription:
    entry = service.take_classmates_topics(tmp_path, sub, 0, list_id)
    fresh = service.update_subscription(tmp_path, sub.token, lambda s: service.with_course_topics(s, entry))
    assert fresh is not None
    return fresh


def test_topics_are_placed_and_shown_on_the_sessions_of_their_week(tmp_path):
    ada = _save(tmp_path, _plan(tmp_path, None, "ada"), TWELVE, share=False)
    view = service.course_topics_view(tmp_path, ada, 0)
    assert len(view["rows"]) == 12 and view["timetable"]
    # Algebra 3 is taught from Monday 28 September, eleven weeks: week 12 has no date.
    assert view["rows"][0]["when"] == "Week of 28 Sep" and view["rows"][11]["when"] == ""
    assert view["text"].splitlines()[2] == "Week 3: Topic 3"
    plan = service.PlanReport.from_dict(ada.plan)
    cards = {s.topic: service.session_card(ada, service.session_id(s), NOW) for s in plan.sessions}
    assert cards["Algebra 3 · week of 12 Oct"]["syllabus"] == ["Topic 3"]
    # The plan began on Tuesday: Monday's lecture is in "taught before", with week 1.
    assert cards["Algebra 3 · taught before 29 Sep"]["syllabus"] == ["Topic 1"]
    cleared = _save(tmp_path, ada, "", share=False)
    assert service.course_topics_view(tmp_path, cleared, 0)["rows"] == []
    assert service.session_card(cleared, service.session_id(plan.sessions[0]), NOW)["syllabus"] == []


def test_a_course_page_keeps_its_assessment_and_credits_with_the_topics(tmp_path):
    ada = _save(tmp_path, _plan(tmp_path, None, "ada"), PAGE, share=False)
    view = service.course_topics_view(tmp_path, ada, 0)
    assert [r["title"] for r in view["rows"]][:2] == ["Vectors and matrices", "Linear maps"]
    assert view["facts"]["assessment"] == "Written exam 70 %; Coursework 30 %"
    assert "150 to 180 hours" in view["facts"]["credits"]
    # The box then holds the topics alone; saving them again keeps what the page said.
    again = _save(tmp_path, ada, view["text"], share=False)
    assert service.course_topics_view(tmp_path, again, 0)["facts"] == view["facts"]
    preview = service.course_topics_view(tmp_path, again, 0, "Week 1: Sets")
    assert preview["preview"]["facts"] == {}


def test_classmates_take_a_shared_list_in_one_tap(tmp_path):
    ada = _plan(tmp_path, "University of Bologna", "ada")
    bea = _plan(tmp_path, "university of bologna", "bea")
    cyd = _plan(tmp_path, "University of Milan", "cyd")
    assert service.topics_nudge(tmp_path, bea) is None
    _save(tmp_path, ada, TWELVE, share=True)
    assert service.topics_overview(tmp_path, bea)[0]["offers"] == 1
    assert service.topics_nudge(tmp_path, bea) == {"index": 0, "course": "Algebra 3"}
    assert service.topics_overview(tmp_path, cyd)[0]["offers"] == 0  # another university
    offer = service.course_topics_view(tmp_path, bea, 0)["offers"][0]
    assert offer["count"] == 12 and offer["uses"] == 0
    assert offer["titles"] == ["Topic 1", "Topic 2", "Topic 3"] and offer["more"] == 9
    for _ in range(2):  # taken twice, counted once
        bea = _take(tmp_path, bea, offer["id"])
    view = service.course_topics_view(tmp_path, bea, 0)
    assert [r["title"] for r in view["rows"]] == [f"Topic {n}" for n in range(1, 13)]
    assert service.topics_nudge(tmp_path, bea) is None
    assert service.course_topics_view(tmp_path, cyd, 0)["offers"] == []
    store = Store(tmp_path)
    with store.connection() as db:
        assert db.execute("SELECT uses FROM course_topics").fetchall() == [(1,)]
    # Ada stops sharing: the list is no longer offered; Bea keeps her copy.
    _save(tmp_path, ada, TWELVE, share=False)
    dan = _plan(tmp_path, "University of Bologna", "dan")
    assert service.topics_overview(tmp_path, dan)[0]["offers"] == 0
    assert len(service.course_topics_view(tmp_path, bea, 0)["rows"]) == 12
    with pytest.raises(service.InvalidInput):
        service.take_classmates_topics(tmp_path, dan, 0, offer["id"])


def test_a_shared_list_is_in_the_plan_s_copy_and_goes_with_the_plan(tmp_path):
    ada = _plan(tmp_path, "University of Bologna", "ada")
    _save(tmp_path, ada, TWELVE, share=True)
    store = Store(tmp_path)
    shared = store.export(ada.token)["shared_topics"]
    assert len(shared) == 1 and shared[0]["name"] == "Algebra 3"
    service.delete_subscription(tmp_path, ada.token)
    bea = _plan(tmp_path, "University of Bologna", "bea")
    assert service.topics_overview(tmp_path, bea)[0]["offers"] == 0


def test_without_a_university_topics_are_kept_but_not_shared(tmp_path):
    ada = _save(tmp_path, _plan(tmp_path, None, "ada"), TWELVE, share=True)
    assert len(service.course_topics_view(tmp_path, ada, 0)["rows"]) == 12
    with Store(tmp_path).connection() as db:
        assert db.execute("SELECT COUNT(*) FROM course_topics").fetchone() == (0,)
    with pytest.raises(service.InvalidInput):
        service.course_topics_view(tmp_path, ada, 5)


# -- the pages -------------------------------------------------------------------------


def test_the_pages_read_save_and_show_topics(tmp_path):
    clock = {"now": NOW}
    config = Config(store=tmp_path, clock=lambda: clock["now"], background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
        start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        token = start.headers["location"].split("/")[2]
        # The courses' topics are reached from the Courses section (D88): its tab is on
        # every page (Settings no longer repeats the link, D103), the old address leads
        # there too, and each course's page to its topics.
        tabs = re.search(r'<nav class="tabbar".*?</nav>', web.get(f"/p/{token}/settings").text, re.S)[0]
        assert f'href="/p/{token}/courses"' in tabs
        moved = web.get(f"/p/{token}/topics", follow_redirects=False)
        assert moved.status_code == 302 and moved.headers["location"] == f"/p/{token}/courses"
        overview = web.get(f"/p/{token}/courses").text
        assert f"/p/{token}/courses/0" in overview
        assert f"/p/{token}/topics/0" in web.get(f"/p/{token}/courses/0").text
        page = web.get(f"/p/{token}/topics/0").text
        assert "Paste the program" in page and "add your university to your profile" in page
        text = "Week 1: Sets\nWeek 2: Maps"
        preview = web.post(f"/p/{token}/topics/0", data={"text": text, "action": "preview"})
        assert "Read from your text" in preview.text and "Sets" in preview.text
        saved = web.post(f"/p/{token}/topics/0", data={"text": text, "action": "save"})
        assert saved.status_code == 200 and "Topics saved." in saved.text
        assert "Change the topics" in saved.text and "Week 2: Maps" in saved.text
        assert web.get(f"/p/{token}/topics/9").status_code == 404
        page = web.post(f"/p/{token}/topics/0", data={"text": PAGE, "action": "preview"}).text
        assert "Written exam 70 %; Coursework 30 %" in page
        assert "6 ECTS credits: about 150 to 180 hours of work in all, lectures included." in page
