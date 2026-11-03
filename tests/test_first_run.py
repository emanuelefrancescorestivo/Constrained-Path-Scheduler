"""
A new student's first run (DECISIONS.md D107), after Strava's: three welcome cards on a
phone, then the account and the timetable; who is studying, the courses, the class, who
sees the sessions; Today with Spark's one line instead of the tour; the first session's
summary; the question about reminders once a session is done. What must hold: every
step can be skipped and leads to the next; a class link opened before the plan existed
is offered at the class step; the default chosen for sessions is the diary's; a plan
made before D107, and a new semester, go as they went; and the first day's short
session is not offered again once a session is logged that day (AUDIT 90).
"""

from __future__ import annotations

import itertools
import re
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
SEMESTER = ROOT / "examples" / "sample-semester.ics"
EVENING = datetime(2026, 9, 29, 20, 0, tzinfo=UTC)  # 22:00 in Paris: nothing left that day
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, a Tuesday


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _web(tmp_path, clock, **extra):
    config = Config(
        store=tmp_path, clock=clock, background=False, sweep_every=None, remind_every=None, **extra
    )
    client = TestClient(create_app(config))
    client.root = tmp_path
    return client


def _start(web, **data) -> str:
    files = {"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")}
    answer = web.post("/start", data={"tz": "Europe/Paris", **data}, files=files, follow_redirects=False)
    assert answer.status_code == 303, answer.text
    return answer.headers["location"]


def _form(html: str) -> dict:
    """Setting up's form as a browser submits it untouched (checkboxes ticked only)."""
    form: dict = {}
    for tag in re.findall(r"<input[^>]*>", html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        if 'type="checkbox"' in tag:
            if " checked" in tag:
                form.setdefault(name.group(1), []).append(unescape(value.group(1)))
            continue
        form[name.group(1)] = unescape(value.group(1)) if value else ""
    for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        chosen = re.search(r'<option(?: value="([^"]*)")? selected>([^<]*)', body)
        form[name] = (chosen.group(2) if chosen.group(1) is None else chosen.group(1)) if chosen else ""
    return form


def _courses(web, token: str) -> str:
    page = web.get(f"/p/{token}/settings?new=1").text
    answer = web.post(f"/p/{token}/settings", data={**_form(page), "new": "1"}, follow_redirects=False)
    assert answer.status_code == 303, answer.text
    return answer.headers["location"]


ALEX = {"handle": "alex", "university": "Politecnico di Torino", "year": "2", "old_enough": "1"}


def test_a_plan_made_on_the_home_page_begins_its_first_run():
    made = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)
    assert service.first_run(made) is None  # a plan made before D107 has none
    begun = service.begin_first_run(made, "Ab3_dE-fG9hK")
    assert service.first_run(begun) == {"step": "you", "class": "Ab3_dE-fG9hK"}
    assert service.first_run(service.begin_first_run(made, "../../etc"))["class"] == ""  # not a code
    assert service.share_default(begun) == "followers"
    with pytest.raises(service.InvalidInput):
        service.set_share_default(begun, "the world")
    over = service.finish_first_run(service.first_run_at(begun, "today"))
    assert service.first_run(over) is None and "today" in service.tours_seen(over)
    assert service.first_run_at(made, "class") == made  # no first run: nothing to move


def test_the_first_run_goes_step_by_step_to_today(tmp_path):
    clock = Clock(EVENING)
    with _web(tmp_path, clock) as web:
        where = _start(web)
        token = where.split("/")[2]
        assert where == f"/p/{token}/start/you"
        you = unescape(web.get(where).text)
        assert "Who’s studying?" in you and "Setting up, step 2 of 5" in you and "formnovalidate" in you
        answer = web.post(where, data=ALEX, follow_redirects=False)
        assert answer.headers["location"] == f"/p/{token}/settings?new=1"
        assert service.profile_of(web.root, token)["handle"] == "alex"
        courses = unescape(web.get(f"/p/{token}/settings?new=1").text)
        assert (
            "What do you study?" in courses
            and "Setting up, step 3 of 5" in courses
            and "Looks right" in courses
        )
        assert _courses(web, token) == f"/p/{token}/start/class"
        mates = unescape(web.get(f"/p/{token}/start/class").text)
        assert "Study with your class" in mates and "Setting up, step 4 of 5" in mates
        answer = web.post(f"/p/{token}/start/class", follow_redirects=False)
        assert answer.headers["location"] == f"/p/{token}/start/sharing"
        sharing = unescape(web.get(f"/p/{token}/start/sharing").text)
        assert "Who sees your sessions?" in sharing and 'value="followers" checked' in sharing
        answer = web.post(
            f"/p/{token}/start/sharing", data={"visibility": "everyone"}, follow_redirects=False
        )
        assert answer.headers["location"] == f"/p/{token}"
        today = unescape(web.get(f"/p/{token}").text)
        # Spark's one line instead of the tour, and the first day's short session.
        assert "Your plan is ready. One session today is enough" in today and 'id="tour"' not in today
        assert "Start today: 25 minutes on" in today
        # The session, timed, then its summary: the diary's page, with the default chosen.
        web.post(f"/p/{token}/focus/start", data={"course": "Algebra 3", "goal": "25"})
        clock.now = EVENING + timedelta(minutes=26)
        assert (
            web.post(f"/p/{token}/focus/finish", follow_redirects=False).headers["location"]
            == f"/p/{token}/log"
        )
        log = unescape(web.get(f"/p/{token}/log").text)
        assert "First session done!" in log and "Day 1" in log and 'value="everyone" checked' in log
        saved = web.post(
            f"/p/{token}/log",
            data={
                "course": "Algebra 3",
                "minutes": "26",
                "effort": "5",
                "progress": "3",
                "visibility": "everyone",
            },
            follow_redirects=False,
        )
        assert saved.headers["location"] == f"/p/{token}/start/remind"
        remind = unescape(web.get(f"/p/{token}/start/remind").text)
        assert "Same time tomorrow?" in remind and f'data-save="/p/{token}/push?then=first-run"' in remind
        # Until it is answered, Today asks; and the first day's session is not offered again.
        today = unescape(web.get(f"/p/{token}").text)
        assert "Your first session is done" in today and "Start today: 25 minutes on" not in today
        answer = web.post(f"/p/{token}/start/remind", data={"answer": "later"}, follow_redirects=False)
        assert answer.headers["location"] == f"/p/{token}"
        after = unescape(web.get(f"/p/{token}").text)
        assert "Your first session is done" not in after and "Your plan is ready" not in after
        assert web.get(f"/p/{token}/start/class", follow_redirects=False).headers["location"] == f"/p/{token}"


