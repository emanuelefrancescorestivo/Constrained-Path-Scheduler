"""
D160 and D164: each study tip with a picture of what its advice asks, and the study's number
where it gives one. D160 drew it on a tile beside the text ("a mix of A and B, especially B");
D164 made it a cover across the top of the card (the owner's "2C", 2026-11-19), after the tile
let a number spill out of it on a phone (AUDIT 113). What must hold: every tip has a cover and
no cover is left without a tip; each is plain SVG in the theme's classes, with no inline style
(the Content-Security-Policy); every number on a cover is one its tip's own words state, as a
caption in the page, translated, never drawn inside the picture. D168 drew the covers again
(the owner, 2026-11-20: "it's not that hard to draw a sun with all its beams"): every class a
cover uses has its colour in the style sheet, and every sun has all its eight beams.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service, tips
from cps.web import Config, covers, create_app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 7, 0, tzinfo=UTC)
SHAPES = {"svg", "g", "path", "circle", "rect", "ellipse"}
CLASSES = {
    *("g", "sh", "p", "p2", "p2l", "ln", "lnd", "ink", "inkl", "tf", "tl", "a", "a2", "a3", "al", "wl", "wf"),
    *("y", "y2", "yl", "r", "r2", "rl", "gr", "gr2", "gr2l", "pu", "sk", "pk", "pkl", "pk2l", "bd"),
    *("br", "br2", "sn", "hr", "nt", "m", "h"),
}
# How each unit may be worded in a tip's English text.
UNITS = {
    "studies": ("stud",),
    "experiments": ("experiment",),
    "trials": ("trial",),
    "tests": ("test",),
    "people": ("people", "adults"),
    "students": ("student",),
    "days": ("days",),
}


def test_every_tip_has_a_cover_in_the_themes_classes():
    keys = {t.key for t in tips.TIPS}
    assert set(covers.COVERS) == keys and len(keys) == 26
    for key in keys:
        svg = str(covers.cover(key))
        root = ET.fromstring(svg)
        assert root.get("aria-hidden") == "true" and root.get("viewBox") == "0 0 300 112", key
        assert root.get("class") == "tip-cover"
        for node in root.iter():
            tag = node.tag.split("}")[-1]
            assert tag in SHAPES, (key, tag)
            assert "style" not in node.attrib, key
            assert node is root or "fill" not in node.attrib, key  # colours are classes
            assert set((node.get("class") or "").split()) <= CLASSES | {"tip-cover"}, (key, node.get("class"))
        # A number belongs in the caption, translated: none is drawn as text.
        assert "<text" not in svg, key
    assert str(covers.cover("no-such-tip")) == ""


def test_every_number_under_a_picture_is_its_tips_own():
    keys = {t.key: t for t in tips.TIPS}
    assert set(tips.FIGURES) <= set(keys)
    for key, (unit, *numbers) in tips.FIGURES.items():
        why = keys[key].why
        for n in numbers:
            assert re.search(rf"(?<![\d,]){n}(?![\d,])", why), (key, n)
        assert any(word in why for word in UNITS[unit]), (key, unit)
    assert "mindset" not in tips.FIGURES  # two meta-analyses: no one number to draw
    assert service.tip_figure("habit") == "18 to 254 days"
    assert service.tip_figure("track") == "138 studies"
    assert service.tip_figure("self-test") == ""


@pytest.fixture
def web(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


def test_the_page_covers_each_tip_with_its_number_and_folds_the_rest(web, tmp_path):
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        tasks=[],
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(tmp_path, service.finish_first_run(sub))
    page = web.get(f"/p/{sub.token}/tips?topic=habits").text
    assert page.count('class="tip-cover"') == 4 and page.count('<figure class="tip-cover-wrap">') == 4
    captions = re.findall(r"<figcaption>([^<]+)</figcaption>", page)
    assert captions == ["138 studies", "94 tests", "18 to 254 days"]
    assert page.count('<details class="tip-more">') == 4 and "Limits, in this app, sources" in page
    web.post(f"/p/{sub.token}/language", data={"lang": "fr"})
    french = web.get(f"/p/{sub.token}/tips?topic=habits").text
    assert re.findall(r"<figcaption>([^<]+)</figcaption>", french) == [
        "138 études",
        "94 tests",
        "18 à 254 jours",
    ]


def test_the_caption_stays_on_its_cover():
    """AUDIT 113: on a phone, "128 experiments" ran out of D160's 68px tile into the title.
    The caption now has its own band under the scene: as wide as the cover at most, wrapped
    if it must, and never laid over the drawing."""
    css = (Path(__file__).resolve().parent.parent / "src/cps/web/static/style.css").read_text()
    rule = re.search(r"\.tip-cover-wrap figcaption \{([^}]*)\}", css)
    assert (
        rule and "position: absolute" not in rule.group(1) and "max-width: calc(100% - 20px)" in rule.group(1)
    )
    assert "white-space: nowrap" not in rule.group(1)
    assert re.search(r"\.tip-cover-wrap \{[^}]*overflow: hidden", css)
    assert ".tip-thumb" not in css  # D160's tile is gone


def test_every_colour_a_cover_uses_is_in_the_style_sheet_and_every_sun_is_whole():
    css = (Path(__file__).resolve().parent.parent / "src/cps/web/static/style.css").read_text()
    used = set()
    for key in covers.COVERS:
        for node in ET.fromstring(str(covers.cover(key))).iter():
            used |= set((node.get("class") or "").split()) - {"tip-cover"}
    for cls in used:
        assert re.search(rf"\.tip-cover \.{cls}\b[^{{]*\{{", css), cls
    # D164's sun had six beams; a sun is drawn whole, its eight beams evenly round it.
    for key in ("caffeine", "sleep-regular", "reward-memory", "effort-reward"):
        beams = [
            node.get("d", "")
            for node in ET.fromstring(str(covers.cover(key))).iter()
            if node.get("class") == "yl" and node.get("d", "").count("M") == 8
        ]
        assert len(beams) == 1 and beams[0].count("L") == 8, key
