"""
D158: a session's points, and its proof of work (the owner, 2026-11-19: "how many points it
will give you if you do it ... and you get those points if you post what you did and your
peers validate it like a proof of work, on top of an inherent bonus for completing it
(because a lazy student could simply say they studied even if they have not)").

What must hold: a session done on the student's word gives 10; a post that says or shows
what was done, vouched for by another student, gives 20 more; nobody vouches for their own,
nor for a post that says nothing; a block takes a vouch away; a day's bar adds up its
sessions; and a session shared from the calendar is logged against it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
SAID = {"course": "Algebra 3", "effort": "6", "progress": "4", "minutes": "50", "visibility": "everyone"}


def _plan(root) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    sub = service.finish_first_run(sub)
    service.save_subscription(root, sub)
    return sub


def _join(root, sub, handle):
    form = {"handle": handle, "university": "Université Lyon 1", "programme": "L2 Maths", "old_enough": "1"}
    service.save_profile(root, sub.token, form)


def test_points_for_doing_it_and_more_for_its_proof():
    items = [
        {"id": "a", "at": "2026-10-01T18:00:00", "course": "Algebra 3"},
        {"id": "b", "at": "2026-10-01T20:00:00", "course": "Algebra 3"},
        {"id": "c", "at": "2026-10-02T18:00:00", "course": "Analysis 3"},
        {"id": "d", "at": "2026-10-03T18:00:00", "course": "Analysis 3"},
    ]
    outcomes = {"a": "done", "b": "some", "c": "skipped"}
    # A proof logged without its session goes to the first of its day and course.
    loose = [{"sid": "", "day": "2026-10-01", "course": "algebra 3"}]
    points = service.session_points(items, outcomes, loose)
    assert {k: (v["state"], v["earned"]) for k, v in points.items()} == {
        "a": ("proven", 30),
        "b": ("done", 10),  # a self-test answered "some" happened
        "c": ("skipped", 0),
        "d": ("to do", 0),
    }
    assert {v["possible"] for v in points.values()} == {service.POINTS_DONE + service.POINTS_PROOF} == {30}
    # A proof naming its session goes to it, reported or not.
    named = service.session_points(items, {}, [{"sid": "d", "day": "2026-10-03", "course": "Analysis 3"}])
    assert named["d"]["state"] == "proven" and named["a"]["state"] == "to do"


def test_a_session_that_says_what_was_done_is_vouched_for_by_others_only(tmp_path):
    ada, bob, cleo, dan = (_plan(tmp_path) for _ in range(4))
    for sub, handle in ((ada, "ada"), (bob, "bob"), (cleo, "cleo")):
        _join(tmp_path, sub, handle)
    _, silent = service.log_session(tmp_path, ada, SAID, now=NOW)
    with pytest.raises(service.InvalidInput):  # minutes alone prove nothing
        service.toggle_check(tmp_path, bob.token, silent.id)
    _, post = service.log_session(
        tmp_path,
        ada,
        {**SAID, "simple": "Proved that eigenvectors of distinct eigenvalues are free."},
        now=NOW,
    )
    assert service.proof_posts(tmp_path, ada.token) == []  # not yet vouched for
    for who in (ada, dan):  # not one's own, and not without a handle
        with pytest.raises(service.InvalidInput):
            service.toggle_check(tmp_path, who.token, post.id)
    assert service.toggle_check(tmp_path, bob.token, post.id)
    proof = {"sid": "", "day": post.day, "course": "Algebra 3"}
    assert service.proof_posts(tmp_path, ada.token) == [proof]
    seen = service.post_view(tmp_path, cleo, post.id)["post"]
    assert (seen["checks"], seen["checked"]) == (1, False)
    service.block(tmp_path, ada.token, "bob")  # someone blocked vouches for nothing
    assert service.proof_posts(tmp_path, ada.token) == []


@pytest.fixture
def clock():
    return [NOW]


@pytest.fixture
def web(tmp_path, clock):
    config = Config(
        store=tmp_path, clock=lambda: clock[0], background=False, sweep_every=None, remind_every=None
    )
    with TestClient(create_app(config)) as client:
        yield client


def test_a_session_shared_from_the_calendar_is_proven_by_a_classmate(web, tmp_path, clock):
    ada, bob = _plan(tmp_path), _plan(tmp_path)
    _join(tmp_path, ada, "ada")
    _join(tmp_path, bob, "bob")
    view = web.get(f"/p/{ada.token}/calendar.json?start=2026-09-29&days=7").json()
    assert view["points_rule"] == {"done": 10, "proof": 20}
    first = min((i for i in view["items"] if i["kind"] == "study"), key=lambda i: i["at"])
    assert first["points"] == {"possible": 30, "earned": 0, "state": "to do"}
    day = view["days"][first["day"]]
    on_that_day = [i for i in view["items"] if i["kind"] == "study" and i["day"] == first["day"]]
    assert day["points"] == {"earned": 0, "possible": 30 * len(on_that_day)}
    # Before it starts, a session cannot be shared to prove it.
    assert 'name="sid"' not in web.get(f"/p/{ada.token}/log?sid={first['id']}").text
    clock[0] = datetime.fromisoformat(first["until"]).replace(tzinfo=UTC) + timedelta(hours=2)
    form = web.get(f"/p/{ada.token}/log?sid={first['id']}").text
    assert f'name="sid" value="{first["id"]}"' in form and "20 more points" in form
    saved = web.post(
        f"/p/{ada.token}/log",
        data={**SAID, "sid": first["id"], "simple": "Twelve exercises on eigenvalues, all checked."},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert service.load_subscription(tmp_path, ada.token).outcomes[first["id"]] == "done"
    post = service.social.posts_of(service._store(tmp_path), ada.token, "session")[0]
    assert post.data["sid"] == first["id"] and post.day == first["at"][:10]

    def mine():
        found = web.get(f"/p/{ada.token}/calendar.json?start=2026-09-29&days=7").json()
        item = next(i for i in found["items"] if i.get("id") == first["id"])
        return item["points"], found["days"][first["day"]]["points"]["earned"]

    assert mine() == ({"possible": 30, "earned": 10, "state": "done"}, 10)
    page = web.get(f"/p/{bob.token}/post/{post.id}").text
    assert ">Vouch<" in page
    web.post(f"/p/{bob.token}/post/{post.id}/check", data={"next": f"/p/{bob.token}/community"})
    assert mine() == ({"possible": 30, "earned": 30, "state": "proven"}, 30)
    assert "Vouched for by 1 classmate" in web.get(f"/p/{ada.token}/post/{post.id}").text
