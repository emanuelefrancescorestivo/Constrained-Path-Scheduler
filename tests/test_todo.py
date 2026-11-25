"""
D195: the to-do page, as in Notion (the owner's pick, 2026-11-24: "for now i like the to-do
lists yes"). What must hold: a line is written as it would be said, and its prefix makes it a
heading ("# "), a note ("> "), a step under the line above ("- ") or a line done ("[x] "); a
line that names a day is that day's, and one that also names hours of work is planned as a
task at once; a line is edited in place, an emptied one removed; ticked off, it goes to the
lines done with its steps and its task is done in the plan, and back again; it moves up and
down, becomes a step or not, changes kind, and is removed with its task; the lines done are
cleared together; the plan's other tasks are listed under it; Today's "On your mind" is the
same list's open lines; a full list drops its oldest line done first. The page works without
its script, with no inline style.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service, usage
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)  # 18:00 in Paris, a Tuesday


def _plan(root: Path, tasks: list[service.TaskSpec] | None = None) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec(n, None, familiarity=3) for n in ("Algebra 3", "Advanced Statistics")],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=tasks or [],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(root, service.finish_first_run(sub))
    return service.load_subscription(root, sub.token)


def _rows(sub: service.Subscription) -> list[tuple[str, str, int, bool]]:
    view = service.todo_view(sub, NOW)
    return [(r["text"], r["kind"], r["indent"], r["done"]) for r in view["rows"]]


def _id(sub: service.Subscription, text: str) -> str:
    view = service.todo_view(sub, NOW)
    return next(r["id"] for r in [*view["rows"], *view["done"]] if r["text"] == text)


def test_a_line_s_prefix_makes_its_kind(tmp_path):
    sub = _plan(tmp_path)
    lines = ("# Week 5", "Read chapter 4", "- the exercises", "> ask about the lab", "[x] Buy index cards")
    for line in lines:
        sub = service.add_todo(sub, line, now=NOW)
    assert _rows(sub) == [
        ("Week 5", "heading", 0, False),
        ("Read chapter 4", "todo", 0, False),
        ("the exercises", "todo", 1, False),
        ("ask about the lab", "note", 0, False),
    ]
    assert [r["text"] for r in service.todo_view(sub, NOW)["done"]] == ["Buy index cards"]
    assert service.todo_view(sub, NOW)["open"] == 2
    # A step needs a line above it; an empty line is refused.
    first = service.add_todo(_plan(tmp_path), "- alone", now=NOW)
    assert _rows(first) == [("alone", "todo", 0, False)]
    with pytest.raises(service.InvalidInput):
        service.add_todo(sub, "#  ", now=NOW)


def test_a_line_with_a_day_is_that_day_s_and_with_hours_is_planned(tmp_path):
    sub = _plan(tmp_path)
    sub = service.add_todo(sub, "Read chapter 4 tomorrow", now=NOW)
    sub = service.add_todo(sub, "Stats report Friday 6 h", now=NOW)
    rows = {r["text"]: r for r in service.todo_view(sub, NOW)["rows"]}
    assert rows["Read chapter 4"]["day"] == "2026-09-30" and rows["Read chapter 4"]["hours"] is None
    report = rows["Stats report"]
    assert report["day"] == "2026-10-02" and report["hours"] == 6.0
    assert report["course"] == "Advanced Statistics" and report["color"] is not None
    task = next(t for t in sub.options["tasks"] if t["name"] == "Stats report")
    assert task["hours"] == 6.0 and task["course"] == "Advanced Statistics"
    planned = service.PlanReport.from_dict(sub.plan).sessions
    assert any(s.kind == "task" and s.topic == "Stats report" for s in planned)
    # The same words again: a second task, named apart.
    again = service.add_todo(sub, "Stats report Friday 6 h", now=NOW)
    assert {t["name"] for t in again.options["tasks"]} >= {"Stats report", "Stats report (2)"}
    # Today's list is the same list's open lines.
    assert [x["text"] for x in service.mind_view(sub, NOW)["entries"]] == ["Read chapter 4", "Stats report"]


def test_ticked_off_with_its_steps_and_its_task_and_back(tmp_path):
    sub = _plan(tmp_path)
    for line in ("Stats report Friday 6 h", "- the data", "- the plots", "Read chapter 4"):
        sub = service.add_todo(sub, line, now=NOW)
    report = _id(sub, "Stats report")
    done = service.tick_todo(sub, report, True, now=NOW)
    view = service.todo_view(done, NOW)
    assert [r["text"] for r in view["rows"]] == ["Read chapter 4"]
    assert [r["text"] for r in view["done"]] == ["Stats report", "the data", "the plots"]
    assert next(t for t in done.options["tasks"] if t["name"] == "Stats report")["done"]
    assert [x["text"] for x in service.mind_view(done, NOW)["entries"]] == ["Read chapter 4"]
    back = service.tick_todo(done, report, False, now=NOW)
    assert not next(t for t in back.options["tasks"] if t["name"] == "Stats report").get("done")
    assert len(service.todo_view(back, NOW)["rows"]) == 4
    # Today's cross-off is the same tick; a line done is not crossed off twice.
    crossed = service.cross_off(back, report, now=NOW)
    with pytest.raises(service.InvalidInput):
        service.cross_off(crossed, report, now=NOW)
    # Only a to-do is ticked.
    noted = service.add_todo(sub, "> a note", now=NOW)
    with pytest.raises(service.InvalidInput, match="only a to-do"):
        service.tick_todo(noted, _id(noted, "a note"), True, now=NOW)
    # Cleared: the lines done leave the page; the task stays done in the plan.
    cleared = service.clear_done(done)
    assert service.todo_view(cleared, NOW)["done"] == []
    assert next(t for t in cleared.options["tasks"] if t["name"] == "Stats report")["done"]


def test_a_line_edited_moved_made_a_step_another_kind_and_removed(tmp_path):
    sub = _plan(tmp_path)
    for line in ("One", "Two", "Three"):
        sub = service.add_todo(sub, line, now=NOW)
    two = _id(sub, "Two")
    sub = service.edit_todo(sub, two, "  Two,   rewritten ")
    assert _rows(sub)[1] == ("Two, rewritten", "todo", 0, False)
    sub = service.change_todo(sub, two, "up", now=NOW)
    assert [r[0] for r in _rows(sub)] == ["Two, rewritten", "One", "Three"]
    sub = service.change_todo(sub, two, "up", now=NOW)  # at the top already: unchanged
    sub = service.change_todo(sub, _id(sub, "Three"), "indent", now=NOW)
    assert _rows(sub)[2] == ("Three", "todo", 1, False)
    sub = service.change_todo(sub, _id(sub, "Three"), "heading", now=NOW)
    assert _rows(sub)[2] == ("Three", "heading", 0, False)
    sub = service.change_todo(sub, _id(sub, "One"), "note", now=NOW)
    assert _rows(sub)[1] == ("One", "note", 0, False)
    sub = service.edit_todo(sub, _id(sub, "One"), "   ")  # emptied: removed
    assert [r[0] for r in _rows(sub)] == ["Two, rewritten", "Three"]
    with pytest.raises(service.InvalidInput, match="move the line"):
        service.change_todo(sub, two, "sideways", now=NOW)
    # Removed with its task.
    planned = service.add_todo(sub, "Stats report Friday 6 h", now=NOW)
    gone = service.change_todo(planned, _id(planned, "Stats report"), "delete", now=NOW)
    assert all(t["name"] != "Stats report" for t in gone.options.get("tasks", ()))
    with pytest.raises(service.InvalidInput):
        service.change_todo(gone, "nope", "up", now=NOW)


def test_the_plan_s_other_tasks_are_listed_under_it_in_their_days_order(tmp_path):
    tasks = [
        service.TaskSpec("Lab write-up", "2026-10-20 18:00", 3, "Algebra 3"),
        service.TaskSpec("Essay", "2026-10-06 12:00", 4, ""),
    ]
    sub = _plan(tmp_path, tasks)
    others = service.todo_view(sub, NOW)["others"]
    assert [(o["name"], o["hours"]) for o in others] == [("Essay", 4.0), ("Lab write-up", 3.0)]
    assert others[1]["color"] is not None


def test_a_full_list_drops_its_oldest_line_done_first(tmp_path):
    sub = _plan(tmp_path)
    items = [
        {"id": f"{i:08x}", "text": f"line {i}", "day": "", "kind": "todo", "done": i == 5}
        for i in range(service.MIND_ITEMS)
    ]
    sub = service.add_todo(service._with_mind(sub, items), "one more", now=NOW)
    texts = [x["text"] for x in sub.options["mind"]]
    assert len(texts) == service.MIND_ITEMS and "line 5" not in texts and texts[-1] == "one more"


def test_the_page_writes_ticks_changes_and_clears_without_a_script(tmp_path):
    sub = _plan(tmp_path)
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        base = f"/p/{sub.token}/todo"
        empty = unescape(client.get(base).text)
        assert "Nothing here yet" in empty and 'id="todo-line"' in empty
        for line in ("# Week 5", "Stats report Friday 6 h", "- the data"):
            answer = client.post(base, data={"line": line}, follow_redirects=False)
            assert answer.status_code == 303 and answer.headers["location"] == base
        page = unescape(client.get(base).text)
        # The table (the owner's pick): a heading with its open lines, the task's columns, a step.
        assert 'value="Week 5"' in page and "1 open" in page and "is-step" in page
        assert re.search(rf'class="todo-work r"><a href="/p/{sub.token}/tasks">6 h</a>', page)
        assert "Advanced Statistics" in page and 'class="todo-head"' in page
        assert page.count('class="todo-tick"') == 2  # a heading has no box
        assert " style=" not in page
        # AUDIT 132: the count in the title is put in place with the list by every form that
        # changes it, so a line added or ticked off in the background is counted at once.
        assert re.search(r'id="todo-count">2 to do<', page)
        assert page.count('data-region="#todo"') == 0
        assert page.count('data-region="#todo, #todo-count"') == page.count('data-region="#todo')
        stored = service.load_subscription(tmp_path, sub.token)
        report = _id(stored, "Stats report")
        client.post(f"{base}/{report}/tick", data={"done": "1"})
        page = unescape(client.get(base).text)
        assert "Done (2)" in page and "Clear the lines done" in page
        client.post(f"{base}/{report}/edit", data={"text": "Stats report, final"})
        client.post(f"{base}/{report}/change", data={"how": "up"})
        stored = service.load_subscription(tmp_path, sub.token)
        assert [x["text"] for x in stored.options["mind"]][:2] == ["Stats report, final", "Week 5"]
        client.post(f"{base}/clear")
        assert service.todo_view(service.load_subscription(tmp_path, sub.token), NOW)["done"] == []
        # A refusal comes back on the page, with its reason; an unknown action is not found.
        refused = client.post(base, data={"line": "> "})
        assert refused.status_code == 400 and 'role="alert"' in refused.text
        assert client.post(f"{base}/{report}/fly", data={}).status_code == 404
        assert client.post(f"{base}/elsewhere", data={}).status_code == 404
        # From the "+" sheet on Today, the line is written and Today comes back.
        answer = client.post(base, data={"line": "Buy index cards", "back": "today"}, follow_redirects=False)
        assert answer.headers["location"] == f"/p/{sub.token}"
        today = unescape(client.get(f"/p/{sub.token}").text)
        sheet = today[today.index('id="quick-add"') :]
        assert re.search(rf'action="/p/{sub.token}/todo"', sheet)
        # Counted as Tasks, for those who said yes (D96).
        assert usage.part_of("GET", "/todo") == usage.part_of("POST", f"/todo/{report}/tick") == "tasks"
        # You and the sidebar lead to it.
        you = client.get(f"/p/{sub.token}/you").text
        assert f'href="/p/{sub.token}/todo"' in you