def test_every_step_can_be_skipped(tmp_path):
    with _web(tmp_path, Clock(MORNING)) as web:
        token = _start(web).split("/")[2]
        answer = web.post(f"/p/{token}/start/you", data={"skip": "1"}, follow_redirects=False)
        assert answer.headers["location"] == f"/p/{token}/settings?new=1"
        assert service.profile_of(web.root, token) is None
        _courses(web, token)
        mates = unescape(web.get(f"/p/{token}/start/class").text)
        assert "choose a handle</a> first" in mates and f'href="/p/{token}/start/you"' in mates
        web.post(f"/p/{token}/start/class")
        sharing = unescape(web.get(f"/p/{token}/start/sharing").text)
        assert "Until you choose a handle, your sessions stay in your diary" in sharing
        web.post(f"/p/{token}/start/sharing", data={"visibility": "followers"})
        state = service.first_run(service.load_subscription(web.root, token))
        assert state is not None and state["step"] == "today"


def test_a_handle_chosen_from_the_class_step_comes_back_to_it(tmp_path):
    with _web(tmp_path, Clock(MORNING)) as web:
        token = _start(web).split("/")[2]
        web.post(f"/p/{token}/start/you", data={"skip": "1"})
        _courses(web, token)
        answer = web.post(f"/p/{token}/start/you", data=ALEX, follow_redirects=False)
        assert answer.headers["location"] == f"/p/{token}/start/class"


def test_a_wrong_profile_shows_the_step_again(tmp_path):
    with _web(tmp_path, Clock(MORNING)) as web:
        token = _start(web).split("/")[2]
        answer = web.post(f"/p/{token}/start/you", data={**ALEX, "handle": "x"})
        assert answer.status_code == 400 and "Who’s studying?" in unescape(answer.text)


def test_the_class_step_offers_classmates_to_follow(tmp_path):
    with _web(tmp_path, Clock(MORNING)) as web:
        mate = _start(web).split("/")[2]
        web.post(f"/p/{mate}/start/you", data={**ALEX, "handle": "giulia"})
        token = _start(web).split("/")[2]
        web.post(f"/p/{token}/start/you", data=ALEX)
        _courses(web, token)
        page = unescape(web.get(f"/p/{token}/start/class").text)
        assert "@giulia" in page and f'action="/p/{token}/u/giulia/follow"' in page
        assert f'name="next" value="/p/{token}/start/class"' in page
        answer = web.post(
            f"/p/{token}/u/giulia/follow", data={"next": f"/p/{token}/start/class"}, follow_redirects=False
        )
        assert answer.headers["location"] == f"/p/{token}/start/class"
        followed = unescape(web.get(f"/p/{token}/start/class").text)
        assert f'action="/p/{token}/u/giulia/follow"' not in followed


