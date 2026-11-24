"""
Crests (DECISIONS.md D189): levels from points, badges in four tiers, and the weekly
league among friends who follow each other.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from cps import crests, progress, service, social
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # a Tuesday, 8:00 in Rome


def _plan(where) -> service.Subscription:
    sub = service.start_subscription(
        tz="Europe/Rome", ics=(EXAMPLES / "sample-semester.ics").read_bytes(), now=NOW
    )
    service.save_subscription(where, sub)
    return sub


def _join(where, sub, handle):
    form = {
        "handle": handle,
        "university": "University of Bologna",
        "programme": "Physics",
        "old_enough": "1",
    }
    return service.save_profile(where, sub.token, form, tz=sub.options["tz"])


def _friends(where, a, b, handle_a, handle_b):
    """Two students who follow each other, both follows accepted."""
    service.follow(where, a.token, handle_b)
    service.answer_follow(where, b.token, handle_a, accept=True)
    service.follow(where, b.token, handle_a)
    service.answer_follow(where, a.token, handle_b, accept=True)


def _log(where, sub, day: date, course="Algebra 3", n=1):
    """`n` sessions typed into the diary on `day`, outside the plan."""
    for _ in range(n):
        form = {"course": course, "effort": "5", "progress": "3", "minutes": "40", "visibility": "me"}
        at = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=16)
        sub, _post = service.log_session(where, sub, form, now=at)
    return service.load_subscription(where, sub.token)


@pytest.fixture
def league(tmp_path):
    """Four students, each the others' friend, all with a profile."""
    subs = [_plan(tmp_path) for _ in range(4)]
    handles = ["ada", "bea", "cyd", "dan"]
    for sub, handle in zip(subs, handles, strict=True):
        _join(tmp_path, sub, handle)
    for i in range(4):
        for j in range(i + 1, 4):
            _friends(tmp_path, subs[i], subs[j], handles[i], handles[j])
    return [service.load_subscription(tmp_path, s.token) for s in subs]


# --------------------------------------------------------------------------- #
# The pure rules
# --------------------------------------------------------------------------- #


def test_each_level_asks_a_hundred_points_more_than_the_one_before():
    assert [crests.level_start(n) for n in range(1, 6)] == [0, 100, 300, 600, 1000]
    first = crests.level_of(0)
    assert (first["n"], first["name"], first["next"], first["left"], first["percent"]) == (
        1,
        "Spark",
        100,
        100,
        0,
    )
    assert crests.level_of(99)["n"] == 1 and crests.level_of(100)["n"] == 2
    middle = crests.level_of(450)
    assert (middle["n"], middle["name"], middle["start"], middle["next"], middle["percent"]) == (
        3,
        "Synapse",
        300,
        600,
        50,
    )
    # Past the last name, the name repeats and the levels go on.
    far = crests.level_of(crests.level_start(12))
    assert far["n"] == 12 and far["name"] == far["next_name"] == "Mastery"
    assert crests.level_of(-5)["n"] == 1


def test_a_tier_is_the_day_its_threshold_was_reached_undated_ones_first():
    days = [date(2026, 10, d) for d in (5, 1, 3)]
    assert crests.tiers_from_events(days, (1, 3, 4, 10)) == [date(2026, 10, 1), date(2026, 10, 5), None, None]
    # A step finished on a day not kept counts, and the tier it reaches says no day.
    mixed = [crests.UNDATED, date(2026, 10, 2), crests.UNDATED]
    assert crests.tiers_from_events(mixed, (1, 2, 3, 4)) == [
        crests.UNDATED,
        crests.UNDATED,
        date(2026, 10, 2),
        None,
    ]
    assert crests.tier_of([date(2026, 1, 1), None, date(2026, 1, 3)]) == 1  # tiers are earned in order
    best = crests.tiers_from_best({3: date(2026, 10, 3), 7: date(2026, 10, 9)}, (3, 7, 30, 100))
    assert crests.tier_of(best) == 2 and best[2] is None


