"""
Step 5 of the readiness review: the university from a list and the classmates it
finds (DECISIONS.md D39), study groups joined by an invitation link (D40), and the
badge shelf (D41).
"""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from cps import crests, service, social, universities
from cps.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


def _plan(where, tz: str = "Europe/Rome") -> service.Subscription:
    sub = service.start_subscription(tz=tz, ics=(EXAMPLES / "sample-semester.ics").read_bytes(), now=NOW)
    service.save_subscription(where, sub)
    return sub


def _join(where, sub, handle, university="University of Bologna", programme="Physics", year="2"):
    form = {
        "handle": handle,
        "university": university,
        "programme": programme,
        "year": year,
        "old_enough": "1",
    }
    return service.save_profile(where, sub.token, form, tz=sub.options["tz"])


# --------------------------------------------------------------------------- #
# The list of universities (D39)
# --------------------------------------------------------------------------- #


def test_a_university_is_found_by_its_name_its_city_or_its_domain():
    assert universities.search("unibo", "IT")[0].name == "University of Bologna"
    assert universities.search("polimi", "IT")[0].name == "Polytechnic Institute of Milan"
    milan = [u.name for u in universities.search("milan", "IT")]
    assert "University of Milan" in milan and all(
        u.country == "IT" for u in universities.search("milan", "IT")[:3]
    )
    assert universities.search("Universite Lyon", "FR")[0].country == "FR"  # accents ignored
    assert universities.search("x") == [] and universities.search("") == []


def test_the_students_country_comes_from_the_plans_time_zone():
    assert universities.country_of_zone("Europe/Rome") == "IT"
    assert universities.country_of_zone("America/Sao_Paulo") == "BR"
    assert universities.country_of_zone("UTC") is None
    assert universities.search("sorbonne", "FR")[0].country == "FR"
    assert service.university_suggestions("bologna", "Europe/Rome")[0] == {
        "name": "University of Bologna",
        "flag": "\U0001f1ee\U0001f1f9",
        "domain": "unibo.it",
    }


def test_a_profile_keeps_the_lists_name_its_country_and_a_year(tmp_path):
    sub = _plan(tmp_path)
    profile = _join(tmp_path, sub, "ada", university="  university of BOLOGNA ")
    assert profile["university"] == "University of Bologna" and profile["flag"] == "\U0001f1ee\U0001f1f9"
    assert profile["year"] == "2" and service.year_words("2") == "2nd year"
    typed = _join(tmp_path, sub, "ada", university="Scuola Normale di Esempio", year="")
    assert typed["university"] == "Scuola Normale di Esempio" and typed["flag"] == ""  # not in the list: kept
    with pytest.raises(service.InvalidInput, match="year"):
        _join(tmp_path, sub, "ada", year="7")


def test_classmates_are_those_at_the_same_university(tmp_path):
    ada, bea, cyd, dan = (_plan(tmp_path) for _ in range(4))
    _join(tmp_path, ada, "ada", programme="Physics", year="2")
    _join(tmp_path, bea, "bea", university="university of bologna", programme="Law", year="1")
    _join(tmp_path, cyd, "cyd", programme="physics", year="3")
    _join(tmp_path, dan, "dan", university="University of Milan")
    view = service.people_view(tmp_path, ada)
    assert view["university"] == "University of Bologna"
    assert [p["handle"] for p in view["classmates"]] == ["cyd", "bea"]  # same programme first
    service.block(tmp_path, ada.token, "cyd") if hasattr(service, "block") else social.block(
        Store(tmp_path), ada.token, cyd.token
    )
    assert [p["handle"] for p in service.people_view(tmp_path, ada)["classmates"]] == ["bea"]
    assert service.people_view(tmp_path, dan)["classmates"] == []


