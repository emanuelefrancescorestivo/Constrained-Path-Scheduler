"""D118: the three explaining pages scroll as stories on a laptop. Each pinned scene is a
story: its words and its steps in one column, its picture in the other, which stays in
view while the steps go by (benchmarks/scroll_flow.mjs measures how much of each screen
is then left empty). Phones keep the pinned stage of D112."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src/cps/web/static"


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    config = Config(store=tmp_path_factory.mktemp("flow"), background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        yield client


@pytest.mark.parametrize(("path", "stories"), [("/garden", 4), ("/science", 3), ("/how", 4)])
def test_every_pinned_scene_is_a_story_with_its_words(web, path, stories):
    page = web.get(path).text
    assert len(re.findall(r'<div class="code-pin[ "]', page)) == stories
    parts = re.split(r'<div class="code-story[ "]', page)[1:]
    assert len(parts) == stories
    for part in parts:
        # Its words, its track, its picture and its steps, in that order or with the
        # words in the stage (the garden's, which a phone shows in one screen).
        track = part.index('<div class="code-pin')
        assert part.index("<figure") > track and part.index('class="code-caps"') > part.index("<figure")
        assert 'class="code-copy"' in part[: part.index("<figure")]


def _motion(css: str) -> str:
    opening = "@supports (animation-timeline: view()) {\n  @media (prefers-reduced-motion: no-preference) {\n"
    start = css.index(opening + '    :root:not([data-motion="reduce"]) .scroll-fig')
    return css[start : css.index("\n}\n", start)]


def test_on_a_laptop_the_story_is_the_track_and_the_picture_stays():
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "style.css").read_text(encoding="utf-8"), flags=re.S)
    motion = _motion(css)
    still = css.replace(motion, "")
    # Two columns at any motion setting, wider than the page's text.
    grid = re.search(r"@media \(min-width: 861px\) \{\n  \.code-story \{([^}]*)\}", still)
    assert grid and 'grid-template-areas: "copy fig" "caps fig"' in grid[1] and "width: min(74rem" in grid[1]
    assert ".code-story .code-pin, .code-story .code-pin-stage { display: contents; }" in still
    # With motion, on a laptop: the story's timeline (the track's set aside, which would
    # otherwise be found first), the picture held in view, each step lit in its turn.
    laptop = motion[motion.index("@media (min-width: 861px)") :]
    assert ".code-story { view-timeline: --pin block; view-timeline-inset: var(--topbar) 0px; }" in laptop
    assert ".code-story .code-pin { view-timeline: none; height: auto; }" in laptop
    assert re.search(r"\.code-story \.code-pin-stage > figure \{\s*position: sticky;", laptop)
    for name in ("code-step", "code-step-first", "code-step-last"):
        assert f"animation-name: {name};" in laptop and f"@keyframes {name} {{" in css
    # The steps' rule comes after the one that stacks them in one place on a phone.
    stacked = motion.index("grid-area: 1 / 1; animation-name: code-cap;")
    assert motion.index("grid-area: auto; min-height: 24svh") > stacked


def test_the_garden_follows_the_same_track():
    js = (STATIC / "garden.js").read_text(encoding="utf-8")
    assert 'getComputedStyle(this.pin).display === "contents" ? this.story : this.pin' in js
