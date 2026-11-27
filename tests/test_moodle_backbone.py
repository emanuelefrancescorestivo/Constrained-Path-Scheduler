"""
The backbone: Moodle to a plan of when, what and how to revise, easy, intuitive, effective
(DECISIONS.md D213). The owner, on their own Moodle: an algebra course's "Lecture 1 Properties of
integers" to "Lecture 4 Cyclic groups" were all planned in its first week, read as the
timetable's first four classes, and weeks 3 to 14 had nothing; "this pipeline ... is LITERALLY
THE BACKBONE of a study planner so this has to be EASY, INTUITIVE, EFFECTIVE".

What must hold: a course's files whose names give no day nor week are placed in the week their
teacher put them on Moodle, when the course's files went up over its weeks; put there all at
once, a lecture's number is a class of the timetable only when the numbers keep up with the
classes held, else an order spread over the weeks; courses waiting their turn to be read say so;
Courses shows the Moodle side's one next action and each course's week, what and how; the
sidebar has Courses as a section.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SITE = "https://moodle.university.example"
FILES = f"{SITE}/webservice/pluginfile.php"
KEY = "0123456789abcdef0123456789abcdef"
PARIS = ZoneInfo("Europe/Paris")
LATE = datetime(2026, 12, 9, 9, 0, tzinfo=UTC)  # late in the semester: many classes held
TITLES = [
    "Lecture 1 Properties of integers",
    "Lecture 2 Introduction to groups",
    "Lecture 3 Finite Groups, Subgroups",
    "Lecture 4 Cyclic groups",
]


def _plan(root: Path, now: datetime) -> service.Subscription:
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
        now=now,
    )
    sub = service.finish_first_run(sub)
    service.save_subscription(root, sub)
    return sub


def _friday(monday: date) -> int:
    """A Friday afternoon of that week, as Moodle writes a file's `timecreated`."""
    return int(datetime.combine(monday + timedelta(days=4), time(16, 0), tzinfo=PARIS).timestamp())


class Moodle:
    """A university's Moodle as its web service answers, written for these tests: the algebra
    course in one section of lectures numbered by chapter and one of exercise sheets, each file
    put there on a given day."""

    def __init__(self, stamps: list[int]) -> None:
        self.stamps = stamps

    def call(self, site, key, function, params=None):
        if function == "core_webservice_get_site_info":
            names = ("core_course_get_contents", "core_enrol_get_users_courses")
            return {"sitename": "U", "userid": 5, "functions": [{"name": n} for n in names]}
        if function == "core_enrol_get_users_courses":
            return [
                {"id": 31, "fullname": "Algebra 3", "shortname": "ALG3", "visible": 1},
                {"id": 32, "fullname": "Analysis 3", "shortname": "AN3", "visible": 1},
            ]
        assert function == "core_course_get_contents"
        return self.contents(params["courseid"])

    def contents(self, number: int) -> list[dict]:
        def module(n: int, name: str, stamp: int) -> dict:
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
                        "fileurl": f"{FILES}/{number}/mod_resource/content/0/f{n}.md",
                        "timecreated": stamp,
                    }
                ],
            }

        lectures = [
            module(number * 100 + k, t, s)
            for k, (t, s) in enumerate(zip(TITLES, self.stamps, strict=True), 1)
        ]
        sheet = module(number * 100 + 50, "td 05-students", self.stamps[-1])
        return [
            {"id": 0, "section": 0, "name": "General", "modules": []},
            {"id": 1, "section": 1, "name": "Lectures", "modules": lectures},
            {"id": 2, "section": 2, "name": "TD", "modules": [sheet]},
        ]

    @staticmethod
    def fetch(url, *, limit):
        return b"# Notes\n\n## A heading\n\nSome text."


def _read(root: Path, sub: service.Subscription, moodle: Moodle, now: datetime) -> service.Subscription:
    sub = service.connect_moodle_key(sub, KEY, call=moodle.call, now=now, site_text=SITE)
    algebra = service.topic_courses(sub).index("Algebra 3")
    read, _summary = service.read_moodle_course(
        root, sub, algebra, call=moodle.call, fetch=moodle.fetch, now=now
    )
    service.save_subscription(root, read)
    return read


def _weeks(sub: service.Subscription) -> dict[str, str]:
    return {x["title"]: x["week"] for x in sub.options["materials"][service.course_key("Algebra 3")]}


def test_files_go_in_the_week_their_teacher_put_them_on_moodle(tmp_path):
    sub = _plan(tmp_path, LATE)
    mondays = service._teaching(sub, "Algebra 3")[0]
    # Lecture k put on Moodle on the Friday of the course's week 3k - 2: weeks 1, 4, 7 and 10.
    chosen = [mondays[0], mondays[3], mondays[6], mondays[9]]
    read = _read(tmp_path, sub, Moodle([_friday(m) for m in chosen]), LATE)
    weeks = _weeks(read)
    assert [weeks[t] for t in TITLES] == [m.isoformat() for m in chosen]
    assert weeks["td 05-students"] == chosen[-1].isoformat()
    algebra = service.topic_courses(read).index("Algebra 3")
    plan = service.study_plan_view(read, algebra, LATE, store=tmp_path)
    assert plan["how"] == "uploaded" and not plan["in_order"]
    # Every week of lectures has its part: a week without a new file goes on with the last.
    assert len(plan["weeks"]) == len(mondays)
    firsts = [w["topics"][0]["title"] for w in plan["weeks"] if not w["continued"]]
    assert firsts == TITLES
    assert plan["weeks"][1]["continued"] == 1 and plan["weeks"][1]["topics"][0]["title"] == TITLES[0]
    # A file put there at the weekend is for the week after.
    saturday = int(datetime.combine(mondays[1] + timedelta(days=5), time(10, 0), tzinfo=PARIS).timestamp())
    stamps = [_friday(mondays[0]), saturday, _friday(mondays[6]), _friday(mondays[9])]
    again = _read(tmp_path, _plan(tmp_path, LATE), Moodle(stamps), LATE)
    assert _weeks(again)[TITLES[1]] == mondays[2].isoformat()