def test_an_older_store_gains_the_new_columns(tmp_path):
    path = tmp_path / "cps.sqlite"
    with contextlib.closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE profiles (token TEXT PRIMARY KEY, handle TEXT NOT NULL UNIQUE COLLATE NOCASE, "
            "university TEXT NOT NULL DEFAULT '', programme TEXT NOT NULL DEFAULT '', "
            "bio TEXT NOT NULL DEFAULT '', created TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO profiles VALUES ('t' || hex(randomblob(12)), 'old', 'Uni', '', '', '2026-01-01')"
        )
        db.execute(
            "CREATE TABLE study_groups (id TEXT PRIMARY KEY, name TEXT NOT NULL, goal INTEGER NOT NULL, "
            "owner TEXT, created TEXT NOT NULL)"
        )
    store = Store(path)
    with store.connection() as db:
        columns = {r[1] for r in db.execute("PRAGMA table_info(profiles)")}
        assert {"year", "country"} <= columns
        assert "invite" in {r[1] for r in db.execute("PRAGMA table_info(study_groups)")}
        assert db.execute("SELECT handle, year, country FROM profiles").fetchone() == ("old", "", "")


# --------------------------------------------------------------------------- #
# Invitation links (D40)
# --------------------------------------------------------------------------- #


def _group(where, owner_sub) -> str:
    return service.create_group(
        where, owner_sub.token, {"name": "Thursday library", "goal": "12"}, now=NOW
    ).id


def test_a_link_lets_a_classmate_join_and_a_new_link_closes_the_old(tmp_path):
    ada, bea, cyd = (_plan(tmp_path) for _ in range(3))
    _join(tmp_path, ada, "ada")
    _join(tmp_path, bea, "bea")
    group = _group(tmp_path, ada)
    code = service.group_invite_code(tmp_path, ada.token, group)
    assert service.group_invite_code(tmp_path, ada.token, group) == code  # the same until renewed
    assert service.invitation_view(tmp_path, code) == {
        "code": code,
        "name": "Thursday library",
        "size": 1,
        "goal": 12.0,
        "id": group,
    }
    assert service.join_group_by_code(tmp_path, bea.token, code, now=NOW) == group
    assert service.join_group_by_code(tmp_path, bea.token, code, now=NOW) == group  # twice: no harm
    assert service.invitation_view(tmp_path, code)["size"] == 2
    with pytest.raises(service.InvalidInput, match="handle"):
        service.join_group_by_code(tmp_path, cyd.token, code, now=NOW)  # no profile yet
    with pytest.raises(service.InvalidInput, match="owner"):
        service.group_invite_code(tmp_path, bea.token, group, renew=True)
    fresh = service.group_invite_code(tmp_path, ada.token, group, renew=True)
    assert fresh != code and service.invitation_view(tmp_path, code) is None
    _join(tmp_path, cyd, "cyd")
    with pytest.raises(service.InvalidInput, match="does not work"):
        service.join_group_by_code(tmp_path, cyd.token, code, now=NOW)
    assert service.join_group_by_code(tmp_path, cyd.token, fresh, now=NOW) == group


def test_a_link_does_not_let_in_someone_blocked_nor_past_the_size(tmp_path):
    subs = [_plan(tmp_path) for _ in range(10)]
    for i, sub in enumerate(subs):
        _join(tmp_path, sub, f"user{i}")
    group = _group(tmp_path, subs[0])
    code = service.group_invite_code(tmp_path, subs[0].token, group)
    social.block(Store(tmp_path), subs[0].token, subs[1].token)
    with pytest.raises(service.InvalidInput, match="does not work"):
        service.join_group_by_code(tmp_path, subs[1].token, code, now=NOW)
    for sub in subs[2 : 2 + social.GROUP_SIZE - 1]:
        service.join_group_by_code(tmp_path, sub.token, code, now=NOW)
    with pytest.raises(service.InvalidInput, match="at most"):
        service.join_group_by_code(tmp_path, subs[-1].token, code, now=NOW)


def test_a_pasted_link_or_code_is_read():
    code = "AbCdEf123_-x"
    assert service.invite_code_from(f"https://study-plan-demo.onrender.com/g/{code}") == code
    assert service.invite_code_from(f"  {code} ") == code
    assert service.invite_code_from(f"https://x.example/g/{code}?utm=1") == code
    assert service.invite_code_from("hello") == "" and service.invite_code_from("") == ""


