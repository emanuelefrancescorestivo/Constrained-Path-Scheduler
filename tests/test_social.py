"""
The study network (DECISIONS.md D16, D19): profiles, who sees which post, follows
asked for and accepted, the two feeds, kudos, comments, explanations, and leaving
the network without losing the diary.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cps import service, social
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
SESSION = {"course": "Algebra 3", "effort": "6", "progress": "4", "minutes": "50"}


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


def _join(tmp_path, sub, handle, university="Université Lyon 1", programme="L2 Maths"):
    form = {"handle": handle, "university": university, "programme": programme, "old_enough": "1"}
    return service.save_profile(tmp_path, sub.token, form)


def _post(tmp_path, sub, visibility, minutes=50, **extra):
    _, post = service.log_session(
        tmp_path, sub, {**SESSION, "minutes": str(minutes), "visibility": visibility, **extra}, now=NOW
    )
    return post


def _follow(tmp_path, follower, followed, handle):
    service.follow(tmp_path, follower.token, handle)
    followed_handle = service.profile_of(tmp_path, follower.token)["handle"]
    service.answer_follow(tmp_path, followed.token, followed_handle, accept=True)


@pytest.fixture
def people(tmp_path):
    """Ada posts; Cleo follows her; Bob is a stranger with a profile; Dan has a plan
    and no profile."""
    ada, bob, cleo, dan = (_plan(tmp_path) for _ in range(4))
    _join(tmp_path, ada, "ada")
    _join(tmp_path, bob, "bob", "Politecnico di Milano", "Ingegneria Fisica")
    _join(tmp_path, cleo, "cleo", "Sorbonne Université", "Licence Histoire")
    _follow(tmp_path, cleo, ada, "ada")
    return ada, bob, cleo, dan


def test_a_profile_needs_a_free_handle_a_university_and_the_age(tmp_path):
    ada, other = _plan(tmp_path), _plan(tmp_path)
    good = {"handle": "@Ada.L", "university": "  Lyon   1 ", "programme": "", "old_enough": "1"}
    for change, words in (
        ({"handle": "ab"}, "3 to 20"),
        ({"handle": "a b c"}, "3 to 20"),
        ({"handle": "émile"}, "3 to 20"),
        ({"handle": "Admin"}, "reserved"),
        ({"university": ""}, "university"),
        ({"old_enough": ""}, "15 or older"),
        ({"bio": "x" * 281}, "at most 280"),
    ):
        with pytest.raises(service.InvalidInput, match=words):
            service.save_profile(tmp_path, ada.token, {**good, **change})
    profile = service.save_profile(tmp_path, ada.token, good)
    assert profile["handle"] == "ada.l" and profile["university"] == "Lyon 1"
    assert profile["initial"] == "A" and profile["hue"].startswith("c")
    assert "token" not in profile  # what others see never holds the plan's key
    with pytest.raises(service.InvalidInput, match="taken"):
        service.save_profile(tmp_path, other.token, {**good, "handle": "ADA.L"})
    # Saving again under the same handle, or a new one, is a change, not a clash.
    assert service.save_profile(tmp_path, ada.token, {**good, "programme": "L2"})["programme"] == "L2"
    assert service.save_profile(tmp_path, ada.token, {**good, "handle": "ada_l"})["handle"] == "ada_l"
    assert service.save_profile(tmp_path, other.token, {**good, "handle": "ada.l"})["handle"] == "ada.l"


def test_who_sees_a_post(tmp_path, people):
    ada, bob, cleo, dan = people
    where = Store(tmp_path)
    posts = {v: _post(tmp_path, ada, v) for v in social.VISIBILITIES}
    seen = {
        name: {v for v, post in posts.items() if social.may_see(where, viewer, post)}
        for name, viewer in (("ada", ada.token), ("bob", bob.token), ("cleo", cleo.token), ("dan", dan.token))
    }
    assert seen == {
        "ada": {"me", "followers", "everyone"},
        "bob": {"everyone"},
        "cleo": {"followers", "everyone"},
        "dan": {"everyone"},
    }
    assert {v for v, post in posts.items() if social.may_see(where, None, post)} == {"everyone"}
    # A post's audience can change afterwards, and only its author can change it.
    service.set_post_visibility(tmp_path, ada.token, posts["me"].id, "followers")
    assert social.may_see(where, cleo.token, social.get_post(where, posts["me"].id))
    assert not service.set_post_visibility(tmp_path, bob.token, posts["me"].id, "everyone")


def test_sharing_needs_a_handle_and_without_one_a_post_stays_private(tmp_path):
    sub = _plan(tmp_path)
    _, quiet = service.log_session(tmp_path, sub, SESSION, now=NOW)
    assert quiet.visibility == "me"  # no choice made, no profile: private
    for visibility in ("followers", "everyone"):
        with pytest.raises(service.InvalidInput, match="choose a handle"):
            service.log_session(tmp_path, sub, {**SESSION, "visibility": visibility}, now=NOW)
    with pytest.raises(service.InvalidInput, match="choose a handle"):
        service.set_post_visibility(tmp_path, sub.token, quiet.id, "everyone")
    _join(tmp_path, sub, "zoe")
    _, shared = service.log_session(tmp_path, sub, SESSION, now=NOW)
    assert shared.visibility == "followers"  # the default once there is a profile (D16)


def test_follows_are_asked_for_and_accepted(tmp_path, people):
    ada, bob, cleo, dan = people
    followers_only = _post(tmp_path, ada, "followers")
    assert service.follow(tmp_path, bob.token, "@ADA") == "pending"
    assert service.post_view(tmp_path, bob, followers_only.id) is None  # asking is not following
    assert [p["handle"] for p in service.people_view(tmp_path, ada)["requests"]] == ["bob"]
    assert [p["handle"] for p in service.people_view(tmp_path, bob)["asked"]] == ["ada"]
    service.answer_follow(tmp_path, ada.token, "bob", accept=False)
    assert service.person_view(tmp_path, bob, "ada")["follow_state"] is None
    service.follow(tmp_path, bob.token, "ada")
    service.answer_follow(tmp_path, ada.token, "bob", accept=True)
    view = service.person_view(tmp_path, bob, "ada", NOW)
    assert view["follow_state"] == "accepted" and not view["follows_me"] and len(view["posts"]) == 1
    assert service.person_view(tmp_path, ada, "bob")["follows_me"]
    circle = service.people_view(tmp_path, ada)
    assert [p["handle"] for p in circle["followers"]] == ["bob", "cleo"] and circle["following"] == []
    # A follower can be removed by the person followed; anyone can stop following.
    service.remove_follower(tmp_path, ada.token, "bob")
    assert service.post_view(tmp_path, bob, followers_only.id) is None
    service.unfollow(tmp_path, cleo.token, "ada")
    assert service.people_view(tmp_path, ada)["followers"] == []
    with pytest.raises(service.InvalidInput, match="yourself"):
        service.follow(tmp_path, ada.token, "ada")
    with pytest.raises(service.InvalidInput, match="choose a handle"):
        service.follow(tmp_path, dan.token, "ada")
    with pytest.raises(service.InvalidInput, match="nobody called @ghost"):
        service.follow(tmp_path, bob.token, "ghost")
    assert service.person_view(tmp_path, bob, "ghost") is None


def test_the_following_feed_and_explore(tmp_path, people):
    ada, bob, cleo, dan = people
    private = _post(tmp_path, ada, "me", minutes=10)
    for_followers = _post(tmp_path, ada, "followers", minutes=20)
    public = _post(tmp_path, ada, "everyone", minutes=30)
    bobs = _post(tmp_path, bob, "everyone", minutes=40, course="Fisica 2")
    cleos = _post(tmp_path, cleo, "followers", minutes=45)
    _, quiet = service.log_session(tmp_path, dan, SESSION, now=NOW)  # Dan has no profile

    def ids(view):
        return {card["id"] for card in view["posts"]}

    following = service.community_view(tmp_path, cleo, now=NOW)
    assert ids(following) == {for_followers.id, public.id, cleos.id}
    assert private.id not in ids(service.community_view(tmp_path, ada, now=NOW))  # the diary keeps it
    explore = service.community_view(tmp_path, dan, tab="explore", now=NOW)
    assert ids(explore) == {public.id, bobs.id} and explore["profile"] is None
    assert quiet.id not in ids(explore)

    def explore_with(**filters):
        return ids(service.community_view(tmp_path, cleo, tab="explore", filters=filters, now=NOW))

    assert explore_with(uni="politecnico") == {bobs.id}
    assert explore_with(prog="l2 MATHS") == {public.id}
    assert explore_with(course="fisica") == {bobs.id}
    assert explore_with(kind="explain") == set()
    assert explore_with(uni="nowhere") == set()
    card = next(c for c in explore["posts"] if c["id"] == bobs.id)
    assert card["author"]["handle"] == "bob" and card["author"]["university"] == "Politecnico di Milano"
    assert card["kudos"] == 0 and not card["kudos_given"] and card["comments"] == 0 and not card["mine"]


def test_kudos_and_comments(tmp_path, people):
    ada, bob, cleo, dan = people
    post = _post(tmp_path, ada, "followers")
    assert service.toggle_kudos(tmp_path, cleo.token, post.id) is True
    assert service.toggle_kudos(tmp_path, ada.token, post.id) is True
    card = service.post_view(tmp_path, cleo, post.id)["post"]
    assert card["kudos"] == 2 and card["kudos_given"]
    assert service.toggle_kudos(tmp_path, cleo.token, post.id) is False  # taken back
    assert service.post_view(tmp_path, ada, post.id)["post"]["kudos"] == 1
    for who in (bob, dan):  # a post one cannot see cannot be cheered or answered
        with pytest.raises(service.InvalidInput, match="not, or no longer, visible"):
            service.toggle_kudos(tmp_path, who.token, post.id)
        with pytest.raises(service.InvalidInput, match="not, or no longer, visible"):
            service.add_comment(tmp_path, who.token, post.id, "hi")
    with pytest.raises(service.InvalidInput, match="write something"):
        service.add_comment(tmp_path, cleo.token, post.id, "   ")
    with pytest.raises(service.InvalidInput, match="at most 600"):
        service.add_comment(tmp_path, cleo.token, post.id, "x" * 601)
    service.add_comment(tmp_path, cleo.token, post.id, "Nice work on sheet 2!")
    service.add_comment(tmp_path, ada.token, post.id, "Thanks <3")
    view = service.post_view(tmp_path, cleo, post.id, NOW)
    assert [c["body"] for c in view["comments"]] == ["Nice work on sheet 2!", "Thanks <3"]
    assert [c["can_delete"] for c in view["comments"]] == [True, False] and view["can_comment"]
    assert view["post"]["comments"] == 2
    cleos, adas = (c["id"] for c in view["comments"])
    assert not service.delete_comment(tmp_path, bob.token, cleos)  # a third person cannot
    assert service.delete_comment(tmp_path, ada.token, cleos)  # the post's author can
    assert service.delete_comment(tmp_path, ada.token, adas)
    assert service.post_view(tmp_path, cleo, post.id)["comments"] == []
    # Without a profile, an everyone-post can be read but not answered.
    public = _post(tmp_path, ada, "everyone")
    assert service.post_view(tmp_path, dan, public.id)["can_comment"] is False
    with pytest.raises(service.InvalidInput, match="choose a handle"):
        service.toggle_kudos(tmp_path, dan.token, public.id)


def test_an_explanation_is_for_everyone_by_default(tmp_path, people):
    ada, bob, _, dan = people
    form = {"concept": "Eigenvalues", "course": "Algebra 3", "text": "A direction a matrix only stretches."}
    post = service.post_explanation(tmp_path, ada, form, now=NOW)
    assert post.kind == "explain" and post.visibility == "everyone"
    card = service.post_view(tmp_path, bob, post.id)["post"]
    assert card["concept"] == "Eigenvalues" and card["text"].startswith("A direction")
    assert service.toggle_kudos(tmp_path, bob.token, post.id)  # "I got it"
    explore = service.community_view(tmp_path, bob, tab="explore", filters={"kind": "explain"}, now=NOW)
    assert [c["id"] for c in explore["posts"]] == [post.id]
    assert service.post_explanation(tmp_path, dan, form, now=NOW).visibility == "me"
    with pytest.raises(service.InvalidInput):
        service.post_explanation(tmp_path, ada, {**form, "concept": ""}, now=NOW)
    with pytest.raises(service.InvalidInput):
        service.post_explanation(tmp_path, ada, {**form, "text": "x" * 1201}, now=NOW)


def test_leaving_the_network_keeps_the_diary(tmp_path, people):
    ada, bob, cleo, dan = people
    shared = _post(tmp_path, ada, "everyone")
    bobs = _post(tmp_path, bob, "everyone")
    service.add_comment(tmp_path, cleo.token, shared.id, "Bravo")
    service.toggle_kudos(tmp_path, cleo.token, shared.id)
    service.add_comment(tmp_path, ada.token, bobs.id, "Same here")
    service.toggle_kudos(tmp_path, ada.token, bobs.id)
    service.leave_network(tmp_path, ada.token)
    assert service.profile_of(tmp_path, ada.token) is None
    diary = service.diary_view(tmp_path, ada, NOW)["posts"]
    assert [p["id"] for p in diary] == [shared.id] and diary[0]["visibility"] == "me"
    assert service.post_view(tmp_path, cleo, shared.id) is None
    on_bobs = service.post_view(tmp_path, bob, bobs.id)
    assert on_bobs["comments"] == [] and on_bobs["post"]["kudos"] == 0
    assert service.people_view(tmp_path, cleo)["following"] == []
    assert _join(tmp_path, dan, "ada")["handle"] == "ada"  # the handle is free again
    # And deleting a plan takes the rest with it.
    service.add_comment(tmp_path, bob.token, bobs.id, "Thanks")
    assert service.delete_subscription(tmp_path, bob.token)
    assert service.profile_of(tmp_path, bob.token) is None
    assert service.community_view(tmp_path, cleo, tab="explore", now=NOW + timedelta(days=1))["posts"] == []


def test_search_finds_people_by_handle_university_or_programme(tmp_path, people):
    ada = people[0]
    assert [p["handle"] for p in service.people_view(tmp_path, ada, "politec")["found"]] == ["bob"]
    assert [p["handle"] for p in service.people_view(tmp_path, ada, "@CLE")["found"]] == ["cleo"]
    assert [p["handle"] for p in service.people_view(tmp_path, ada, "histoire")["found"]] == ["cleo"]
    assert service.people_view(tmp_path, ada, "ada")["found"] == []  # not oneself
    assert service.people_view(tmp_path, ada, "a")["found"] == []  # too short to mean anything
