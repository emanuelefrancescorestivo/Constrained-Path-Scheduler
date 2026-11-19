"""
D160: each study tip with a picture, a drawing of what its advice asks, and under it the
study's number where it gives one (the owner, 2026-11-19: "a mix of A and B, especially
B"). What must hold: every tip has a drawing and no drawing is left without a tip; each is
plain SVG in the app's classes, with no inline style (the Content-Security-Policy); every
number under a picture is one its tip's own words state; the page shows them, translated.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import service, tips
from cps.web import Config, create_app, thumbs

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 7, 0, tzinfo=UTC)
SHAPES = {"svg", "path", "circle", "rect", "line", "polyline", "ellipse"}
CLASSES = {"a", "f", "w", "s", "m"}
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


def test_every_tip_has_a_drawing_in_the_apps_classes():
    keys = {t.key for t in tips.TIPS}
    assert set(thumbs.DRAWINGS) == keys and len(keys) == 26
    for key in keys:
        svg = str(thumbs.drawing(key))
        root = ET.fromstring(svg)
        assert root.get("aria-hidden") == "true" and root.get("viewBox") == "0 0 64 64", key
        for node in root.iter():
            tag = node.tag.split("}")[-1]
            assert tag in SHAPES, (key, tag)
            assert "style" not in node.attrib, key
            assert set((node.get("class") or "").split()) <= CLASSES | {"tip-art"}, (key, node.get("class"))
    assert str(thumbs.drawing("no-such-tip")) == ""


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


def test_the_page_draws_each_tip_with_its_number_and_folds_the_rest(web, tmp_path):
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
    assert page.count('class="tip-art"') == 4
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
