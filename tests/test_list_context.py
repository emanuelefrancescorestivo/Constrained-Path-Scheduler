"""
D162: on a wide screen, Tasks, Courses, Flashcards and Community show their list and, beside
it, what helps act on it (the owner's 4-A, 2026-11-19). What must hold: the week's time for
tasks counts the plan's task sessions of this week only, a skipped one never as done; the
learning platform's card says whether its link is in place and what it brought; beside the
feed, the student's groups and classmates they neither follow nor asked to; each page puts
its list and its context in the two columns, Community's context first in the page so a
phone keeps Studying now above the feed; the narrow screen keeps one column.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
STYLE = Path(__file__).resolve().parent.parent / "src" / "cps" / "web" / "static" / "style.css"
NOW = datetime(2026, 9, 29, 7, 0, tzinfo=UTC)  # a Tuesday
PARIS = ZoneInfo("Europe/Paris")
MOODLE = "https://moodle.example.edu/calendar/export_execute.php?userid=7&authtoken=abc&preset_what=all"


def _plan(root: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", date(2027, 1, 26), familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[
            service.TaskSpec("Problem sheet", "2026-10-02 18:00", 4, "Algebra 3"),
            service.TaskSpec("Statistics report", "2026-10-23 18:00", 8, "Advanced Statistics"),
        ],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    sub = service.finish_first_run(sub)
    service.save_subscription(root, sub)
    return sub


def _hours(session: dict) -> float:
    start, end = datetime.fromisoformat(session["start"]), datetime.fromisoformat(session["end"])
    return (end - start) / timedelta(hours=1)


def _local_day(session: dict) -> date:
    return datetime.fromisoformat(session["start"]).astimezone(PARIS).date()


def test_the_week_counts_this_weeks_task_sessions_and_a_skipped_one_is_not_done(tmp_path):
    sub = _plan(tmp_path)
    tasks = [s for s in sub.plan["sessions"] if s["kind"] == "task"]
    monday, sunday = date(2026, 9, 28), date(2026, 10, 4)
    in_week = [s for s in tasks if monday <= _local_day(s) <= sunday]
    assert in_week and len(in_week) < len(tasks)  # the later task has sessions after Sunday
    week = service.task_week(sub, NOW)
    assert week["planned"] == pytest.approx(sum(map(_hours, in_week)))
    assert week["done"] == 0 and week["percent"] == 0
    first = min(tasks, key=lambda s: s["start"])
    assert week["next"]["task"] == service.SessionView(**first).title

    # Two of this week's sessions behind: one kept, one reported skipped.
    kept, skipped = in_week[0], in_week[1]
    plan = {
        **sub.plan,
        "sessions": [s for s in sub.plan["sessions"] if s not in (kept, skipped)],
        "history": [*sub.plan["history"], {**kept, "outcome": "recalled"}, {**skipped, "outcome": "skipped"}],
    }
    week = service.task_week(replace(sub, plan=plan), NOW)
    assert week["planned"] == pytest.approx(sum(map(_hours, in_week)))
    assert week["done"] == pytest.approx(_hours(kept))
    assert week["percent"] == round(100 * _hours(kept) / sum(map(_hours, in_week)))
    assert service.task_week(replace(sub, plan=None), NOW) is None


def test_the_learning_platforms_card_says_what_its_link_brought(tmp_path):
    sub = _plan(tmp_path)
    assert service.tasks_view(sub, NOW)["platform"] == {
        "linked": False,
        "error": None,
        "brought": 0,
        "site": None,
    }
    options = {**sub.options, "deadlines_url": MOODLE, "imported": {"alg|problem sheet": "Problem sheet"}}
    platform = service.tasks_view(replace(sub, options=options), NOW)["platform"]
    assert platform == {"linked": True, "error": None, "brought": 1, "site": "https://moodle.example.edu/my/"}
    broken = {**options, "deadlines_error": "not a calendar"}
    assert service.tasks_view(replace(sub, options=broken), NOW)["platform"]["error"] == "not a calendar"


def _profile(root: Path, token: str, handle: str, programme: str = "L2 Maths") -> None:
    form = {"handle": handle, "university": "Université Lyon 1", "programme": programme, "old_enough": "1"}
    service.save_profile(root, token, form)


def test_beside_the_feed_groups_and_classmates_not_followed_yet(tmp_path):
    sub = _plan(tmp_path)
    assert service.community_context(tmp_path, sub, NOW) == {"groups": [], "classmates": [], "university": ""}
    _profile(tmp_path, sub.token, "alex")
    for handle in ("marco", "lena", "ines", "maya", "tom"):
        _profile(tmp_path, f"tok-{handle}", handle)
    _profile(tmp_path, "tok-zoe", "zoe", programme="M1 History")  # same university, other programme
    service.follow(tmp_path, sub.token, "marco")
    service.answer_follow(tmp_path, "tok-marco", "alex", True)
    service.follow(tmp_path, sub.token, "lena")  # asked, not answered yet
    service.create_group(tmp_path, sub.token, {"name": "Thursday library", "goal": "10"}, now=NOW)

    beside = service.community_context(tmp_path, sub, NOW)
    handles = [m["handle"] for m in beside["classmates"]]
    assert handles == ["ines", "maya", "tom"]  # same programme first, at most three
    assert not {"marco", "lena", "alex"} & set(handles)
    assert beside["university"] == "Université Lyon 1"
    assert [g["name"] for g in beside["groups"]] == ["Thursday library"]
    assert beside["groups"][0]["week"]["goal"] == 10
    view = service.community_view(tmp_path, sub, now=NOW)
    assert view["classmates"] == beside["classmates"] and view["groups"][0]["id"] == beside["groups"][0]["id"]


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def _columns(page: str) -> tuple[str, str]:
    """The list column and the context column of a page, in the page's order."""
    grid = page[page.index('<div class="with-context') :]
    at_list, at_context = grid.index('<div class="list-col">'), grid.index('<div class="context-col">')
    return (
        (grid[at_list:at_context], grid[at_context:])
        if at_list < at_context
        else (
            grid[at_list:],
            grid[at_context:at_list],
        )
    )


