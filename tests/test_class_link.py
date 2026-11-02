"""
A class's link that brings the program (DECISIONS.md D104). What must hold: a plan
offers only the courses it has topics for, and the link shows them with the
university and programme of its maker's profile, never who made it; whoever opens it
takes the courses they also have, matched by the words of their names, the topics
placed on their own weeks, their own topics kept unless they tick the course; a
plan's first take counts once; a new link or a stopped one no longer works, nor one
whose plan is deleted; it is in the plan's copy of its data.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service, social
from cps.store import Store
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, a Tuesday
TWELVE = "\n".join(f"Week {n}: Topic {n}" for n in range(1, 13))


def _plan(
    root: Path, subjects: list[str], handle: str | None = None, university: str = ""
) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec(name, None, familiarity=3) for name in subjects],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(root, sub)
    if handle:
        form = {"handle": handle, "university": university, "programme": "L3 Maths", "old_enough": "1"}
        service.save_profile(root, sub.token, form)
    return sub


def _topics(root: Path, sub: service.Subscription, index: int, text: str, share: bool = False):
    fresh = service.update_subscription(
        root, sub.token, lambda now: service.save_course_topics(root, now, index, text, share=share)
    )
    assert fresh is not None
    return fresh


def _take(root: Path, sub: service.Subscription, code: str, picks: set[int]) -> service.Subscription:
    entries = service.take_class_program(root, sub, code, picks)
    fresh = service.update_subscription(root, sub.token, lambda now: service.with_class_program(now, entries))
    assert fresh is not None
    return fresh


def test_a_link_offers_the_courses_with_topics_and_never_who_made_it(tmp_path):
    ada = _plan(tmp_path, ["Algebra 3", "Advanced Statistics"], "ada", "Université Lyon 1")
    with pytest.raises(service.InvalidInput, match="topics first"):
        service.make_class_link(tmp_path, ada)
    ada = _topics(tmp_path, ada, 0, TWELVE)
    code = service.make_class_link(tmp_path, ada, now=NOW)
    assert re.fullmatch(r"[A-Za-z0-9_-]{12}", code)
    assert service.make_class_link(tmp_path, ada) == code  # one link a plan
    view = service.class_page_view(tmp_path, code)
    assert view is not None and [c["course"] for c in view["courses"]] == ["Algebra 3"]  # only with topics
    (algebra,) = view["courses"]
    assert algebra["count"] == 12 and algebra["titles"] == ["Topic 1", "Topic 2", "Topic 3"]
    assert (
        algebra["more"] == 9 and view["university"] == "Université Lyon 1" and view["programme"] == "L3 Maths"
    )
    assert "ada" not in repr(view) and ada.token not in repr(view)  # never who made it
    assert (
        service.class_page_view(tmp_path, "x" * 12) is None
        and service.class_page_view(tmp_path, "../x") is None
    )
    # A new link replaces it; stopping it ends it.
    fresh = service.make_class_link(tmp_path, ada, renew=True)
    assert fresh != code and service.class_page_view(tmp_path, code) is None
    service.stop_class_link(tmp_path, ada)
    assert service.class_page_view(tmp_path, fresh) is None
    assert service.class_link_view(tmp_path, ada)["code"] is None


def test_taking_matches_courses_by_name_and_places_topics_on_the_takers_weeks(tmp_path):
    ada = _topics(tmp_path, _plan(tmp_path, ["Algebra 3", "Advanced Statistics"], "ada", "Lyon"), 0, TWELVE)
    code = service.make_class_link(tmp_path, ada)
    # Ben's timetable writes the course in capitals; Analysis 3 is not on the link.
    ben = _plan(tmp_path, ["ALGEBRA 3", "Analysis 3"])
    view = service.class_take_view(tmp_path, ben, code)
    assert view is not None and view["matched"] == 1 and not view["own"] and not view["profile"]
    (row,) = view["rows"]
    assert row["mine"] == "ALGEBRA 3" and row["course"] == "Algebra 3" and row["own_topics"] == 0
    ben = _take(tmp_path, ben, code, {0})
    kept = ben.options["topics"]["algebra 3"]
    assert kept["course"] == "ALGEBRA 3" and kept["from"] == f"class:{code}" and len(kept["items"]) == 12
    # Placed on Ben's own weeks of lectures: Algebra 3 is taught from Monday 28 September.
    assert kept["items"][1]["monday"] == "2026-10-05"
    assert service.class_link_view(tmp_path, ada)["uses"] == 1
    _take(tmp_path, ben, code, {0})  # taken again: still one plan
    assert service.class_link_view(tmp_path, ada)["uses"] == 1
    assert service.class_take_view(tmp_path, ben, code)["rows"][0]["taken"]


def test_ones_own_topics_stay_unless_the_course_is_ticked(tmp_path):
    ada = _topics(tmp_path, _plan(tmp_path, ["Algebra 3"], "ada", "Lyon"), 0, TWELVE)
    code = service.make_class_link(tmp_path, ada)
    cleo = _plan(tmp_path, ["Algebra 3"], "cleo", "Lyon")
    cleo = _topics(tmp_path, cleo, 0, "Week 1: Groups\nWeek 2: Rings", share=True)
    assert social.shared_by(Store(tmp_path), cleo.token) == {"algebra 3"}
    assert service.class_take_view(tmp_path, cleo, code)["rows"][0]["own_topics"] == 2  # shown unticked
    with pytest.raises(service.InvalidInput, match="tick at least one"):
        service.take_class_program(tmp_path, cleo, code, set())
    cleo = _take(tmp_path, cleo, code, {0})
    assert len(cleo.options["topics"]["algebra 3"]["items"]) == 12
    assert social.shared_by(Store(tmp_path), cleo.token) == set()  # no longer her own list to share
    with pytest.raises(service.InvalidInput, match="your own"):
        service.take_class_program(tmp_path, ada, code, {0})


def test_the_pages_make_open_take_and_paste_a_class_link(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    ada = _topics(tmp_path, _plan(tmp_path, ["Algebra 3"], "ada", "Université Lyon 1"), 0, TWELVE)
    ben = _plan(tmp_path, ["Algebra 3", "Analysis 3"])
    with TestClient(create_app(config)) as web:
        courses = unescape(web.get(f"/p/{ada.token}/courses").text)
        assert 'id="class"' in courses and "Make your class's link" in courses
        assert "never your handle, your plan or your sessions" in courses
        made = web.post(f"/p/{ada.token}/class-link", data={"action": "make"}, follow_redirects=False)
        assert made.headers["location"] == f"/p/{ada.token}/courses#class"
        code = service.class_link_view(tmp_path, ada)["code"]
        courses = web.get(f"/p/{ada.token}/courses").text
        assert f'value="http://testserver/c/{code}"' in courses and 'data-copy="#c-link"' in courses
        # The link, as anyone opens it.
        public = unescape(web.get(f"/c/{code}").text)
        assert "Your class's program" in public and "Algebra 3" in public and "12 topics" in public
        assert f'data-invite="{code}" data-join-path="class"' in public and "@ada" not in public
        assert web.get("/c/" + "x" * 12).status_code == 404
        # Taken with Ben's plan.
        take = web.get(f"/p/{ben.token}/class/{code}").text
        assert 'name="take" value="0" checked' in take and "not one of your courses" not in take
        assert f'href="/p/{ben.token}/profile?for_class={code}"' in take  # no handle yet: meet the class
        taken = web.post(f"/p/{ben.token}/class/{code}", data={"take": "0"}, follow_redirects=False)
        assert taken.headers["location"] == f"/p/{ben.token}/courses?imported=1"
        assert "Program saved for 1 course." in unescape(web.get(taken.headers["location"]).text)
        assert "Taken by 1 plan so far." in unescape(web.get(f"/p/{ada.token}/courses").text)
        profile = web.get(f"/p/{ben.token}/profile?for_class={code}").text
        assert 'value="Université Lyon 1"' in unescape(profile) and 'value="L3 Maths"' in profile
        # Pasted on Courses: the whole link or its code.
        pasted = web.post(
            f"/p/{ben.token}/class-paste", data={"code": f"https://x.org/c/{code}"}, follow_redirects=False
        )
        assert pasted.headers["location"] == f"/p/{ben.token}/class/{code}"
        wrong = web.post(f"/p/{ben.token}/class-paste", data={"code": "hello"}, follow_redirects=False)
        assert "That is not a class link." in unescape(web.get(wrong.headers["location"]).text)
        assert "This is your own class's link" in unescape(web.get(f"/p/{ada.token}/class/{code}").text)
        # The privacy notice says what the link shows.
        assert "never your handle, your plan or your sessions" in unescape(web.get("/privacy").text)
        # Stopped: the link says it no longer works.
        web.post(f"/p/{ada.token}/class-link", data={"action": "stop"})
        assert web.get(f"/c/{code}").status_code == 404
        assert web.get(f"/p/{ben.token}/class/{code}").status_code == 404


def test_the_link_goes_with_the_plan_and_is_in_its_copy(tmp_path):
    ada = _topics(tmp_path, _plan(tmp_path, ["Algebra 3"], "ada", "Lyon"), 0, TWELVE)
    code = service.make_class_link(tmp_path, ada, now=NOW)
    copy = service.export_data(tmp_path, ada.token, NOW)
    assert copy["class_link"] == [{"created": NOW.isoformat(timespec="seconds"), "uses": 0}]
    assert code not in repr(copy)  # the code is the link itself
    service.delete_subscription(tmp_path, ada.token)
    assert service.class_page_view(tmp_path, code) is None
    assert social.class_link_owner(Store(tmp_path), code) is None
