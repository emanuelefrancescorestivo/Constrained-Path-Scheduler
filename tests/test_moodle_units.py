"""
Courses rebuilt around the week (DECISIONS.md D214). The owner, after D213, on their own Moodle:
"Moodle n'a rien donné pour ce cours" on a course they were enrolled in, then "ricomincia tutto il
sistema dell'upload da moodle, la lettura dei materiali e la pianificazione attraverso il nostro
engine, mostrami in maniera trasparente come funziona"; of the three designs shown, "i like C but
i also like the holistic view of B over all courses".

What must hold: each week of lectures is one unit, and a week that goes on from an earlier part
(D212, D213) gives the sessions that review it what to study and how (AUDIT 142); notes a teacher
links in a text area or a section's description are read (AUDIT 143); a course with nothing to
read says what Moodle sent, on the course, with ways out, never "are you enrolled?" (AUDIT 143);
a course planned from Moodle is looked at again once a day and keeps its plan when nothing
changed (AUDIT 144); Courses shows this week course by course, what, how (ticked as done) and
when, how the plan was made a tap away, and the whole semester as a grid.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service, studyplan
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SITE = "https://moodle.university.example"
FILES = f"{SITE}/webservice/pluginfile.php"
KEY = "0123456789abcdef0123456789abcdef"
PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)  # the sample semester's eighth teaching week
TITLES = [
    "Lecture 1 Properties of integers",
    "Lecture 2 Introduction to groups",
    "Lecture 3 Finite Groups, Subgroups",
    "Lecture 4 Cyclic groups",
]


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


def _friday(monday: date) -> int:
    return int(datetime.combine(monday + timedelta(days=4), time(16, 0), tzinfo=PARIS).timestamp())


def _file(n: int, name: str, stamp: int, course: int = 31) -> dict:
    return {
        "id": n,
        "name": name,
        "modname": "resource",
        "url": f"{SITE}/mod/resource/view.php?id={n}",
        "contents": [
            {
                "type": "file",
                "filename": f"f{n}.md",
                "filesize": 900,
                "fileurl": f"{FILES}/{course}/mod_resource/content/0/f{n}.md",
                "timecreated": stamp,
            }
        ],
    }


class Moodle:
    """A Moodle as its web service answers, written for these tests. Algebra 3: four lectures put
    there over the weeks; Analysis 3: what `analysis` holds (a forum, text areas, homework...)."""

    def __init__(self, stamps: list[int], analysis: list[dict] | None = None) -> None:
        self.stamps = stamps
        self.analysis = analysis or []
        self.calls = 0

    def call(self, site, key, function, params=None):
        if function == "core_webservice_get_site_info":
            names = ("core_course_get_contents", "core_enrol_get_users_courses")
            return {"sitename": "U", "userid": 5, "functions": [{"name": n} for n in names]}
        if function == "core_enrol_get_users_courses":
            return [
                {"id": 31, "fullname": "Algebra 3", "shortname": "ALG3", "visible": 1},
                {"id": 32, "fullname": "Analysis 3", "shortname": "AN3", "visible": 1},
                {"id": 33, "fullname": "Analysis 3 (2025)", "shortname": "AN3-25", "visible": 1},
            ]
        assert function == "core_course_get_contents"
        self.calls += 1
        if params["courseid"] != 31:
            return self.analysis
        lectures = [
            _file(3100 + k, t, s) for k, (t, s) in enumerate(zip(TITLES, self.stamps, strict=False), 1)
        ]
        return [
            {"id": 0, "section": 0, "name": "General", "modules": []},
            {"id": 1, "section": 1, "name": "Lectures", "modules": lectures},
        ]

    @staticmethod
    def fetch(url, *, limit):
        return b"# Notes\n\n## A heading\n\nSome text."


def _app(root: Path, moodle: Moodle) -> TestClient:
    config = Config(
        store=root,
        clock=lambda: NOW,
        background=False,
        sweep_every=None,
        remind_every=None,
        moodle_call=moodle.call,
        fetch_file=moodle.fetch,
    )
    return TestClient(create_app(config))


def _read(root: Path, sub: service.Subscription, moodle: Moodle, course: str = "Algebra 3"):
    sub = service.connect_moodle_key(sub, KEY, call=moodle.call, now=NOW, site_text=SITE)
    index = service.topic_courses(sub).index(course)
    read, summary = service.read_moodle_course(
        root, sub, index, call=moodle.call, fetch=moodle.fetch, now=NOW
    )
    service.save_subscription(root, read)
    return read, summary


def test_a_week_that_goes_on_from_an_earlier_part_says_so_on_its_sessions(tmp_path):
    sub = _plan(tmp_path)
    mondays = service._teaching(sub, "Algebra 3")[0]
    chosen = [mondays[0], mondays[2], mondays[4], mondays[6]]  # weeks 1, 3, 5, 7
    read, _summary = _read(tmp_path, sub, Moodle([_friday(m) for m in chosen]))
    index = service._syllabus_index(read)
    # Week 2 has no file of its own and goes on from Lecture 1: its sessions say so (AUDIT 142).
    label = service._week_label(mondays[1])
    assert service._syllabus_of_name(f"Algebra 3 · week of {label}", "Algebra 3", index) == [TITLES[0]]
    # Weeks 8 and after go on from Lecture 4, the last part on Moodle.
    later = service._week_label(mondays[8])
    assert service._syllabus_of_name(f"Algebra 3 · week of {later}", "Algebra 3", index) == [TITLES[3]]
    # And they say how to study it, from the course's plan.
    plan = service.PlanReport.from_dict(read.plan)
    session = next(s for s in plan.sessions if s.topic == f"Algebra 3 · week of {later}")
    assert service._steps_of(session, index)[:2] == ["Read", "Test yourself"]


def test_notes_linked_in_a_text_area_or_a_section_s_description_are_read(tmp_path):
    analysis = [
        {
            "id": 1,
            "section": 1,
            "name": "Chapter 1",
            "summary": f'<p>Read <a href="{SITE}/pluginfile.php/77/course/section/1/ch1.pdf">'
            "Chapter 1 notes</a>.</p>",
            "modules": [
                {
                    "id": 3203,
                    "name": "Exercises",
                    "modname": "label",
                    "description": f'<a href="{FILES}/78/mod_label/intro/ex1.pdf">Exercise sheet 1</a>'
                    '<a href="https://elsewhere.example/ex.pdf">not the course\'s site</a>',
                },
                {
                    "id": 3204,
                    "name": "Homework 1",
                    "modname": "assign",
                    "url": f"{SITE}/mod/assign/view.php?id=3204",
                },
            ],
        }
    ]
    found = studyplan.sections(analysis, SITE)
    files = [f for sec in found for a in sec.activities for f in a.files]
    assert [f.activity for f in files] == ["Chapter 1 notes", "Exercise sheet 1"]
    # Fetched through the web service with the key, opened by the student through Moodle's own page.
    assert files[0].address == f"{FILES}/77/course/section/1/ch1.pdf"
    assert files[0].page == f"{SITE}/pluginfile.php/77/course/section/1/ch1.pdf"
    sub = _plan(tmp_path)
    moodle = Moodle([], analysis)
    _read_sub, summary = _read(tmp_path, sub, moodle, "Analysis 3")
    assert summary["files"] == 2


def test_a_course_with_nothing_to_read_says_what_moodle_sent_and_stays_on_the_course(tmp_path):
    nothing = [
        {
            "id": 0,
            "section": 0,
            "name": "General",
            "modules": [
                {
                    "id": 1,
                    "name": "Announcements",
                    "modname": "forum",
                    "url": f"{SITE}/mod/forum/view.php?id=1",
                },
                {"id": 2, "name": "Welcome", "modname": "label", "description": "<p>Welcome!</p>"},
                {
                    "id": 3,
                    "name": "Homework 1",
                    "modname": "assign",
                    "url": f"{SITE}/mod/assign/view.php?id=3",
                },
            ],
        }
    ]
    sub = _plan(tmp_path)
    token = sub.token
    moodle = Moodle([_friday(service._teaching(sub, "Algebra 3")[0][0])], nothing)
    with _app(tmp_path, moodle) as web:
        web.post(f"/p/{token}/moodle/key", data={"key": KEY, "site": SITE, "back": "guide"})
        analysis = service.topic_courses(sub).index("Analysis 3")
        answer = web.post(f"/p/{token}/courses/{analysis}/moodle/read", follow_redirects=False)
        assert answer.status_code == 303 and answer.headers["location"].endswith(
            f"/courses/{analysis}#moodle"
        )
        page = unescape(web.get(f"/p/{token}/courses/{analysis}").text)
        said = (
            "Moodle sent this course with 3 activities and no file of notes in them: "
            "forums, text areas, assignments."
        )
        assert said in page and "are you enrolled" not in page
        # The ways out, on the course itself: another of the student's courses, another address,
        # or the timetable's lectures alone.
        assert "Use this course" in page and "Analysis 3 (2025)" in page
        assert "Add your teacher's site or a document" in page and "Only the lectures of my timetable" in page
        courses = unescape(web.get(f"/p/{token}/courses").text)
        assert said in courses
    with service.use("fr"):
        assert "Moodle a envoyé ce cours vide" in service._moodle_nothing({})


def test_a_course_planned_from_moodle_is_looked_at_again_once_a_day(tmp_path):
    sub = _plan(tmp_path)
    mondays = service._teaching(sub, "Algebra 3")[0]
    moodle = Moodle([_friday(m) for m in (mondays[0], mondays[2], mondays[4])])
    read, _summary = _read(tmp_path, sub, moodle)
    algebra = service.topic_courses(read).index("Algebra 3")
    assert service.moodle_due(read, NOW) == []
    tomorrow = NOW + timedelta(days=1)
    assert service.moodle_due(read, tomorrow) == [algebra]
    # Nothing changed on Moodle: the plan stays, its files are not read again.
    summary = service.plan_moodle_course(
        tmp_path, read.token, algebra, call=moodle.call, now=tomorrow, again=True
    )
    kept = service.load_subscription(tmp_path, read.token)
    assert summary["unchanged"] and kept.options["study_plans"]["algebra 3"]["by"] == "rules"
    assert service.moodle_due(kept, tomorrow) == []
    # A teacher put the next lecture there: the course is planned again, its files to read.
    moodle.stamps.append(_friday(mondays[6]))
    later = tomorrow + timedelta(days=1)
    summary = service.plan_moodle_course(
        tmp_path, read.token, algebra, call=moodle.call, now=later, again=True
    )
    assert not summary.get("unchanged") and summary["pending"] == 4


def test_courses_shows_this_week_course_by_course_and_the_whole_semester(tmp_path):
    sub = _plan(tmp_path)
    token = sub.token
    mondays = service._teaching(sub, "Algebra 3")[0]
    read, _summary = _read(
        tmp_path, sub, Moodle([_friday(m) for m in (mondays[0], mondays[2], mondays[4], mondays[6])])
    )
    algebra = service.topic_courses(read).index("Algebra 3")
    with _app(tmp_path, Moodle([])) as web:
        page = unescape(web.get(f"/p/{token}/courses").text)
        assert "What · week 8" in page and "Lecture 4 Cyclic groups" in page and "goes on from week 7" in page
        assert (
            f'action="/p/{token}/courses/{algebra}/steps"' in page and 'name="step" value="self-test"' in page
        )
        assert "When" in page and "Start now" in page and "How this plan was made" in page
        # A step ticked is kept, and the card shows it done.
        monday = mondays[7].isoformat()
        web.post(f"/p/{token}/courses/{algebra}/steps", data={"monday": monday, "step": "read"})
        page = unescape(web.get(f"/p/{token}/courses").text)
        assert 'name="step" value="read" checked' in page
        assert (
            web.post(f"/p/{token}/courses/{algebra}/steps", data={"monday": "2020-01-06"}).status_code == 400
        )
        # The whole semester: a row per course, a box per week of lectures, this week marked.
        grid = unescape(web.get(f"/p/{token}/courses?view=semester").text)
        assert 'class="sem-grid"' in grid and 'aria-current="date"' in grid
        assert "Lecture 1 Properties of integers" in grid and "goes on" in grid
        assert f'href="/p/{token}/courses/{algebra}#w2"' in grid
