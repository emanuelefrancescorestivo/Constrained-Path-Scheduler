"""
The notes library (DECISIONS.md D26): a student's own notes by course, marked
helpful by others, found by course, university and programme, and the month's most
helpful notes of each course marked for everyone, notes ranked and never people.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cps import service, social
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
LAST_MONTH = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
PAGE = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xc0\x00\x11\x08\x00\x10\x00\x10\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00pixels\xff\xd9"
)
NOTES = {"course": "Analysis 1", "title": "Limits in one page", "note": "The definitions.", "own_work": "1"}


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


def _join(tmp_path, sub, handle, university="Université Lyon 1", programme="L1 Maths"):
    form = {"handle": handle, "university": university, "programme": programme, "old_enough": "1"}
    service.save_profile(tmp_path, sub.token, form)


def _notes(tmp_path, sub, pages=1, when=NOW, **changes) -> social.Post:
    return service.post_notes(tmp_path, sub, {**NOTES, **changes}, [PAGE] * pages, now=when)


def _mark(tmp_path, who, post, when=NOW) -> None:
    assert social.toggle_kudos(Store(tmp_path), who.token, post, now=when)


@pytest.fixture
def people(tmp_path):
    """Five students with handles, four of them in Lyon and one in Milan, and Dan
    with a plan and no handle."""
    subs = [_plan(tmp_path) for _ in range(6)]
    for sub, handle in zip(subs[:4], ("ada", "bob", "cleo", "eve"), strict=True):
        _join(tmp_path, sub, handle)
    _join(tmp_path, subs[4], "fra", "Politecnico di Milano", "Ingegneria Fisica")
    return subs


def test_notes_need_a_course_a_title_pages_and_the_authors_word(tmp_path, people):
    ada, *_, dan = people
    with pytest.raises(service.InvalidInput, match="your own notes"):
        _notes(tmp_path, ada, own_work="")
    with pytest.raises(service.InvalidInput, match="at least one photo"):
        _notes(tmp_path, ada, pages=0)
    with pytest.raises(service.InvalidInput, match="at most 8 photos"):
        _notes(tmp_path, ada, pages=9)
    for field in ("course", "title"):
        with pytest.raises(service.InvalidInput):
            _notes(tmp_path, ada, **{field: " "})
    eight = _notes(tmp_path, ada, pages=8)
    assert eight.kind == "notes" and len(eight.data["photos"]) == 8 and eight.data["own_work"] is True
    assert eight.visibility == "everyone"  # the library is for others
    assert _notes(tmp_path, dan).visibility == "me"  # without a handle, notes stay private
    with pytest.raises(service.InvalidInput, match="choose a handle"):
        _notes(tmp_path, dan, visibility="everyone")
    # A session or an explanation still takes four photos at most.
    with pytest.raises(service.InvalidInput, match="at most 4 photos"):
        service.post_explanation(tmp_path, ada, {"concept": "Limits", "text": "Close."}, [PAGE] * 5, now=NOW)


def test_others_mark_notes_helpful_and_the_profile_says_so(tmp_path, people):
    ada, bob, cleo, *_ = people
    shared = _notes(tmp_path, ada)
    private = _notes(tmp_path, ada, visibility="me", title="Drafts")
    with pytest.raises(service.InvalidInput, match="cannot mark your own"):
        service.toggle_kudos(tmp_path, ada.token, shared.id)
    assert service.toggle_kudos(tmp_path, bob.token, shared.id)
    assert service.toggle_kudos(tmp_path, cleo.token, shared.id)
    assert not service.toggle_kudos(tmp_path, cleo.token, shared.id)  # taken back
    assert social.notes_recognition(Store(tmp_path), ada.token) == (1, 1)  # private notes do not count
    page = service.person_view(tmp_path, bob, "ada", NOW)
    assert page["notes"] == {"shared": 1, "helpful": 1}
    assert [p["id"] for p in page["posts"]] == [shared.id]
    assert private.id not in [c["id"] for c in service.library_view(tmp_path, bob, now=NOW)["notes"]]
    assert private.id in [c["id"] for c in service.library_view(tmp_path, ada, now=NOW)["notes"]]


def test_the_library_finds_notes_and_puts_the_months_most_helpful_first(tmp_path, people):
    ada, bob, cleo, eve, fra, _ = people
    old = _notes(tmp_path, cleo, when=LAST_MONTH, title="Last month's sheet")
    limits = _notes(tmp_path, ada, when=NOW - timedelta(hours=3))
    series = _notes(tmp_path, bob, when=NOW - timedelta(hours=2), title="Series, worked examples")
    vectors = _notes(tmp_path, fra, when=NOW - timedelta(hours=1), course="Fisica 1", title="Vettori")
    for who in (bob, cleo):
        _mark(tmp_path, who, limits)
    for who in (ada, bob, eve, fra):
        _mark(tmp_path, who, old, LAST_MONTH + timedelta(days=1))  # many marks, all last month
    _mark(tmp_path, ada, series)

    def ids(viewer=eve, **filters):
        sort = filters.pop("sort", "helpful")
        view = service.library_view(tmp_path, viewer, filters=filters, sort=sort, now=NOW)
        return [c["id"] for c in view["notes"]]

    # This month's marks first, then all marks, then the newest.
    assert ids() == [limits.id, series.id, old.id, vectors.id]
    assert ids(sort="new") == [vectors.id, series.id, limits.id, old.id]
    assert ids(course="analysis") == [limits.id, series.id, old.id]
    assert ids(uni="milano") == [vectors.id]
    assert ids(prog="ingegneria", course="fisica") == [vectors.id]
    assert ids(course="chemistry") == []
    view = service.library_view(tmp_path, eve, now=NOW)
    first = view["notes"][0]
    assert first["kudos"] == 2 and first["month"] == 2 and first["author"]["handle"] == "ada"
    assert first["top"] == 1 and view["notes"][1]["top"] is None  # one mark is not enough
    # Followers-only notes reach followers alone; a block hides them both ways.
    private = _notes(tmp_path, cleo, visibility="followers", title="For my study group")
    assert private.id not in ids()
    service.follow(tmp_path, eve.token, "cleo")
    service.answer_follow(tmp_path, cleo.token, "eve", accept=True)
    assert private.id in ids()
    service.block(tmp_path, ada.token, "eve")
    assert limits.id not in ids() and limits.id in ids(viewer=ada)


def test_the_months_top_is_per_course_and_needs_two_people(tmp_path, people):
    ada, bob, cleo, eve, fra, _ = people
    where = Store(tmp_path)
    a = _notes(tmp_path, ada, title="A")
    b = _notes(tmp_path, bob, title="B")
    c = _notes(tmp_path, cleo, title="C", course="  ANALYSIS 1 ")  # the same course, typed otherwise
    d = _notes(tmp_path, eve, title="D")
    e = _notes(tmp_path, fra, title="E", course="Fisica 1")
    lone = _notes(tmp_path, ada, title="One mark")
    friends = _notes(tmp_path, ada, title="For followers", visibility="followers")
    for who in (bob, cleo, eve, fra):
        _mark(tmp_path, who, a)
    for who in (ada, cleo, eve):
        _mark(tmp_path, who, b)
    for who in (ada, bob):
        _mark(tmp_path, who, c, NOW - timedelta(hours=2))
    for who in (ada, bob):
        _mark(tmp_path, who, d)  # as many as c, later: c keeps its place
    for who in (ada, bob):
        _mark(tmp_path, who, e)
    _mark(tmp_path, bob, lone)
    service.follow(tmp_path, bob.token, "ada")
    service.answer_follow(tmp_path, ada.token, "bob", accept=True)
    _mark(tmp_path, bob, friends)
    month = "2026-09-01T00:00:00+00:00"
    top = social.top_notes(where, month)
    assert top == {a.id: 1, b.id: 2, c.id: 3, e.id: 1}  # three per course; d is fourth
    assert social.top_notes(where, "2026-10-01T00:00:00+00:00") == {}  # a new month starts empty
    # A hidden post leaves the top while it is reviewed.
    for who in (bob, cleo, eve):
        service.report(tmp_path, who.token, "post", a.id, "copied")
    assert social.get_post(where, a.id).hidden
    assert social.top_notes(where, month) == {b.id: 1, c.id: 2, d.id: 3, e.id: 1}
    assert service.REPORT_REASONS["copied"] == "Not their own work"


def test_the_pilots_measures_count_notes(tmp_path, people):
    ada, bob, *_ = people
    notes = _notes(tmp_path, ada)
    service.toggle_kudos(tmp_path, bob.token, notes.id)
    network = service.engagement(tmp_path, now=NOW + timedelta(days=1))["network"]
    assert network["notes"] == 1
