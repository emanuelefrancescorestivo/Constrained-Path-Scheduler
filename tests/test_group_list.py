"""
A study group's list (DECISIONS.md D204, the to-do page's proposal 12): lines every member
writes and ticks off for all of them, who wrote each and who ticked it said to the members
only; the author or the owner takes a line off; a line of one's own to-do list sent to it;
the list goes with its group, a deleted plan's lines with the plan.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service, social
from cps.store import Store
from cps.web.app import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # a Tuesday, 08:00 in Paris


def _plan(tmp_path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(tmp_path, sub)
    return sub


@pytest.fixture
def people(tmp_path):
    """Ada owns a group with Bob in it; Cleo has a handle and is not in it."""
    subs = [_plan(tmp_path) for _ in range(3)]
    for sub, handle in zip(subs, ("ada", "bob", "cleo"), strict=True):
        form = {"handle": handle, "university": "Université Lyon 1", "old_enough": "1"}
        service.save_profile(tmp_path, sub.token, form)
    ada, bob, _cleo = subs
    group = service.create_group(tmp_path, ada.token, {"name": "Thursday library", "goal": "4"}, now=NOW)
    service.invite_to_group(tmp_path, ada.token, group.id, "bob", now=NOW)
    assert service.answer_group(tmp_path, bob.token, group.id, accept=True, now=NOW)
    return (*subs, group.id)


def _view(tmp_path, sub, group):
    return service.group_list_view(tmp_path, service.load_subscription(tmp_path, sub.token), group, NOW)


def test_members_write_and_tick_off_a_line_for_all_of_them(tmp_path, people):
    ada, bob, cleo, group = people
    service.add_group_line(tmp_path, ada.token, group, {"text": "Book a room for **Thursday**"}, now=NOW)
    service.add_group_line(
        tmp_path,
        bob.token,
        group,
        {"text": "Bring the slides https://x.edu/l6.pdf", "day": "2026-09-30"},
        now=NOW,
    )
    seen = _view(tmp_path, bob, group)
    # The line with a day comes first; each says who wrote it, its marks and links apart.
    assert [x["by"] for x in seen["lines"]] == ["bob", "ada"]
    first, second = seen["lines"]
    assert first["when"] == "tomorrow" and first["mine"] and first["links"][0]["label"] == "l6.pdf"
    assert {"k": "b", "x": "Thursday"} in second["parts"]
    assert first["can_drop"] and not second["can_drop"]  # Bob wrote the first, Ada owns the group
    assert (
        service.group_list_view(tmp_path, service.load_subscription(tmp_path, cleo.token), group, NOW) is None
    )
    with pytest.raises(service.InvalidInput, match="not, or no longer, yours"):
        service.add_group_line(tmp_path, cleo.token, group, {"text": "Hello"}, now=NOW)

    # Bob ticks Ada's line off: Ada sees it done, by Bob.
    service.change_group_line(tmp_path, bob.token, group, second["id"], "tick", now=NOW)
    mine = _view(tmp_path, ada, group)
    assert [x["text"] for x in mine["done"]] == ["Book a room for **Thursday**"]
    assert mine["done"][0]["done_by"] == "bob"
    service.change_group_line(tmp_path, ada.token, group, second["id"], "tick", now=NOW)  # open again
    assert len(_view(tmp_path, ada, group)["lines"]) == 2


def test_only_the_author_or_the_owner_takes_a_line_off(tmp_path, people):
    ada, bob, _cleo, group = people
    adas = social.add_group_line(Store(tmp_path), ada.token, group, "Ask about exercise 4", now=NOW)
    bobs = social.add_group_line(Store(tmp_path), bob.token, group, "Print the past paper", now=NOW)
    with pytest.raises(service.InvalidInput, match="only whoever wrote it"):
        service.change_group_line(tmp_path, bob.token, group, adas.id, "drop")
    service.change_group_line(tmp_path, ada.token, group, bobs.id, "drop")  # the owner may
    service.change_group_line(tmp_path, ada.token, group, adas.id, "drop")
    assert _view(tmp_path, bob, group)["lines"] == []
    with pytest.raises(service.InvalidInput, match="no longer on the list"):
        service.change_group_line(tmp_path, ada.token, group, adas.id, "tick")


def test_a_line_has_words_a_day_and_the_list_a_limit(tmp_path, people, monkeypatch):
    ada, _bob, _cleo, group = people
    with pytest.raises(service.InvalidInput, match="write a line first"):
        service.add_group_line(tmp_path, ada.token, group, {"text": "   "}, now=NOW)
    with pytest.raises(service.InvalidInput, match="at most 200"):
        service.add_group_line(tmp_path, ada.token, group, {"text": "x" * 201}, now=NOW)
    with pytest.raises(service.InvalidInput, match="not a day"):
        service.add_group_line(tmp_path, ada.token, group, {"text": "Lab", "day": "Thursday"}, now=NOW)
    monkeypatch.setattr(social, "GROUP_LINES", 2)
    for text in ("One", "Two"):
        service.add_group_line(tmp_path, ada.token, group, {"text": text}, now=NOW)
    with pytest.raises(service.InvalidInput, match="the list is full"):
        service.add_group_line(tmp_path, ada.token, group, {"text": "Three"}, now=NOW)
    first = _view(tmp_path, ada, group)["lines"][0]
    service.change_group_line(tmp_path, ada.token, group, first["id"], "tick", now=NOW)
    assert service.clear_group_lines(tmp_path, ada.token, group) == 1
    service.add_group_line(tmp_path, ada.token, group, {"text": "Three"}, now=NOW)


def test_a_blocked_member_writes_without_a_name(tmp_path, people):
    ada, bob, _cleo, group = people
    service.add_group_line(tmp_path, bob.token, group, {"text": "Bring snacks"}, now=NOW)
    service.block(tmp_path, ada.token, "bob")
    assert _view(tmp_path, ada, group)["lines"][0]["by"] == ""


def test_a_line_of_one_s_own_list_goes_to_the_group(tmp_path, people):
    ada, _bob, _cleo, group = people
    sub = service.load_subscription(tmp_path, ada.token)
    sub = service.add_todo(sub, "Summary of chapter 3 tomorrow https://x.edu/ch3.pdf", now=NOW)
    item = service.todo_view(sub, NOW)["rows"][0]["id"]
    service.share_todo(tmp_path, sub, item, group, now=NOW)
    line = _view(tmp_path, ada, group)["lines"][0]
    assert line["day"] == (NOW.date() + timedelta(days=1)).isoformat()
    assert line["links"][0]["label"] == "ch3.pdf"
    beside = service.todo_groups(tmp_path, sub, NOW)
    assert [(g["name"], g["open"]) for g in beside] == [("Thursday library", 1)]


def test_the_list_goes_with_its_group_and_a_plan_s_lines_with_the_plan(tmp_path, people):
    ada, bob, _cleo, group = people
    store = Store(tmp_path)
    line = social.add_group_line(store, bob.token, group, "Book the room", now=NOW)
    social.add_group_line(store, ada.token, group, "Bring the slides", now=NOW)
    social.tick_group_line(store, ada.token, group, line.id, now=NOW)
    assert [x["text"] for x in store.export(bob.token)["group_lines"]] == ["Book the room"]
    service.delete_subscription(tmp_path, ada.token)  # Ada's line goes, her tick stays a tick
    seen = _view(tmp_path, bob, group)
    assert [x["text"] for x in seen["done"]] == ["Book the room"] and seen["done"][0]["done_by"] == ""
    assert seen["lines"] == []
    service.leave_group(tmp_path, bob.token, group)  # the last member leaves: the group goes
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM group_lines").fetchone() == (0,)


def test_the_group_s_page_and_the_to_do_page_show_the_list(tmp_path, people):
    ada, bob, _cleo, group = people
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as web:
        _pages(web, tmp_path, ada, bob, group)


def _pages(web, tmp_path, ada, bob, group):
    answer = web.post(f"/p/{bob.token}/groups/{group}/list", data={"text": "Ask about **exercise 4**"})
    assert answer.status_code == 200 and answer.url.path == f"/p/{bob.token}/groups/{group}"
    page = web.get(f"/p/{ada.token}/groups/{group}").text
    assert 'id="group-list"' in page and "<b>exercise 4</b>" in page and "@bob" in page
    line = _view(tmp_path, ada, group)["lines"][0]["id"]
    assert web.post(f"/p/{ada.token}/groups/{group}/list/{line}/tick").status_code == 200
    assert "ticked off by @ada" in web.get(f"/p/{bob.token}/groups/{group}").text
    assert web.post(f"/p/{ada.token}/groups/{group}/list/{line}/burn").status_code == 404
    todo = web.get(f"/p/{ada.token}/todo").text
    assert "Thursday library" in todo and f"/p/{ada.token}/groups/{group}#list" in todo