def test_every_family_climbs_four_tiers_with_its_own_icon():
    assert len(crests.FAMILIES) == 20 and len(crests.RARES) == 4
    keys = [f.key for f in crests.FAMILIES] + [r.key for r in crests.RARES]
    assert len(set(keys)) == len(keys)
    for f in crests.FAMILIES:
        assert len(f.thresholds) == 4 and list(f.thresholds) == sorted(set(f.thresholds)), f.key
        assert f.group in {g for g, _, _ in crests.GROUPS}, f.key
    streak = next(f for f in crests.FAMILIES if f.key == "streak")
    assert crests.ladder(streak) == "3 · 7 · 30 · 100 days"
    cards = next(f for f in crests.FAMILIES if f.key == "cards")
    assert crests.ladder(cards) == "100 · 500 · 2,000 · 5,000"


def test_the_top_three_move_up_and_the_bottom_three_only_without_three_days():
    # A table of three moves no one.
    assert [crests.league_move(r, 3, 0) for r in (1, 2, 3)] == [0, 0, 0]
    # Of four to six, the top three move up and nobody down.
    assert [crests.league_move(r, 4, 0) for r in (1, 2, 3, 4)] == [1, 1, 1, 0]
    assert [crests.league_move(r, 6, 0) for r in range(1, 7)] == [1, 1, 1, 0, 0, 0]
    # Of ten, the bottom three move down, unless they studied on three days of the week.
    assert [crests.league_move(r, 10, 0) for r in range(1, 11)] == [1, 1, 1, 0, 0, 0, 0, -1, -1, -1]
    assert crests.league_move(10, 10, crests.LEAGUE_FLOOR_DAYS) == 0
    assert crests.league_move(10, 10, crests.LEAGUE_FLOOR_DAYS - 1) == -1
    # Bronze is the floor, Aurora the top.
    assert crests.next_league(0, -1) == 0 and crests.next_league(4, 1) == 4 and crests.next_league(1, 1) == 2


def test_a_tie_is_told_apart_by_name_the_same_way_for_everyone():
    points = {"t1": 30, "t2": 50, "t3": 30}
    names = {"t1": "Zoe", "t2": "max", "t3": "anna"}
    assert crests.ranking(points, names) == ["t2", "t3", "t1"]


def test_a_full_week_ends_on_its_sunday():
    monday = date(2026, 9, 7)
    tz = service._zone("Europe/Rome")
    week = [
        progress.Session(
            datetime.combine(monday + timedelta(days=d), datetime.min.time(), tz), 60, "review", "A", "done"
        )
        for d in range(progress.FULL_WEEK)
    ]
    assert progress.full_week_ends(week, date(2026, 9, 20)) == [date(2026, 9, 13)]
    # The week under way is not full yet, and one session not done keeps it from being.
    assert progress.full_week_ends(week, date(2026, 9, 9)) == []
    open_ = [*week[:-1], progress.Session(week[-1].start, 60, "review", "A", None)]
    assert progress.full_week_ends(open_, date(2026, 9, 20)) == []


# --------------------------------------------------------------------------- #
# Points, the level and the week's league
# --------------------------------------------------------------------------- #


def test_only_people_who_follow_each_other_are_friends(tmp_path):
    a, b, c = (_plan(tmp_path) for _ in range(3))
    for sub, handle in ((a, "ada"), (b, "bea"), (c, "cyd")):
        _join(tmp_path, sub, handle)
    _friends(tmp_path, a, b, "ada", "bea")
    service.follow(tmp_path, a.token, "cyd")
    service.answer_follow(tmp_path, c.token, "ada", accept=True)  # one way only
    store = Store(tmp_path)
    assert social.mutual_follows(store, a.token) == [b.token]
    assert social.mutual_follows(store, c.token) == []


def test_diary_sessions_earn_points_three_a_day_at_most(tmp_path):
    sub = _plan(tmp_path)
    day = date(2026, 9, 28)
    sub = _log(tmp_path, sub, day, n=5)
    points, days = service._week_points(tmp_path, sub, day)
    assert (points, days) == (crests.LOGGED_A_DAY * service.POINTS_DONE, 1)
    assert service._week_points(tmp_path, sub, day - timedelta(days=7)) == (0, 0)
    # They make the level too.
    assert service.crest_of(tmp_path, sub)["points"] == crests.LOGGED_A_DAY * service.POINTS_DONE


