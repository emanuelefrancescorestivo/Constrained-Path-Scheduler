"""D122: the science page told as a student's first month in a new city, each figure
first the story's picture and turning into the real chart as its scene is scrolled
through. Every number in either state is the service's; the real state is what a page
without its script shows; static/morph.js only reads between the two."""

import html
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import cards as flashcards
from cps import service
from cps.memory import FSRS_6
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src/cps/web/static"


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    config = Config(store=tmp_path_factory.mktemp("city"), background=False, sweep_every=None)
    with TestClient(create_app(config)) as client:
        yield client


@pytest.fixture(scope="module")
def city():
    return service.science_view()["city"]


FIGURES = ("streets", "walks", "kept", "phone", "week")


def _points(text: str) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in (p.split(",") for p in text.split())]


def test_every_shape_ends_as_the_real_figure_draws_it(city):
    """The last keyframe of each moving shape is its real state, the attributes it is
    drawn with; the keyframes run forward over the scene, each with the same attributes;
    a line that moves keeps its number of points, so it moves point by point."""
    for name in FIGURES:
        shapes = city[name]["shapes"]
        assert shapes and any(s["k"] for s in shapes), name
        for shape in shapes:
            frames = shape["k"]
            if not frames:
                continue
            at = [p for p, _ in frames]
            assert at == sorted(at) and at[0] >= 0 and at[-1] <= 1, (name, shape)
            keys = set(frames[0][1])
            assert all(set(step) == keys for _, step in frames), (name, shape)
            assert frames[-1][1] == {k: shape["attrs"][k] for k in keys}, (name, shape)
            if "points" in keys:
                counts = {len(_points(step["points"])) for _, step in frames}
                assert len(counts) == 1, (name, shape["cls"])


def test_the_streets_unroll_into_the_forgetting_curves(city):
    """Three routes of a first week (a café, the library, home) become the curves of the
    topics that hold 2, 7 and 30 days, drawn from FSRS-6."""
    routes = [s for s in city["streets"]["shapes"] if s["cls"].startswith("mo-route")]
    assert [s["cls"] for s in routes] == ["mo-route lvl-review", "mo-route lvl-good", "mo-route lvl-strong"]
    for shape, stability in zip(routes, service.SCIENCE_STABILITIES, strict=True):
        curve = _points(shape["attrs"]["points"])
        assert len(curve) == service.CITY_POINTS
        for x, y in curve:
            recall = FSRS_6.retrievability(service.SCIENCE_DAYS * x / 300, stability)
            assert y == pytest.approx(service.CITY_H * (1 - recall), abs=0.06)
        street = _points(shape["k"][0][1]["points"])
        assert street != curve and len(street) == len(curve)
        # A week on, the shakier the topic, the fainter its street.
        faded = [step["stroke-opacity"] for _, step in shape["k"]]
        assert min(faded) < 1 or stability >= 30
    week = city["week_recall"]
    assert week == [round(100 * FSRS_6.retrievability(7.0, s)) for s in service.SCIENCE_STABILITIES]
    assert week == [79, 90, 97]  # "you would find the café 79 % of the time…"


def test_the_walks_stand_up_into_the_spacing_effect(city):
    """The library route walked again at five moments: each walk's bar is as tall as the
    times longer it then lasts; the later the walk, the taller, and the sturdier topics'
    dots lower at every moment but the first."""
    land = service.science_view()["land"]
    bars = [s for s in city["walks"]["shapes"] if "mo-bar" in s["cls"]]
    assert len(bars) == len(service.SCIENCE_LAND_RECALLS)
    heights = [service.CITY_H - min(y for _, y in _points(b["attrs"]["points"])) for b in bars]
    assert heights == sorted(heights)
    times = [land["times"][(service.SCIENCE_LAND_FOLLOWS, r)] for r in service.SCIENCE_LAND_RECALLS]
    for h, t in zip(heights, times, strict=True):
        assert h == pytest.approx(140 * t / max(land["times"].values()), abs=0.2)
    # The story's walks: thicker the longer the route then lasts, as D122 says.
    assert [c["after"] for c in land["columns"]] == ["7", "15", "23", "41", "60"]
    widths = [b["k"][0][1]["stroke-width"] for b in bars]
    assert widths == sorted(widths)
    labels = [s["text"] for s in city["walks"]["shapes"] if s["cls"] == "mo-times"]
    assert labels == [f"×{c['times']}" for c in land["columns"]]


