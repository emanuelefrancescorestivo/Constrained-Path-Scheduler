"""
The study map (DECISIONS.md D133): public places to study, added by students, and at
each who is studying there now. What must hold: a place needs a handle, a name and a
point, the same name within about 50 m is the same place, ten new places a day at most;
only students who said yes to Studying now are ever at a place, and only at the place
they chose for a running session; the people a student follows show by name, everyone
else as a count, nobody blocked either way at all; a place outlives the plan that added
it without naming it; the map's page alone may load OpenStreetMap's tiles and ask the
device where it is, and the tiles never carry a plan's address.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from cps import service, social
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)  # 10:00 in Paris, a Tuesday
LIBRARY = (48.84695, 2.34479)


def _plan(where: Path) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(where, service.finish_first_run(sub))
    return service.load_subscription(where, sub.token)


@pytest.fixture
def people(tmp_path):
    """Ada, Bob, Cleo, Dan and Eve, with handles; all but Eve said yes to Studying now.
    Ada follows Bob; Ada blocked Dan."""
    subs = {}
    for handle in ("ada", "bob", "cleo", "dan", "eve"):
        sub = _plan(tmp_path)
        form = {"handle": handle, "university": "Sorbonne Université", "old_enough": "1"}
        service.save_profile(tmp_path, sub.token, form)
        if handle != "eve":
            service.set_studying_now(tmp_path, sub.token, True)
        subs[handle] = sub
    service.follow(tmp_path, subs["ada"].token, "bob")
    service.answer_follow(tmp_path, subs["bob"].token, "ada", True)
    service.block(tmp_path, subs["ada"].token, "dan")
    return subs


def _study_at(
    where: Path, sub: service.Subscription, place: str, now: datetime = NOW
) -> service.Subscription:
    """Start a 45-minute focus session at `place`, as the focus page does."""
    started = service.start_focus(sub, "Algebra 3", goal=45, place=place, now=now)
    service.save_subscription(where, started)
    service.sync_presence(where, started, now)
    return started


def test_a_place_needs_a_handle_a_name_and_a_point_and_is_found_again(tmp_path, people):
    store = Store(tmp_path)
    nobody = _plan(tmp_path)
    with pytest.raises(social.SocialError):
        social.add_place(store, nobody.token, "University library", *LIBRARY, now=NOW)
    ada = people["ada"].token
    with pytest.raises(social.SocialError):
        social.add_place(store, ada, "B", *LIBRARY, now=NOW)
    for lat, lon in (("north", "2.3"), ("91", "2.3"), ("48.8", "181")):
        with pytest.raises(social.SocialError):
            social.add_place(store, ada, "Somewhere", lat, lon, now=NOW)
    first = social.add_place(store, ada, "  University   library ", *LIBRARY, now=NOW)
    assert first.name == "University library" and (first.lat, first.lon) == LIBRARY
    # The same name within about 50 m is the same place; another name is another place.
    again = social.add_place(
        store, people["bob"].token, "university library", LIBRARY[0] + 0.0003, LIBRARY[1], now=NOW
    )
    assert again == first
    other = social.add_place(store, ada, "Corner café", LIBRARY[0] + 0.0003, LIBRARY[1], now=NOW)
    assert other.id != first.id


def test_ten_new_places_a_day_at_most(tmp_path, people):
    store = Store(tmp_path)
    ada = people["ada"].token
    for n in range(social.PLACES_A_DAY):
        social.add_place(store, ada, f"Place {n}", 48.8 + n * 0.01, 2.3, now=NOW)
    with pytest.raises(social.SocialError):
        social.add_place(store, ada, "One more", 49.5, 2.3, now=NOW)
    social.add_place(store, ada, "Tomorrow", 49.5, 2.3, now=NOW + timedelta(days=1, minutes=1))


def test_followers_by_name_others_counted_and_nobody_blocked(tmp_path, people):
    place = social.add_place(Store(tmp_path), people["ada"].token, "University library", *LIBRARY, now=NOW)
    for handle in ("bob", "cleo", "dan", "eve"):
        _study_at(tmp_path, people[handle], place.id)
    ada = people["ada"]
    view = service.map_view(tmp_path, ada, NOW + timedelta(minutes=5))
    row = next(r for r in view["places"] if r["id"] == place.id)
    # Bob is followed: by name. Cleo is not: counted. Dan is blocked: neither. Eve said no
    # to Studying now: never at a place.
    assert [n["handle"] for n in row["named"]] == ["bob"] and row["named"][0]["course"] == "Algebra 3"
    assert row["others"] == 1 and row["count"] == 2 and not row["me"]
    assert row["line"] == "@bob and 1 other"
    # Seen from Cleo, who follows nobody: two people counted, no names (Ada does not study).
    cleo = service.map_view(tmp_path, people["cleo"], NOW + timedelta(minutes=5))
    seen = next(r for r in cleo["places"] if r["id"] == place.id)
    assert seen["named"] == [] and seen["others"] == 2 and seen["me"] and seen["count"] == 3
    # Past the session's end (and its grace) nobody is there any more.
    later = service.map_view(tmp_path, ada, NOW + timedelta(hours=2))
    assert next(r for r in later["places"] if r["id"] == place.id)["count"] == 0


def test_a_place_needs_studying_now_and_a_running_session(tmp_path, people):
    place = social.add_place(Store(tmp_path), people["ada"].token, "University library", *LIBRARY, now=NOW)
    eve = people["eve"]
    running = service.start_focus(eve, "Algebra 3", goal=45, now=NOW)
    with pytest.raises(service.InvalidInput):
        service.choose_place(tmp_path, running, place.id)  # Eve said no to Studying now
    ada = people["ada"]
    with pytest.raises(service.InvalidInput):
        service.choose_place(tmp_path, ada, place.id)  # no session running
    at = service.choose_place(tmp_path, service.start_focus(ada, "Algebra 3", goal=45, now=NOW), place.id)
    assert at.options["focus"]["place"] == place.id and at.options["places_recent"] == [place.id]
    assert service.leave_place(at).options["focus"]["place"] == ""
    with pytest.raises(service.InvalidInput):
        service.choose_place(tmp_path, at, "no-such-place")


def test_the_map_centres_on_the_students_place_else_their_city(tmp_path, people):
    ada = people["ada"]
    view = service.map_view(tmp_path, ada, NOW)
    assert view["zoom"] == 13 and view["places"] == [] and view["studying"] == 0
    assert 48 < view["center"][0] < 49.5 and 2 < view["center"][1] < 2.7  # Paris, Europe/Paris's city
    place = social.add_place(Store(tmp_path), ada.token, "University library", *LIBRARY, now=NOW)
    started = _study_at(tmp_path, ada, place.id)
    view = service.map_view(tmp_path, started, NOW)
    assert (
        view["center"] == list(LIBRARY) and view["zoom"] == 15 and view["mine_name"] == "University library"
    )
    assert view["places"][0]["line"] == "Just you"


def test_a_place_outlives_the_plan_that_added_it_and_is_in_its_copy(tmp_path, people):
    store = Store(tmp_path)
    ada = people["ada"].token
    place = social.add_place(store, ada, "University library", *LIBRARY, now=NOW)
    copy = store.export(ada)
    assert [(r["name"], r["lat"], r["lon"]) for r in copy["places_added"]] == [
        ("University library", *LIBRARY)
    ]
    service.delete_subscription(tmp_path, ada)
    assert social.get_place(store, place.id) == place
    with store.connection() as db:
        assert db.execute("SELECT added_by FROM places WHERE id = ?", (place.id,)).fetchone() == (None,)


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def test_the_map_page_alone_loads_tiles_and_asks_where_the_device_is(web, people):
    ada = people["ada"].token
    page = web.get(f"/p/{ada}/map")
    assert page.status_code == 200
    csp = page.headers["content-security-policy"]
    assert f"img-src 'self' {service.MAP_TILE_HOST};" in csp and "script-src 'self';" in csp
    assert "geolocation=(self)" in page.headers["permissions-policy"]
    assert page.headers["referrer-policy"] == "no-referrer"
    for other in (f"/p/{ada}", f"/p/{ada}/community", f"/p/{ada}/focus"):
        answer = web.get(other)
        assert "tile.openstreetmap.org" not in answer.headers["content-security-policy"], other
        assert "geolocation=()" in answer.headers["permissions-policy"], other
    html = unescape(page.text)
    assert "OpenStreetMap</a> contributors" in html and "openstreetmap.org/fixthemap" in html
    assert f'data-tiles="{service.MAP_TILES}"' in html
    script = web.get("/static/map.js").text
    assert 'referrerPolicy: "origin"' in script and "tile.openstreetmap.org" not in script
    for path in ("/static/vendor/leaflet/leaflet.js", "/static/vendor/leaflet/leaflet.css"):
        assert web.get(path).status_code == 200, path
    licence = Path(service.__file__).parent / "web" / "static" / "vendor" / "leaflet" / "LICENSE"
    assert "BSD 2-Clause License" in licence.read_text(encoding="utf-8")


def test_add_a_place_study_there_be_seen_and_leave(web, tmp_path, people):
    ada, bob = people["ada"].token, people["bob"].token
    added = web.post(
        f"/p/{bob}/map/place", data={"name": "University library", "lat": LIBRARY[0], "lon": LIBRARY[1]}
    )
    assert added.status_code == 200 and "University library" in unescape(added.text)
    place = social.places_around(Store(tmp_path), *LIBRARY, 0.01)[0]
    # Without a session, "Study here" opens the focus page with the place chosen.
    here = web.post(f"/p/{bob}/map/here", data={"place": place.id}, follow_redirects=False)
    assert here.status_code == 303 and here.headers["location"] == f"/p/{bob}/focus?place={place.id}"
    focus = unescape(web.get(here.headers["location"]).text)
    assert "At University library" in focus and f'name="place" value="{place.id}"' in focus
    web.post(f"/p/{bob}/focus/start", data={"course": "Algebra 3", "goal": "45", "place": place.id})
    assert "At University library" in unescape(web.get(f"/p/{bob}/focus").text)
    # Ada follows Bob: on her map, Bob by name, with Join.
    seen = unescape(web.get(f"/p/{ada}/map").text)
    assert "University library" in seen and "@bob" in seen and ">Join<" in seen
    mine = unescape(web.get(f"/p/{bob}/map").text)
    assert "You're studying at University library" in mine and "You are here" in mine
    left = web.post(f"/p/{bob}/map/leave", follow_redirects=False)
    assert left.status_code == 303
    assert "@bob" not in unescape(web.get(f"/p/{ada}/map").text)
