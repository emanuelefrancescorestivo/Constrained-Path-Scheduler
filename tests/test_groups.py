"""
Study groups (DECISIONS.md D28): a few students with handles and a shared weekly
goal in hours. Invitations are asked for and answered; the week counts every
member's study once, from the week they joined; the page shows the group's total
and who studied, never one member's hours; nobody is ranked.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cps import service, social
from cps.store import Store

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
    """Ada, Bob, Cleo and Eve have handles; Dan has a plan and no handle."""
    subs = [_plan(tmp_path) for _ in range(5)]
    for sub, handle in zip(subs[:4], ("ada", "bob", "cleo", "eve"), strict=True):
        form = {"handle": handle, "university": "Université Lyon 1", "old_enough": "1"}
        service.save_profile(tmp_path, sub.token, form)
    return subs


def _log(tmp_path, sub, minutes, when):
    form = {
        "course": "Algebra 3",
        "effort": "6",
        "progress": "3",
        "minutes": str(minutes),
        "visibility": "me",
    }
    changed, _ = service.log_session(tmp_path, service.load_subscription(tmp_path, sub.token), form, now=when)
    service.save_subscription(tmp_path, changed)


def _group(tmp_path, owner, *others, goal="2"):
    group = service.create_group(tmp_path, owner.token, {"name": "Thursday library", "goal": goal}, now=NOW)
    for sub in others:
        handle = service.profile_of(tmp_path, sub.token)["handle"]
        service.invite_to_group(tmp_path, owner.token, group.id, handle, now=NOW)
        assert service.answer_group(tmp_path, sub.token, group.id, accept=True, now=NOW)
    return group


def test_a_group_needs_a_handle_a_name_and_a_weekly_goal(tmp_path, people):
    ada, *_, dan = people
    with pytest.raises(service.InvalidInput, match="choose a handle"):
        service.create_group(tmp_path, dan.token, {"name": "Us", "goal": "5"}, now=NOW)
    with pytest.raises(service.InvalidInput, match="give the group a name"):
        service.create_group(tmp_path, ada.token, {"name": "  ", "goal": "5"}, now=NOW)
    for goal in ("0", "201", "lots", ""):
        with pytest.raises(service.InvalidInput, match="from 1 to 200 hours"):
            service.create_group(tmp_path, ada.token, {"name": "Us", "goal": goal}, now=NOW)
    group = service.create_group(
        tmp_path, ada.token, {"name": "  Thursday   library ", "goal": "7,5"}, now=NOW
    )
    assert group.name == "Thursday library" and group.goal == 450 and group.owner == ada.token
    view = service.group_view(tmp_path, ada, group.id, NOW)
    assert view["owner"] and view["size"] == 1 and view["goal"] == 7.5 and view["week"]["hours"] == 0
    assert service.group_view(tmp_path, dan, group.id, NOW) is None  # members only


def test_an_invitation_is_asked_for_and_answered(tmp_path, people, monkeypatch):
    ada, bob, cleo, eve, dan = people
    group = service.create_group(tmp_path, ada.token, {"name": "Us", "goal": "5"}, now=NOW)
    assert service.invite_to_group(tmp_path, ada.token, group.id, "@bob", now=NOW)["handle"] == "bob"
    waiting = service.groups_view(tmp_path, bob, NOW)["invitations"]
    assert [(i["name"], i["by"], i["size"]) for i in waiting] == [("Us", "ada", 1)]
    assert service.group_view(tmp_path, bob, group.id, NOW) is None  # not yet a member
    assert service.group_view(tmp_path, ada, group.id, NOW)["invited"][0]["handle"] == "bob"
    with pytest.raises(service.InvalidInput, match="already in this group, or invited"):
        service.invite_to_group(tmp_path, ada.token, group.id, "bob", now=NOW)
    with pytest.raises(service.InvalidInput, match="nobody called @nobody"):
        service.invite_to_group(tmp_path, ada.token, group.id, "nobody", now=NOW)
    with pytest.raises(service.InvalidInput, match="not, or no longer, yours"):
        service.invite_to_group(tmp_path, cleo.token, group.id, "eve", now=NOW)  # not a member
    assert not service.answer_group(tmp_path, bob.token, group.id, accept=False, now=NOW)  # declined: it goes
    assert service.groups_view(tmp_path, bob, NOW)["invitations"] == []
    with pytest.raises(service.InvalidInput, match="no longer, there"):
        service.answer_group(tmp_path, bob.token, group.id, accept=True, now=NOW)
    service.invite_to_group(tmp_path, ada.token, group.id, "bob", now=NOW)
    assert service.answer_group(tmp_path, bob.token, group.id, accept=True, now=NOW)
    assert service.group_view(tmp_path, bob, group.id, NOW)["size"] == 2
    # Bob, a member, may invite too; a group has room for so many, invitations included.
    monkeypatch.setattr(social, "GROUP_SIZE", 3)
    service.invite_to_group(tmp_path, bob.token, group.id, "cleo", now=NOW)
    with pytest.raises(service.InvalidInput, match="3 people at most"):
        service.invite_to_group(tmp_path, ada.token, group.id, "eve", now=NOW)
    # And a student belongs to so many groups.
    monkeypatch.setattr(social, "GROUPS_EACH", 1)
    with pytest.raises(service.InvalidInput, match="in 1 groups already"):
        service.create_group(tmp_path, bob.token, {"name": "Another", "goal": "3"}, now=NOW)
    other = service.create_group(tmp_path, eve.token, {"name": "Eve's", "goal": "3"}, now=NOW)
    service.invite_to_group(tmp_path, eve.token, other.id, "cleo", now=NOW)
    service.answer_group(tmp_path, cleo.token, other.id, accept=True, now=NOW)
    with pytest.raises(service.InvalidInput, match="in 1 groups already"):
        service.answer_group(tmp_path, cleo.token, group.id, accept=True, now=NOW)
    # A block takes away the invitations between the two, and refuses new ones.
    service.block(tmp_path, cleo.token, "bob")
    assert service.groups_view(tmp_path, cleo, NOW)["invitations"] == []
    with pytest.raises(service.InvalidInput, match="nobody called @cleo"):
        service.invite_to_group(tmp_path, bob.token, group.id, "cleo", now=NOW)
    assert dan.token not in [m.token for m in social.members(Store(tmp_path), group.id)]


def test_the_week_counts_every_members_study_once_from_the_week_they_joined(tmp_path, people):
    ada, bob, cleo, *_ = people
    _log(tmp_path, bob, 120, NOW - timedelta(days=7))  # the week before: before the group
    group = _group(tmp_path, ada, bob, goal="1.5")
    _log(tmp_path, ada, 60, NOW + timedelta(hours=3))
    _log(tmp_path, bob, 30, NOW + timedelta(hours=4))
    _log(tmp_path, cleo, 300, NOW + timedelta(hours=5))  # not a member
    view = service.group_view(tmp_path, ada, group.id, NOW + timedelta(hours=6))
    week = view["week"]
    assert week["hours"] == pytest.approx(1.5) and week["met"] and week["percent"] == 100
    assert week["studied"] == 2 and view["run"] == 1
    assert [(m["handle"], m["studied"], m["owner"], m["me"]) for m in view["members"]] == [
        ("ada", True, True, True),
        ("bob", True, False, False),
    ]
    assert all("hours" not in m for m in view["members"])  # the total, never one person's
    # The next week: not met yet, and that does not break the run.
    later = NOW + timedelta(days=7)
    view = service.group_view(tmp_path, bob, group.id, later)
    assert view["run"] == 1 and view["week"]["hours"] == 0 and view["week"]["left"] == pytest.approx(1.5)
    assert [w["met"] for w in view["weeks"]] == [True, False] and view["weeks"][-1]["current"]
    assert not any(m["studied"] for m in view["members"])
    _log(tmp_path, ada, 90, later + timedelta(hours=2))
    assert service.group_view(tmp_path, bob, group.id, later + timedelta(hours=3))["run"] == 2
    # The groups page carries the same card.
    cards = service.groups_view(tmp_path, ada, later + timedelta(hours=3))["groups"]
    assert [(c["name"], c["run"], c["week"]["met"]) for c in cards] == [("Thursday library", 2, True)]


def test_owners_leaving_blocks_and_leaving_the_network(tmp_path, people):
    ada, bob, cleo, eve, _ = people
    group = _group(tmp_path, ada, bob, cleo)
    with pytest.raises(service.InvalidInput, match="only the group's owner"):
        service.remove_from_group(tmp_path, bob.token, group.id, "cleo")
    with pytest.raises(service.InvalidInput, match="only the group's owner"):
        service.change_group(tmp_path, bob.token, group.id, {"name": "Ours", "goal": "9"})
    service.change_group(tmp_path, ada.token, group.id, {"name": "Ours", "goal": "9"})
    assert service.group_view(tmp_path, bob, group.id, NOW)["name"] == "Ours"
    service.remove_from_group(tmp_path, ada.token, group.id, "cleo")
    assert service.group_view(tmp_path, cleo, group.id, NOW) is None
    # Blocked, a member is left out of the other's list but still counts.
    service.invite_to_group(tmp_path, ada.token, group.id, "eve", now=NOW)
    service.answer_group(tmp_path, eve.token, group.id, accept=True, now=NOW)
    service.block(tmp_path, bob.token, "eve")
    _log(tmp_path, eve, 60, NOW + timedelta(hours=1))
    seen = service.group_view(tmp_path, bob, group.id, NOW + timedelta(hours=2))
    assert [m["handle"] for m in seen["members"]] == ["ada", "bob"] and seen["hidden"] == 1
    assert seen["week"]["hours"] == pytest.approx(1.0)
    # The owner leaves: the next member in line owns it; the last one out ends it.
    other = service.create_group(tmp_path, ada.token, {"name": "Ada's other", "goal": "2"}, now=NOW)
    service.leave_group(tmp_path, ada.token, group.id)
    assert social.get_group(Store(tmp_path), group.id).owner == bob.token
    assert social.get_group(Store(tmp_path), other.id).owner == ada.token  # her other group is hers
    service.invite_to_group(tmp_path, bob.token, group.id, "cleo", now=NOW)
    service.leave_group(tmp_path, eve.token, group.id)
    service.leave_group(tmp_path, bob.token, group.id)
    assert social.get_group(Store(tmp_path), group.id) is None
    assert service.groups_view(tmp_path, cleo, NOW)["invitations"] == []
    # Leaving the network leaves every group.
    service.leave_network(tmp_path, ada.token)
    assert social.get_group(Store(tmp_path), other.id) is None


def test_deleting_a_plan_takes_it_out_of_its_groups(tmp_path, people):
    ada, bob, cleo, *_ = people
    group = _group(tmp_path, ada, bob)
    service.invite_to_group(tmp_path, bob.token, group.id, "cleo", now=NOW)
    assert service.delete_subscription(tmp_path, ada.token)
    assert social.get_group(Store(tmp_path), group.id).owner == bob.token
    assert [m.token for m in social.members(Store(tmp_path), group.id)] == [bob.token, cleo.token]
    assert service.delete_subscription(tmp_path, bob.token)
    assert social.get_group(Store(tmp_path), group.id) is None
    assert service.groups_view(tmp_path, cleo, NOW)["invitations"] == []


def test_the_pilots_measures_count_groups_of_two_or_more(tmp_path, people):
    ada, bob, cleo, *_ = people
    _group(tmp_path, ada, bob)
    service.create_group(tmp_path, cleo.token, {"name": "Alone", "goal": "3"}, now=NOW)
    assert service.engagement(tmp_path, now=NOW)["network"]["groups"] == 1
