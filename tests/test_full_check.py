"""
The fixes of the full check of 2026-11-03 (DECISIONS.md D106). What must hold: a whole
lecture can sign up from one network; `/health` reads the data; a new student's first
Today offers a short session when nothing is left that day, and asks about counting
only from the next day; the timetable is expanded once for the same window; the app's
files are named under a version and kept for a year; Progress sends its fold only when
it is opened; Community shows ten posts at a time; every page has one main heading,
no level skipped and no ID twice; no text under 12px, and colours at 4.5:1.
"""

from __future__ import annotations

import itertools
import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import calendar_io, service
from cps.web import Config, create_app, sky
from cps.web.app import asset_version

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
SEMESTER = EXAMPLES / "sample-semester.ics"
CSS = ROOT / "src/cps/web/static/style.css"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)  # 08:00 in Paris, a Tuesday
EVENING = datetime(2026, 9, 29, 20, 0, tzinfo=UTC)  # 22:00 in Paris: nothing left that day


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _web(tmp_path, clock):
    config = Config(store=tmp_path, clock=clock, background=False, sweep_every=None, remind_every=None)
    return TestClient(create_app(config))


def _start(web) -> str:
    files = {"file": ("t.ics", SEMESTER.read_bytes(), "text/calendar")}
    answer = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    return answer.headers["location"].split("/")[2]


def test_a_whole_lecture_can_sign_up_from_one_network():
    """AUDIT 85: forty an hour turned away the 41st of a first year of fifty on one Wi-Fi."""
    defaults = Config(store=".")
    assert defaults.signup_limit[0] >= 200 and defaults.start_limit[0] >= 200


def test_health_reads_the_data(tmp_path):
    """AUDIT 88: "ok" was answered without opening the database."""
    with _web(tmp_path, Clock(MORNING)) as web:
        assert web.get("/health").status_code == 200
        (tmp_path / "cps.sqlite").write_bytes(b"not a database" * 100)
        broken = web.get("/health")
        assert broken.status_code == 503 and "store unavailable" in broken.text


def test_the_first_day_offers_a_short_session_when_nothing_is_left(tmp_path):
    sub = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=EVENING)
    offer = service.first_day(sub, EVENING)
    assert offer is not None and offer["minutes"] == 25 and offer["course"] and offer["when"]
    plan = service.PlanReport.from_dict(sub.plan)
    first = min((s for s in plan.sessions if datetime.fromisoformat(s.end) > EVENING), key=lambda s: s.start)
    assert offer["course"] == first.subject
    assert service.first_day(sub, EVENING + timedelta(days=1)) is None  # only the first day
    # A plan made in the morning with a session still to come that day offers nothing.
    morning = service.start_subscription(tz="Europe/Paris", ics=SEMESTER.read_bytes(), now=MORNING)
    later_today = [
        s
        for s in service.PlanReport.from_dict(morning.plan).sessions
        if datetime.fromisoformat(s.start).date() == date(2026, 9, 29)
        and datetime.fromisoformat(s.end) > MORNING
    ]
    assert (service.first_day(morning, MORNING) is None) == bool(later_today)
    assert service.today_message(first=offer, semester=True, tip=True) == "first"
    assert service.today_message(exam=[{"course": "x"}], first=offer) == "exam"


def test_the_first_today_is_the_plan_and_the_next_day_asks_about_counting(tmp_path):
    clock = Clock(EVENING)
    with _web(tmp_path, clock) as web:
        token = _start(web)
        today = unescape(web.get(f"/p/{token}").text)
        assert "Your first day" in today and "Start today: 25 minutes on" in today
        assert 'name="goal" value="25"' in today and f'action="/p/{token}/focus/start"' in today
        assert "May the app count which of its parts you use?" not in today
        started = web.post(
            f"/p/{token}/focus/start", data={"course": "Algebra 3", "goal": "25"}, follow_redirects=False
        )
        assert started.status_code == 303
        clock.now = EVENING + timedelta(days=1)
        tomorrow = unescape(web.get(f"/p/{token}").text)
        assert "Your first day" not in tomorrow


