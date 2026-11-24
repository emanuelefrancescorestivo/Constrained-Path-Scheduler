"""The front page's story (D188): eight questions beside the real app, its numbers the
model's, its pictures in each language, still without a script or without motion."""

from __future__ import annotations

import importlib.util
import itertools
import re
import struct
import sys
from html import unescape
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps import memory, service
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src/cps/web/static"
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
LANGS = ("en", "fr", "it")
PICTURES = (
    "today-laptop",
    "today-phone",
    "week-laptop",
    "focus-phone",
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
    assert view["badge_count"] == len(service.BADGES)
    assert {b["key"] for b in view["badges"]} <= {key for key, *_ in service.BADGES}
    assert view["repair"] == service.REPAIR_MINUTES
    assert view["rhythm"] * 60 == service.RHYTHM_WORK
    assert view["kept_months"] == 12 and service.RETENTION.days == 365


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
