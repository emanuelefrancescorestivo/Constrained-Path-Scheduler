"""
AUDIT 100: what the owner's iPhone showed broken after D140. What must hold: the calendar's tip,
opened, lies over the grid and leaves the grid its height; its line sits in a box of its own
inside the summary (Safari gives a flex summary the wrong height); the study map's card is a
block, not an inline link wrapped around its lines; Today scrolls with the page, not inside a box
of the old plan screen's height. The layout itself was checked in a browser; these keep the
rules that make it.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "src" / "cps" / "web"
CSS = (WEB / "static" / "style.css").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    """Every declaration the style sheet gives `selector` alone, in order."""
    found = re.findall(r"(?:^|\n|\}\s*)" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert found, selector
    return " ".join(found)


def test_the_calendars_tip_opens_over_the_grid_and_its_line_is_not_a_flex_summary():
    why = (WEB / "templates" / "_why.html").read_text(encoding="utf-8")
    assert '<summary><span class="why-line-head">' in why
    assert "display: block" in _rule(".why-line > summary")
    assert "display: flex" in _rule(".why-line-head")
    body = _rule(".board > .why-line[open] .why-line-body")
    assert "position: absolute" in body and "top: calc(100% - 4px)" in body
    assert "position: relative" in _rule(".board")


def test_the_study_maps_card_is_a_block():
    community = (WEB / "templates" / "community.html").read_text(encoding="utf-8")
    assert 'class="card link-card section map-link"' in community
    assert "display: grid" in _rule(".map-link")


def test_today_scrolls_with_the_page():
    assert "height: auto" in _rule('.plan[data-tab="today"]')
    assert "overflow: visible" in _rule('.plan[data-tab="today"] .panel')