def test_the_timetable_is_expanded_once_for_the_same_window():
    text = SEMESTER.read_text(encoding="utf-8")
    start = datetime(2026, 9, 28, tzinfo=UTC)
    before = calendar_io._expanded.cache_info().hits
    first = calendar_io.expand_events(text, start, start + timedelta(days=30))
    second = calendar_io.expand_events(text, start, start + timedelta(days=30))
    assert first == second and first is not second  # each caller its own list
    assert calendar_io._expanded.cache_info().hits > before


def test_the_apps_files_are_named_under_a_version_and_kept_for_a_year(tmp_path):
    with _web(tmp_path, Clock(MORNING)) as web:
        token = _start(web)
        page = web.get(f"/p/{token}").text
        found = re.search(r'href="/static/v/([0-9a-f]{10})/style\.css"', page)
        assert found, "the stylesheet is named under the version"
        version = found.group(1)
        assert f'src="/static/v/{version}/ui.js"' in page or f"/static/v/{version}/" in page
        kept = web.get(f"/static/v/{version}/style.css")
        assert kept.status_code == 200 and "immutable" in kept.headers["cache-control"]
        assert 'url("fonts/figtree-latin.woff2")' in kept.text  # relative: under the version too
        assert (
            "immutable" in web.get(f"/static/v/{version}/fonts/figtree-latin.woff2").headers["cache-control"]
        )
        # An older version (a page kept offline) gets today's file, asked about again.
        old = web.get("/static/v/0123456789/style.css")
        assert old.status_code == 200 and old.headers["cache-control"] == "no-cache"
        assert web.get("/static/style.css").headers["cache-control"] == "no-cache"
        assert web.get(f"/static/v/{version}/..%2F..%2Fapp.py").status_code == 404
        worker = web.get("/sw.js").text
        assert f'const VERSION = "v7-{version}";' in worker and f'"/static/v/{version}/style.css"' in worker


def test_the_version_follows_the_files(tmp_path):
    (tmp_path / "a.css").write_text("a {}")
    first = asset_version(tmp_path)
    assert asset_version(tmp_path) == first
    (tmp_path / "a.css").write_text("a { color: red }")
    assert asset_version(tmp_path) != first


def test_progress_sends_its_fold_only_when_opened(tmp_path):
    with _web(tmp_path, Clock(MORNING)) as web:
        token = _start(web)
        closed = web.get(f"/p/{token}/progress").text
        link = f'id="more" href="/p/{token}/progress?more=1#more">'
        assert '<a class="more more-closed section progress-more" ' + link in closed
        assert '<details class="more' not in closed and "Your diary</h2>" not in closed
        opened = web.get(f"/p/{token}/progress?more=1").text
        assert '<details class="more section progress-more" id="more" open>' in opened
        assert "Your diary</h2>" in unescape(opened)