def test_a_first_look_starts_in_bronze_and_a_week_won_moves_up(tmp_path, league):
    ada, bea, cyd, dan = league
    ada = service.settle_league(tmp_path, ada, NOW)
    service.save_subscription(tmp_path, ada)  # as the page does, through update_subscription
    assert ada.options["league"] == {"tier": 0, "week": "2026-09-28", "results": []}
    assert not service.league_due(ada, NOW)
    # The week: ada studies on three days, bea on two, cyd on one, dan not at all.
    monday = date(2026, 9, 28)
    ada = _log(tmp_path, ada, monday, n=2)
    ada = _log(tmp_path, ada, monday + timedelta(days=1))
    ada = _log(tmp_path, ada, monday + timedelta(days=2))
    bea = _log(tmp_path, bea, monday, n=1)
    bea = _log(tmp_path, bea, monday + timedelta(days=3))
    cyd = _log(tmp_path, cyd, monday)
    table = service.league_view(tmp_path, ada, NOW + timedelta(days=2))
    assert table["on"] and table["enough"] and table["league"] == "bronze"
    assert [(r["handle"], r["points"], r["zone"]) for r in table["rows"]] == [
        ("ada", 40, "up"),
        ("bea", 20, "up"),
        ("cyd", 10, "up"),
        ("dan", 0, "stay"),
    ]
    assert [r["me"] for r in table["rows"]] == [True, False, False, False]
    # The next Monday settles it: ada moves up, and Today says so for three days.
    next_week = NOW + timedelta(days=6)
    assert service.league_due(ada, next_week)
    ada = service.settle_league(tmp_path, ada, next_week)
    result = ada.options["league"]["results"][-1]
    assert result == {"week": "2026-09-28", "rank": 1, "size": 4, "points": 40, "from": 0, "to": 1}
    assert ada.options["league"]["tier"] == 1 and service.crest_of(tmp_path, ada)["league"] == "silver"
    said = service.league_result(ada, next_week)
    assert said is not None and said["up"] and said["rank"] == 1 and said["league"] == "silver"
    assert service.league_result(ada, next_week + timedelta(days=3)) is None  # Thursday: not any more
    # dan finished last of four: nobody moves down below seven.
    dan = service.settle_league(tmp_path, service.settle_league(tmp_path, dan, NOW), next_week)
    assert dan.options["league"]["tier"] == 0 and dan.options["league"]["results"][-1]["rank"] == 4


def test_a_table_too_small_moves_no_one_and_says_nothing(tmp_path):
    a, b = _plan(tmp_path), _plan(tmp_path)
    _join(tmp_path, a, "ada")
    _join(tmp_path, b, "bea")
    _friends(tmp_path, a, b, "ada", "bea")
    a = service.settle_league(tmp_path, service.load_subscription(tmp_path, a.token), NOW)
    service.save_subscription(tmp_path, a)
    a = _log(tmp_path, a, date(2026, 9, 28))
    view = service.league_view(tmp_path, a, NOW)
    assert not view["enough"] and {r["zone"] for r in view["rows"]} == {"stay"}
    a = service.settle_league(tmp_path, a, NOW + timedelta(days=6))
    assert a.options["league"]["tier"] == 0 and service.league_result(a, NOW + timedelta(days=6)) is None


def test_a_student_who_leaves_the_league_is_in_no_one_s_table(tmp_path, league):
    ada, bea, *_ = league
    bea = service.set_league(bea, False)
    service.save_subscription(tmp_path, bea)
    assert service.league_view(tmp_path, bea, NOW) == {"on": False}
    assert not service.league_due(bea, NOW) and service.league_result(bea, NOW) is None
    handles = [r["handle"] for r in service.league_view(tmp_path, ada, NOW)["rows"]]
    assert handles == ["ada", "cyd", "dan"]
    assert service.league_on(service.set_league(bea, True))


