"""
D140: Today as the app's home, Calendar the calendar alone. What must hold: Today draws
its widgets, the most urgent first, and no calendar; Calendar draws the calendar and no
panel; the day's list holds today's classes, exams, busy times and sessions in time order,
each over, going on or to come, a session with its report, the trips and lunch left to the
calendar; the classmates' window shows who of the people followed is studying now and their
newest posts, never the student's own, and nothing for a student without a handle; the page
redraws the same widgets after a change; a report from Today needs no calendar.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service, social
from cps.store import Store
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)  # 18:00 in Paris, a Tuesday


def _plan(root: Path, handle: str | None = None) -> service.Subscription:
    sub = service.new_subscription(
        subjects=[service.SubjectSpec(n, None, familiarity=3) for n in ("Algebra 3", "Advanced Statistics")],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(root, service.finish_first_run(sub))
    if handle:
        form = {"handle": handle, "university": "Université Lyon 1", "programme": "L2", "old_enough": "1"}
        service.save_profile(root, sub.token, form)
    return service.load_subscription(root, sub.token)


def _friends(root: Path, a: service.Subscription, b: service.Subscription, a_handle: str, b_handle: str):
    """`a` follows `b`, accepted."""
    service.follow(root, a.token, b_handle)
    service.answer_follow(root, b.token, a_handle, accept=True)


def test_the_days_list_holds_today_in_order_with_where_each_stands(tmp_path):
    sub = _plan(tmp_path)
    day = service.day_agenda(sub, NOW)
    assert day == sorted(day, key=lambda d: (d["start"], d["end"]))
    # The same day as the week's list (the calendar without a script), less the trips and lunch.
    week = service.agenda(sub, 0, NOW)["days"][0]["entries"]
    expected = sorted((e["start"], e["end"]) for e in week if e["kind"] not in service.DAY_LEFT_OUT)
    assert sorted((d["start"], d["end"]) for d in day) == expected and day
    for d in day:
        assert d["state"] == ("past" if d["end"] <= "18:00" else "now" if d["start"] <= "18:00" else "later")
        assert d["kind"] in {"class", "exam", "typed", "skipped", "study", *service.TEST_KINDS}
    # On the day of the plan's first session, just after it: over, and once reported, said so.
    first = service.PlanReport.from_dict(sub.plan).sessions[0]
    after = datetime.fromisoformat(first.end) + timedelta(minutes=10)
    studied = [d for d in service.day_agenda(sub, after) if d["kind"] == "study"]
    assert studied and studied[0]["state"] == "past" and studied[0]["reported"] is None
    reported = service.report_session(sub, service.session_id(first), "done", now=after)
    assert next(d["reported"] for d in service.day_agenda(reported, after) if d["kind"] == "study") == "done"
    assert service.day_agenda(_plan(tmp_path), NOW - timedelta(days=400)) == []  # no day of the plan then


def test_classmates_are_the_people_followed_never_oneself(tmp_path):
    ada = _plan(tmp_path, "ada")
    ben = _plan(tmp_path, "ben")
    assert service.classmates_glance(tmp_path, _plan(tmp_path), NOW) is None  # no handle
    assert service.classmates_glance(tmp_path, ben, NOW) is None  # nobody followed: no empty window
    _friends(tmp_path, ben, ada, "ben", "ada")
    fresh, _post = service.log_session(
        tmp_path,
        ada,
        {"course": "Algebra 3", "minutes": "30", "effort": "5", "progress": "3", "visibility": "followers"},
        [],
        now=NOW,
    )
    service.save_subscription(tmp_path, fresh)
    service.post_explanation(
        tmp_path,
        ada,
        {
            "concept": "Eigenvalues",
            "course": "Algebra 3",
            "text": "Directions a matrix only stretches.",
            "visibility": "everyone",
        },
        now=NOW,
    )
    seen = service.classmates_glance(tmp_path, ben, NOW)
    assert seen is not None and seen["now"] == []
    assert [(p["author"]["handle"], p["course"]) for p in seen["posts"]] == [("ada", "Algebra 3")] * 2
    says = {p["says"] for p in seen["posts"]}
    assert "explained Eigenvalues" in says and any(s.startswith("studied ") for s in says)
    assert service.classmates_glance(tmp_path, ada, NOW) is None  # her own posts are not her classmates'
    # Studying now, with her yes (D108): she is in his window, with a Join.
    service.set_studying_now(tmp_path, ada.token, True)
    focused = service.update_subscription(
        tmp_path, ada.token, lambda current: service.start_focus(current, "Algebra 3", goal=25, now=NOW)
    )
    service.sync_presence(tmp_path, focused, NOW)
    seen = service.classmates_glance(tmp_path, ben, NOW + timedelta(minutes=5))
    assert seen is not None and [(s["handle"], s["join"]) for s in seen["now"]] == [("ada", "Algebra 3")]
    assert seen["join_minutes"] == service.JOIN_MINUTES


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def test_today_is_the_widgets_and_calendar_the_calendar(web, tmp_path):
    ada = _plan(tmp_path, "ada")
    ben = _plan(tmp_path, "ben")
    _friends(tmp_path, ben, ada, "ben", "ada")
    service.post_explanation(
        tmp_path,
        ada,
        {
            "concept": "Eigenvalues",
            "course": "Algebra 3",
            "text": "Directions a matrix only stretches.",
            "visibility": "everyone",
        },
        now=NOW,
    )
    # Every widget on (D155 starts with five), to see them all in their order.
    every = [key for key, _name, _line in service.TODAY_WIDGETS]
    service.update_subscription(tmp_path, ben.token, lambda s: service.set_today_widgets(s, every))
    today = unescape(web.get(f"/p/{ben.token}").text)
    assert (
        '<div class="hub">' in today
        and '<section class="board"' not in today
        and 'id="calendar"' not in today
    )
    # D171: tasks after the day; D177: Community right under the next step.
    widgets = ("next", "friends", "day", "tasks", "time", "tips", "week")
    order = [today.index(f'class="w w-{name}') for name in widgets]
    assert order == sorted(order)  # the most urgent first, as a phone reads it
    for section in ("/week", "/progress", "/tasks", "/community", "/focus"):
        assert f'class="w-link" href="/p/{ben.token}{section}"' in today, section
    day = today[today.index('class="w w-day w-wide"') :]
    day = day[: day.index("</section>")]
    # The day as one strip (D171): each item once, a now line of its own.
    assert day.count('<li class="dl-item ') == len(service.day_agenda(ben, NOW)) and 'class="dl-now' in day
    # An item's state never takes the now line's class (AUDIT 106): an item under way
    # was drawn as a line of no height, over the next one.
    items = re.findall(r'<li class="(dl-item [^"]*)"', day)
    assert items and all("dl-now" not in c.split() for c in items)
    assert {c.split()[2] for c in items} <= {"at-past", "at-now", "at-later"}
    assert "@ada" in today and "explained Eigenvalues" in today
    # The page's redraw is the same widgets.
    assert '<div class="hub">' in web.get(f"/p/{ben.token}/panel").text
    calendar = web.get(f"/p/{ben.token}/week").text
    assert 'id="calendar"' in calendar and 'id="panel"' not in calendar and 'class="hub"' not in calendar


def test_a_report_from_today_needs_no_calendar():
    script = (ROOT / "src" / "cps" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "const calendar = board && new Calendar(board" in script
    assert (
        "await change(`sessions/${sid}/report`" in script and "if (calendar) return calendar.change" in script
    )
    assert re.search(r"if \(calendar\) calendar\.load\(\);\s*$", script)


def test_the_days_hours_add_up_and_attention_counts_what_the_timer_logged(tmp_path):
    sub = _plan(tmp_path)
    seen = service.time_and_attention(tmp_path, sub, NOW)
    assert seen is not None and (seen["wake"], seen["bed"]) == (
        service.day_view(sub)["wake"],
        service.day_view(sub)["bed"],
    )
    day = service.day_agenda(sub, NOW)
    classes = sum(service._span(d["start"], d["end"]) for d in day if d["kind"] == "class")
    assert seen["hours"]["classes"] == service.format_number(round(classes * 2) / 2)
    widths = [b["width"] for b in seen["bar"]]
    assert all(w > 0 for w in widths) and 99.5 <= sum(widths) <= 100.5
    assert [b["part"] for b in seen["bar"]] == [
        p for p in service.TIME_PARTS if p in {b["part"] for b in seen["bar"]}
    ]
    counted = ("minutes", "time", "sessions", "checked", "whole")
    assert {k: seen["focus"][k] for k in counted} == {
        "minutes": 0,
        "time": "",
        "sessions": 0,
        "checked": 0,
        "whole": 0,
    }

    # Two sessions timed this week, one checked without leaving the app, one left twice;
    # one logged by hand, and one timed last week: neither counts.
    def log(day, **data):
        social.create_post(
            Store(tmp_path),
            sub.token,
            "session",
            day=day,
            visibility="me",
            data={"course": "Algebra 3", "effort": 5, "progress": 3, **data},
            now=NOW,
        )

    log("2026-09-29", minutes=50, timed=True, checked=True)
    log("2026-09-29", minutes=25, timed=True, checked=True, interruptions=2, away_minutes=3)
    log("2026-09-29", minutes=40)
    log("2026-09-22", minutes=90, timed=True, checked=True)
    focus = service.time_and_attention(tmp_path, sub, NOW)["focus"]
    # The days and the part of the day (D144) are tested in test_focus_today.py.
    assert {k: focus[k] for k in counted} == {
        "minutes": 75,
        "time": service.duration_words(75),
        "sessions": 2,
        "checked": 2,
        "whole": 1,
    }


def test_a_tip_to_study_and_a_tip_to_live_well_each_day(tmp_path):
    sub = _plan(tmp_path)
    tips = service.tips_for_today(sub, NOW)
    assert [t["kind"] for t in tips] == ["study", "live"]
    assert tips[0]["topic"] in service.STUDY_TOPICS and tips[1]["topic"] in service.LIVE_TOPICS
    week = {tuple(t["key"] for t in service.tips_for_today(sub, NOW + timedelta(days=d))) for d in range(7)}
    assert len(week) == 7  # a different pair each day
    other = service.tips_for_today(sub, NOW, besides=tips[0]["key"])
    assert tips[0]["key"] not in {t["key"] for t in other}
    assert service.tips_for_today(service.set_tips(sub, "off"), NOW) == []


def test_the_learning_platform_is_known_only_from_its_calendar_link(tmp_path):
    from dataclasses import replace

    sub = _plan(tmp_path)
    assert service.learning_platform(sub) is None  # no link given: no guess
    for given, page in (
        (
            "https://moodle.example.edu/calendar/export_execute.php?userid=7&authtoken=x",
            "https://moodle.example.edu/my/",
        ),
        (
            "https://elearning.univ.example.it/moodle/calendar/export_execute.php?preset_what=all",
            "https://elearning.univ.example.it/moodle/my/",
        ),
        ("https://canvas.example.edu/feeds/calendars/user_abc.ics", None),  # not Moodle
        ("webcal://moodle.example.edu/calendar/export_execute.php", None),  # not an address to open
    ):
        assert (
            service.learning_platform(replace(sub, options={**sub.options, "deadlines_url": given})) == page
        ), given


def test_the_workspace_holds_each_courses_links_this_weeks_first(web, tmp_path):
    from dataclasses import replace

    sub = _plan(tmp_path, "ada")
    # The workspace is on, as a student turns it on (D155 starts with five).
    with_work = [*service.TODAY_START, "work"]
    sub = service.update_subscription(tmp_path, sub.token, lambda s: service.set_today_widgets(s, with_work))
    for title, url, week in (
        ("Course page", "https://moodle.example.edu/course/view.php?id=12", ""),
        ("Slides, week 1", "https://www.example.edu/slides/w1.pdf", "2026-09-28"),
        ("Last month", "https://example.edu/old", "2026-10-05"),  # another week: on the course's page
    ):
        sub = service.add_course_link(sub, 0, {"title": title, "url": url, "week": week})
    view = service.workspace_view(sub, NOW)
    assert view is not None and [c["name"] for c in view["courses"]] == ["Algebra 3", "Advanced Statistics"]
    algebra = view["courses"][0]
    assert [(x["title"], x["host"], x["this_week"]) for x in algebra["links"]] == [
        ("Slides, week 1", "example.edu", True),
        ("Course page", "moodle.example.edu", False),
    ]
    assert view["courses"][1]["links"] == [] and view["platform"] is None
    sub = replace(
        sub,
        options={**sub.options, "deadlines_url": "https://moodle.example.edu/calendar/export_execute.php"},
    )
    service.save_subscription(tmp_path, sub)
    today = unescape(web.get(f"/p/{sub.token}").text)
    work = today[today.index('class="w w-work"') :]
    work = work[: work.index("</section>")]
    assert (
        'href="https://moodle.example.edu/my/" target="_blank" rel="noopener noreferrer">Open Moodle' in work
    )
    assert (
        'href="https://moodle.example.edu/course/view.php?id=12" target="_blank" rel="noopener noreferrer"'
        in work
    )
    # A course without links is one row, its name and "+ Add a link"; what a link can be is
    # said once, only while no course has one (the owner, 2026-11-19: it was said per course).
    assert f'href="/p/{sub.token}/courses/1#link"' in work and "its Moodle page, slides, a folder" not in work
    bare = _plan(tmp_path)  # a plan of its own, no links yet
    service.update_subscription(tmp_path, bare.token, lambda s: service.set_today_widgets(s, with_work))
    empty = unescape(web.get(f"/p/{bare.token}").text)
    empty = empty[empty.index('class="w w-work"') :]
    empty = empty[: empty.index("</section>")]
    assert empty.count("its Moodle page, slides, a folder") == 1
    assert empty.count('class="work-add"') == len(service.workspace_view(bare, NOW)["courses"])


def test_an_item_under_way_is_drawn_as_an_item_with_the_now_line_after_it(tmp_path):
    # AUDIT 106, from the owner's screenshot: during a class, the class took the now line's
    # own class (`day-now`), lost its height and was drawn over the next item.
    sub = _plan(tmp_path)
    first = service.PlanReport.from_dict(sub.plan).sessions[0]
    during = datetime.fromisoformat(first.start) + timedelta(minutes=10)
    assert any(d["state"] == "now" for d in service.day_agenda(sub, during))
    config = Config(
        store=tmp_path, clock=lambda: during, background=False, sweep_every=None, remind_every=None
    )
    with TestClient(create_app(config)) as web:
        page = unescape(web.get(f"/p/{sub.token}").text)
    day = page[page.index('class="w w-day') :]
    day = day[: day.index("</section>")]
    rows = re.findall(r'<li class="([^"]*)"', day)
    under_way = [r for r in rows if "at-now" in r.split()]
    assert len(under_way) >= 1 and all(r.startswith("dl-item ") for r in under_way)
    # One now line (D171: placed on the strip), inside what is under way, after what is
    # over and before what is to come; each place a whole percent, so within one.
    lines = re.findall(r'class="dl-now dl-x(\d+)"', day)
    assert len(lines) == 1
    now = int(lines[0])

    def span(row: str) -> tuple[int, int]:
        x = int(re.search(r"\bdl-x(\d+)\b", row).group(1))
        return x, x + int(re.search(r"\bdl-w(\d+)\b", row).group(1))

    for row in rows:
        start, end = span(row)
        if "at-now" in row.split():
            assert start <= now <= end + 1
        elif "at-past" in row.split():
            assert end <= now + 1
        else:
            assert start >= now - 1