def test_community_shows_ten_posts_then_more(tmp_path):
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=SEMESTER.read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=MORNING,
    )
    service.save_subscription(tmp_path, sub)
    service.save_profile(
        tmp_path, sub.token, {"handle": "ada", "university": "Lyon", "programme": "L2", "old_enough": "1"}
    )
    for n in range(23):
        form = {
            "course": "Algebra 3",
            "effort": "6",
            "progress": "4",
            "minutes": str(20 + n),
            "visibility": "followers",
        }
        service.log_session(tmp_path, sub, form, now=MORNING + timedelta(minutes=n))
    first = service.community_view(tmp_path, sub, now=MORNING)
    assert len(first["posts"]) == service.FEED_FIRST == 10 and first["more"] == 20 and first["next"]
    second = service.community_view(tmp_path, sub, shown=20, now=MORNING)
    assert len(second["posts"]) == 20 and second["posts"][:10] == first["posts"]
    assert second["posts"][10]["id"] == first["next"]
    last = service.community_view(tmp_path, sub, shown=30, now=MORNING)
    assert len(last["posts"]) == 23 and last["more"] is None
    with _web(tmp_path, Clock(MORNING)) as web:
        page = unescape(web.get(f"/p/{sub.token}/community").text)
        assert page.count('class="card post"') == 10
        assert f'href="/p/{sub.token}/community?n=20#post-{first["next"]}">Show more</a>' in page


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/progress",
        "/progress?more=1",
        "/log",
        "/explain",
        "/notes/new",
        "/agenda",
        "/cards/review",
        "/science",
        "/community",
        "/semester",
        "/courses",
        "/tasks",
    ],
)
def test_one_main_heading_no_level_skipped_and_no_id_twice(tmp_path, path):
    with _web(tmp_path, Clock(MORNING)) as web:
        token = _start(web)
        html = web.get(f"/p/{token}{path}").text
    assert html.count("<h1") == 1, path
    levels = [int(n) for n in re.findall(r"<h([1-6])\b", html)]
    assert all(b <= a + 1 for a, b in itertools.pairwise(levels)), (path, levels)
    ids = re.findall(r'\sid="([^"]+)"', html)
    assert len(ids) == len(set(ids)), (path, sorted({i for i in ids if ids.count(i) > 1}))


def test_no_text_under_12px():
    small = []
    for line in CSS.read_text().splitlines():
        if ".climb-plot text" in line or ".sci-tick" in line or ".mo-figure " in line:
            continue  # inside a drawing, which scales its text with it (a phone's sizes below are 12+)
        if ".tabbar a" in line and re.search(r"\{ font-size: 0\.6(875|25)rem", line):
            continue  # the tab bar on phones under 376px and 341px: "Communauté" must fit (AUDIT 69)
        for value, unit in re.findall(r"font-size:\s*([0-9.]+)(rem|px)\b", line):
            if float(value) * (16 if unit == "rem" else 1) < 12:
                small.append(line.strip())
    assert small == []


def _token(name: str, css: str) -> str:
    return re.search(rf"\s{re.escape(name)}:\s*([^;]+);", css).group(1).strip()


def test_buttons_links_and_grey_text_read_at_4_5_to_1():
    css = CSS.read_text()
    root = css[: css.index("@media")]  # the light tokens come first
    hexed = lambda value: tuple(int(value.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))  # noqa: E731
    page, white = hexed(_token("--bg", root)), sky.WHITE
    accent, ink = hexed(_token("--accent", root)), hexed(_token("--accent-ink", root))
    assert sky.contrast(accent, white) >= 4.5 and sky.contrast(ink, page) >= 4.5
    red, green, blue, alpha = re.match(r"rgb\((\d+) (\d+) (\d+) / (\d+)%\)", _token("--muted", root)).groups()
    a = int(alpha) / 100
    muted = tuple(round(int(c) * a + p * (1 - a)) for c, p in zip((red, green, blue), page, strict=True))
    assert sky.contrast(muted, page) >= 4.5
    assert sky.contrast(hexed(_token("--danger-fill", root)), white) >= 4.5  # red buttons, the "now" label
    # Dark mode's buttons, in both copies of its tokens.
    for dark in re.findall(r"--accent:\s*(#[0-9a-f]{6});", css)[1:]:
        assert sky.contrast(hexed(dark), white) >= 4.5, dark


def test_the_home_pages_glow_stays_inside_the_screen():
    """AUDIT 86: the glow behind the sign-up spilled 30 % past each side, and a phone
    widened the whole page to fit it (91px on a 390px screen, measured in Chromium)."""
    css = CSS.read_text()
    assert "inset: -60px max(-30%, calc(50% - 50vw)) 0" in css
    assert "html:has(> body.home) { overflow-x: hidden; overflow-x: clip; }" in css
