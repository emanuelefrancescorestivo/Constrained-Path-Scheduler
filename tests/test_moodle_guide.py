"""
Planning a course from Moodle, step by step (DECISIONS.md D209). The owner, about D208: "i
need a tutorial, in the app too because i cant find it".

What must hold: the guide says which of its three steps are done (the key given, a course
linked, a course read and planned; the key first since D211) and lights the first one not
done; it is reached from Courses, from a course's Moodle card and from Connections; the key
given on it comes back to it, and a course is read and planned from it in one tap. The demo's
made-up Moodle answers the demo's courses only, refuses a key that is not its own, and leaves
any other site to the real web service.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service, showcase
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # the sample semester's second teaching week
ADDRESS = f"{showcase.DEMO_MOODLE}/course/view.php?id={showcase.DEMO_COURSES['2']}"


def _plan(root: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[
            service.SubjectSpec("Algebra 3", None, familiarity=3),
            service.SubjectSpec("Analysis 3", None, familiarity=3),
        ],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    sub = service.finish_first_run(sub)
    service.save_subscription(root, sub)
    return sub


def _app(root: Path) -> TestClient:
    config = Config(
        store=root,
        clock=lambda: NOW,
        background=False,
        sweep_every=None,
        remind_every=None,
        moodle_call=showcase.demo_moodle_call,
        fetch_file=showcase.demo_fetch_file,
    )
    return TestClient(create_app(config))


def test_the_guide_follows_the_student_step_by_step(tmp_path):
    sub = _plan(tmp_path)
    algebra = service.topic_courses(sub).index("Algebra 3")
    guide = service.moodle_guide_view(sub)
    # D211: the key first, as it finds the courses; then the courses linked; then planned.
    assert [s["key"] for s in guide["steps"]] == ["key", "link", "read"]
    assert (guide["done"], guide["next"], guide["site"]) == (0, "key", False)
    assert guide["example"].startswith(service.MOODLE_EXAMPLE)  # the picture's address, no site yet
    assert [c["name"] for c in guide["courses"]] == service.topic_courses(sub)

    # A course linked by its address first still works (D209's way), and teaches the site.
    sub = service.link_moodle_course(sub, algebra, {"address": ADDRESS})
    guide = service.moodle_guide_view(sub)
    assert (guide["done"], guide["next"], guide["host"]) == (1, "key", "moodle.example.edu")
    assert guide["example"] == f"{showcase.DEMO_MOODLE}/course/view.php?id="  # the student's own Moodle

    sub = service.connect_moodle_key(sub, showcase.DEMO_KEY, call=showcase.demo_moodle_call, now=NOW)
    guide = service.moodle_guide_view(sub)
    assert (guide["done"], guide["next"], guide["to_read"]["name"]) == (2, "read", "Algebra 3")
    # The key found the other course and linked it by its name.
    analysis = next(c for c in guide["courses"] if c["name"] == "Analysis 3")
    assert analysis["found"] and analysis["moodle_name"] == "Analysis 3 · 2026–27"
    assert [c["name"] for c in guide["to_plan"]] == ["Algebra 3", "Analysis 3"]

    sub, summary = service.read_moodle_course(
        tmp_path, sub, algebra, call=showcase.demo_moodle_call, fetch=showcase.demo_fetch_file, now=NOW
    )
    assert summary["by"] == "rules" and summary["files"] == 24
    guide = service.moodle_guide_view(sub)
    assert (guide["done"], guide["next"], guide["to_read"]["name"]) == (3, None, "Analysis 3")
    assert [c["name"] for c in guide["planned"]] == ["Algebra 3"]


def test_the_guide_is_found_where_the_student_looks_and_does_each_step(tmp_path):
    sub = _plan(tmp_path)
    token = sub.token
    algebra = service.topic_courses(sub).index("Algebra 3")
    with _app(tmp_path) as web:
        page = unescape(web.get(f"/p/{token}/guide/moodle").text)
        assert "0 of 3 steps done" in page and 'id="step-key"' in page
        assert page.index("is-next") < page.index('id="step-key"') < page.index('id="step-link"')
        # No Moodle known yet: its address comes with the key, the keys' page follows it.
        assert 'name="site"' in page and 'data-moodle-page="/user/managetoken.php"' in page
        # Found from Courses (above the list until a course is planned), a course's card, Connections.
        courses = web.get(f"/p/{token}/courses").text
        assert courses.index(f"/p/{token}/guide/moodle") < courses.index("course-rows")
        assert f"/p/{token}/guide/moodle" in web.get(f"/p/{token}/courses/{algebra}").text
        connections = web.get(f"/p/{token}/connections").text
        assert f"/p/{token}/guide/moodle" in connections and 'name="site"' in connections

        given = web.post(
            f"/p/{token}/moodle/key",
            data={"key": showcase.DEMO_KEY, "site": "moodle.example.edu/my/", "back": "guide"},
            follow_redirects=False,
        )
        # D216: the courses the key linked are planned at once, without "Plan all my courses".
        assert given.headers["location"] == f"/p/{token}/guide/moodle#step-read"
        page = unescape(web.get(f"/p/{token}/guide/moodle").text)
        assert "Your key found 6 courses on Moodle." in page
        assert "Linked to «Algebra 3 · 2026–27»" in page and "Linked to «Analysis 3 · 2026–27»" in page
        assert "Plan all my 2 courses" not in page
        # "Plan all" stays for courses linked later; with nothing left to plan it does nothing.
        read = web.post(f"/p/{token}/moodle/read-all", follow_redirects=False)
        assert read.headers["location"] == f"/p/{token}/guide/moodle#step-read"
        page = unescape(web.get(f"/p/{token}/guide/moodle").text)
        assert "3 of 3 steps done" in page and "You are set." in page and page.count("See the plan") >= 2
        assert "is-next" not in page and "data-reading=" not in page  # read at once in the tests
        courses = web.get(f"/p/{token}/courses").text  # once planned, beside the list
        assert courses.index("course-rows") < courses.index(f"/p/{token}/guide/moodle")
        course = unescape(web.get(f"/p/{token}/courses/{algebra}").text)
        assert "Lecture 2 · Linear maps, kernel and image" in course  # the plan, from its files
        assert "from 24 files on Moodle" in course


def test_the_demo_s_moodle_answers_its_own_courses_only(monkeypatch):
    with pytest.raises(service.MoodleError):
        showcase.demo_moodle_call(showcase.DEMO_MOODLE, "0" * 32, "core_webservice_get_site_info")
    with pytest.raises(service.MoodleError):
        showcase.demo_moodle_call(
            showcase.DEMO_MOODLE, showcase.DEMO_KEY, "core_course_get_contents", {"courseid": 7}
        )
    asked = []
    monkeypatch.setattr(service, "moodle_call", lambda *a, **k: asked.append(a) or {"real": True})
    monkeypatch.setattr(service, "fetch_file", lambda url, *, limit: b"real")
    assert showcase.demo_moodle_call("https://moodle.univ.example", "k", "f") == {"real": True}
    assert asked == [("https://moodle.univ.example", "k", "f", None)]
    assert showcase.demo_fetch_file("https://elsewhere.example/a.pdf", limit=10) == b"real"
    # Every file the made-up course lists is one it serves.
    for section in showcase._demo_contents(showcase.DEMO_COURSES["1"]):
        for module in section["modules"]:
            for item in module.get("contents") or ():
                assert showcase.demo_fetch_file(item["fileurl"] + "?token=x", limit=10_000).startswith(b"# ")
