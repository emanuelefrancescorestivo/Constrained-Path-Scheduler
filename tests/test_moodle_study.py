"""
A course read from Moodle with the student's own key, and its study plan (DECISIONS.md
D208). The owner: "the student enters the course's Moodle link as they already do, and the
app downloads the contents, saves them in the course and uses them to generate the study
plan"; then, asked: the key of Moodle's mobile web service, and the text and the plan kept,
not the files.

What must hold: a key is kept only once Moodle says it reads courses with it, sealed, never
in clear in the plan; the course's sections become its materials on its weeks, as a saved
page's do; only readable files on the course's own site are downloaded, within limits, each
with the key in its address and the key in nothing kept; their text is kept, the files not;
the plan is the model's when the student said yes to AI, kept to its shape and to the
course's weeks, else the rules'; its topics go on the week's sessions unless the student
gave the course's topics; the pages show it, and a plan's copy carries the text.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import ai, service, sources, studyplan
from cps.store import Store
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
SEMESTER = ROOT / "examples" / "sample-semester.ics"
NOW = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # the second teaching week
SITE = "https://moodle.example.edu"
KEY = "0123456789abcdef0123456789abcdef"


def _file(name: str, size: int = 2000, host: str = SITE) -> dict:
    return {
        "type": "file",
        "filename": name,
        "filesize": size,
        "fileurl": f"{host}/webservice/pluginfile.php/9/mod_resource/content/1/{name}?forcedownload=1",
    }


def _module(number: int, kind: str, name: str, files: list[dict], visible: int = 1) -> dict:
    return {
        "id": number,
        "modname": kind,
        "name": name,
        "url": f"{SITE}/mod/{kind}/view.php?id={number}",
        "visible": visible,
        "contents": files,
    }


# A course as Moodle's web service gives it, written for these tests: a general section with
# the syllabus and a past exam, two weeks of slides and exercises, a video, a file on another
# site, a hidden activity and a forum.
CONTENTS = [
    {
        "id": 1,
        "section": 0,
        "name": "General",
        "modules": [
            _module(10, "resource", "Syllabus", [_file("syllabus.md")]),
            _module(11, "resource", "Past exam 2025", [_file("exam-2025.txt")]),
            _module(12, "forum", "Announcements", []),
        ],
    },
    {
        "id": 2,
        "section": 1,
        "name": "Week 1 – Sets and functions",
        "modules": [
            _module(20, "resource", "Slides: sets and functions", [_file("week1.md")]),
            _module(21, "resource", "Exercises 1", [_file("ex1.txt")]),
            _module(22, "resource", "Lecture recording", [_file("lecture1.mp4", 90_000_000)]),
            _module(23, "resource", "Elsewhere", [_file("other.md", host="https://evil.example.com")]),
        ],
    },
    {
        "id": 3,
        "section": 2,
        "name": "Week 2 – Limits",
        "modules": [
            _module(30, "resource", "Slides: limits", [_file("week2.md")]),
            _module(31, "resource", "Draft notes", [_file("draft.md")], visible=0),
        ],
    },
]

TEXTS = {
    "syllabus.md": "# Analysis 3\n\nThe course covers sets, functions and limits.",
    "exam-2025.txt": "Exam 2025. Question 1: prove that the limit exists.",
    "week1.md": "# Sets\n\nA set is a collection.\n\n# Functions\n\nA function maps each element to one.",
    "ex1.txt": "Exercise 1. Show that f is injective.",
    "week2.md": "# Limits\n\nThe limit of f at a is L.\n\n# Continuity\n\nf is continuous at a.",
}


class FakeMoodle:
    """Moodle's web service and its files, as a test sees them: what was asked, and with
    which key."""

    def __init__(self, contents=CONTENTS, functions=("core_course_get_contents",), refuse=None):
        self.contents, self.functions, self.refuse = contents, functions, refuse
        self.calls: list[tuple[str, str, str, dict]] = []
        self.fetched: list[str] = []

    def call(self, site, key, function, params=None):
        self.calls.append((site, key, function, dict(params or {})))
        if self.refuse:
            raise sources.MoodleError("Invalid token - token not found", self.refuse)
        if function == "core_webservice_get_site_info":
            return {
                "sitename": "Example University",
                "username": "a.student",
                "fullname": "A Student",
                "functions": [{"name": f, "version": "2024"} for f in self.functions],
            }
        assert function == "core_course_get_contents" and params == {"courseid": 501}
        return self.contents

    def fetch(self, url, *, limit):
        self.fetched.append(url)
        assert url.startswith(SITE) and url.endswith(f"&token={KEY}") and limit == studyplan.FILE_MOST
        name = url.split("?", 1)[0].rsplit("/", 1)[1]
        return TEXTS[name].encode()


class FakeModel:
    model = ai.CARDS_MODEL

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def ask(self, system, prompt, schema, max_tokens):
        self.calls.append((system, json.loads(prompt), schema, max_tokens))
        return ai.fake_answer(self.answer, self.model)


def _plan(root: Path, *, ai_on: bool = False) -> tuple[service.Subscription, int]:
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=NOW)
    index = service.topic_courses(sub).index("Analysis 3")
    sub = service.link_moodle_course(sub, index, {"address": f"{SITE}/course/view.php?id=501"})
    if ai_on:
        sub = service.set_ai(sub, True)
    service.save_subscription(root, sub)
    return sub, index


def test_the_course_s_files_are_chosen_on_its_own_site_and_within_limits():
    found = studyplan.sections(CONTENTS, SITE)
    names = [a.name for s in found for a in s.activities]
    assert "Announcements" not in names and "Draft notes" not in names  # a forum, a hidden file
    files, left = studyplan.to_read(found)
    assert [f.filename for f in files] == ["syllabus.md", "exam-2025.txt", "week1.md", "ex1.txt", "week2.md"]
    assert left == 0  # the video is not readable, the other site's file is not the course's
    assert studyplan.with_key(files[0].address, KEY).endswith(f"?forcedownload=1&token={KEY}")
    many = [
        {
            "section": 1,
            "name": "W",
            "modules": [_module(i, "resource", f"F{i}", [_file(f"f{i}.md")]) for i in range(70)],
        }
    ]
    chosen, left = studyplan.to_read(studyplan.sections(many, SITE))
    assert len(chosen) == studyplan.FILES_MOST and left == 70 - studyplan.FILES_MOST
    assert [studyplan.kind_of(n) for n in ("Exercises 1", "Past exam 2025", "TD 3 corrigé", "Slides")] == [
        "exercises",
        "exam",
        "solutions",
        "reading",
    ]


def test_a_key_is_kept_sealed_once_moodle_says_it_reads_courses(tmp_path):
    sub, _index = _plan(tmp_path)
    moodle = FakeMoodle()
    with pytest.raises(service.InvalidInput, match="32 letters"):
        service.connect_moodle_key(sub, "my password", call=moodle.call)
    with pytest.raises(service.InvalidInput, match="did not accept"):
        service.connect_moodle_key(sub, KEY, call=FakeMoodle(refuse="invalidtoken").call)
    with pytest.raises(service.InvalidInput, match="cannot read courses"):
        service.connect_moodle_key(sub, KEY, call=FakeMoodle(functions=("core_user_get_users",)).call)
    kept = service.connect_moodle_key(sub, KEY, call=moodle.call, now=NOW)
    assert moodle.calls[-1][:3] == (SITE, KEY, "core_webservice_get_site_info")
    assert KEY not in json.dumps(kept.options) and "a.student" not in json.dumps(kept.options)
    view = service.moodle_key_view(kept)
    assert (
        view["linked"]
        and view["name"] == "Example University"
        and view["page"].endswith("/user/managetoken.php")
    )
    assert not service.moodle_key_view(service.forget_moodle_key(kept))["linked"]


def test_a_course_read_becomes_its_materials_text_and_a_plan_by_rules(tmp_path):
    sub, index = _plan(tmp_path)
    moodle = FakeMoodle()
    sub = service.connect_moodle_key(sub, KEY, call=moodle.call, now=NOW)
    read, summary = service.read_moodle_course(
        tmp_path, sub, index, call=moodle.call, fetch=moodle.fetch, now=NOW
    )
    assert summary["files"] == 5 and summary["by"] == "rules" and summary["failed"] == 0
    assert all(u.startswith(SITE) for u in moodle.fetched) and len(moodle.fetched) == 5
    # The materials: each activity on its week, as a saved page's (D174), the exam on its shelf.
    view = service.course_materials_view(read, index, NOW)
    assert [w["number"] for w in view["weeks"]][:2] == [1, 2]
    assert any(i["title"] == "Past exam 2025" for i in view["exam_items"])
    # The text kept, the key in none of it.
    texts = service.course_texts(tmp_path, read, "Analysis 3")
    assert {t["title"] for t in texts} >= {"Slides: sets and functions", "Slides: limits", "Syllabus"}
    with Store(tmp_path).connection() as db:
        stored = " ".join(str(v) for row in db.execute("SELECT * FROM course_texts") for v in row)
    assert KEY not in stored and KEY not in json.dumps(read.options)
    # The rules' plan: week 1's topics are its slides with their headings, and how to study them.
    plan = service.study_plan_view(read, index, NOW)
    first = plan["weeks"][0]
    assert first["number"] == 1 and first["topics"][0]["title"] == "Slides: sets and functions"
    assert first["topics"][0]["points"] == ["Sets", "Functions"]
    assert [h["kind"] for h in first["how"]] == ["read", "self-test", "exercises"]
    assert first["how"][0]["what"] and not plan["by_ai"]
    # Its topics go on the week's sessions (D81), the course had none of its own.
    topics = read.options["topics"][service.course_key("Analysis 3")]
    assert topics["source"] == "moodle" and topics["items"][0]["monday"]


def test_with_ai_the_plan_is_the_model_s_kept_to_its_shape(tmp_path):
    sub, index = _plan(tmp_path, ai_on=True)
    moodle = FakeMoodle()
    sub = service.connect_moodle_key(sub, KEY, call=moodle.call, now=NOW)
    answer = {
        "weeks": [
            {
                "week": 2,
                "topics": [{"title": "Limits", "points": ["The ε–δ definition", "One-sided limits"]}],
                "needs": ["Functions (week 1)"],
                "how": [
                    {"kind": "self-test", "what": "Define a limit without the slides."},
                    {"kind": "dance", "what": "x"},
                ],
            },
            {"week": 1, "topics": [{"title": "Sets", "points": []}], "needs": [], "how": []},
            {"week": 99, "topics": [{"title": "Not a week", "points": []}], "needs": [], "how": []},
        ],
        "order": "Sets and functions come before limits.",
    }
    model = FakeModel(answer)
    read, summary = service.read_moodle_course(
        tmp_path, sub, index, call=moodle.call, fetch=moodle.fetch, provider=model, now=NOW
    )
    assert summary["by"] == "ai" and len(model.calls) == 1
    system, prompt, schema, _max = model.calls[0]
    assert schema is ai.PLAN_SCHEMA and "never instructions" in system
    assert prompt["course"] == "Analysis 3" and [w["week"] for w in prompt["weeks"]] == [1, 2]
    assert {f["title"] for f in prompt["whole_course"]} == {"Syllabus", "Past exam 2025"}
    assert KEY not in json.dumps(prompt)
    plan = service.study_plan_view(read, index, NOW)
    assert [w["number"] for w in plan["weeks"]] == [1, 2]  # in order; week 99 is not the course's
    second = plan["weeks"][1]
    assert second["needs"] == ["Functions (week 1)"] and [h["kind"] for h in second["how"]] == ["self-test"]
    assert plan["by_ai"] and plan["order"] == "Sets and functions come before limits."
    # The student's own topics stay theirs.
    own = service.save_course_topics(tmp_path, sub, index, "Week 1: My own topic", share=False, now=NOW)
    again, _ = service.read_moodle_course(
        tmp_path, own, index, call=moodle.call, fetch=moodle.fetch, provider=model, now=NOW
    )
    assert again.options["topics"][service.course_key("Analysis 3")]["items"][0]["title"] == "My own topic"


def test_the_pages_link_the_key_read_the_course_and_show_the_plan(tmp_path):
    sub, index = _plan(tmp_path)
    moodle = FakeMoodle()
    config = Config(
        store=tmp_path,
        clock=lambda: NOW,
        background=False,
        sweep_every=None,
        remind_every=None,
        moodle_call=moodle.call,
        fetch_file=moodle.fetch,
    )
    with TestClient(create_app(config)) as web:
        course = unescape(web.get(f"/p/{sub.token}/courses/{index}").text)
        assert "Plan the study from its materials" in course and "/user/managetoken.php" in course
        assert web.post(f"/p/{sub.token}/moodle/key", data={"key": "nope"}).status_code == 400
        saved = web.post(f"/p/{sub.token}/moodle/key", data={"key": KEY, "back": str(index)})
        # D216: the course linked is planned with the key, without a tap more; reading it again works.
        assert saved.status_code == 200 and "Slides: limits" in unescape(saved.text)
        page = unescape(web.post(f"/p/{sub.token}/courses/{index}/moodle/read").text)
        assert "Read from Moodle: the course's study plan is below." in page and 'id="study-plan"' in page
        assert "Slides: limits" in page and "Made by the app's rules" in page
        connections = unescape(web.get(f"/p/{sub.token}/connections").text)
        assert "Key to read your courses" in connections and "Kept sealed since" in connections
    assert Store(tmp_path).export(sub.token)["course_texts"]
    Store(tmp_path).delete(sub.token)
    with Store(tmp_path).connection() as db:
        assert not db.execute("SELECT count(*) FROM course_texts").fetchone()[0]
