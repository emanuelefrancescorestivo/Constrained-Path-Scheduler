"""The front page's story (D188): eight questions beside the real app, its numbers the
model's, its pictures in each language, still without a script or without motion."""

from __future__ import annotations

import importlib.util
import itertools
import json
import re
import struct
import sys
from datetime import date
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import cards, crests, memory, service
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src/cps/web/static"
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
LANGS = ("en", "fr", "it")
PICTURES = (
    "today-laptop",
    "today-phone",
    "progress-phone",
    "community-phone",
    "course-laptop",
    "setup-phone",
)


def _web(tmp_path, **extra) -> TestClient:
    config = Config(store=tmp_path, background=False, sweep_every=None, remind_every=None, **extra)
    return TestClient(create_app(config))


def _jpeg_size(path: Path) -> tuple[int, int]:
    """A JPEG's width and height, read from its frame header (no imaging library)."""
    data = path.read_bytes()
    assert data[:2] == b"\xff\xd8", path
    at = 2
    while at < len(data):
        marker, length = data[at + 1], struct.unpack(">H", data[at + 2 : at + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[at + 5 : at + 9])
            return width, height
        at += 2 + length
    raise AssertionError(f"no frame header in {path}")


def _spacing():
    spec = importlib.util.spec_from_file_location("figures", ROOT / "benchmarks" / "figures.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["figures"] = module
    spec.loader.exec_module(module)
    return module.spacing_data(memory.FSRS_6)


def test_the_story_s_numbers_are_the_app_s_own():
    view = service.landing_view()
    # The three ways of preparing an exam: benchmarks/figures.py's method, with FSRS-6.
    reference = _spacing()
    for name in ("spaced", "crammed", "once"):
        curve = view["spacing"][name]
        assert curve["end"] == round(100 * reference[name]["end"]), name
        assert curve["exam"] == round(100 * reference[name]["exam"]), name
        assert curve["sessions"] == len(reference[name]["days"]), name
    spaced, crammed, once = (view["spacing"][n] for n in ("spaced", "crammed", "once"))
    assert spaced["end"] > crammed["end"] > once["end"]
    assert spaced["sessions"] < crammed["sessions"]  # fewer sessions, and more kept
    assert view["weeks_after"] == 12
    # The ends' labels never sit on one another.
    labels = sorted(c["label_y"] for c in view["curves"])
    assert all(b - a >= 14 for a, b in itertools.pairwise(labels))
    assert (view["points_done"], view["points_proof"]) == (service.POINTS_DONE, service.POINTS_PROOF)
    # D189, D190: the crests and the league shown are the app's own families, tiers and rules.
    assert (view["badge_count"], view["tiers"]) == (len(crests.FAMILIES), len(crests.TIERS))
    families = {f.key for f in crests.FAMILIES}
    assert {c["key"] for c in view["crests"]} <= families and len(view["crests"]) == 8
    assert {c["metal"] for c in view["crests"]} <= set(crests.TIERS)
    assert (view["league_move"], view["league_floor"]) == (crests.LEAGUE_MOVE, crests.LEAGUE_FLOOR_DAYS)
    points = [r["points"] for r in view["league"]]
    assert points == sorted(points, reverse=True) and sum(r["me"] for r in view["league"]) == 1
    assert [r["zone"] for r in view["league"]] == ["up"] * crests.LEAGUE_MOVE + ["stay"] * (len(points) - 3)
    assert all(crests.level_of(0)["n"] <= r["level"] <= len(crests.LEVEL_NAMES) for r in view["league"])
    assert view["repair"] == service.REPAIR_MINUTES
    assert view["rhythm"] * 60 == service.RHYTHM_WORK
    assert view["kept_months"] == 12 and service.RETENTION.days == 365


def test_the_course_is_spaced_by_the_app_s_rule_and_crammed_in_the_last_days():
    """D190's chart C: a course of twelve weeks prepared by the plan's rule (a self-test
    when recall falls to the target) or crammed, four sessions a week of lectures in the
    exam's last four days; and how long one week's topic stays above 80 % after its exam."""
    view = service.landing_view()
    course = view["course"]
    weeks, days = service.LANDING_COURSE_WEEKS, len(service.LANDING_CRAM)
    assert (course["weeks"], course["cram_days"]) == (weeks, days) == (12, 4)
    assert course["crammed"] == days * weeks and course["crammed_day"] == weeks
    assert course["spaced"] < course["crammed"] and course["spaced_week"] < course["crammed_day"]
    # The rule replayed here on its own: each week's topic, seen once and shaky, reviewed
    # each time its recall falls to the target, until the day before the exam.
    model, exam, spaced = memory.FSRS_6, 7.0 * (weeks - 1) + service.LANDING_COURSE_GAP, 0
    for k in range(weeks):
        state, last, t = model.initial_state(memory.Grade.HARD), 7.0 * k, 7.0 * k
        while t < exam - 1:
            t += 0.25
            if model.retrievability(t - last, state.stability) <= cards.RETENTION:
                state, last, spaced = model.review(state, t - last, memory.Grade.GOOD), t, spaced + 1
    assert course["spaced"] == spaced
    holds = view["holds"]
    assert holds["spaced"] > 2 * holds["crammed"] > 0 == holds["once"]
    assert view["holds_ratio"] == f"{holds['spaced'] / holds['crammed']:.1f}" and view["hold_percent"] == 80


@pytest.mark.parametrize("lang", LANGS)
def test_the_front_page_tells_the_story_in_eight_questions(tmp_path, lang):
    page = unescape(_web(tmp_path).get(f"/?lang={lang}").text)
    view = service.landing_view()
    assert [f"q{n}" for n in range(1, 9)] == re.findall(r'<article class="landing-q[^"]*" id="(q\d)"', page)
    # The index and the rail name the same eight questions, each a link to its place.
    assert page.count('href="#q1"') == 2 and page.count('href="#q8"') == 2
    # Every picture is the real app in the page's own language, its size given.
    for name in PICTURES:
        shown = rf'src="(/static/v/[^/]+/landing/{lang}/{name}\.jpg)" width="(\d+)" height="(\d+)"'
        found = re.search(shown, page)
        assert found, name
        assert _jpeg_size(STATIC / "landing" / lang / f"{name}.jpg") == (int(found[2]), int(found[3])), name
        # The same screen in dark, shown where the reader's device is dark.
        dark = f'srcset="{found[1][: -len(".jpg")]}-dark.jpg" media="(prefers-color-scheme: dark)"'
        assert dark in page, name
    # Only the first screen's pictures are fetched at once.
    assert page.count('loading="lazy"') == len(PICTURES) - 2
    # The numbers are the model's and the app's.
    for curve in view["curves"]:
        assert f"{curve['end']} %" in page
    assert f"+{view['points_done']}" in page and f"+{view['points_proof']}" in page
    # D190: the course's three tiles, and the friends' league with each one's avatar.
    course = view["course"]
    assert f"<b>{course['spaced']}</b>" in page and f"<b>{course['spaced_week']}</b>" in page
    assert f"{view['holds']['spaced']}" in page and f"{view['holds']['crammed']}" in page
    assert (
        len(re.findall(r'class="league-row(?: me)?(?: lg-up| lg-down)?"', page)) == len(view["league"])
        and page.count('class="crest-badge"') == 8
    )
    # One main heading, levels never skipped, no id twice.
    assert page.count("<h1") == 1
    levels = [int(n) for n in re.findall(r"<h([1-6])\b", page)]
    assert all(b <= a + 1 for a, b in itertools.pairwise(levels)), levels
    ids = re.findall(r'\sid="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    # The ways in are still there, after the story.
    assert page.index('id="story"') < page.index('id="start"') < page.index('id="signin"')


def test_the_demo_tells_the_story_with_its_own_buttons(tmp_path):
    page = _web(tmp_path, demo=lambda year: "").get("/?lang=en").text
    assert 'id="story"' in page
    # The demo's two buttons, at the top and again at the close.
    assert page.count('name="year" value="1"') == 2 and page.count('name="year" value="2"') == 2


def test_a_failed_start_puts_the_form_first(tmp_path):
    failed = _web(tmp_path).post("/start", data={"tz": "Europe/Paris"}).text
    assert 'id="story"' not in failed and "hero-short" in failed and 'id="start"' in failed


@pytest.mark.parametrize("lang", LANGS)
def test_each_language_has_every_picture_in_light_and_dark_and_stays_light(lang):
    folder = STATIC / "landing" / lang
    names = [*PICTURES, *(f"{name}-dark" for name in PICTURES)]
    assert sorted(p.stem for p in folder.glob("*.jpg")) == sorted(names)
    # A laptop's 1280 × 800, a phone's 390 × 664 at 1.5 (benchmarks/landing_shots.mjs).
    for name in names:
        width, height = _jpeg_size(folder / f"{name}.jpg")
        assert (width, height) == ((1280, 800) if "laptop" in name else (585, 996)), name
    # A reader downloads one theme's pictures: under 0.8 MB.
    for dark in (False, True):
        theme = [folder / f"{name}{'-dark' if dark else ''}.jpg" for name in PICTURES]
        assert sum(p.stat().st_size for p in theme) < 800_000, dark


def test_without_scroll_timelines_every_part_is_in_place():
    """Each scene's keyframes give only where a part starts from: where the browser does
    not tie animations to scrolling, or motion is off, every part is simply where it is.
    The rail and the reading line are shown only where they can follow the scroll."""
    plain = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    frames = dict(re.findall(r"@keyframes (landing-[a-z]+)\s*\{(.*?\})\s*\}", plain, flags=re.S))
    assert frames
    for name, body in frames.items():
        if name in ("landing-fill", "landing-lit"):
            continue  # the reading line and the rail, hidden where they cannot follow
        words = re.sub(r"from|transform|translate", "", body)
        assert body.strip().startswith("from") and "to" not in words, name
    assert ".landing-rail, .landing-progress { display: none; }" in plain


# --------------------------------------------------------------------------- #
# The front page as a launch (D191)
# --------------------------------------------------------------------------- #


def test_the_front_page_s_week_is_the_app_s_own_plan():
    """The week the front page draws is kept as data, since a server's package has no
    examples; made again here, by the app's own rules, it is the same. If the planner
    changed, `python benchmarks/landing_week.py` writes it again."""
    from cps.showcase import sample_path

    made = service.landing_week_compute(sample_path().read_bytes())
    kept = json.loads(service.LANDING_WEEK_FILE.read_text(encoding="utf-8"))
    assert made == kept
    week = service.landing_week()
    assert week["count"] == len(kept["sessions"]) and week["off"] == 2
    # Taking Tuesday off moves its sessions, and some after them; nothing before it moves.
    moved = [s for s in week["sessions"] if s["to"] != "stay"]
    assert moved and all(s["day"] >= week["off"] for s in moved)
    assert all(s["to"] is None or s["to"]["day"] > week["off"] for s in moved)
    assert week["moved"] + week["later"] == len(moved) and week["today"]
    # A block never sits on a class of the same day, before or after the change.
    busy = {(b["day"], r) for b in week["busy"] for r in range(b["row"], b["row"] + b["span"])}
    for s in week["sessions"]:
        for place in (s, s["to"]) if isinstance(s["to"], dict) else (s,):
            assert all(
                (place["day"], r) not in busy for r in range(place["row"], place["row"] + place["span"])
            )


@pytest.mark.parametrize("lang", LANGS)
def test_the_scenes_are_the_app_s_and_work_without_a_script(tmp_path, lang):
    page = unescape(_web(tmp_path).get(f"/?lang={lang}").text)
    view = service.landing_view()
    week = view["week"]
    # The week: every session where the plan put it, those that move a second time.
    assert page.count('class="lw-sess from') == week["count"]
    assert page.count('class="lw-sess to') == week["moved"]
    assert '<input type="checkbox" id="lw-off" class="lw-toggle visually-hidden">' in page
    # Today: the last session is the reader's to tick, a real checkbox with its label.
    assert page.count('class="lt-card') == len(week["today"])
    assert '<input type="checkbox" id="lt-done"' in page and '<label for="lt-done" class="lt-check"' in page
    # The league: from below the friend in fourth to the top three, with the reader's tick.
    climb = view["climb"]
    fourth = next(r["points"] for r in view["league"] if r["rank"] == view["league_move"] + 1)
    assert climb["before"] < fourth < climb["after"] < climb["tap"]
    assert climb["after"] - climb["before"] == service.POINTS_DONE + service.POINTS_PROOF
    assert all(f">{n}<" in page for n in (climb["before"], climb["after"], climb["tap"]))
    # The neurons run behind the whole story.
    assert '<div class="landing-neurons" aria-hidden="true">' in page and '<svg class="neurons"' in page
    # The scenes' places are classes, never a style attribute (CSP).
    assert " style=" not in page


def test_the_story_s_neurons_are_drawn_by_the_generator():
    from cps.web import neural

    assert neural.STORY_PARTIAL.read_text(encoding="utf-8") == neural.render_story()
    assert neural.PARTIAL.read_text(encoding="utf-8") == neural.render()  # the hero's, unchanged
    # Each of the story's stretches fires its own signals, and lights the neurons they reach.
    for k in range(1, neural.STORY_WINDOWS + 1):
        assert f".landing-neurons .ns-signals .k{k} {{ animation: ns-fire" in CSS
        assert f".landing-neurons .glow.k{k} {{ animation: ns-glow" in CSS


def test_the_scenes_move_only_with_motion_on_and_nothing_loops():
    scenes = (".landing-week", ".landing-today", ".landing-league", ".landing-neurons")
    tied = [
        line for line in CSS.splitlines() if "animation-timeline" in line and any(s in line for s in scenes)
    ]
    assert len(tied) > 40
    assert all(':root:not([data-motion="reduce"])' in line for line in tied)
    block = CSS[CSS.index("/* -- D191: the front page as a launch") :]
    block = block[: block.index("/* 8: three steps and three promises. */")]
    assert "infinite" not in block


# D193: the front page's language and its light or dark.


def _misses(monkeypatch):
    """The sentences looked up in Spanish and not found: what a reader would see in English."""
    from cps import i18n

    missed: set[str] = set()
    real = i18n.catalogue

    class Recording(dict):
        def get(self, key, default=None):
            if key not in self:
                missed.add(key)
            return super().get(key, default)

    monkeypatch.setattr(i18n, "catalogue", lambda lang: Recording(real(lang)) if lang == "es" else real(lang))
    return missed


@pytest.mark.parametrize(
    "extra",
    [{}, {"demo": lambda year: "x"}, {"accounts": True, "show_mail": True}],
    ids=["plain", "demo", "accounts"],
)
def test_the_front_page_and_the_account_s_pages_speak_spanish_whole(tmp_path, monkeypatch, extra):
    missed = _misses(monkeypatch)
    web = _web(tmp_path, **extra)
    spanish = {"accept-language": "es-ES,es;q=0.9,en;q=0.5"}
    for path in (
        "/",
        "/?theme=dark",
        "/account/forgot",
        "/account/signin",
        "/account/reset/x",
        "/account/confirm/x",
    ):
        page = web.get(path, headers=spanish).text
        assert '<html lang="es"' in page, path
    if extra.get("accounts"):
        origin = {**spanish, "origin": "http://testserver"}
        web.post("/account/signup", data={"email": "nope", "password": "x"}, headers=origin)
        web.post("/account/signin", data={"email": "a@b.example", "password": "x"}, headers=origin)
        web.post("/account/forgot", data={"email": "a@b.example"}, headers=origin)
    assert not missed, "in English on a Spanish front page:\n" + "\n".join(sorted(missed))


def test_spanish_stays_at_the_door_a_plan_speaks_the_app_s_languages(tmp_path):
    from cps import i18n

    assert i18n.pick("es-ES,es;q=0.9,fr;q=0.8") == "fr"  # the app's pages: the next one the reader reads
    assert i18n.pick("es-ES,es;q=0.9,fr;q=0.8", front=True) == "es"
    with i18n.use("es"):
        assert service.FRONT_LANGUAGES["es"] == "Español" and "es" not in service.LANGUAGES
        # A plan begun while the front page was in Spanish is planned in English.
        sub = service.start_subscription(tz="Europe/Paris", draw=True, now=service.LANDING_WEEK_START)
        assert sub.options["lang"] == "en"
        with pytest.raises(service.InvalidInput):
            service.set_language(sub, "es")
        assert i18n.format_date(date(2026, 10, 19)) == "lunes 19 de octubre"
        assert i18n.format_date(date(2026, 10, 19), "short") == "lun 19 oct"
        assert i18n.number(1.5) == "1,5"
    web = _web(tmp_path)
    # A plan's own pages, the explaining ones too, are never in Spanish.
    assert '<html lang="en"' in web.get("/garden", headers={"accept-language": "es"}).text
    assert '<html lang="es"' in web.get("/?lang=es").text


def test_the_front_page_switches_language_and_mode_without_a_script(tmp_path):
    web = _web(tmp_path, demo=lambda year: "x")
    page = web.get("/?lang=it").text
    menu = page[page.index('<details class="lang-menu">') : page.index("</details>")]
    for code in ("en", "fr", "it", "es"):
        assert f'href="?lang={code}"' in menu
    assert 'href="?lang=it" hreflang="it" lang="it" aria-current="true"' in menu
    # The device decides until the switch is used: both ways offered, CSS shows the other one.
    assert "data-theme" not in page[: page.index("<head>")]
    assert 'href="?lang=it&amp;theme=dark" data-mode="dark"' in page
    assert 'media="(prefers-color-scheme: dark)" data-dark' in page
    # Chosen dark: the page, its pictures and every link and form carry it.
    dark = web.get("/?lang=it&theme=dark").text
    assert '<html lang="it" data-theme="dark"' in dark
    assert (
        dark.count('media="all" data-dark') == len(PICTURES)
        and 'prefers-color-scheme: dark)" data-dark' not in dark
    )
    assert 'href="?lang=fr&amp;theme=dark"' in dark and 'href="?lang=es&amp;theme=dark"' in dark
    assert '<form method="post" action="/demo?lang=it"' in dark
    assert '<input type="hidden" name="theme" value="dark" data-front-mode>' in dark
    light = web.get("/?theme=light").text
    assert 'data-theme="light"' in light and light.count('media="not all" data-dark') == len(PICTURES)
    assert "data-theme" not in web.get("/?theme=purple").text[:400]
    # The note on the app's own languages shows to a Spanish reader only.
    note = "front-note"
    assert note in web.get("/?lang=es").text and note not in page


def test_the_demo_student_keeps_the_mode_chosen_on_the_front_page(tmp_path):
    made: list[str] = []

    def demo(year):
        sub = service.start_subscription(tz="Europe/Paris", draw=True, now=service.LANDING_WEEK_START)
        service.save_subscription(tmp_path, sub)
        made.append(sub.token)
        return sub.token

    web = _web(tmp_path, demo=demo)
    answer = web.post(
        "/demo?lang=fr",
        data={"year": "1", "theme": "dark"},
        headers={"origin": "http://testserver"},
        follow_redirects=False,
    )
    assert answer.status_code == 303
    assert service.theme_of(service.load_subscription(tmp_path, made[0])) == "dark"