def test_kept_up_and_left_alone_flatten_into_their_curves(city):
    kept = service.science_view()["kept"]
    shapes = city["kept"]["shapes"]
    lines = [s for s in shapes if s["cls"].startswith("mo-route")]
    assert [s["attrs"]["points"] for s in lines] == [
        service._taller(kept["alone"]),
        service._taller(kept["points"]),
    ]
    steps = [s for s in shapes if s["cls"] == "mo-step"]
    assert len(steps) == kept["reviews"] == len(kept["days"])
    target_y = round(service.CITY_H * (1 - flashcards.RETENTION), 1)
    assert all(s["attrs"]["cy"] == pytest.approx(target_y, abs=0.1) for s in steps)


def test_the_phone_and_the_week_turn_into_what_the_page_cites_and_runs(city):
    words = [s["text"] for s in city["phone"]["shapes"] if s["text"]]
    assert "Rated low" in words and "Rated high" in words and "Dunlosky and colleagues, 2013" in words
    rules = [s for s in city["week"]["shapes"] if "mo-rule" in s["cls"]]
    ranks = [s for s in city["week"]["shapes"] if s["cls"] == "mo-rank"]
    order = [
        r["text"]
        for _, r in sorted(zip([int(k["text"]) for k in ranks], rules, strict=True), key=lambda p: p[0])
    ]
    assert order == [
        "A deadline getting close.",
        "Exam practice.",
        "The topic you are forgetting most.",
        "Working ahead",
        "Free time.",
    ]


def test_the_page_draws_each_figure_in_its_real_state(web, city):
    page = web.get("/science").text
    assert page.count('class="mo-figure') == len(FIGURES)
    assert len(re.findall(r'<div class="code-story[ "]', page)) == len(FIGURES)
    assert '<script type="module" src="/static/' in page and "morph.js" in page
    for name in ("streets", "kept"):
        for shape in city[name]["shapes"]:
            if shape["tag"] == "polyline":
                assert f'points="{shape["attrs"]["points"]}"' in page
    frames = [json.loads(html.unescape(m)) for m in re.findall(r"data-k='([^']*)'", page)]
    assert len(frames) == sum(1 for n in FIGURES for s in city[n]["shapes"] if s["k"])
    # The two buttons for a reader without motion, hidden until the script offers them.
    assert page.count('<p class="mo-toggle" hidden>') == len(FIGURES)
    assert "style=" not in page.split("<main", 1)[1]
    for chapter in ("forgetting", "spacing", "testing", "engine"):
        part = page.split(f'id="{chapter}"', 1)[1]
        assert (
            part.index('class="code-copy"') < part.index('class="mo-figure') < part.index('class="code-caps"')
        )
    assert "Streets you walked once" in page and "Put the phone away" in page
    assert "Les rues prises une seule fois" in web.get("/science?lang=fr").text


def test_the_script_only_reads_between_the_server_s_states():
    js = (STATIC / "morph.js").read_text(encoding="utf-8")
    assert ".style" not in js and "setTimeout" not in js and "setInterval" not in js
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in js and 'root.dataset.motion === "reduce"' in js
    assert 'CSS.supports("animation-timeline: view()")' in js
    assert "setAttribute(name, value)" in js and 'addEventListener("scroll", wake, { passive: true })' in js
    assert not re.search(r"\bt\(\s*[\"']", js)  # its words are the server's
    # The stylesheet never sets what the script moves, which would override it.
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "style.css").read_text(encoding="utf-8"), flags=re.S)
    for rule in re.findall(r"(\.mo-[^{]*)\{([^}]*)\}", css):
        selector, body = rule
        assert "opacity" not in body, selector
        if any(c in selector for c in (".mo-route", ".mo-bar")):  # their width moves
            assert "stroke-width" not in body, selector
