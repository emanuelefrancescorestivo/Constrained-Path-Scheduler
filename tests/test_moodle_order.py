"""
Moodle as each teacher uses it (DECISIONS.md D212). The owner: "none of our professors uses
moodle in a homogeneous standardised way, so we need to take into account that moodle only
serves to parse lecture materials week by week if done properly or else just for the topic
progression, whilst we want to maintain the flexibility in case one day the app is deployed to
institutions that do use it as it should be used. again, in our uni moodle is not used to
schedule exams or deadlines".

What must hold: a course whose Moodle names its weeks is placed by them, as before; one that
gives only an order has each part spread over the course's weeks of lectures in that order, each
week of the plan going on from the last part when a part runs over several, the plan saying so
and the student able to move a file; the model is told and given every week; the exams of the
plan stay those of the timetable, whatever Moodle holds.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import ai, service, showcase
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # the sample semester's second teaching week
SITE = showcase.DEMO_MOODLE


def _plan(root: Path, *, ai_on: bool = False) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[
            service.SubjectSpec("Algebra 3", None, familiarity=3),
            service.SubjectSpec("Advanced Statistics", None, familiarity=3),
        ],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    sub = service.finish_first_run(sub)
    if ai_on:
        sub = service.set_ai(sub, True)
    sub = service.connect_moodle_key(
        sub, showcase.DEMO_KEY, call=showcase.demo_moodle_call, now=NOW, site_text=SITE
    )
    service.save_subscription(root, sub)
    return sub


def test_a_course_whose_moodle_gives_an_order_is_spread_over_its_weeks(tmp_path):
    sub = _plan(tmp_path)
    stats = service.topic_courses(sub).index("Advanced Statistics")
    exams_before = [e for e in sub.options.get("exams") or ()]
    read, summary = service.read_moodle_course(
        tmp_path, sub, stats, call=showcase.demo_moodle_call, fetch=showcase.demo_fetch_file, now=NOW
    )
    assert summary["by"] == "rules" and summary["files"] == 12
    plan = service.study_plan_view(read, stats, NOW, store=tmp_path)
    assert plan["in_order"] and plan["steps"] == 6
    weeks = service._teaching(read, "Advanced Statistics")[0]
    assert plan["teaching"] == len(weeks) > plan["steps"]  # more weeks than parts: some run over two
    # Every week of lectures has its part, in the course's order, the first in the first week.
    assert [w["number"] for w in plan["weeks"]] == list(range(1, len(weeks) + 1))
    firsts = [w for w in plan["weeks"] if not w["continued"]]
    assert [w["topics"][0]["title"] for w in firsts] == [
        "Slides · Estimators and their bias",
        "Slides · Confidence intervals",
        "Slides · Hypothesis tests",
        "Slides · Linear regression",
        "Slides · Analysis of variance",
        "Slides · Non-parametric tests",
    ]
    assert plan["weeks"][0]["number"] == 1 and not plan["weeks"][0]["continued"]
    going_on = next(w for w in plan["weeks"] if w["continued"])
    assert going_on["topics"] == plan["weeks"][going_on["continued"] - 1]["topics"]
    # The sessions' topics are the parts once each, not again in the weeks they go on.
    titles = [t["title"] for t in read.options["topics"][service.course_key("Advanced Statistics")]["items"]]
    assert len(titles) == len(set(titles)) == 6
    # The materials: each part on the week its share begins, the exam's paper apart.
    materials = service.course_materials_view(read, stats, NOW)
    assert any(s["in_order"] for s in materials["sources"])
    assert [i["title"] for i in materials["exam_items"]] == ["Past exam 2025"]
    # Moodle does not set the exams: the plan's are the timetable's, as before.
    assert (read.options.get("exams") or []) == exams_before


def test_a_course_whose_moodle_names_its_weeks_is_placed_by_them(tmp_path):
    sub = _plan(tmp_path)
    algebra = service.topic_courses(sub).index("Algebra 3")
    read, _summary = service.read_moodle_course(
        tmp_path, sub, algebra, call=showcase.demo_moodle_call, fetch=showcase.demo_fetch_file, now=NOW
    )
    plan = service.study_plan_view(read, algebra, NOW, store=tmp_path)
    assert not plan["in_order"] and not any(w["continued"] for w in plan["weeks"])


class FakeModel:
    model = ai.CARDS_MODEL  # a model with a price, so the cap lets the call through

    def __init__(self) -> None:
        self.calls: list = []

    def ask(self, system, prompt, schema, max_tokens):
        self.calls.append((system, json.loads(prompt)))
        return ai.fake_answer({"weeks": [], "order": ""}, self.model)


def test_the_model_is_told_the_order_and_given_every_week(tmp_path):
    sub = _plan(tmp_path, ai_on=True)
    stats = service.topic_courses(sub).index("Advanced Statistics")
    model = FakeModel()
    read, summary = service.read_moodle_course(
        tmp_path,
        sub,
        stats,
        call=showcase.demo_moodle_call,
        fetch=showcase.demo_fetch_file,
        provider=model,
        now=NOW,
    )
    system, prompt = model.calls[0]
    assert "gives the order of its materials, not their weeks" in system
    weeks = service._teaching(read, "Advanced Statistics")[0]
    assert [w["week"] for w in prompt["weeks"]] == list(range(1, len(weeks) + 1))
    assert any(not w["files"] for w in prompt["weeks"])  # weeks without files, to plan too
    assert summary["by"] == "rules"  # the model gave nothing usable: the rules' plan, spread
    # A course placed by its weeks gives the model only the weeks with files, as before.
    algebra = service.topic_courses(sub).index("Algebra 3")
    service.read_moodle_course(
        tmp_path,
        sub,
        algebra,
        call=showcase.demo_moodle_call,
        fetch=showcase.demo_fetch_file,
        provider=model,
        now=NOW,
    )
    system, prompt = model.calls[-1]
    assert "not their weeks" not in system and all(w["files"] for w in prompt["weeks"])


def test_the_pages_say_the_course_is_planned_in_its_order_and_moodle_holds_no_exams(tmp_path):
    sub = _plan(tmp_path)
    token = sub.token
    stats = service.topic_courses(sub).index("Advanced Statistics")
    config = Config(
        store=tmp_path,
        clock=lambda: NOW,
        background=False,
        sweep_every=None,
        remind_every=None,
        moodle_call=showcase.demo_moodle_call,
        fetch_file=showcase.demo_fetch_file,
    )
    with TestClient(create_app(config)) as web:
        web.post(f"/p/{token}/courses/{stats}/moodle/read")
        page = unescape(web.get(f"/p/{token}/courses/{stats}").text)
        assert "This course's Moodle gives its order, not its weeks: the app spread its 6 parts" in page
        assert "going on from week" in page and f"/p/{token}/courses/{stats}/materials" in page
        materials = unescape(web.get(f"/p/{token}/courses/{stats}/materials").text)
        assert "Its Moodle gives the course's order, not its weeks" in materials
        guide = unescape(web.get(f"/p/{token}/guide/moodle").text)
        assert "Exams and deadlines are not read from your courses on Moodle" in guide
        connections = unescape(web.get(f"/p/{token}/connections").text)
        assert "Only if your teachers put deadlines on Moodle's calendar" in connections
