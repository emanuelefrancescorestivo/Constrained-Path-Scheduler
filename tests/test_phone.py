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


def test_the_calendar_has_no_tip_line_over_its_grid():
    # The line AUDIT 100 fixed is gone: the owner found it pushed the grid down (2026-11-19).
    plan = (WEB / "templates" / "plan.html").read_text(encoding="utf-8")
    assert "why_line" not in plan and "why-line" not in CSS


def test_the_study_maps_card_is_a_block():
    community = (WEB / "templates" / "community.html").read_text(encoding="utf-8")
    assert 'class="card link-card section map-link"' in community
    assert "display: grid" in _rule(".map-link")


def test_today_scrolls_with_the_page():
    assert "height: auto" in _rule('.plan[data-tab="today"]')
    assert "overflow: visible" in _rule('.plan[data-tab="today"] .panel')