def test_each_page_puts_its_list_and_its_context_in_two_columns(web, tmp_path):
    sub = _plan(tmp_path)
    _profile(tmp_path, sub.token, "alex")
    _profile(tmp_path, "tok-ines", "ines")
    service.create_group(tmp_path, sub.token, {"name": "Thursday library", "goal": "10"}, now=NOW)
    base = f"/p/{sub.token}"

    tasks = web.get(f"{base}/tasks").text
    listed, beside = _columns(tasks)
    assert 'data-tour="task-list"' in listed and "Problem sheet" in listed
    assert "<h2>This week</h2>" in beside and "From your learning platform" in beside
    assert re.search(r"[\d.]+ / [\d.]+ h", beside)  # the week's sessions for tasks, done of planned

    courses = web.get(f"{base}/courses").text
    listed, beside = _columns(courses)
    assert 'data-tour="course-list"' in listed
    assert 'class="card import-card"' in beside and 'id="class"' in beside

    cards = web.get(f"{base}/cards").text
    listed, beside = _columns(cards)
    assert 'id="from-notes"' in beside and 'class="card card-new"' in beside
    assert "make cards from your notes" in listed  # the empty list points beside it, not "above"

    community = web.get(f"{base}/community").text
    grid = community[community.index('<div class="with-context') :]
    assert grid.index('<div class="context-col">') < grid.index('<div class="list-col">')
    listed, beside = _columns(community)
    assert '<div class="feed">' in listed
    assert "Thursday library" in beside and "@ines" in beside and "Everyone at your university" in beside
    assert beside.count("context-extra") == 2  # hidden on a phone, where the tabs have them


def test_one_column_on_a_phone_two_from_960px():
    css = STYLE.read_text()
    block = css[css.index("/* -- D162") : css.index("/* -- end of D162")]
    assert ".with-context { display: grid; grid-template-columns: minmax(0, 1fr);" in block
    # The side column's own track never grows past its width (a file field did, at first).
    assert re.search(r"\.context-col \{[^}]*grid-template-columns: minmax\(0, 1fr\)", block)
    narrow = re.search(r"@media \(max-width: 959\.98px\) \{([^}]*)\}", block)
    assert narrow and ".context-extra { display: none;" in narrow.group(1)
    wide = block[block.index("@media (min-width: 960px)") :]
    assert 'grid-template-areas: "list context"' in wide and "max-width: 70rem" in wide
