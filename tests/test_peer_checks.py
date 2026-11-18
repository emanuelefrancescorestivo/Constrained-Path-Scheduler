"""
D141: classmates check each other's work, and sharing takes fewer taps. A reader of an
explanation or of notes says "It's right" with one tap, or suggests a fix, which the
author may accept and which readers then see first. The share forms open with the course
being studied already picked, as chips, and keep their button where the form ends.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service, social
from cps.store import Store
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
EXPLAINED = {"concept": "Eigenvalues", "course": "Algebra 3", "text": "A direction a matrix only stretches."}


def _plan(root) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(root, sub)
    return sub


def _join(root, sub, handle):
    form = {"handle": handle, "university": "Université Lyon 1", "programme": "L2 Maths", "old_enough": "1"}
    service.save_profile(root, sub.token, form)


@pytest.fixture
def people(tmp_path):
    """Ada explains; Bob and Cleo read; Dan has a plan and no handle."""
    ada, bob, cleo, dan = (_plan(tmp_path) for _ in range(4))
    for sub, handle in ((ada, "ada"), (bob, "bob"), (cleo, "cleo")):
        _join(tmp_path, sub, handle)
    return ada, bob, cleo, dan


def test_a_reader_says_it_is_right_with_one_tap_and_can_take_it_back(tmp_path, people):
    ada, bob, cleo, dan = people
    post = service.post_explanation(tmp_path, ada, EXPLAINED, now=NOW)
    assert service.toggle_check(tmp_path, bob.token, post.id)
    assert service.toggle_check(tmp_path, cleo.token, post.id)
    seen_by_bob = service.post_view(tmp_path, bob, post.id)["post"]
    assert (seen_by_bob["checks"], seen_by_bob["checked"]) == (2, True)
    assert not service.toggle_check(tmp_path, bob.token, post.id)  # the same tap takes it back
    assert service.post_view(tmp_path, ada, post.id)["post"]["checks"] == 1
    for who in (ada, dan):  # not one's own, and not without a handle
        with pytest.raises(service.InvalidInput):
            service.toggle_check(tmp_path, who.token, post.id)


def test_a_session_is_not_checked_nor_given_fixes(tmp_path, people):
    ada, bob, _, _ = people
    session = {
        "course": "Algebra 3",
        "effort": "6",
        "progress": "4",
        "minutes": "50",
        "visibility": "everyone",
    }
    _, post = service.log_session(tmp_path, ada, session, now=NOW)
    with pytest.raises(service.InvalidInput):
        service.toggle_check(tmp_path, bob.token, post.id)
    with pytest.raises(service.InvalidInput):
        service.add_comment(tmp_path, bob.token, post.id, "Should be 40 minutes", "fix")
    assert "checks" not in service.post_view(tmp_path, bob, post.id)["post"]


def test_a_fix_is_suggested_accepted_by_the_author_alone_and_shown_first(tmp_path, people):
    ada, bob, cleo, _ = people
    post = service.post_explanation(tmp_path, ada, EXPLAINED, now=NOW)
    service.add_comment(tmp_path, cleo.token, post.id, "Nice one!")
    service.add_comment(tmp_path, bob.token, post.id, "It stretches or flips: λ can be negative.", "fix")
    with pytest.raises(service.InvalidInput):  # the author changes their own by posting again
        service.add_comment(tmp_path, ada.token, post.id, "Also shrinks", "fix")
    with pytest.raises(service.InvalidInput):
        service.add_comment(tmp_path, bob.token, post.id, "?", "rewrite")
    view = service.post_view(tmp_path, ada, post.id)
    comments = view["comments"]
    assert [c["kind"] for c in comments] == ["comment", "fix"]
    fix = comments[1]
    assert fix["can_accept"] and not comments[0]["can_accept"]
    assert view["post"]["fixes"] == 1 and view["post"]["fixes_accepted"] == 0
    assert not view["can_fix"]  # the author is offered no fix button
    assert service.post_view(tmp_path, bob, post.id)["can_fix"]
    with pytest.raises(service.InvalidInput):  # only the author accepts
        service.accept_fix(tmp_path, bob.token, fix["id"])
    with pytest.raises(service.InvalidInput):  # and only a fix
        service.accept_fix(tmp_path, ada.token, comments[0]["id"])
    assert service.accept_fix(tmp_path, ada.token, fix["id"]) == (True, post.id)
    after = service.post_view(tmp_path, cleo, post.id)
    assert after["comments"][0]["id"] == fix["id"] and after["comments"][0]["accepted"]
    assert after["post"]["fixes_accepted"] == 1
    assert service.accept_fix(tmp_path, ada.token, fix["id"]) == (False, post.id)  # taken back


def test_notes_are_checked_too_and_a_blocked_reader_is_not_counted(tmp_path, people):
    ada, bob, cleo, _ = people
    form = {"course": "Algebra 3", "title": "Chapter 3", "body": "Eigenvalue: Av = λv", "own_work": "1"}
    notes = service.post_notes(tmp_path, ada, form, now=NOW)
    assert service.toggle_check(tmp_path, bob.token, notes.id)
    assert service.toggle_check(tmp_path, cleo.token, notes.id)
    assert service.post_view(tmp_path, ada, notes.id)["post"]["checks"] == 2
    service.block(tmp_path, ada.token, "bob")
    assert service.post_view(tmp_path, ada, notes.id)["post"]["checks"] == 1


def test_checks_and_fixes_go_with_the_post_with_who_left_and_with_a_deleted_plan(tmp_path, people):
    ada, bob, cleo, _ = people
    where = Store(tmp_path)

    def counts(post_id: str) -> tuple[int, int]:
        with where.connection() as db:
            checks = db.execute("SELECT COUNT(*) FROM checks WHERE post = ?", (post_id,)).fetchone()[0]
            fixes = db.execute(
                "SELECT COUNT(*) FROM comments WHERE post = ? AND kind = 'fix'", (post_id,)
            ).fetchone()[0]
        return checks, fixes

    first = service.post_explanation(tmp_path, ada, EXPLAINED, now=NOW)
    second = service.post_explanation(tmp_path, ada, {**EXPLAINED, "concept": "Rank"}, now=NOW)
    for post in (first, second):
        service.toggle_check(tmp_path, bob.token, post.id)
        service.add_comment(tmp_path, cleo.token, post.id, "Not quite: …", "fix")
    assert social.delete_post(where, ada.token, first.id)
    assert counts(first.id) == (0, 0)
    social.leave(where, bob.token)  # what Bob gave goes with him
    assert counts(second.id) == (0, 1)
    service.toggle_check(tmp_path, cleo.token, second.id)
    where.delete(ada.token)  # a deleted plan takes what others left on its posts
    assert counts(second.id) == (0, 0)


def test_the_share_form_starts_on_the_course_being_studied(tmp_path):
    sub = _plan(tmp_path)
    plan = service.PlanReport.from_dict(sub.plan)
    first = plan.sessions[0]
    start, end = datetime.fromisoformat(first.start), datetime.fromisoformat(first.end)
    assert service.share_course(sub, start + timedelta(minutes=5)) == first.subject  # under way
    assert service.share_course(sub, end + timedelta(minutes=5)) == first.subject  # ended today
    assert service.share_course(sub, NOW) == first.subject  # the next one
    assert service.share_course(replace(sub, plan=None), NOW) == ""


def test_a_course_typed_wins_over_the_chip(tmp_path, people):
    ada, *_ = people
    typed = service.post_explanation(tmp_path, ada, {**EXPLAINED, "course_other": " Topology "}, now=NOW)
    assert typed.data["course"] == "Topology"
    picked = service.post_explanation(tmp_path, ada, {**EXPLAINED, "course_other": ""}, now=NOW)
    assert picked.data["course"] == "Algebra 3"


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        client.root = tmp_path
        yield client


def test_the_forms_pick_the_course_fold_the_optional_and_end_with_their_button(web):
    ada = _plan(web.root)
    _join(web.root, ada, "ada")
    studied = service.share_course(ada, NOW)
    for path in (f"/p/{ada.token}/explain", f"/p/{ada.token}/notes/new"):
        page = web.get(path).text
        assert re.search(rf'name="course" value="{re.escape(studied)}" checked', page), path
        assert 'name="course_other"' in page and 'class="more-fields"' in page
        assert "sticky-actions" not in page, path  # the button no longer floats over the fields
        assert 'list="qa-courses"' not in page
    # A link from a course's page names its course; the idea of a week comes along.
    page = web.get(f"/p/{ada.token}/explain", params={"course": "Algebra 3", "concept": "Kernels"}).text
    assert 'name="course" value="Algebra 3" checked' in page and 'value="Kernels"' in page
    shared = web.post(
        f"/p/{ada.token}/explain", data={**EXPLAINED, "visibility": "everyone"}, follow_redirects=False
    )
    assert shared.status_code == 303 and shared.headers["location"].endswith("?saved=shared")
    assert "tell you it&#39;s right, or suggest a fix" in web.get(shared.headers["location"]).text
    kept = web.post(f"/p/{ada.token}/explain", data={**EXPLAINED, "visibility": "me"}, follow_redirects=False)
    assert kept.headers["location"].endswith("?saved=posted")


def test_on_the_page_a_reader_checks_suggests_a_fix_and_the_author_accepts_it(web):
    ada, bob = _plan(web.root), _plan(web.root)
    _join(web.root, ada, "ada")
    _join(web.root, bob, "bob")
    post = service.post_explanation(web.root, ada, EXPLAINED, now=NOW)
    page = web.get(f"/p/{bob.token}/post/{post.id}").text
    assert f'action="/p/{bob.token}/post/{post.id}/check"' in page and "It&#39;s right" in page
    assert 'name="kind" value="fix"' in page
    web.post(f"/p/{bob.token}/post/{post.id}/check", data={"next": f"/p/{bob.token}/community"})
    fix = web.post(
        f"/p/{bob.token}/post/{post.id}/comments",
        data={"body": "λ can be negative: it flips.", "kind": "fix"},
        follow_redirects=False,
    )
    assert fix.status_code == 303 and "saved=fix" in fix.headers["location"]
    mine = web.get(f"/p/{ada.token}/post/{post.id}").text
    assert "Checked by 1 classmate" in mine and "Suggested fix" in mine and "Accept the fix" in mine
    assert 'name="kind" value="fix"' not in mine  # no fix to one's own post
    comment_id = service.post_view(web.root, ada, post.id)["comments"][0]["id"]
    assert web.post(f"/p/{bob.token}/comments/{comment_id}/accept").status_code == 400
    accepted = web.post(f"/p/{ada.token}/comments/{comment_id}/accept", follow_redirects=False)
    assert accepted.status_code == 303
    assert "Fix accepted by the author" in web.get(f"/p/{bob.token}/post/{post.id}").text
    assert "1 fix suggested · 1 fix accepted" in web.get(f"/p/{bob.token}/community?tab=explore").text