def test_the_crests_count_what_the_app_already_counts(tmp_path):
    sub = _plan(tmp_path)
    sub = service.record_cheer(sub, NOW)
    assert sub.options["cheers_sent"] == ["2026-09-29"]
    # A session at a place on the map, for the Explorer.
    running = service.start_focus(sub, "Algebra 3", now=NOW)
    running = dataclasses.replace(
        running, options={**running.options, "focus": {**running.options["focus"], "place": "p1"}}
    )
    finished = service.finish_focus(running, now=NOW + timedelta(minutes=40))
    assert finished.options["place_sessions"] == ["2026-09-29"]
    view = service.crests_view(tmp_path, finished, NOW + timedelta(hours=1))
    by_key = {b["key"]: b for g in view["groups"] for b in g["badges"]}
    assert by_key["explorer"]["tier"] == 1 and by_key["explorer"]["metal"] == "bronze"
    assert by_key["cheers"]["tier"] == 0 and by_key["cheers"]["status"] == "1 of 5"
    # The snapshot a friend's level is read from is kept only when it changed.
    kept = service.keep_crest(finished, view)
    assert service.crest_kept(kept, view) and not service.crest_kept(finished, view)
    assert service.crest_of(tmp_path, kept)["points"] == view["bonus"] + view["session_points"]


def test_a_new_semester_is_counted_for_the_seasons(tmp_path):
    sub = _plan(tmp_path)
    ics = (EXAMPLES / "sample-semester.ics").read_bytes()
    later = NOW + timedelta(days=150)
    second = service.start_next_semester(sub, ics=ics, now=later)
    assert second.options["semesters"] == [later.astimezone(service._zone("Europe/Rome")).date().isoformat()]
    view = service.crests_view(tmp_path, second, later)
    seasons = next(b for g in view["groups"] for b in g["badges"] if b["key"] == "seasons")
    assert seasons["tier"] == 1 and seasons["status"] == "2 of 3"


# --------------------------------------------------------------------------- #
# The pages
# --------------------------------------------------------------------------- #


@pytest.fixture
def web(tmp_path, league):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    clock = {"now": NOW}
    config = Config(
        store=tmp_path, clock=lambda: clock["now"], background=False, sweep_every=None, remind_every=None
    )
    with TestClient(create_app(config)) as client:
        client.clock = clock
        yield client


def test_community_shows_the_league_with_each_friend_s_avatar(web, league):
    ada = league[0]
    page = web.get(f"/p/{ada.token}/community").text
    assert 'id="league"' in page and "Bronze league" in page
    assert page.count('class="league-row') == 4 + 1  # four rows, and the list's own class
    assert f'href="/p/{ada.token}/u/bea"' in page and ">You<" in page
    assert "Moving up to Silver league" in page
    # Leaving takes the student out at once; the profile's switch brings them back.
    left = web.post(f"/p/{ada.token}/league", data={"on": "0"}, follow_redirects=False)
    assert left.status_code == 303
    assert 'id="league"' not in web.get(f"/p/{ada.token}/community").text
    profile = web.get(f"/p/{ada.token}/profile").text
    assert 'name="league" value="1">' in profile  # unticked
    form = {"handle": "ada", "university": "University of Bologna", "programme": "Physics", "old_enough": "1"}
    web.post(f"/p/{ada.token}/profile", data={**form, "league": "1"}, follow_redirects=False)
    assert 'id="league"' in web.get(f"/p/{ada.token}/community").text


def test_today_says_last_week_s_league_on_monday(tmp_path, web, league):
    ada = league[0]
    web.get(f"/p/{ada.token}/community")  # the first look, this week
    for k in range(7):  # a session in the diary every day of the week, Monday to Sunday
        _log(tmp_path, service.load_subscription(tmp_path, ada.token), date(2026, 9, 28) + timedelta(days=k))
    web.clock["now"] = NOW + timedelta(days=6)  # the Monday after, 8:00 in Rome
    page = unescape(web.get(f"/p/{ada.token}").text)
    assert re.findall(r'class="tip-reason">([^<]+)<', page)[0] == "Last week's league"
    assert "1 of 4: up to the Silver league!" in page and f"/p/{ada.token}/community#league" in page
    assert "70 points last week" in page
    # A make-up session still comes first: it lasts two days, the league's word three.
    assert service.today_message(repair={"day": "x"}, league={"rank": 1}) == "repair"
    assert service.today_message(league={"rank": 1}, insight={"title": "x"}, tip=True) == "league"
