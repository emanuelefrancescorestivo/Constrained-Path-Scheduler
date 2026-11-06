"""D118, D120: how the three explaining pages read on a laptop.

D118 made each pinned scene a story: its words and steps in one column, its picture in
the other. D120 (the owner: the formatting was not even, the text sparse, the laptop's
screen mostly empty, "starting from removing liquid glass from those pages that
explain") puts the three pages on the app's plain grey without the sky or the glass,
lays every part of them out as a row of two columns (words left, figure or evidence
right), and pins each scene as one panel: its words and all its steps on the left, the
one the figure is at lit, its figure filling the right. benchmarks/scroll_flow.mjs
measures how much of each screen is then covered, and how much can be read on it.
Phones keep the pinned stage of D112."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src/cps/web/static"
PAGES = ("/garden", "/science", "/how")


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    config = Config(store=tmp_path_factory.mktemp("flow"), background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        yield client


@pytest.fixture(scope="module")
def token(web) -> str:
    files = {"file": ("timetable.ics", (ROOT / "examples/sample-semester.ics").read_bytes(), "text/calendar")}
    start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    return start.headers["location"].split("/")[2]


def _css() -> str:
    return re.sub(r"/\*.*?\*/", "", (STATIC / "style.css").read_text(encoding="utf-8"), flags=re.S)


def _motion(css: str) -> str:
    opening = "@supports (animation-timeline: view()) {\n  @media (prefers-reduced-motion: no-preference) {\n"
    start = css.index(opening + '    :root:not([data-motion="reduce"]) .scroll-fig')
    return css[start : css.index("\n}\n", start)]


@pytest.mark.parametrize(("path", "stories"), [("/garden", 4), ("/science", 3), ("/how", 4)])
def test_every_pinned_scene_is_a_panel_with_its_words(web, path, stories):
    page = web.get(path).text
    assert len(re.findall(r'<div class="code-pin[ "]', page)) == stories
    parts = re.split(r'<div class="code-story[ "]', page)[1:]
    assert len(parts) == stories
    for part in parts:
        # One panel holds the whole scene: its words, its track, its picture and its
        # steps, in that order, or with the words in the stage (the garden's, which a
        # phone shows in one screen).
        assert re.match(r'[^>]*>\s*<div class="code-panel">', part)
        track = part.index('<div class="code-pin')
        assert part.index("<figure") > track and part.index('class="code-caps"') > part.index("<figure")
        assert 'class="code-copy"' in part[: part.index("<figure")]


@pytest.mark.parametrize("path", PAGES)
def test_every_part_is_a_row_of_words_and_figure(web, path):
    """Similarity: the three pages are built of the same rows, each with its words."""
    page = web.get(path).text
    main = page[page.index("<main") : page.index("</main>")]
    sections = re.findall(r'<section class="([^"]*)"', main)
    assert sections and all(re.search(r"\b(code-scene|code-row)\b", c) for c in sections)
    for part in re.split(r"<section ", main)[1:]:
        assert 'class="code-copy"' in part[: part.index("</section>")]
    hero = main[main.index('<header class="code-hero') :]
    assert hero.index('class="code-copy"') < hero.index("</header>")


@pytest.mark.parametrize("path", PAGES)
def test_the_explaining_pages_have_no_sky_and_no_glass(web, token, path):
    """Prägnanz: the app's plain grey, solid panels, solid bars; on a plan's page too."""
    for url in (path, f"/p/{token}{path}"):
        page = web.get(url).text
        assert "data-sky" not in page and "/sky/" not in page and 'class="garden"' not in page
        assert re.search(r'<body class="[^"]*\breading\b', page)
        assert re.search(r'<main class="site[^"]*\bread\b', page)


def test_on_a_laptop_the_panel_stays_and_its_steps_are_read_together():
    css = _css()
    motion = _motion(css)
    still = css.replace(motion, "")
    # Two columns at any motion setting: the panel's grid, the track and the stage
    # giving their pieces to it.
    grid = re.search(r"\.read \.code-panel \{ grid-template-areas: \"copy fig\" \"caps fig\";", still)
    assert grid and ".read .code-panel :is(.code-pin, .code-pin-stage) { display: contents; }" in still
    assert "--read-cols: minmax(0, 5fr) minmax(0, 7fr);" in still
    # With motion, on a laptop: the story is the track, its panel held in view, its
    # figure's card filling the right column.
    laptop = motion[motion.index("@media (min-width: 861px)") :]
    track = "view-timeline: --pin block; view-timeline-inset: var(--topbar) 0px; height: 180vh; }"
    assert ".read .code-story { " + track in laptop
    assert ".read .code-story .code-pin { view-timeline: none; height: auto; }" in laptop
    held = r"\.read \.code-panel \{\s*position: sticky; top: var\(--topbar\);"
    assert re.search(held + r" height: calc\(100svh - var\(--topbar\)\);", laptop)
    assert "> figure { align-self: stretch;" in laptop
    for name in ("code-step", "code-step-first", "code-step-last"):
        assert f"animation-name: {name};" in laptop
        # Lit by colour, every step readable: none faded.
        frames = re.search(r"@keyframes " + name + r" \{(.*)\}\n", css)
        assert frames and "var(--muted)" in frames[1] and "opacity" not in frames[1]
    # The steps' rule comes after the one that stacks them in one place on a phone.
    stacked = motion.index("grid-area: 1 / 1; animation-name: code-cap;")
    assert motion.index("code-caps > li { grid-area: auto; animation-name: code-step; }") > stacked
    # A short window sets the panel's words a size smaller; a very short one does not pin.
    assert "@media (min-width: 861px) and (max-height: 860px) {" in still
    short = r"@media \(min-width: 861px\) and \(max-height: 639px\) \{[^@]*position: static;"
    assert re.search(short, motion)


def test_the_garden_follows_the_same_track():
    js = (STATIC / "garden.js").read_text(encoding="utf-8")
    assert 'getComputedStyle(this.pin).display === "contents" ? this.story : this.pin' in js


def test_the_flow_benchmark_measures_area_and_words():
    bench = (ROOT / "benchmarks/scroll_flow.mjs").read_text(encoding="utf-8")
    assert "area covered" in bench and "words read" in bench and "alpha >= 0.6" in bench