# --------------------------------------------------------------------------- #
# The crests (D189), after the badge shelf (D41)
# --------------------------------------------------------------------------- #


def _crest(view: dict, key: str) -> dict:
    return next(b for g in view["groups"] for b in g["badges"] if b["key"] == key)


def test_crests_carry_the_day_they_were_earned_and_never_count_hours(tmp_path):
    """D189 took over D41's shelf: twenty families of four tiers and four rare ones,
    each tier with its day; the level is every session point and the crests' own."""
    sub = _plan(tmp_path, tz="Europe/Paris")
    view = service.crests_view(tmp_path, sub, NOW)
    assert view["earned"] == 0 and view["total"] == 4 * len(crests.FAMILIES) + len(crests.RARES) == 84
    assert view["level"]["n"] == 1 and view["level"]["name"] == "Spark" and view["bonus"] == 0
    # D41 kept hours off the shelf; so does D189: only Deep focus reads minutes, and
    # those of a single session. No badge adds hours up.
    words = [
        b["name"] + " " + b["how"] for g in view["groups"] for b in g["badges"] if b["key"] != "deep_focus"
    ]
    assert all("hour" not in w.lower() for w in words)
    assert "session" in _crest(view, "deep_focus")["how"]
    plan = service.PlanReport.from_dict(sub.plan)
    week = [s for s in plan.sessions if datetime.fromisoformat(s.start) < NOW + timedelta(days=7)]
    later = NOW + timedelta(days=8)
    for s in week:  # reported afterwards, as from the calendar's links
        sub = service.report_session(sub, service.session_id(s), "done", now=later)
    view = service.crests_view(tmp_path, sub, later)
    first = datetime.fromisoformat(week[0].start).astimezone(service._zone("Europe/Paris")).date()
    rare = {r["key"]: r for r in view["rare"]}
    assert rare["first"]["earned"] == service.format_date(first, "day") and rare["first"]["got"]
    assert rare["first"]["celebrate"] == "milestone-first" and not rare["champion"]["got"]
    assert _crest(view, "seasons")["tier"] == 0 and _crest(view, "teacher")["tier"] == 0
    # Every tier and rare one adds to the level; the sessions add their D158 points.
    tiers = sum(b["tier"] for g in view["groups"] for b in g["badges"])
    got = sum(r["got"] for r in view["rare"])
    assert view["bonus"] == tiers * crests.TIER_POINTS + got * crests.RARE_POINTS
    assert view["session_points"] == service.POINTS_DONE * len(week)
    assert view["level"]["points"] == view["session_points"] + view["bonus"]
    streak = _crest(view, "streak")
    assert streak["ladder"] == "3 · 7 · 30 · 100 days" and streak["celebrate"] in (
        "",
        "milestone-streak_3",
        "milestone-streak_7",
    )


def test_the_streak_badges_follow_the_forgiving_streak():
    from cps import progress

    days = [
        progress.Day(date(2026, 10, 1) + timedelta(days=i), state, 1, state == "studied")
        for i, state in enumerate(
            ["studied", "studied", "rest", "studied", "forgiven", "studied", "missed", "studied"]
        )
    ]
    run = progress.Streak(current=1, best=4, today_planned=0, today_done=False, goal=3, days=tuple(days))
    assert progress.first_runs(run, (3, 4, 7)) == {3: date(2026, 10, 4), 4: date(2026, 10, 6)}


# --------------------------------------------------------------------------- #
# The pages
# --------------------------------------------------------------------------- #


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        client.store_path = tmp_path
        yield client


