"""
Pictures, and a livelier look back (DECISIONS.md D99, D100; AUDIT 80). What must hold:
every place under You and every insight has a drawing of its own, drawn without inline
style or script; the climb adds the hours up, so it only ever rises, with a flag at each
milestone passed; the diary shows what each session was, its effort and how it went at
a glance, week by week; the lengths on Focus are no pane of their own.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from itertools import pairwise
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import service, trends
from cps.web import Config, charts, create_app

ROOT = Path(__file__).resolve().parent.parent
SEMESTER = ROOT / "examples" / "sample-semester.ics"
WEB = ROOT / "src" / "cps" / "web"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock(MORNING)


@pytest.fixture
def web(tmp_path, clock):
    config = Config(store=tmp_path, clock=clock, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        client.root = tmp_path
        yield client


def _start(client) -> str:
    files = {"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")}
    answer = client.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    return answer.headers["location"].split("/")[2]


def _log(client, token, title, *, effort="6", progress="3", minutes="45", course="Algebra 3"):
    form = {"course": course, "title": title, "effort": effort, "progress": progress, "minutes": minutes}
    assert client.post(f"/p/{token}/log", data={**form, "visibility": "me"}).status_code == 200


def test_each_place_under_you_has_its_own_picture(web):
    # D99: a short-attention reader knows a row by its picture before its words.
    token = _start(web)
    you = web.get(f"/p/{token}/you").text
    rows = re.findall(r'<li><a href="(/p/[^"]+)"><span class="art art-([a-z]+)" aria-hidden="true"><svg', you)
    assert len(rows) == 9 and len({name for _, name in rows}) == 9
    art = (WEB / "templates" / "_art.html").read_text()
    assert "style=" not in art and "<script" not in art  # the Content-Security-Policy allows neither
    assert all(f'name == "{name}"' in art for _, name in rows)
    # Every picture drawn anywhere has its tile's colour in the style sheet.
    css = (WEB / "static" / "style.css").read_text()
    for name in re.findall(r'name == "([a-z]+)"', art):
        assert f".art.art-{name} {{ background: linear-gradient" in css, name


def test_each_topic_of_study_tips_has_its_own_picture(web):
    # D99 again, on the owner's word for Study tips (2026-10-26): six topics, the coach
    # and the weekly note, each with a drawing of its own.
    from cps import tips

    token = _start(web)
    page = web.get(f"/p/{token}/tips").text
    topics = re.findall(r'href="/p/[^"]+/tips\?topic=([a-z]+)"><span class="art art-([a-z]+)"', page)
    assert [key for key, _ in topics] == list(tips.TOPICS)
    links = re.findall(r'href="/p/[^"]+/(coach|weekly)">\s*<span class="art art-([a-z]+)"', page)
    assert [where for where, _ in links] == ["coach", "weekly"]
    drawn = [name for _, name in topics + links]
    assert len(set(drawn)) == len(drawn) == 8


def test_the_lengths_on_focus_are_no_pane_of_their_own(web):
    # AUDIT 80: the glass gave the lengths' fieldset a pane with nothing padded inside.
    token = _start(web)
    focus = web.get(f"/p/{token}/focus").text
    assert focus.count('<fieldset class="focus-lengths bare">') == focus.count("<fieldset") >= 1
    css = (WEB / "static" / "style.css").read_text()
    rule = re.search(r"\nfieldset\.bare, (.*?) \{(.*?)\}", css, re.S)
    assert rule, "no rule for a bare fieldset"
    for prefix in ("body[data-sky]", ':root:not([data-glass="clear"])', ':root[data-glass="solid"]'):
        assert prefix in rule[1], prefix  # under every glass setting
    assert "background: none" in rule[2] and "border: 0" in rule[2] and "backdrop-filter: none" in rule[2]


def test_the_climb_only_rises_and_flags_each_milestone_passed():
    hours = [0.5, 4.0, 0.0, 12.0, 9.5]
    totals = trends.climb(hours)
    assert totals == [0.5, 4.5, 4.5, 16.5, 26.0]
    assert all(b >= a for a, b in pairwise(totals))
    assert trends.climb([3.0, -1.0]) == [3.0, 3.0]  # never down
    passed = trends.flags(totals)
    assert [m for m, _ in passed] == [1, 5, 10, 25]
    one = dict(passed)[1]  # passed in the second week, an eighth of the way through it
    assert one == pytest.approx(1 + 0.5 / 4.0)
    assert trends.next_milestone(26.0) == 50 and trends.next_milestone(5000.0) is None
    assert trends.flags([0.0, 0.0]) == []


def test_the_climbs_drawing_keeps_its_flags_apart_and_below_the_next():
    totals = trends.climb([2.0] * 30 + [40.0])  # many flags early, then a big week
    passed = trends.flags(totals)
    g = charts.climb(totals, passed, trends.next_milestone(totals[-1]))
    assert 1 <= len(g["flags"]) <= charts.FLAGS_SHOWN
    xs = [f["x"] for f in g["flags"]]
    assert xs == sorted(xs) and all(b - a >= charts.FLAG_GAP for a, b in pairwise(xs))
    assert g["flags"][-1]["m"] == passed[-1][0]  # the latest flag is always drawn
    assert g["next"]["y"] == charts.CLIMB_TOP <= g["summit"] <= charts.CLIMB_BASE
    ys = [float(p.split(",")[1]) for p in g["d"][1:].split(" L")]
    assert all(b <= a for a, b in pairwise(ys))  # up the page, never down
    assert len(g["hits"]) == len(totals)


def test_climb_view_names_the_biggest_week_and_a_flag_passed_now():
    weeks = [
        {"first": "7 Sep", "current": False, "hours": 3.0},
        {"first": "14 Sep", "current": False, "hours": 6.5},
        {"first": "21 Sep", "current": True, "hours": 2.0},
    ]
    view = service.climb_view({"weeks": weeks, "since": "7 Sep"})
    assert view["total"] == 11.5 and view["this_week"] == 2.0 and view["next"] == 25
    assert view["new"] == 10  # 9.5 before this week, 11.5 now
    assert view["biggest"] == {"hours": 6.5, "first": "14 Sep", "now": False}
    assert len(view["texts"]) == 3 and "11.5 h in all" in view["texts"][-1]
    assert service.climb_view({"weeks": [], "since": ""}) is None


def test_progress_shows_the_climb_and_a_picture_on_each_insight(web, clock):
    token = _start(web)
    for day, title in enumerate(("Past paper 2024", "Problem sheet 3", "Chapter 4")):
        clock.now = MORNING + timedelta(days=day)
        _log(web, token, title, minutes="90")
    page = unescape(web.get(f"/p/{token}/progress").text)
    assert "Your climb" in page and "4.5 h" in page and "Next flag: 5 h" in page
    assert 'class="climb-plot"' in page and 'class="climb-flag"' in page and "style=" not in page
    pictured = (
        r'<article class="card insight insight-([a-z_]+)[^>]*>\s*<header>\s*'
        r'<span class="art art-([a-z]+) art-sm"'
    )
    cards = re.findall(pictured, page)
    assert {key for key, _ in cards} == {"pace", "on_time", "focus", "recall_time", "forecast"}
    assert len({art for _, art in cards}) == len(cards)


@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("Past paper 2024", "paper"),
        ("Mock exam", "paper"),
        ("Esame di prova", "paper"),
        ("Problem sheets 2 and 3", "exercises"),
        ("Exercices du TD 4", "exercises"),
        ("Esercizi capitolo 2", "exercises"),
        ("Übungsblatt", "study"),  # a compound word: no whole word known, the default
        ("Lecture notes, again", "summary"),
        ("Riassunto", "summary"),
        ("Reading chapter 5", "reading"),
        ("Capitolo 3", "reading"),
        ("Watched the lecture video", "lecture"),
        ("Flashcards", "cards"),
        ("Lab project", "project"),
        ("", "study"),
        ("Revision", "study"),
    ],
)
def test_what_a_session_was_is_read_from_its_title(title, kind):
    assert service.work_kind(title) == kind


def test_the_diary_is_week_by_week_with_effort_and_progress_drawn(web, clock):
    token = _start(web)
    _log(web, token, "Past paper", effort="8", progress="5", minutes="120")
    clock.now = MORNING + timedelta(days=8)
    _log(web, token, "Reading", effort="3", progress="2", minutes="30")
    clock.now = MORNING + timedelta(days=22)
    _log(web, token, "Problem sheet", effort="6", progress="3", minutes="60")
    plan = service.load_subscription(web.root, token)
    weeks = service.diary_view(web.root, plan, clock.now)["weeks"]
    assert [w["name"] for w in weeks] == ["This week", "Week of Mon 5 Oct", "Week of Mon 28 Sep"]
    assert [w["open"] for w in weeks] == [True, False, False]
    assert [w["sessions"] for w in weeks] == [1, 1, 1] and [w["best"] for w in weeks] == [3, 2, 5]
    assert [w["biggest"] for w in weeks] == [False, False, True]
    assert weeks[0]["posts"][0]["work"] == "exercises" and weeks[0]["posts"][0]["color"] is not None
    page = unescape(web.get(f"/p/{token}/progress").text)
    assert page.count('<details class="diary-week"') == 3
    assert page.count('<details class="diary-week" open>') == 1
    assert "Biggest week" in page
    start = page.rfind("<article", 0, page.index("Past paper"))
    paper = page[start : page.index("</article>", start)]
    assert '<span class="art art-paper"' in paper and '<span class="dot c' in paper
    assert len(re.findall(r'<i class="e\d+', paper)) == 10  # ten bars
    assert len(re.findall(r'<i class="e\d+ on">', paper)) == 8  # lit up to the effort, 8
    assert '<svg class="plant plant-5"' in paper


def test_a_week_with_no_session_counts_its_entries(web):
    token = _start(web)
    plan = service.load_subscription(web.root, token)
    form = {"concept": "Limits", "text": "Getting as close as you like.", "visibility": "me"}
    service.post_explanation(web.root, plan, form, now=MORNING)
    weeks = service.diary_view(web.root, plan, MORNING)["weeks"]
    assert weeks[0]["sessions"] == 0 and len(weeks[0]["posts"]) == 1 and not weeks[0]["biggest"]
    assert "1 entry" in unescape(web.get(f"/p/{token}/progress").text)


def test_the_climb_starts_with_the_plan(web):
    token = _start(web)
    page = unescape(web.get(f"/p/{token}/progress").text)
    assert "Your climb" in page and "Next flag: 1 h" in page and 'class="climb-flag"' not in page
    plan = service.load_subscription(web.root, token)
    assert date.fromisoformat(plan.options["start"]) <= MORNING.date()