def test_files_put_there_at_once_are_read_in_their_order_when_their_numbers_are_chapters(tmp_path):
    sub = _plan(tmp_path, LATE)
    mondays = service._teaching(sub, "Algebra 3")[0]
    before = _friday(mondays[0] - timedelta(days=14))  # everything put there before the term
    read = _read(tmp_path, sub, Moodle([before] * 4), LATE)
    weeks = _weeks(read)
    placed = [weeks[t] for t in TITLES]
    # Not the four first classes of the timetable: four chapters over the course's weeks.
    assert placed[0] == mondays[0].isoformat() and len(set(placed)) == 4
    assert placed == sorted(placed) and placed[-1] >= mondays[len(mondays) // 2].isoformat()
    algebra = service.topic_courses(read).index("Algebra 3")
    assert service.study_plan_view(read, algebra, LATE, store=tmp_path)["how"] == "order"


def test_early_in_the_term_numbers_that_keep_up_with_the_classes_are_classes(tmp_path):
    early = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # the course's second week
    sub = _plan(tmp_path, early)
    mondays = service._teaching(sub, "Algebra 3")[0]
    read = _read(tmp_path, sub, Moodle([_friday(mondays[0] - timedelta(days=14))] * 4), early)
    weeks = _weeks(read)
    # Two classes a week: lectures 1 and 2 in the first week, 3 and 4 in the second, as D174.
    assert weeks[TITLES[0]] == weeks[TITLES[1]] == mondays[0].isoformat()
    assert weeks[TITLES[2]] == weeks[TITLES[3]] == mondays[1].isoformat()


def test_courses_waiting_their_turn_say_so(tmp_path):
    sub = _plan(tmp_path, LATE)
    moodle = Moodle([_friday(m) for m in service._teaching(sub, "Algebra 3")[0][:4]])
    sub = service.connect_moodle_key(sub, KEY, call=moodle.call, now=LATE, site_text=SITE)
    service.save_subscription(tmp_path, sub)
    analysis = service.topic_courses(sub).index("Analysis 3")
    service.plan_moodle_course(tmp_path, sub.token, analysis, call=moodle.call, now=LATE, queued=True)
    state = service.moodle_reading(tmp_path, sub, "Analysis 3", LATE)
    assert state["queued"] and state["done"] == 0 and not state["stopped"]
    with service.use("fr"):
        said = service.moodle_readings(tmp_path, sub, LATE)["courses"][0]["said"]
    assert said == "En attente des cours avant lui"
    # Still waiting after ten minutes is not stopped: the course before may be long.
    assert not service.moodle_reading(tmp_path, sub, "Analysis 3", LATE + timedelta(minutes=20))["stopped"]
    # Once its turn comes, it says how far it is.
    seen = []
    keep = service._set_reading

    def recorded(store, token, key, state):
        seen.append(state)
        keep(store, token, key, state)

    service._set_reading = recorded
    try:
        service.read_moodle_files(
            tmp_path, sub.token, analysis, call=moodle.call, fetch=moodle.fetch, clock=lambda: LATE
        )
    finally:
        service._set_reading = keep
    assert seen[0]["done"] == 0 and seen[-1] is None


def _app(root: Path, moodle: Moodle) -> TestClient:
    config = Config(
        store=root,
        clock=lambda: LATE,
        background=False,
        sweep_every=None,
        remind_every=None,
        moodle_call=moodle.call,
        fetch_file=moodle.fetch,
    )
    return TestClient(create_app(config))


def test_courses_shows_the_moodle_side_s_next_action_and_each_course_s_week(tmp_path):
    sub = _plan(tmp_path, LATE)
    token = sub.token
    moodle = Moodle([_friday(m) for m in service._teaching(sub, "Algebra 3")[0][:10:3]])
    with _app(tmp_path, moodle) as web:
        page = unescape(web.get(f"/p/{token}/courses").text)
        assert "Plan every course from Moodle" in page and f"/p/{token}/guide/moodle#step-key" in page
        # The laptop's sidebar has Courses as a section of its own.
        assert f'href="/p/{token}/courses" aria-current="page"' in page
        web.post(f"/p/{token}/moodle/key", data={"key": KEY, "site": SITE, "back": "guide"})
        page = unescape(web.get(f"/p/{token}/courses").text)
        assert "2 courses to plan from Moodle" in page and f'action="/p/{token}/moodle/read-all"' in page
        assert page.count("Plan it from Moodle") == 2
        web.post(f"/p/{token}/moodle/read-all")
        page = unescape(web.get(f"/p/{token}/courses").text)
        assert "courses to plan from Moodle" not in page and "Plan every course" not in page
        # D214: each course's card is this week's unit, design C (what, how, when), with how its
        # plan was made a tap away.
        assert "What · week 11" in page or "What · week 10" in page
        assert "Lecture 4 Cyclic groups" in page and "each file in the week it was put on Moodle" in page
        assert page.count("How this plan was made") == 2
        # The course's page opens on its plan, said to follow the files' days on Moodle.
        algebra = service.topic_courses(sub).index("Algebra 3")
        course = unescape(web.get(f"/p/{token}/courses/{algebra}").text)
        assert course.index('id="study-plan"') < course.index('id="moodle"')
        assert "each file is in the week your teacher put it on Moodle" in course
