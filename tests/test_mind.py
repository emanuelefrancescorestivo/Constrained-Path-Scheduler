"""
D145: the clear-your-mind list and assignments in steps (the owner's pick, 2026-11-18:
"steps, clear-mind"). What must hold: what is written down is kept as the student wrote it,
trimmed; an item given a day is a plan, listed first in its days' order, today's and those
left from before marked, and one without stays a note, newest first; a day before today is
refused; a full list drops its oldest note, never a plan; "Close the day" is asked from three
hours before bed until getting up, once an evening, and gives tomorrow's first three the day
after that evening, after midnight too; a task's steps are the student's own, ticked and
removed one by one, change nothing in the plan, and go with the task; both tips cite only
checked sources.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service
from cps import tips as tips_module
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)  # 18:00 in Paris, a Tuesday
EVENING = datetime(2026, 9, 29, 19, 0, tzinfo=UTC)  # 21:00, bed at 22:30
AFTER_MIDNIGHT = datetime(2026, 9, 29, 22, 30, tzinfo=UTC)  # 00:30 on Wednesday
MORNING = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)  # 08:00, up since 07:30


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


def _texts(view: dict) -> list[str]:
    return [x["text"] for x in view["entries"]]


def test_what_is_written_down_and_its_day(tmp_path):
    sub = _plan(tmp_path)
    assert service.day_view(sub)["wake"] == "07:30" and service.day_view(sub)["bed"] == "22:30"
    sub = service.add_to_mind(sub, "  call the   library about the thesis  ", now=NOW)
    sub = service.add_to_mind(sub, "Buy index cards", "tomorrow", now=NOW)
    sub = service.add_to_mind(sub, "Email Prof. Rossi", "today", now=NOW)
    sub = service.add_to_mind(sub, "Book the lab", "2026-10-05", now=NOW)
    sub = service.add_to_mind(sub, "Renew the bus pass", now=NOW)
    view = service.mind_view(sub, NOW)
    # Plans first, by day; then the notes, newest first.
    assert _texts(view) == [
        "Email Prof. Rossi",
        "Buy index cards",
        "Book the lab",
        "Renew the bus pass",
        "call the library about the thesis",
    ]
    whens = [x["when"] for x in view["entries"]]
    assert whens[:2] == ["today", "tomorrow"] and whens[3:] == ["", ""]
    assert view["today"] == 1 and view["entries"][0]["today"] and not view["entries"][0]["left"]
    # The next day, yesterday's plan is left from before, and still today's to do.
    later = service.mind_view(sub, NOW + timedelta(days=2))
    left = next(x for x in later["entries"] if x["text"] == "Email Prof. Rossi")
    assert left["left"] and left["today"] and left["when"].startswith("since ")
    for wrong in ("", "   "):
        with pytest.raises(service.InvalidInput):
            service.add_to_mind(sub, wrong, now=NOW)
    with pytest.raises(service.InvalidInput):
        service.add_to_mind(sub, "Too late", "2026-09-28", now=NOW)  # a day gone by
    long = service.add_to_mind(sub, "x" * 500, now=NOW)
    newest_note = next(x for x in service.mind_view(long, NOW)["entries"] if not x["day"])
    assert newest_note["text"] == "x" * service.MIND_TEXT


def test_an_item_gets_a_day_loses_it_and_is_crossed_off(tmp_path):
    sub = service.add_to_mind(_plan(tmp_path), "Read chapter 4", now=NOW)
    item = service.mind_view(sub, NOW)["entries"][0]["id"]
    planned = service.plan_mind_item(sub, item, "tomorrow", now=NOW)
    assert service.mind_view(planned, NOW)["entries"][0]["day"] == "2026-09-30"
    back = service.plan_mind_item(planned, item, "none", now=NOW)
    assert service.mind_view(back, NOW)["entries"][0]["day"] == ""
    gone = service.cross_off(back, item)
    assert service.mind_view(gone, NOW)["entries"] == []
    with pytest.raises(service.InvalidInput):
        service.cross_off(gone, item)
    with pytest.raises(service.InvalidInput):
        service.plan_mind_item(sub, item, "someday", now=NOW)


def test_a_full_list_drops_its_oldest_note_never_a_plan(tmp_path):
    sub = service.add_to_mind(_plan(tmp_path), "the plan", "today", now=NOW)
    for n in range(service.MIND_ITEMS):
        sub = service.add_to_mind(sub, f"note {n}", now=NOW)
    texts = _texts(service.mind_view(sub, NOW))
    assert len(texts) == service.MIND_ITEMS
    assert "the plan" in texts and "note 0" not in texts and f"note {service.MIND_ITEMS - 1}" in texts


def test_close_the_day_is_asked_before_bed_once_and_gives_tomorrow_its_first_three(tmp_path):
    sub = _plan(tmp_path)
    assert not service.mind_view(sub, NOW)["close"]  # 18:00: before 19:30
    assert service.mind_view(sub, EVENING)["close"]
    assert service.mind_view(sub, AFTER_MIDNIGHT)["close"]  # the same evening, past midnight
    assert not service.mind_view(sub, MORNING)["close"]  # up since 07:30
    # The evening's tip is the bedtime list; the day's, a plan for what is left.
    assert service.mind_view(sub, EVENING)["tip"]["key"] == "bedtime-list"
    assert service.mind_view(sub, NOW)["tip"]["key"] == "plan-it"
    closed = service.close_day(sub, ["Stats exercises 1-4", "", "Print the slides", "a", "b"], now=EVENING)
    view = service.mind_view(closed, EVENING)
    assert not view["close"] and view["closed"]
    assert _texts(view) == ["Stats exercises 1-4", "Print the slides", "a"]  # three at most
    assert {x["day"] for x in view["entries"]} == {"2026-09-30"}
    assert not service.mind_view(closed, AFTER_MIDNIGHT)["close"]  # once an evening
    assert service.mind_view(closed, EVENING + timedelta(days=1))["close"]  # and again the next
    # Past midnight, tomorrow is the day that has begun.
    late = service.close_day(sub, ["Gym at 7"], now=AFTER_MIDNIGHT)
    assert service.mind_view(late, AFTER_MIDNIGHT)["entries"][0]["day"] == "2026-09-30"
    assert service.mind_view(late, AFTER_MIDNIGHT)["entries"][0]["when"] == "today"
    # Nothing written closes the day all the same.
    assert service.mind_view(service.close_day(sub, [], now=EVENING), EVENING)["closed"]


def test_a_tasks_steps_are_the_students_own_and_go_with_it(tmp_path):
    task = service.TaskSpec("Stats report", "2026-10-20 18:00", 6, "Advanced Statistics")
    sub = _plan(tmp_path, [task])
    plan_before = sub.plan
    with pytest.raises(service.InvalidInput):
        service.add_step(sub, "No such task", "Outline")
    with pytest.raises(service.InvalidInput):
        service.add_step(sub, "Stats report", "   ")
    for text in ("Find the data set", "Run the regressions", "Write the discussion"):
        sub = service.add_step(sub, "stats REPORT", text)  # the task's name in any case
    sub = service.tick_step(sub, "Stats report", 0)
    sub = service.drop_step(sub, "Stats report", 2)
    assert sub.plan == plan_before  # the steps change nothing in the plan
    row = next(r for r in service.tasks_view(sub, NOW)["rows"] if r["name"] == "Stats report")
    assert row["steps"]["done"] == 1 and row["steps"]["count"] == 2
    assert [(x["text"], x["done"]) for x in row["steps"]["entries"]] == [
        ("Find the data set", True),
        ("Run the regressions", False),
    ]
    sub = service.tick_step(sub, "Stats report", 0, done=False)
    assert service.task_steps(sub, "Stats report")[0]["done"] is False
    with pytest.raises(service.InvalidInput):
        service.tick_step(sub, "Stats report", 5)
    for n in range(service.TASK_STEPS - 2):
        sub = service.add_step(sub, "Stats report", f"step {n}")
    with pytest.raises(service.InvalidInput):
        service.add_step(sub, "Stats report", "one too many")
    gone = service.delete_task(sub, "Stats report", now=NOW)
    assert service.task_steps(gone, "Stats report") == []
    assert "steps" in service._SEMESTER_ONLY  # a new semester starts without them, as without tasks


def test_both_tips_cite_checked_sources():
    references = (ROOT / "docs" / "REFERENCES.md").read_text(encoding="utf-8")
    for key, source in (("plan-it", "masicampo2011"), ("bedtime-list", "scullin2018")):
        tip = next(t for t in tips_module.TIPS if t.key == key)
        assert tip.sources == (source,)
        entry = references.split(f"### {source}\n", 1)[1].split("\n### ", 1)[0]
        assert "- status: checked" in entry and "2026-11-18" in entry


def test_today_writes_down_closes_the_day_and_a_task_takes_steps(tmp_path):
    task = service.TaskSpec("Stats report", "2026-10-20 18:00", 6, "Advanced Statistics")
    sub = _plan(tmp_path, [task])
    # On your mind is on, as a student turns it on (D155 starts with five).
    mind = [*service.TODAY_START, "mind"]
    sub = service.update_subscription(tmp_path, sub.token, lambda s: service.set_today_widgets(s, mind))
    clock = {"now": EVENING}
    config = Config(
        store=tmp_path, clock=lambda: clock["now"], background=False, sweep_every=None, remind_every=None
    )
    with TestClient(create_app(config)) as client:
        page = client.get(f"/p/{sub.token}").text
        card = re.search(r'<section class="w w-mind".*?</section>', page, re.S).group(0)
        assert f'action="/p/{sub.token}/mind/close"' in card and card.count('name="first"') == 3
        assert "#tip-bedtime-list" in card
        added = client.post(f"/p/{sub.token}/mind", data={"text": "Ask about the lab", "when": "tomorrow"})
        assert added.status_code == 200
        closed = client.post(f"/p/{sub.token}/mind/close", data={"first": ["Exercises 1-4", "", "Slides"]})
        assert closed.status_code == 200
        view = service.mind_view(service.load_subscription(tmp_path, sub.token), EVENING)
        assert not view["close"] and len(view["entries"]) == 3
        page = unescape(client.get(f"/p/{sub.token}").text)
        assert 'action="/p/' + sub.token + '/mind/close"' not in page
        assert "Ask about the lab" in page and "Exercises 1-4" in page
        item = view["entries"][0]["id"]
        client.post(f"/p/{sub.token}/mind/{item}/when", data={"when": "day", "day": "2026-10-02"})
        fresh = service.mind_view(service.load_subscription(tmp_path, sub.token), EVENING)
        assert next(x for x in fresh["entries"] if x["id"] == item)["day"] == "2026-10-02"
        refused = client.post(f"/p/{sub.token}/mind/{item}/when", data={"when": "day", "day": ""})
        assert refused.status_code == 400
        client.post(f"/p/{sub.token}/mind/{item}/off")
        assert item not in {
            x["id"]
            for x in service.mind_view(service.load_subscription(tmp_path, sub.token), EVENING)["entries"]
        }
        # Steps, from the Tasks page.
        client.post(
            f"/p/{sub.token}/tasks/steps",
            data={"name": "Stats report", "text": "Find the data", "back": "tasks"},
        )
        client.post(
            f"/p/{sub.token}/tasks/steps",
            data={"name": "Stats report", "text": "Write it up", "back": "tasks"},
        )
        ticked = client.post(
            f"/p/{sub.token}/tasks/steps/tick",
            data={"name": "Stats report", "index": "0", "done": "1", "back": "tasks"},
            follow_redirects=False,
        )
        assert ticked.headers["location"] == f"/p/{sub.token}/tasks"
        tasks_page = unescape(client.get(f"/p/{sub.token}/tasks").text)
        assert "1 of 2 steps" in tasks_page and "Write it up" in tasks_page
        wrong = client.post(f"/p/{sub.token}/tasks/steps/drop", data={"name": "Stats report", "index": "x"})
        assert wrong.status_code == 400
        # In the day, the list asks nothing and links to the plan's tip.
        clock["now"] = NOW + timedelta(days=1)
        card = re.search(
            r'<section class="w w-mind".*?</section>', client.get(f"/p/{sub.token}").text, re.S
        ).group(0)
        assert "mind/close" not in card and "#tip-plan-it" in card
