"""
Focus, the owner's picks (DECISIONS.md D178): the aim and its steps (1A, 1B), the rewards as
a stem (2C), a 5-minute pause by itself after every 25 minutes of work (3A), and the
classmates studying too as one line, each with a cheer (4C).

What must hold: with the rhythm, the clock stops during each pause and the minutes saved
are the work's; a pause is never time away; going back early counts the pause as it lasted;
a page without its script shows the pause when it is due; without the rhythm nothing
changes. The aim is the diary's "What did you do?", its steps are added and ticked (five at
most), and the diary asks whether it was finished, its answer already given by the steps.
The stem's buds are the rewards, open once reached. A cheer reaches only a classmate who
is seen studying now, once a pair in ten minutes, and the one cheered sees who.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)  # 18:00 in Paris


def _plan(root: Path, handle: str | None = None) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    sub = service.finish_first_run(sub)
    service.save_subscription(root, sub)
    if handle:
        form = {"handle": handle, "university": "Université Lyon 1", "old_enough": "1"}
        service.save_profile(root, sub.token, form)
    return service.load_subscription(root, sub.token)


def _beats(sub: service.Subscription, minutes: range) -> service.Subscription:
    for m in minutes:
        sub = service.focus_beat(sub, now=NOW + timedelta(minutes=m))
    return sub


def test_the_rhythm_pauses_by_itself_and_the_minutes_saved_are_the_works(tmp_path):
    sub = service.start_focus(_plan(tmp_path), "Algebra 3", goal=60, rhythm=True, now=NOW)
    working = _beats(sub, range(1, 27))
    view = service.focus_view(working, NOW + timedelta(minutes=26))
    assert view["pausing"] and view["pause_left"] == 240 and view["elapsed"] == 25 * 60
    assert view["round"] == 1 and view["rounds"] == 3
    # The phone put down for the pause: no heartbeat from 26 to 30, and no time away.
    back = service.focus_beat(working, now=NOW + timedelta(minutes=30))
    focus = back.options["focus"]
    assert focus["away"] == 0 and focus["interruptions"] == 0 and focus["rest"] == 300
    after = service.focus_view(back, NOW + timedelta(minutes=31))
    assert not after["pausing"] and after["round"] == 2 and after["elapsed"] == 26 * 60
    # Back a minute after the pause ended: that minute, less a heartbeat's, is away.
    late = service.focus_beat(working, now=NOW + timedelta(minutes=31)).options["focus"]
    assert late["away"] == 30 and late["interruptions"] == 1
    done = service.finish_focus(_beats(back, range(31, 41)), now=NOW + timedelta(minutes=40))
    assert done.options["focus_draft"]["minutes"] == 35 and done.options["focus_draft"]["rounds"] == 1
    # Back to work two minutes into the pause: the pause counts two minutes.
    early = service.resume_focus(_beats(sub, range(1, 28)), now=NOW + timedelta(minutes=27))
    assert early.options["focus"]["rest"] == 120 and not early.options["focus"]["pause_from"]
    finished = service.finish_focus(_beats(early, range(28, 41)), now=NOW + timedelta(minutes=40))
    assert finished.options["focus_draft"]["minutes"] == 38
    # Time away at work still counts, as before (D17): ten minutes off the page.
    gone = service.focus_beat(_beats(sub, range(1, 6)), now=NOW + timedelta(minutes=15))
    assert gone.options["focus"]["away"] == 570 and gone.options["focus"]["interruptions"] == 1


def test_without_a_script_or_without_the_rhythm(tmp_path):
    sub = service.start_focus(_plan(tmp_path), "Algebra 3", goal=45, rhythm=True, now=NOW)
    # No heartbeat at all (a page without its script): the pause is shown when it is due.
    assert service.focus_view(sub, NOW + timedelta(minutes=27))["pausing"]
    assert not service.focus_view(sub, NOW + timedelta(minutes=31))["pausing"]
    assert service.finish_focus(sub, now=NOW + timedelta(minutes=40)).options["focus_draft"]["minutes"] == 35
    # Without the rhythm, nothing pauses: forty minutes are forty.
    plain = service.start_focus(_plan(tmp_path), "Algebra 3", goal=45, now=NOW)
    assert not service.focus_view(plain, NOW + timedelta(minutes=27))["pausing"]
    assert (
        service.finish_focus(plain, now=NOW + timedelta(minutes=40)).options["focus_draft"]["minutes"] == 40
    )


def test_the_aim_its_steps_and_whether_it_was_finished_reach_the_diary(tmp_path):
    sub = service.start_focus(_plan(tmp_path), "Algebra 3", goal=25, aim="  the   exercises 3–5 ", now=NOW)
    assert sub.options["focus"]["aim"] == "the exercises 3–5"
    for step in ("Exercise 3", "Exercise 4", "Exercise 5"):
        sub = service.add_focus_step(sub, step)
    with pytest.raises(service.InvalidInput):
        service.add_focus_step(sub, "   ")
    sub = service.tick_focus_step(service.tick_focus_step(sub, 0, True), 1, True)
    with pytest.raises(service.InvalidInput):
        service.tick_focus_step(sub, 7, True)
    full = sub
    for extra in ("a", "b"):
        full = service.add_focus_step(full, extra)
    with pytest.raises(service.InvalidInput, match="at most 5"):
        service.add_focus_step(full, "c")
    view = service.focus_view(sub, NOW + timedelta(minutes=10))
    assert view["aim"] == "the exercises 3–5" and view["steps_done"] == 2 and len(view["steps"]) == 3
    draft = service.finish_focus(sub, now=NOW + timedelta(minutes=26)).options["focus_draft"]
    assert draft["title"] == "the exercises 3–5" and draft["finished"] == "partly"
    assert service._finished_guess([{"done": True}]) == "yes" and service._finished_guess([]) == ""


def test_the_stem_opens_a_bud_at_each_reward(tmp_path):
    sub = service.start_focus(_plan(tmp_path), "Algebra 3", goal=25, now=NOW)
    stem = service.focus_view(sub, NOW + timedelta(minutes=27))["stem"]
    assert [(b["label"], b["open"], b["goal"]) for b in stem["buds"]] == [
        ("25 min", True, True),
        ("30 min", False, False),
        ("1 h", False, False),
    ]
    assert stem["next"] == 3 and stem["percent"] == 45 and stem["horizon"] == 3600
    long = service.focus_view(service.start_focus(sub, "Algebra 3", goal=90, now=NOW), NOW)["stem"]
    assert [b["label"] for b in long["buds"]] == ["30 min", "1 h", "1 h 30"] and long["next"] == 30


def test_a_cheer_reaches_a_classmate_studying_now_once_in_ten_minutes(tmp_path):
    ada, ben, cam = _plan(tmp_path, "ada"), _plan(tmp_path, "ben"), _plan(tmp_path, "cam")
    service.follow(tmp_path, ben.token, "ada")
    service.answer_follow(tmp_path, ada.token, "ben", accept=True)
    with pytest.raises(service.InvalidInput, match="not studying now"):
        service.cheer_classmate(tmp_path, ben, "ada", NOW)  # not studying yet
    service.set_studying_now(tmp_path, ada.token, True)
    ada = service.update_subscription(
        tmp_path, ada.token, lambda s: service.start_focus(s, "Algebra 3", goal=45, now=NOW)
    )
    service.sync_presence(tmp_path, ada, NOW)
    assert service.cheer_classmate(tmp_path, ben, "@ada", NOW + timedelta(minutes=5))
    assert not service.cheer_classmate(tmp_path, ben, "ada", NOW + timedelta(minutes=9))  # too soon
    with pytest.raises(service.InvalidInput):
        service.cheer_classmate(tmp_path, cam, "ada", NOW)  # cam does not follow her
    assert [p["handle"] for p in service.cheers_view(tmp_path, ada)] == ["ben"]

    clock = [NOW + timedelta(minutes=12)]
    config = Config(store=tmp_path, clock=lambda: clock[0], background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        page = unescape(web.get(f"/p/{ada.token}/focus").text)
        assert "@ben is cheering you on." in page and 'data-cheers="ben"' in page
        assert web.post(f"/p/{ada.token}/focus/beat").json()["cheers"] == ["ben"]
        sent = web.post(f"/p/{ben.token}/cheer", data={"handle": "ada"}, follow_redirects=False)
        assert sent.status_code == 303 and sent.headers["location"] == f"/p/{ben.token}/focus?cheered=ada"
        refused = web.post(f"/p/{cam.token}/cheer", data={"handle": "ada"})
        assert refused.status_code == 400


def test_the_pages_start_with_an_aim_and_the_rhythm_and_show_the_pause(tmp_path):
    sub = _plan(tmp_path)
    clock = [NOW]
    config = Config(store=tmp_path, clock=lambda: clock[0], background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        start = unescape(web.get(f"/p/{sub.token}/focus").text)
        assert 'name="aim"' in start and 'name="rhythm" value="1" checked' in start
        # The plan's session is a first review: aims for one, to tap.
        assert 'data-fills="focus-aim-in" hidden' in start and 'data-fill="10 flashcards"' in start
        form = {"course": "Algebra 3", "goal": "60", "aim": "chapter 4", "rhythm": "1"}
        web.post(f"/p/{sub.token}/focus/start", data=form)
        web.post(f"/p/{sub.token}/focus/step", data={"step": "Read 4.1"})
        web.post(f"/p/{sub.token}/focus/step/0", data={"done": "1"})
        clock[0] = NOW + timedelta(minutes=10)
        running = unescape(web.get(f"/p/{sub.token}/focus").text)
        assert "chapter 4" in running and "1 of 1" in running and 'class="aim-step is-done"' in running
        assert 'class="focus"' in running and 'id="focus-pause" hidden' in running
        assert (
            'class="stem-bud is-open' not in running
            and "15 more minutes and the next flower opens." not in running
        )
        assert "20 more minutes and the next flower opens." in running  # the first reward at 30 min
        clock[0] = NOW + timedelta(minutes=27)
        pausing = unescape(web.get(f"/p/{sub.token}/focus").text)
        assert 'class="focus is-pausing"' in pausing and 'id="focus-pause">' in pausing
        assert "Round 1 of 3" in pausing and "3:00" in pausing
        resumed = web.post(f"/p/{sub.token}/focus/resume", follow_redirects=False)
        assert resumed.status_code == 303
        clock[0] = NOW + timedelta(minutes=40)
        web.post(f"/p/{sub.token}/focus/finish")
        log = unescape(web.get(f"/p/{sub.token}/log").text)
        assert "Did you finish chapter 4?" in log and 'value="chapter 4"' in log
        assert 'name="finished" value="yes" checked' in log and "1 of 1 step ticked" in log
        saved = web.post(
            f"/p/{sub.token}/log",
            data={
                "course": "Algebra 3",
                "title": "chapter 4",
                "effort": "5",
                "progress": "4",
                "finished": "partly",
                "minutes": "38",
                "visibility": "me",
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303
    post = service.social.posts_of(service._store(tmp_path), sub.token, "session")[0]
    assert post.data["aim"] == "chapter 4" and post.data["finished"] == "partly"
    assert post.data["steps"] == [{"text": "Read 4.1", "done": True}]


def test_the_weeks_garden_has_a_plant_a_day(tmp_path):
    """The owner's 2B: from Monday a plant a day, the day's best session kept; today's also
    the running session's, a sprout until its length is reached."""
    sub = _plan(tmp_path)  # NOW is a Tuesday

    def log(day: str, minutes: int, goal: int, *, timed: bool = True) -> None:
        data = {
            "course": "Algebra 3",
            "effort": 5,
            "progress": 3,
            "minutes": minutes,
            "goal": goal,
            "timed": timed,
        }
        service.social.create_post(
            service._store(tmp_path), sub.token, "session", day=day, visibility="me", data=data, now=NOW
        )

    log("2026-09-28", 50, 45)  # Monday: its length reached
    log("2026-09-28", 10, 45)  # a short one the same day does not undo it
    log("2026-09-27", 60, 45)  # last week
    log("2026-09-29", 10, 45)  # today: stopped before half
    log("2026-09-29", 90, 45, timed=False)  # typed by hand: not the timer's
    garden = service.focus_garden(tmp_path, sub, NOW)
    assert [d["state"] for d in garden["days"]] == ["bloom", "wilt"] + ["seed"] * 5
    assert garden["flowers"] == 1 and garden["days"][0]["colour"] == "c0"
    assert garden["days"][1]["today"] and garden["days"][1]["label"] == "today" and garden["days"][6]["ahead"]
    running = service.start_focus(sub, "Algebra 3", goal=25, now=NOW)
    growing = service.focus_garden(
        tmp_path, sub, NOW, service.focus_view(running, NOW + timedelta(minutes=10))
    )
    assert growing["days"][1]["state"] == "sprout"
    reached = service.focus_garden(
        tmp_path, sub, NOW, service.focus_view(running, NOW + timedelta(minutes=30))
    )
    assert reached["days"][1]["state"] == "bloom" and reached["flowers"] == 2

    service.save_subscription(tmp_path, running)
    clock = [NOW + timedelta(minutes=5)]
    config = Config(store=tmp_path, clock=lambda: clock[0], background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        page = unescape(web.get(f"/p/{sub.token}/focus").text)
    assert 'class="card focus-garden"' in page and "c0 is-bloom" in page and "Monday: in flower" in page
