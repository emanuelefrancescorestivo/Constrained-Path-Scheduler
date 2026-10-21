"""
D86, D87, D88: the Courses section. Each course week by week (taught or to come,
reviewed or not, today's recall, its syllabus topics), the workload its ECTS credits
stand for against the timetable's lectures and the plan's study, cards filed under a
week and practised from that week's session, and links to the course's material.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import cards, service
from cps.store import Store
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # a Tuesday, 08:00 in Paris
ZONE = service._zone("Europe/Paris")
TWELVE = "\n".join(f"Week {n}: Topic {n}" for n in range(1, 13))


def _plan(tmp_path: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(tmp_path, sub)
    return sub


def _update(tmp_path: Path, sub: service.Subscription, change) -> service.Subscription:
    fresh = service.update_subscription(tmp_path, sub.token, change)
    assert fresh is not None
    return fresh


# -- the weeks of a course ---------------------------------------------------------------


def test_a_course_is_followed_week_by_week(tmp_path):
    sub = _plan(tmp_path)
    sub = _update(tmp_path, sub, lambda s: service.save_course_topics(tmp_path, s, 0, TWELVE, share=False))
    [row] = service.courses_view(tmp_path, sub, NOW)
    # Algebra 3 is taught on eleven weeks from Monday 28 September; on the Tuesday of
    # the first, that week is under way and none is reviewed yet.
    assert (row["course"], row["weeks"], row["taught"], row["reviewed"], row["topics"]) == (
        "Algebra 3",
        11,
        1,
        0,
        12,
    )
    assert row["exam"]["days"] == 119
    later = NOW + timedelta(days=15)  # Wednesday 14 October
    view = service.course_view(tmp_path, sub, 0, later)
    weeks = view["weeks"]
    assert [w["state"] for w in weeks[:4]] == ["taught", "taught", "now", "ahead"]
    assert weeks[0]["label"] == "Week 1 · 28 Sep" and weeks[0]["topics"] == ["Topic 1"]
    assert weeks[0]["memory"]["level"] in ("strong", "good", "fading", "review")
    assert weeks[3]["memory"] is None  # not taught yet: no recall to speak of
    assert all(w["ahead"] > 0 for w in weeks[:3])  # every taught week has reviews planned
    assert view["unplaced"] == ["Topic 12"]  # a twelfth week the timetable does not have
    assert view["now"]["label"] == "Week 3 · 12 Oct"


def test_a_week_counts_as_reviewed_once_one_of_its_sessions_is_done(tmp_path):
    sub = _plan(tmp_path)
    plan = service.PlanReport.from_dict(sub.plan)
    first = next(s for s in plan.sessions if s.topic == "Algebra 3 · week of 28 Sep")
    after = datetime.fromisoformat(first.end) + timedelta(hours=1)
    done = service.report_session(sub, service.session_id(first), "most", now=after)
    week = service.course_view(tmp_path, done, 0, after)["weeks"][0]
    assert week["done"] == 1 and week["state"] in ("taught", "now")
    assert service.courses_view(tmp_path, done, after)[0]["reviewed"] == 1


# -- the workload check (D86) -------------------------------------------------------------


def test_the_workload_compares_the_credits_with_lectures_and_study(tmp_path):
    sub = _plan(tmp_path)
    assert service.course_workload(sub, "Algebra 3") is None  # no credits, no check
    sub = service.set_course_credits(sub, 0, "6")
    w = service.course_workload(sub, "Algebra 3")
    assert (w["low"], w["high"]) == (150, 180)  # 25 to 30 hours an ECTS credit
    teaching = round(sum(service._teaching(sub, "Algebra 3")[1]))
    assert w["lectures"] == teaching == 33  # eleven weeks of three hours
    plan = service.PlanReport.from_dict(sub.plan)
    minutes = sum(
        (datetime.fromisoformat(s.end) - datetime.fromisoformat(s.start)) / timedelta(minutes=1)
        for s in plan.sessions
        if s.subject == "Algebra 3"
    )
    assert w["study"] == round(minutes / 60) and w["total"] == w["lectures"] + w["study"]
    assert w["verdict"] == "below" and f"{150 - w['total']} h under" in w["says"]
    assert (
        service.course_workload(service.set_course_credits(sub, 0, "3,5"), "Algebra 3")["verdict"] == "within"
    )
    assert service.course_workload(service.set_course_credits(sub, 0, "1"), "Algebra 3")["verdict"] == "above"


@pytest.mark.parametrize("bad", ["0", "31", "six", "-2"])
def test_credits_are_a_number_from_one_to_thirty(tmp_path, bad):
    sub = _plan(tmp_path)
    with pytest.raises(service.InvalidInput):
        service.set_course_credits(sub, 0, bad)
    assert service._credits(service.set_course_credits(sub, 0, "7,5"), "Algebra 3") == 7.5
    assert service._credits(service.set_course_credits(sub, 0, ""), "Algebra 3") is None


def test_credits_read_from_a_course_page_count_until_the_student_types_their_own(tmp_path):
    sub = _plan(tmp_path)
    page = "Credits: 6 ECTS\nCourse content\nWeek 1: Sets\nWeek 2: Maps"
    sub = _update(tmp_path, sub, lambda s: service.save_course_topics(tmp_path, s, 0, page, share=False))
    assert service._credits(sub, "Algebra 3") == 6.0
    assert service._credits(service.set_course_credits(sub, 0, "9"), "Algebra 3") == 9.0


# -- cards filed under a week (D87) --------------------------------------------------------


def test_cards_of_a_week_are_practised_once_a_day_from_that_week(tmp_path):
    sub = _plan(tmp_path)
    where = Store(tmp_path)
    pairs = [("What is a set?", "A collection."), ("Union?", "Elements in either.")]
    week = cards.add(where, sub.token, "Algebra 3", pairs, NOW, week="2026-09-28")
    cards.add(where, sub.token, "Algebra 3", [("A map?", "A function.")], NOW)
    with pytest.raises(cards.CardError):
        cards.add(where, sub.token, "Algebra 3", pairs, NOW, week="2026-09-29")  # not a Monday
    assert [c.front for c in cards.of_week(where, sub.token, "algebra 3", "2026-09-28")] == [
        "What is a set?",
        "Union?",
    ]
    view = service.card_review_view(tmp_path, sub, NOW, course="Algebra 3", week="2026-09-28")
    assert view["left"] == 2 and view["week_label"] == "week of 28 Sep"
    service.answer_card(tmp_path, sub, week[0].id, "good", now=NOW)
    # Answered today, it waits for tomorrow's practice; the other is still there.
    view = service.card_review_view(
        tmp_path, sub, NOW + timedelta(minutes=1), course="Algebra 3", week="2026-09-28"
    )
    assert view["left"] == 1 and view["card"]["front"] == "Union?"
    tomorrow = NOW + timedelta(days=1)
    assert (
        service.card_review_view(tmp_path, sub, tomorrow, course="Algebra 3", week="2026-09-28")["left"] == 2
    )
    # The course's page counts them under their week.
    weeks = service.course_view(tmp_path, sub, 0, NOW)["weeks"]
    assert weeks[0]["cards"] == 2 and service.course_view(tmp_path, sub, 0, NOW)["unfiled"] == 1
    # Moving a card to another week, or off any.
    moved = cards.edit(where, sub.token, week[1].id, "Algebra 3", "Union?", "Either.", "2026-10-05")
    assert moved.week == "2026-10-05"
    assert cards.edit(where, sub.token, week[1].id, "Algebra 3", "Union?", "Either.").week == "2026-10-05"
    assert cards.edit(where, sub.token, week[1].id, "Algebra 3", "Union?", "Either.", None).week is None


def test_a_week_s_self_test_leads_to_its_cards_and_the_maker_files_them_there(tmp_path):
    sub = _plan(tmp_path)
    plan = service.PlanReport.from_dict(sub.plan)
    test = next(s for s in plan.sessions if s.topic == "Algebra 3 · week of 05 Oct")
    assert service.session_card(sub, service.session_id(test), NOW)["week"] == "2026-10-05"
    task = next((s for s in plan.sessions if s.kind == "practice"), None)
    if task is not None:
        assert service.session_card(sub, service.session_id(task), NOW)["week"] is None
    made = service.card_maker_view(tmp_path, sub, text="Rank: the dimension", course="Algebra 3", now=NOW)
    assert made["week"] == "2026-09-28" and made["weeks"][1]["label"] == "Week 2 · 5 Oct"
    chosen = service.card_maker_view(tmp_path, sub, course="Algebra 3", week="2026-10-05", now=NOW)
    assert chosen["week"] == "2026-10-05"
    added = service.add_made_cards(
        tmp_path, sub, "Algebra 3", ["Rank"], ["the dimension"], ["0"], NOW, week="2026-10-05"
    )
    assert added == 1 and cards.of(Store(tmp_path), sub.token)[0].week == "2026-10-05"


# -- links to the course's material (D88) ------------------------------------------------


def test_links_to_material_are_http_kept_per_course_and_removed(tmp_path):
    sub = _plan(tmp_path)
    sub = service.add_course_link(
        sub, 0, {"url": "https://moodle.example.org/course/42", "title": "Moodle", "week": "2026-10-05"}
    )
    sub = service.add_course_link(sub, 0, {"url": "https://drive.example.org/slides"})
    links = service._links(sub, "Algebra 3")
    assert [(x["title"], x["week"]) for x in links] == [("Moodle", "2026-10-05"), ("drive.example.org", "")]
    for bad in ("javascript:alert(1)", "ftp://example.org/x", "example.org", "https://"):
        with pytest.raises(service.InvalidInput):
            service.add_course_link(sub, 0, {"url": bad})
    view = service.course_view(tmp_path, sub, 0, NOW)
    assert [x["title"] for x in view["links"]] == ["drive.example.org"]  # the whole course's
    assert [x["title"] for x in view["weeks"][1]["links"]] == ["Moodle"]  # the week's
    sub = service.remove_course_link(sub, 0, links[0]["id"])
    assert [x["title"] for x in service._links(sub, "Algebra 3")] == ["drive.example.org"]


# -- the pages ----------------------------------------------------------------------------


def test_the_courses_section_and_its_pages(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
        start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        token = start.headers["location"].split("/")[2]
        today = web.get(f"/p/{token}").text
        assert f'href="/p/{token}/courses"' in today  # in both bars
        overview = web.get(f"/p/{token}/courses").text
        assert "1 of 11 weeks taught" in overview and f"/p/{token}/courses/0" in overview
        page = web.get(f"/p/{token}/courses/0").text
        assert "Week by week" in page and "Week 1 · 28 Sep" in page and "this week" in page
        saved = web.post(f"/p/{token}/courses/0/credits", data={"credits": "6"}, follow_redirects=False)
        assert saved.status_code == 303
        page = web.get(saved.headers["location"]).text
        assert "Credits saved." in page and "6 ECTS: 150 to 180 h" in page and 'class="workload-bar"' in page
        assert web.post(f"/p/{token}/courses/0/credits", data={"credits": "99"}).status_code == 400
        link = {"url": "https://moodle.example.org/course/42", "title": "Moodle", "week": ""}
        added = web.post(f"/p/{token}/courses/0/links", data=link, follow_redirects=False)
        page = web.get(added.headers["location"]).text
        assert 'href="https://moodle.example.org/course/42" rel="noopener noreferrer"' in page
        refused = web.post(f"/p/{token}/courses/0/links", data={"url": "javascript:alert(1)"})
        assert refused.status_code == 400 and 'href="javascript:' not in refused.text
        assert web.get(f"/p/{token}/courses/7").status_code == 404
        practice = web.get(f"/p/{token}/cards/review?course=Algebra%203&week=2026-09-28").text
        assert "No cards for this week yet" in practice and "week=2026-09-28" in practice
        maker = web.get(f"/p/{token}/cards/make?course=Algebra%203&week=2026-10-05").text
        # The week rides with the notes until they are read, then is offered to change.
        assert '<input type="hidden" name="week" value="2026-10-05">' in maker
        read = web.post(
            f"/p/{token}/cards/make",
            data={
                "text": "Rank: the dimension",
                "course": "Algebra 3",
                "week": "2026-10-05",
                "action": "read",
            },
        ).text
        assert '<option value="2026-10-05" selected>' in read


def test_every_list_that_is_a_card_keeps_its_rows_inside_the_card():
    # AUDIT item 73: lists drawn as cards had no margins unless a rule named them.
    css = (ROOT / "src/cps/web/static/style.css").read_text()
    assert ".card.list { padding: 4px 16px; }" in css
    assert ".card.list a.grow { color: inherit; text-decoration: none !important; }" in css