def _start(web) -> str:
    files = {"file": ("t.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
    answer = web.post("/start", data={"tz": "Europe/Rome"}, files=files, follow_redirects=False)
    return answer.headers["location"].split("/")[2]


def test_the_profile_suggests_universities_and_asks_the_year(web):
    token = _start(web)
    page = web.get(f"/p/{token}/profile").text
    assert 'list="universities"' in page and f'data-suggest="/p/{token}/universities"' in page
    assert 'name="year"' in page and "2nd year" in page
    found = web.get(f"/p/{token}/universities", params={"q": "unibo"}).json()
    assert found[0]["name"] == "University of Bologna"
    assert web.get("/p/" + "x" * 32 + "/universities", params={"q": "unibo"}).status_code == 404


def test_an_invitation_link_opens_joins_and_needs_a_handle_first(web):
    owner, friend = _start(web), _start(web)
    web.post(f"/p/{owner}/profile", data={"handle": "ada", "university": "unibo", "old_enough": "1"})
    group = service.create_group(
        web.store_path, owner, {"name": "Thursday library", "goal": "10"}, now=NOW
    ).id
    page = web.get(f"/p/{owner}/groups/{group}").text
    assert "Make an invitation link" in page
    web.post(f"/p/{owner}/groups/{group}/link")
    page = web.get(f"/p/{owner}/groups/{group}").text
    code = service.group_invite_code(web.store_path, owner, group)
    assert f"http://testserver/g/{code}" in page and "https://wa.me/?text=" in page
    public = web.get(f"/g/{code}").text
    assert (
        "Thursday library" in public
        and "data-join-mine" in public
        and owner not in public
        and "ada" not in public
    )
    assert web.get("/g/AAAAAAAAAAAA").status_code == 404
    asked = web.get(f"/p/{friend}/join/{code}").text
    assert "Choose a handle" in asked and f"next=/p/{friend}/join/{code}" in asked
    saved = web.post(
        f"/p/{friend}/profile",
        data={"handle": "bea", "university": "unibo", "old_enough": "1", "next": f"/p/{friend}/join/{code}"},
        follow_redirects=False,
    )
    assert saved.headers["location"] == f"/p/{friend}/join/{code}"
    evil = web.post(
        f"/p/{friend}/profile",
        data={"handle": "bea", "university": "unibo", "old_enough": "1", "next": "https://evil.example/"},
        follow_redirects=False,
    )
    assert "evil" not in evil.headers["location"]
    joined = web.post(f"/p/{friend}/join/{code}", follow_redirects=False)
    assert joined.headers["location"] == f"/p/{friend}/groups/{group}?saved=joined_group"
    pasted = web.post(
        f"/p/{friend}/join", data={"code": f"http://testserver/g/{code}"}, follow_redirects=False
    )
    assert pasted.headers["location"] == f"/p/{friend}/join/{code}"
    assert (
        web.post(f"/p/{friend}/join", data={"code": "nope"}, follow_redirects=False)
        .headers["location"]
        .endswith("saved=bad_invite")
    )


def test_the_progress_page_shows_the_level_and_the_crests(web):
    token = _start(web)
    first = web.get(f"/p/{token}/progress").text
    # The level's card is above "More detail", a link to the league (D189).
    assert 'class="card crest-card link-card"' in first and f"/p/{token}/community#league" in first
    page = web.get(f"/p/{token}/progress?more=1").text  # the collection is inside "More detail" (D103, D106)
    assert page.count('class="crest-tile') == len(crests.FAMILIES) + len(crests.RARES)
    assert f"each tier adds {crests.TIER_POINTS} points to your level" in page


def test_every_plan_page_lets_the_device_remember_its_plan(web):
    token = _start(web)
    assert f'data-plan="{token}"' in web.get(f"/p/{token}").text
    assert "data-plan=" not in web.get("/").text


def test_the_words_looked_up_by_key_are_translated():
    """The crests' and the years' words are passed to `_()` as variables, which the
    catalogue check (tests/test_i18n.py) cannot find in the code: checked here."""
    from cps.locales import fr, it

    words = [w for f in crests.FAMILIES for w in (f.name, f.how)]
    words += [w for r in crests.RARES for w in (r.name, r.how)]
    words += list(crests.LEVEL_NAMES)
    words += list(service.YEAR_WORDS.values())
    for catalogue in (fr.MESSAGES, it.MESSAGES):
        assert [w for w in words if w not in catalogue] == []
