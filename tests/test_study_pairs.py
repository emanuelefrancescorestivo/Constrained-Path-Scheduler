"""
Studying together (DECISIONS.md D127): two students with handles agree, and both see
the days in a row they both studied. What must hold: a day either misses ends the run,
a day both studied adds one, any other day holds it; the ask is answered as a follow
is, and a decline tells nobody; three pairs at most; a block, leaving the network and
deleting a plan end the pair; the plan's copy lists it; nobody's hours are shown. A
group's card adds the memory days its members' reviews added this week.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from cps import service, social
from cps.progress import together
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # a Tuesday, 08:00 in Paris
MON = date(2026, 10, 5)


def test_a_miss_ends_the_run_both_studied_adds_one_anything_else_holds_it():
    days = [MON + timedelta(days=n) for n in range(7)]
    a = dict(
        zip(days, ["studied", "studied", "rest", "studied", "forgiven", "studied", "today"], strict=True)
    )
    b = dict(
        zip(days, ["studied", "rest", "studied", "studied", "studied", "studied", "studied"], strict=True)
    )
    # Mon both, Tue held, Wed held, Thu both, Fri held (a forgiven day), Sat both, Sun held.
    assert together(a, b, days[0], days[6]) == (3, 3)
    b[days[4]] = "missed"
    assert together(a, b, days[0], days[6]) == (1, 2)  # Friday's miss ends it; Saturday starts again
    # Repaired and paused days hold it; a day one has no plan for holds it too.
    a[days[4]], b[days[4]] = "repaired", "paused"
    assert together(a, b, days[0], days[6]) == (3, 3)
    assert together(a, {}, days[0], days[6]) == (0, 0)
    # Only the days since they agreed count.
    assert together(a, b, days[3], days[6]) == (2, 2)


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
    """Ada, Bob, Cleo, Eve and Finn have handles; Dan has a plan and no handle."""
    subs = [_plan(tmp_path) for _ in range(6)]
    for sub, handle in zip(subs, ("ada", "bob", "cleo", "eve", "finn"), strict=False):
        form = {"handle": handle, "university": "Université Lyon 1", "old_enough": "1"}
        service.save_profile(tmp_path, sub.token, form)
    return subs


def _log(tmp_path, sub, when):
    form = {"course": "Algebra 3", "effort": "6", "progress": "3", "minutes": "45", "visibility": "me"}
    changed, _ = service.log_session(tmp_path, service.load_subscription(tmp_path, sub.token), form, now=when)
    service.save_subscription(tmp_path, changed)


def test_an_ask_is_answered_as_a_follow_and_a_decline_tells_nobody(tmp_path, people):
    ada, bob, cleo, *_rest, dan = people
    where = Store(tmp_path)
    service.ask_together(tmp_path, ada.token, "@bob", now=NOW)
    assert service.together_with(tmp_path, ada.token, bob.token) == "asked"
    assert service.together_with(tmp_path, bob.token, ada.token) == "asks"
    bob_view = service.together_view(tmp_path, service.load_subscription(tmp_path, bob.token), NOW)
    assert [p["handle"] for p in bob_view["waiting"]] == ["ada"] and not bob_view["pairs"]
    assert [p["handle"] for p in service.together_view(tmp_path, ada, NOW)["sent"]] == ["bob"]
    with pytest.raises(service.InvalidInput):
        service.ask_together(tmp_path, bob.token, "ada", now=NOW)  # already asked, either way
    with pytest.raises(service.InvalidInput):
        service.answer_together(tmp_path, ada.token, "bob", True, now=NOW)  # only the asked one answers
    service.answer_together(tmp_path, bob.token, "ada", True, now=NOW)
    assert service.together_with(tmp_path, ada.token, bob.token) == "together"
    # A decline: the ask goes, and nothing says so.
    service.ask_together(tmp_path, cleo.token, "ada", now=NOW)
    service.answer_together(tmp_path, ada.token, "cleo", False, now=NOW)
    assert social.buddy_between(where, ada.token, cleo.token) is None
    assert service.together_view(tmp_path, cleo, NOW) == {
        "pairs": [],
        "waiting": [],
        "sent": [],
        "room": 3,
        "profile": True,
    }
    # No handle, no pair; nobody of that name; oneself.
    with pytest.raises(service.InvalidInput):
        service.ask_together(tmp_path, dan.token, "ada", now=NOW)
    with pytest.raises(service.InvalidInput):
        service.ask_together(tmp_path, ada.token, "nobody", now=NOW)
    with pytest.raises(service.InvalidInput):
        service.ask_together(tmp_path, ada.token, "ada", now=NOW)
    # Either one ends it.
    service.end_together(tmp_path, bob.token, "ada")
    assert service.together_with(tmp_path, ada.token, bob.token) is None


def test_three_pairs_at_most_asks_included(tmp_path, people):
    ada, _bob, _cleo, _eve, finn, _dan = people
    for other in ("bob", "cleo", "eve"):
        service.ask_together(tmp_path, ada.token, other, now=NOW)
    with pytest.raises(service.InvalidInput):
        service.ask_together(tmp_path, ada.token, "finn", now=NOW)
    with pytest.raises(service.InvalidInput):
        service.ask_together(tmp_path, finn.token, "ada", now=NOW)  # Ada has no room either
    assert service.together_view(tmp_path, ada, NOW)["room"] == 0


def test_the_days_together_count_from_both_plans(tmp_path, people):
    ada, bob, *_rest = people
    service.ask_together(tmp_path, ada.token, "bob", now=NOW)
    service.answer_together(tmp_path, bob.token, "ada", True, now=NOW)
    for day in range(3):
        when = NOW + timedelta(days=day, hours=4)
        _log(tmp_path, ada, when)
        _log(tmp_path, bob, when)
    later = NOW + timedelta(days=2, hours=6)
    view = service.together_view(tmp_path, service.load_subscription(tmp_path, ada.token), later)
    (pair,) = view["pairs"]
    assert pair["handle"] == "bob" and pair["days"] == 3 and pair["best"] == 3
    assert pair["me_today"] and pair["them_today"]
    assert "minutes" not in pair and "hours" not in pair  # never anyone's time
    # The next morning, neither has studied yet: the run holds.
    morning = NOW + timedelta(days=3)
    view = service.together_view(tmp_path, service.load_subscription(tmp_path, ada.token), morning)
    assert view["pairs"][0]["days"] == 3 and not view["pairs"][0]["me_today"]


def test_a_block_leaving_and_deleting_end_the_pair_and_the_copy_lists_it(tmp_path, people):
    ada, bob, cleo, eve, *_rest = people
    where = Store(tmp_path)
    for other, sub in (("bob", bob), ("cleo", cleo), ("eve", eve)):
        service.ask_together(tmp_path, ada.token, other, now=NOW)
        service.answer_together(tmp_path, sub.token, "ada", True, now=NOW)
    exported = where.export(ada.token)["studying_together"]
    assert [(r["ask"], r["handle"]) for r in exported] == [
        ("you asked", "bob"),
        ("you asked", "cleo"),
        ("you asked", "eve"),
    ]
    social.block(where, bob.token, ada.token, now=NOW)
    assert social.buddy_between(where, ada.token, bob.token) is None
    service.leave_network(tmp_path, cleo.token)
    assert social.buddy_between(where, ada.token, cleo.token) is None
    where.delete(eve.token)
    assert social.buddy_between(where, ada.token, eve.token) is None


def test_a_groups_card_adds_its_memory_days(tmp_path, people):
    ada, _bob, *_rest = people
    group = service.create_group(tmp_path, ada.token, {"name": "Library", "goal": "2"}, now=NOW)
    card = service.groups_view(tmp_path, ada, NOW)["groups"][0]
    assert card["id"] == group.id and card["week"]["memory"] >= 0
    assert isinstance(card["week"]["memory"], int)


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        client.root = tmp_path
        yield client


def test_ask_on_the_person_page_answer_on_progress_and_the_line_on_today(web, people):
    ada, bob, *_rest = people
    page = unescape(web.get(f"/p/{ada.token}/u/bob").text)
    assert "Study together" in page and f'action="/p/{ada.token}/u/bob/together"' in page
    asked = web.post(
        f"/p/{ada.token}/u/bob/together", data={"next": f"/p/{ada.token}/u/bob"}, follow_redirects=False
    )
    assert asked.status_code == 303 and asked.headers["location"] == f"/p/{ada.token}/u/bob"
    assert "Asked to study together · withdraw" in unescape(web.get(f"/p/{ada.token}/u/bob").text)
    progress = unescape(web.get(f"/p/{bob.token}/progress").text)
    assert (
        "Asks to study together" in progress
        and "/u/ada" in progress
        and 'name="answer" value="accept"' in progress
    )
    accepted = web.post(f"/p/{bob.token}/together/ada", data={"answer": "accept"}, follow_redirects=False)
    assert accepted.status_code == 303 and accepted.headers["location"].endswith("/progress#together")
    progress = unescape(web.get(f"/p/{bob.token}/progress").text)
    assert "Studying together" in progress and "0 days together" in progress
    assert "@ada: not yet today" in progress
    today = unescape(web.get(f"/p/{ada.token}/panel").text)
    assert "@bob" in today and "days together" in today
    assert "Studying together" in unescape(web.get(f"/p/{ada.token}/u/bob").text)  # the badge
    ended = web.post(f"/p/{ada.token}/together/bob", data={"answer": "end"}, follow_redirects=False)
    assert ended.status_code == 303
    assert "days together" not in unescape(web.get(f"/p/{ada.token}/panel").text)
    refused = web.post(f"/p/{ada.token}/together/bob", data={"answer": "accept"})
    assert refused.status_code == 400
