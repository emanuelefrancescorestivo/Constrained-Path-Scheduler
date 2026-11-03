"""
Studying now and a session's reactions (DECISIONS.md D108). What must hold: nobody is
shown studying unless they said yes, and then only to the people who follow them, never
to anyone blocked either way; it follows the focus timer (shown while it runs, gone when
it stops, not past its planned end and never past four hours); a no takes it away at
once, and leaving the network too; Join starts the viewer's own timer on their course
of the same name. A session takes four reactions, one per person, the same again taking
it back, never from its author; notes and explanations keep their one mark; kudos given
before stay kudos.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service, social
from cps.store import Store
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
EXPLANATION = {
    "concept": "Eigenvalues",
    "course": "Algebra 3",
    "text": "Directions a matrix only stretches.",
    "visibility": "everyone",
}
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)  # 18:00 in Paris, a Tuesday


def _plan(
    root: Path, handle: str | None, subjects=("Algebra 3", "Advanced Statistics")
) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec(name, None, familiarity=3) for name in subjects],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(root, sub)
    if handle:
        form = {"handle": handle, "university": "Université Lyon 1", "programme": "L2", "old_enough": "1"}
        service.save_profile(root, sub.token, form)
    return sub


def _friends(root: Path, a: service.Subscription, b: service.Subscription, a_handle: str, b_handle: str):
    """`a` follows `b`, accepted."""
    service.follow(root, a.token, b_handle)
    service.answer_follow(root, b.token, a_handle, accept=True)


def _focus(
    root: Path, sub: service.Subscription, course: str, goal: int, now: datetime
) -> service.Subscription:
    fresh = service.update_subscription(
        root, sub.token, lambda current: service.start_focus(current, course, goal=goal, now=now)
    )
    service.sync_presence(root, fresh, now)
    return fresh


def test_nobody_is_shown_studying_without_their_yes(tmp_path):
    ada = _plan(tmp_path, "ada")
    ben = _plan(tmp_path, "ben")
    _friends(tmp_path, ben, ada, "ben", "ada")
    assert not service.studying_now_on(tmp_path, ada.token)  # off by default
    _focus(tmp_path, ada, "Algebra 3", 25, NOW)
    assert service.studying_now_view(tmp_path, ben, NOW) == []
    service.set_studying_now(tmp_path, ada.token, True)
    _focus(tmp_path, ada, "Algebra 3", 25, NOW)
    shown = service.studying_now_view(tmp_path, ben, NOW + timedelta(minutes=12))
    assert [(s["handle"], s["course"], s["minutes"]) for s in shown] == [("ada", "Algebra 3", 12)]
    assert shown[0]["join"] == "Algebra 3"  # ben has a course of that name
    # A no takes it away at once.
    service.set_studying_now(tmp_path, ada.token, False)
    assert service.studying_now_view(tmp_path, ben, NOW + timedelta(minutes=13)) == []


def test_only_followers_see_it_and_never_anyone_blocked(tmp_path):
    ada = _plan(tmp_path, "ada")
    ben = _plan(tmp_path, "ben")
    cleo = _plan(tmp_path, "cleo")
    _friends(tmp_path, ben, ada, "ben", "ada")
    service.set_studying_now(tmp_path, ada.token, True)
    _focus(tmp_path, ada, "Algebra 3", 45, NOW)
    assert service.studying_now_view(tmp_path, cleo, NOW) == []  # cleo does not follow ada
    assert service.studying_now_view(tmp_path, ada, NOW) == []  # never oneself
    service.follow(tmp_path, cleo.token, "ada")  # asked, not accepted
    assert service.studying_now_view(tmp_path, cleo, NOW) == []
    service.block(tmp_path, ada.token, "ben")
    assert service.studying_now_view(tmp_path, ben, NOW) == []


def test_it_follows_the_focus_timer(tmp_path):
    ada = _plan(tmp_path, "ada")
    ben = _plan(tmp_path, "ben")
    _friends(tmp_path, ben, ada, "ben", "ada")
    service.set_studying_now(tmp_path, ada.token, True)
    fresh = _focus(tmp_path, ada, "Algebra 3", 25, NOW)
    # Shown for the goal and ten minutes more; then gone, if nothing said it went on.
    assert service.studying_now_view(tmp_path, ben, NOW + timedelta(minutes=34))
    assert service.studying_now_view(tmp_path, ben, NOW + timedelta(minutes=36)) == []
    # The page's heartbeat past the goal keeps it, never past four hours.
    later = NOW + timedelta(minutes=50)
    service.sync_presence(tmp_path, service.focus_beat(fresh, now=later), later)
    assert service.studying_now_view(tmp_path, ben, later + timedelta(minutes=9))
    end = NOW + timedelta(hours=4, minutes=5)
    service.sync_presence(tmp_path, service.focus_beat(fresh, now=end), end)
    assert service.studying_now_view(tmp_path, ben, end) == []
    # Finished: gone at once.
    again = _focus(tmp_path, ada, "Algebra 3", 0, NOW)
    assert service.studying_now_view(tmp_path, ben, NOW + timedelta(minutes=100))  # no goal: two hours
    finished = service.finish_focus(again, now=NOW + timedelta(minutes=30))
    service.sync_presence(tmp_path, finished, NOW + timedelta(minutes=30))
    assert service.studying_now_view(tmp_path, ben, NOW + timedelta(minutes=31)) == []


def test_leaving_the_network_takes_it_away_and_the_copy_has_it(tmp_path):
    ada = _plan(tmp_path, "ada")
    service.set_studying_now(tmp_path, ada.token, True)
    _focus(tmp_path, ada, "Algebra 3", 25, NOW)
    copied = Store(tmp_path).export(ada.token)
    assert [r["course"] for r in copied["studying now"]] == ["Algebra 3"]
    service.leave_network(tmp_path, ada.token)
    with Store(tmp_path).connection() as db:
        assert db.execute("SELECT COUNT(*) FROM presence").fetchone()[0] == 0
    with pytest.raises(service.InvalidInput):
        service.set_studying_now(tmp_path, ada.token, True)  # no handle, no Studying now


def _post(root: Path, sub: service.Subscription, course="Algebra 3") -> str:
    fresh, post = service.log_session(
        root,
        sub,
        {"course": course, "minutes": "30", "effort": "5", "progress": "3", "visibility": "followers"},
        [],
        now=NOW,
    )
    service.save_subscription(root, fresh)
    assert post is not None
    return post.id


def test_a_session_takes_four_reactions_one_per_person(tmp_path):
    ada = _plan(tmp_path, "ada")
    ben = _plan(tmp_path, "ben")
    cleo = _plan(tmp_path, "cleo")
    for other, name in ((ben, "ben"), (cleo, "cleo")):
        _friends(tmp_path, other, ada, name, "ada")
    post = _post(tmp_path, ada)
    assert service.react(tmp_path, ben.token, post, "fire", now=NOW) == "fire"
    assert service.react(tmp_path, cleo.token, post, "kudos", now=NOW) == "kudos"
    assert service.react(tmp_path, ben.token, post, "brain", now=NOW) == "brain"  # replaces fire
    found = social.reactions_of(Store(tmp_path), [post], ben.token)[post]
    assert found["counts"] == {"kudos": 1, "fire": 0, "strong": 0, "brain": 1} and found["mine"] == "brain"
    assert service.react(tmp_path, ben.token, post, "brain", now=NOW) is None  # the same again: back
    assert social.reactions_of(Store(tmp_path), [post], ben.token)[post]["mine"] is None
    with pytest.raises(service.InvalidInput):
        service.react(tmp_path, ben.token, post, "love", now=NOW)
    with pytest.raises(service.InvalidInput):
        service.react(tmp_path, ada.token, post, "fire", now=NOW)  # never one's own
    # Kudos given before D108 count as the first reaction.
    assert social.kudos_of(Store(tmp_path), [post], cleo.token)[post] == (1, True)


def test_notes_and_explanations_keep_their_one_mark(tmp_path):
    ada = _plan(tmp_path, "ada")
    ben = _plan(tmp_path, "ben")
    _friends(tmp_path, ben, ada, "ben", "ada")
    explained = service.post_explanation(tmp_path, ada, EXPLANATION, now=NOW)
    post_id = explained.id
    with pytest.raises(service.InvalidInput):
        service.react(tmp_path, ben.token, post_id, "fire", now=NOW)
    assert service.toggle_kudos(tmp_path, ben.token, post_id, now=NOW)


@pytest.fixture
def web(tmp_path):
    clock = {"now": NOW}
    config = Config(
        store=tmp_path, clock=lambda: clock["now"], background=False, sweep_every=None, remind_every=None
    )
    with TestClient(create_app(config)) as client:
        client.root = tmp_path
        client.clock = clock
        yield client


def test_community_shows_who_is_studying_with_a_join(web):
    root = web.root
    ada = _plan(root, "ada")
    ben = _plan(root, "ben", subjects=("Advanced Statistics", "Algebra 3"))
    _friends(root, ben, ada, "ben", "ada")
    service.set_studying_now(root, ada.token, True)
    web.clock["now"] = NOW
    started = web.post(f"/p/{ada.token}/focus/start", data={"course": "Algebra 3", "goal": "45"})
    assert started.status_code == 200
    web.clock["now"] = NOW + timedelta(minutes=18)
    page = unescape(web.get(f"/p/{ben.token}/community").text)
    assert "Studying now" in page and "@ada" in page and "Algebra 3 · for 18 minutes" in page
    assert f'action="/p/{ben.token}/focus/start"' in page and 'name="course" value="Algebra 3"' in page
    joined = web.post(f"/p/{ben.token}/focus/start", data={"course": "Algebra 3", "goal": "25"})
    assert joined.status_code == 200
    focus = unescape(web.get(f"/p/{ben.token}/focus").text)
    assert "Also studying now:" in focus and "@ada" in focus
    again = unescape(web.get(f"/p/{ben.token}/community").text)
    assert "You are studying too." in again and f'href="/p/{ben.token}/focus"' in again
    # Stopped: gone from the feed.
    web.post(f"/p/{ada.token}/focus/cancel")
    assert "Studying now" not in unescape(web.get(f"/p/{ben.token}/community").text)


def test_the_yes_is_asked_on_the_profile_and_in_the_first_run(web):
    root = web.root
    ada = _plan(root, "ada")
    page = unescape(web.get(f"/p/{ada.token}/profile").text)
    assert 'name="studying_now" value="1">' in page  # not ticked
    form = {"handle": "ada", "university": "Université Lyon 1", "programme": "L2", "old_enough": "1"}
    web.post(f"/p/{ada.token}/profile", data={**form, "studying_now": "1"})
    assert service.studying_now_on(root, ada.token)
    assert 'name="studying_now" value="1" checked' in web.get(f"/p/{ada.token}/profile").text
    web.post(f"/p/{ada.token}/profile", data=form)
    assert not service.studying_now_on(root, ada.token)
    # A new plan's first run asks it with who sees the sessions, once there is a handle.
    files = {"file": ("t.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
    token = (
        web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        .headers["location"]
        .split("/")[2]
    )
    web.post(f"/p/{token}/start/you", data={**form, "handle": "bea"})
    sharing = unescape(web.get(f"/p/{token}/start/sharing").text)
    assert "Show my followers when I’m studying" in sharing
    web.post(f"/p/{token}/start/sharing", data={"visibility": "followers", "studying_now": "1"})
    assert service.studying_now_on(root, token)


def test_the_feed_shows_reactions_on_sessions(web):
    root = web.root
    ada = _plan(root, "ada")
    ben = _plan(root, "ben")
    _friends(root, ben, ada, "ben", "ada")
    post = _post(root, ada)
    page = unescape(web.get(f"/p/{ben.token}/community").text)
    assert f'action="/p/{ben.token}/post/{post}/react"' in page
    for kind in social.REACTIONS:
        assert f'name="kind" value="{kind}"' in page
    reacted = web.post(f"/p/{ben.token}/post/{post}/react", data={"kind": "fire"}, follow_redirects=False)
    assert reacted.status_code == 303
    page = unescape(web.get(f"/p/{ben.token}/community").text)
    assert 'value="fire" class="act react given" aria-pressed="true"' in page
    # The author sees what came, without buttons of their own.
    mine = unescape(web.get(f"/p/{ada.token}/community").text)
    assert f'/post/{post}/react"' not in mine and "On fire: 1" in mine
