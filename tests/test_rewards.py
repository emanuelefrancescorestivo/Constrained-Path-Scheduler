"""
Rewards to unlock (DECISIONS.md D206): the focus page's rings and backdrops come with the
sessions timed with Focus and saved in the diary, the avatar's accessories with the level;
what is locked is refused, what is unlocked is chosen in one tap and drawn.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service, social
from cps.store import Store
from cps.web.app import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)


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


def _timed(tmp_path, sub, n):
    """n sessions timed with Focus and saved in the diary, as the focus page saves them."""
    for k in range(n):
        social.create_post(
            Store(tmp_path),
            sub.token,
            "session",
            day=f"2026-09-{1 + k % 28:02d}",
            visibility="me",
            data={
                "course": "Algebra 3",
                "effort": 5,
                "progress": 3,
                "minutes": 25,
                "timed": True,
                "checked": True,
            },
            now=NOW,
        )


def test_rings_and_backdrops_unlock_with_sessions_timed(tmp_path):
    sub = _plan(tmp_path)
    view = service.rewards_view(tmp_path, sub, NOW)
    assert view["sessions"] == 0
    assert [x["unlocked"] for x in view["rings"]] == [True, False, False, False]
    assert view["rings"][0]["chosen"] and view["backdrops"][0]["chosen"]
    with pytest.raises(service.InvalidInput, match="unlocked after 5 sessions"):
        service.choose_look(tmp_path, sub, "ring", "gold", NOW)
    _timed(tmp_path, sub, 4)
    assert service.unlocks_next(tmp_path, sub, NOW) == ["Gold"]  # the fifth unlocks it
    _timed(tmp_path, sub, 1)
    chosen = service.choose_look(tmp_path, sub, "ring", "gold", NOW)
    assert service.focus_look(chosen) == {"ring": "gold", "backdrop": "plain"}
    gold = next(x for x in service.rewards_view(tmp_path, chosen, NOW)["rings"] if x["key"] == "gold")
    assert gold["unlocked"] and gold["chosen"] and gold["percent"] == 100
    with pytest.raises(service.InvalidInput, match="choose one of the options"):
        service.choose_look(tmp_path, sub, "ring", "rainbow", NOW)
    # A look saved under a name this version does not draw falls back to the first.
    odd = service.replace(sub, options={**sub.options, "look": {"ring": "rainbow"}})
    assert service.focus_look(odd)["ring"] == "vine"


def test_accessories_unlock_with_the_level(tmp_path):
    sub = _plan(tmp_path)
    spec = {**service.AVATAR_START, "head": "crown"}
    with pytest.raises(service.InvalidInput, match="unlocked at level 6"):
        service.set_avatar(sub, spec, level=5)
    worn = service.set_avatar(sub, spec, level=6)
    assert service.avatar_of(worn)["head"] == "crown"
    view = service.rewards_view(tmp_path, sub, NOW)
    assert [(x["key"], x["level"], x["unlocked"]) for x in view["accessories"]] == [
        ("mortarboard", 2, False),
        ("laurel", 4, False),
        ("crown", 6, False),
    ]
    assert all(x["key"] in service.AVATAR_PARTS["head"] for x in view["accessories"])


def test_the_pages_show_and_choose_the_rewards(tmp_path):
    sub = _plan(tmp_path)
    _timed(tmp_path, sub, 10)
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as web:
        page = web.get(f"/p/{sub.token}/rewards").text
        assert "Focus rings" in page and "Avatar accessories" in page and "Level 6" in page
        assert page.count('class="rw-ring ring-') == len(service.REWARD_RINGS)
        answer = web.post(f"/p/{sub.token}/rewards/backdrop", data={"key": "dawn"})
        assert answer.status_code == 200 and "Your focus page has its new look." in answer.text
        assert web.post(f"/p/{sub.token}/rewards/backdrop", data={"key": "night"}).status_code == 400
        assert web.post(f"/p/{sub.token}/rewards/hat", data={"key": "x"}).status_code == 404
        saved = service.load_subscription(tmp_path, sub.token)
        assert service.focus_look(saved)["backdrop"] == "dawn"
        avatar = web.get(f"/p/{sub.token}/avatar").text
        assert 'id="av-head-crown"' in avatar and "disabled" in avatar.split('id="av-head-crown"')[1][:40]
