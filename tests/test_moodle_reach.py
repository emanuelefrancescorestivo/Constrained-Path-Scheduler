"""
D163: Moodle within reach (the owner's 1A, 2026-11-19: "the moodle input should be
accessible so one can check their study materials"). What must hold: a course is linked
by pasting its address from Moodle, or the site alone so Moodle's own course search can
find it; anything else is refused, and every address the app opens is built from the site
and the course's number, never from the text pasted; the course's page, the focus screen,
the sidebar and You lead to Moodle, which keeps its own sign-in.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 7, 0, tzinfo=UTC)
EXPORT = "https://moodle.example.edu/calendar/export_execute.php?userid=7&authtoken=abc&preset_what=all"


@pytest.mark.parametrize(
    ("pasted", "site", "number"),
    [
        ("https://moodle.example.edu/course/view.php?id=4127", "https://moodle.example.edu", 4127),
        ("https://moodle.example.edu/course/view.php?id=4127#section-3", "https://moodle.example.edu", 4127),
        ("https://moodle.example.edu/course/view.php?section=2&id=4127", "https://moodle.example.edu", 4127),
        ("http://elearning.uni.it/moodle/course/view.php?id=55", "http://elearning.uni.it/moodle", 55),
        ("https://moodle.example.edu/mod/assign/index.php?id=9", "https://moodle.example.edu", 9),
        ("  moodle.example.edu  ", "https://moodle.example.edu", None),
        ("https://moodle.example.edu/my/", "https://moodle.example.edu", None),
        ("https://x.edu/moodle/my/courses.php", "https://x.edu/moodle", None),
    ],
)
def test_a_course_or_the_site_alone_is_read_from_what_is_pasted(pasted, site, number):
    assert service.read_moodle_address(pasted) == (site, number)


@pytest.mark.parametrize(
    "pasted",
    [
        "javascript:alert(1)",
        "https://user:pw@evil.example/course/view.php?id=1",
        "https://drive.google.com/file/d/abc?usp=sharing",
        "https://moodle.example.edu/mod/resource/view.php?id=77",  # a file's number, not the course's
        'https://moodle.example.edu/course/view.php?id=4127"><script>',
        "not an address",
        "",
    ],
)
def test_anything_else_is_refused(pasted):
    with pytest.raises(service.InvalidInput):
        service.read_moodle_address(pasted)


def _plan(root: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", date(2027, 1, 26), familiarity=3)],
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


def test_the_site_is_the_one_given_else_the_calendar_links_else_a_linked_courses(tmp_path):
    sub = _plan(tmp_path)
    assert service.moodle_site(sub) is None and service.moodle_home(sub) is None
    assert service.learning_platform(sub) is None
    with_export = replace(sub, options={**sub.options, "deadlines_url": EXPORT})
    assert service.moodle_site(with_export) == "https://moodle.example.edu"
    assert service.learning_platform(with_export) == "https://moodle.example.edu/my/"
    linked = service.link_moodle_course(sub, 0, {"address": "https://m.uni.it/lms/course/view.php?id=8"})
    assert service.moodle_site(linked) == "https://m.uni.it/lms"
    given = service.link_moodle_course(with_export, 0, {"address": "moodle.other.edu"})
    assert service.moodle_site(given) == "https://moodle.other.edu"  # the one given first
    assert service.moodle_home(given) == {
        "dashboard": "https://moodle.other.edu/my/",
        "courses": "https://moodle.other.edu/my/courses.php",
        "host": "moodle.other.edu",
    }


def test_a_course_once_linked_opens_its_four_pages(tmp_path):
    sub = replace(_plan(tmp_path), options={**_plan(tmp_path).options, "deadlines_url": EXPORT})
    course = service.topic_courses(sub)[0]
    before = service.moodle_course(sub, course)
    assert not before["linked"] and before["pages"] == {}
    query = "search=" + course.replace(" ", "+")
    assert before["find"] == f"https://moodle.example.edu/course/search.php?{query}"
    linked = service.link_moodle_course(
        sub, 0, {"address": "https://moodle.example.edu/course/view.php?id=4127#s"}
    )
    after = service.moodle_course(linked, course)
    assert after["linked"] and after["id"] == 4127 and after["host"] == "moodle.example.edu"
    assert after["pages"] == {
        "course": "https://moodle.example.edu/course/view.php?id=4127",
        "materials": "https://moodle.example.edu/course/resources.php?id=4127",
        "assignments": "https://moodle.example.edu/mod/assign/index.php?id=4127",
        "grades": "https://moodle.example.edu/grade/report/user/index.php?id=4127",
    }
    # Another course is not linked by it; unlinking leaves the site known.
    if len(service.topic_courses(sub)) > 1:
        assert not service.moodle_course(linked, service.topic_courses(sub)[1])["linked"]
    gone = service.unlink_moodle_course(linked, 0)
    assert not service.moodle_course(gone, course)["linked"] and service.moodle_site(gone)
    with pytest.raises(service.InvalidInput):
        service.link_moodle_course(sub, 0, {"address": "x" * 501})


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def test_the_course_page_links_it_and_the_rest_of_the_app_follows(web, tmp_path):
    sub = _plan(tmp_path)
    base = f"/p/{sub.token}"
    course = service.topic_courses(sub)[0]
    page = unescape(web.get(f"{base}/courses/0").text)
    assert 'id="moodle"' in page and "Find it on Moodle" not in page  # no site known yet
    assert "Or paste your Moodle's address alone" in page
    you = unescape(web.get(f"{base}/you").text)
    assert f'href="{base}/settings/exams#deadlines"' in you and "Link your university's Moodle" in you

    # A wrong address: refused, said, and kept in the field.
    bad = web.post(f"{base}/courses/0/moodle", data={"address": "https://drive.google.com/x?y=1"})
    assert bad.status_code == 400 and "a course&#39;s address on Moodle" in bad.text
    assert 'value="https://drive.google.com/x?y=1"' in bad.text

    # The site alone, then the course's address.
    answer = web.post(
        f"{base}/courses/0/moodle", data={"address": "moodle.example.edu"}, follow_redirects=False
    )
    assert answer.headers["location"] == f"{base}/courses/0?saved=moodle_site#moodle"
    page = unescape(web.get(f"{base}/courses/0?saved=moodle_site").text)
    assert "Your Moodle is saved: now find the course on it." in page
    assert f'href="https://moodle.example.edu/course/search.php?search={course.replace(" ", "+")}"' in page
    answer = web.post(
        f"{base}/courses/0/moodle",
        data={"address": "https://moodle.example.edu/course/view.php?id=4127"},
        follow_redirects=False,
    )
    assert answer.headers["location"] == f"{base}/courses/0?saved=moodle#moodle"
    page = web.get(f"{base}/courses/0").text
    for path in (
        "course/view.php",
        "course/resources.php",
        "mod/assign/index.php",
        "grade/report/user/index.php",
    ):
        link = re.search(rf'<a href="https://moodle\.example\.edu/{re.escape(path)}\?id=4127"([^>]*)>', page)
        assert link and 'rel="noopener noreferrer"' in link.group(1) and 'target="_blank"' in link.group(1), (
            path
        )

    # The sidebar and You lead to the student's Moodle.
    today = web.get(base).text
    assert 'href="https://moodle.example.edu/my/" rel="noopener noreferrer" target="_blank"' in today
    you = web.get(f"{base}/you").text
    assert 'href="https://moodle.example.edu/my/courses.php"' in you

    # The focus screen, for the session's course.
    web.post(f"{base}/focus/start", data={"course": course, "goal": "25"})
    focus = web.get(f"{base}/focus").text
    assert 'href="https://moodle.example.edu/course/resources.php?id=4127"' in focus
    assert "which the clock counts as time away" in focus

    # Unlinked: the course page asks again; the site stays.
    web.post(f"{base}/courses/0/moodle", data={"action": "unlink"})
    page = unescape(web.get(f"{base}/courses/0").text)
    assert "course/resources.php" not in page and "Find it on Moodle" in page