def test_a_class_link_opened_before_the_plan_is_offered_at_the_class_step(tmp_path):
    with _web(tmp_path, Clock(MORNING)) as web:
        maker = _start(web).split("/")[2]
        web.post(f"/p/{maker}/start/you", data=ALEX)
        lines = "\n".join(f"Week {n}: topic {n}" for n in range(1, 5))
        service.update_subscription(
            web.root, maker, lambda now: service.save_course_topics(web.root, now, 0, lines, share=False)
        )
        code = service.make_class_link(web.root, service.load_subscription(web.root, maker))
        public = unescape(web.get(f"/c/{code}").text)
        assert f'href="/?class={code}#start"' in public
        home = web.get(f"/?class={code}").text
        assert f'name="class" value="{code}"' in home
        token = _start(web, **{"class": code}).split("/")[2]
        web.post(f"/p/{token}/start/you", data={"skip": "1"})
        _courses(web, token)
        page = unescape(web.get(f"/p/{token}/start/class").text)
        assert "Your class" in page and f'href="/p/{token}/class/{code}?next=/p/{token}/start/class"' in page
        take = unescape(web.get(f"/p/{token}/class/{code}?next=/p/{token}/start/class").text)
        assert f'name="next" value="/p/{token}/start/class"' in take
        # Somewhere else than the plan's own pages is never followed.
        other = unescape(web.get(f"/p/{token}/class/{code}?next=https://example.org").text)
        assert 'name="next"' not in other


def test_the_class_code_survives_the_account(tmp_path):
    with _web(tmp_path, Clock(MORNING), accounts=True) as web:
        code = "Ab3_dE-fG9hK"
        assert f'name="class" value="{code}"' in web.get(f"/?class={code}").text
        made = web.post(
            "/account/signup",
            data={"email": "alex@example.org", "password": "a long walk in the park", "class": code},
            follow_redirects=False,
        )
        assert made.status_code == 303 and f"class={code}" in made.headers["location"]


def test_the_welcome_cards_are_the_first_visit_of_a_phone(tmp_path):
    with _web(tmp_path, Clock(MORNING)) as web:
        home = unescape(web.get("/").text)
        assert 'class="welcome"' in home and home.count('class="welcome-card"') == 3
        assert "Get started" in home and 'href="#start"' in home and "hero-welcomed" in home
        # After a failed start the pitch is short and the cards are not shown again.
        failed = unescape(web.post("/start", data={"tz": "Europe/Paris"}).text)
        assert 'class="welcome"' not in failed
    with _web(tmp_path, Clock(MORNING), demo=True) as demo:
        assert 'class="welcome"' not in demo.get("/").text  # the demo's pitch is its button
    css = (ROOT / "src/cps/web/static/style.css").read_text()
    assert ".welcome { display: none; }" in css and "scroll-snap-type: x mandatory" in css


@pytest.mark.parametrize("step", ["you", "class", "sharing", "remind"])
def test_each_step_has_one_main_heading_and_no_id_twice(tmp_path, step):
    with _web(tmp_path, Clock(MORNING)) as web:
        token = _start(web).split("/")[2]
        html = web.get(f"/p/{token}/start/{step}").text
    assert html.count("<h1") == 1, step
    levels = [int(n) for n in re.findall(r"<h([1-6])\b", html)]
    assert all(b <= a + 1 for a, b in itertools.pairwise(levels)), (step, levels)
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), step


def test_a_plan_made_before_d107_and_a_new_semester_go_as_they_went(tmp_path):
    with _web(tmp_path, Clock(MORNING)) as web:
        token = _start(web).split("/")[2]

        def old(current):
            return service.Subscription(
                **{
                    **current.__dict__,
                    "options": {k: v for k, v in current.options.items() if k != "first_run"},
                }
            )

        service.update_subscription(web.root, token, old)
        assert web.get(f"/p/{token}/start/you", follow_redirects=False).headers["location"] == f"/p/{token}"
        assert _courses(web, token) == f"/p/{token}/week?new=1"
        page = unescape(web.get(f"/p/{token}/settings?new=1").text)
        assert "Your exams" in page and "Setting up, step 2 of 3" in page
        today = unescape(web.get(f"/p/{token}").text)
        assert "Your plan is ready" not in today


def test_reminders_turned_on_from_the_first_run_end_it(tmp_path):
    with _web(tmp_path, Clock(MORNING)) as web:
        token = _start(web).split("/")[2]
        device = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
            "keys": {"p256dh": "B" * 87, "auth": "A" * 22},
        }
        answer = web.post(f"/p/{token}/push?then=first-run", json=device)
        assert answer.status_code == 200, answer.text
        assert answer.json()["go"] == f"/p/{token}?saved=reminders"
        assert service.first_run(service.load_subscription(web.root, token)) is None


def test_the_first_days_session_stops_once_a_session_is_logged(tmp_path):
    """AUDIT 90: after the first day's short session was timed and logged, Today
    offered it again that evening."""
    clock = Clock(EVENING)
    with _web(tmp_path, clock) as web:
        token = _start(web).split("/")[2]
        sub = service.load_subscription(web.root, token)
        assert service.first_day(sub, EVENING, web.root) is not None
        web.post(
            f"/p/{token}/log",
            data={"course": "Algebra 3", "minutes": "25", "effort": "5", "progress": "3", "visibility": "me"},
        )
        sub = service.load_subscription(web.root, token)
        assert service.first_day(sub, EVENING, web.root) is None
        assert service.first_day(sub, EVENING) is not None  # without the diary, as before
