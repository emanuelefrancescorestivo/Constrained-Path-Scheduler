"""
D149: the owner's screenshots of 2026-11-19 ("too many blank void empty spaces, some pads
don't separate well", "this whole upper bar is annoying"). What must hold: on a laptop the
top bar is the sidebar's head and the page starts at the window's top; Today's widgets are
packed by its script into the space under shorter ones, and without the script the grid is as
before; a widget's chips, the first run's year and the feed under the map keep their space; a
select's options are readable in either theme. The layout itself was checked in Chromium; these
keep the rules that make it.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "src" / "cps" / "web"
CSS = (WEB / "static" / "style.css").read_text(encoding="utf-8")
APP = (WEB / "static" / "app.js").read_text(encoding="utf-8")


def _rule(selector: str, css: str = CSS) -> str:
    """Every declaration the style sheet gives `selector` alone, in order."""
    found = re.findall(r"(?:^|\n|\}\s*)\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert found, selector
    return " ".join(found)


def _laptop() -> str:
    """The sidebar's block, from 1100px."""
    start = CSS.index("body.has-sidebar { padding-left: var(--sidebar);")
    return CSS[start : CSS.index("\n}\n", start)]


def test_on_a_laptop_the_top_bar_is_the_sidebars_head():
    laptop = _laptop()
    body = _rule("body.has-sidebar", laptop)
    assert "--topbar: 0px" in body and "--side-head:" in body
    bar = _rule("body.has-sidebar > .topbar", laptop)
    assert "position: fixed" in bar and "width: var(--sidebar)" in bar and "height: var(--side-head)" in bar
    assert "top: var(--side-head)" in _rule("body.has-sidebar .sidebar", laptop)
    # New task under the name and Spark, the width of the sidebar.
    assert "flex: 1 0 100%" in _rule('body.has-sidebar > .topbar > .btn[popovertarget="quick-add"]', laptop)


def test_todays_widgets_are_packed_by_the_script_only():
    assert "--hub-gap: 24px" in _rule(".hub") and "gap: var(--hub-gap) 18px" in _rule(".hub")
    packed = _rule(".hub.is-packed")
    assert "grid-auto-rows: 2px" in packed and "row-gap: 0" in packed
    # The script adds the class and each widget's span, in two or three columns only.
    assert "const HUB_ROW = 2;" in APP and 'hub.classList.add("is-packed")' in APP
    assert "if (columns < 2)" in APP and "gridRowEnd = `span ${" in APP
    assert "new ResizeObserver" in APP and "packHub();" in APP


def test_cards_and_fields_keep_their_space():
    assert "padding: 12px 14px" in _rule(".hub .strip, body[data-sky] .hub .strip")
    first_run = (WEB / "templates" / "first_run.html").read_text(encoding="utf-8")
    assert '<fieldset class="run-years bare">' in first_run
    assert "margin-top: 12px" in _rule(".map-link + .feed")
    options = _rule("select option, select optgroup")
    assert "background-color: var(--surface)" in options and "color: var(--text)" in options
    note = (WEB / "templates" / "weekly_note.html").read_text(encoding="utf-8")
    assert 'class="for-you-title"' in note and "display: block" in _rule(".weekly-for-you .for-you-title")
