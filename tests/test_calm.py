"""D73, calm glass: the sky of the hour, the garden of the streak, two type faces
shipped with the app, and Today with one next step large and the rest folded."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service
from cps.web import Config, create_app

SEMESTER = Path(__file__).resolve().parent.parent / "examples" / "sample-semester.ics"
STATIC = Path(service.__file__).parent / "web" / "static"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, a Tuesday


@pytest.mark.parametrize(
    ("hour", "sky"),
    [
        (0, "night"),
        (4, "night"),
        (5, "morning"),
        (11, "morning"),
        (12, "afternoon"),
        (17, "afternoon"),
        (18, "evening"),
        (21, "evening"),
        (22, "night"),
        (23, "night"),
    ],
)
def test_the_sky_follows_the_hour(hour, sky):
    assert service.sky_at(hour) == sky


@pytest.mark.parametrize(
    ("streak", "stage"), [(0, 1), (2, 1), (3, 2), (6, 2), (7, 3), (13, 3), (14, 4), (60, 4)]
)
def test_the_garden_grows_with_the_streak(streak, stage):
    assert service.garden_stage(streak) == stage


def test_the_sky_is_the_plans_hour_not_the_servers(tmp_path):
    plan = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)
    # 06:00 UTC is 08:00 in Paris: morning there, night by the server's clock; the sun
    # rose there a few minutes before (D76).
    expected = {"sky": "morning", "garden": 1, "weekday": 2, "daylight": 1.04}
    assert service.look_of(tmp_path, plan, MORNING) == expected
    assert (
        service.look_of(tmp_path, plan, MORNING + timedelta(hours=11))["sky"] == "evening"
    )  # 19:00 in Paris
    assert service.look_of(tmp_path, None, MORNING)["garden"] == 1


@pytest.fixture
def clock():
    return {"now": MORNING}


@pytest.fixture
def web(tmp_path, clock):
    config = Config(store=tmp_path, clock=lambda: clock["now"], background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def _start(web, **data) -> str:
    files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
    start = web.post("/start", data={"tz": "Europe/Paris", **data}, files=files, follow_redirects=False)
    return start.headers["location"].split("/")[2]


def test_a_plans_pages_carry_the_sky_and_the_garden_and_the_home_page_does_not(web):
    token = _start(web)
    for path in ("", "/panel", "/progress", "/focus", "/settings", "/community"):
        page = web.get(f"/p/{token}{path}").text
        if path == "/panel":  # a fragment: the greeting of the hour, no <body>
            assert "<h1>Good morning</h1>" in page
            continue
        assert 'data-sky="morning" data-garden="1"' in page, path
        assert '<div class="garden" aria-hidden="true">' in page, path
    home = web.get("/").text
    assert "data-sky" not in home and 'class="garden"' not in home


def test_the_type_faces_are_the_apps_own_files_with_their_licences(web):
    """Fetched from Google's servers, the fonts would give Google every student's
    address; they are files of the app, under the SIL Open Font Licence."""
    css = web.get("/static/style.css").text
    for name in (
        "figtree-latin",
        "figtree-latin-ext",
        "bricolage-grotesque-latin",
        "bricolage-grotesque-latin-ext",
    ):
        assert f'url("/static/fonts/{name}.woff2")' in css
        font = web.get(f"/static/fonts/{name}.woff2")
        assert font.status_code == 200 and font.headers["content-type"] == "font/woff2"
        assert font.content[:4] == b"wOF2"
    for licence in ("OFL-Figtree.txt", "OFL-BricolageGrotesque.txt"):
        assert "SIL Open Font License, Version 1.1" in (STATIC / "fonts" / licence).read_text(
            encoding="utf-8"
        )
    assert "googleapis" not in css and "gstatic" not in css
    assert "font-src 'self'" in web.get("/").headers["content-security-policy"]


def test_every_drawing_and_file_the_style_sheet_and_the_worker_name_is_served(web):
    css = web.get("/static/style.css").text
    drawings = set(re.findall(r"/static/garden/([a-z]+\.svg)", css))
    assert drawings == {"seedling.svg", "sprout.svg", "vine.svg", "bud.svg", "flower.svg"}
    worker = web.get("/static/sw.js").text
    for path in {f"/static/garden/{d}" for d in drawings} | set(re.findall(r'"(/static/[^"]+)"', worker)):
        assert web.get(path).status_code == 200, path


def test_today_puts_the_next_step_first_and_folds_the_rest(web, clock):
    token = _start(web)
    clock["now"] = MORNING + timedelta(days=8, hours=8)  # 16:00 in Paris, a week of sessions behind
    page = unescape(web.get(f"/p/{token}").text)
    assert "<h1>Good afternoon</h1>" in page
    # The next step, then what needs an answer, then everything else under "More".
    assert page.index('class="section next-step"') < page.index('id="reports"') < page.index("data-more")
    assert page.index("data-more") < page.index("<h2>Tasks</h2>")
    assert '<details class="why-now"><summary>Why now</summary>' in page
    # One session to report, the latest (D97); the ones before it count as done.
    asked = page[page.index('id="reports"') : page.index("data-more")]
    assert asked.count('class="mini-outcomes"') == 1 and "more-asked" not in page
    # The summary says what is folded; the session's own page keeps its reasons open.
    assert re.search(r'<span class="more-what">[^<]*Tasks[^<]*</span>', page)
    session = re.search(r'action="(/s/[\w-]+/\w+)"', page).group(1)
    shown = unescape(web.get(session).text)
    assert "session-card" in shown and "why-now" not in shown


def test_the_greeting_speaks_the_plans_language(web):
    token = _start(web, lang="fr")
    assert "<h1>Bonjour</h1>" in unescape(web.get(f"/p/{token}").text)
