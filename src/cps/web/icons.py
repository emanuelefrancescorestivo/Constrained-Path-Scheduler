"""
The app's icons (DECISIONS.md D139): Lucide's, drawn inline by the server.

The owner found the hand-drawn icons imprecise (2026-11-16: the ticks of Tasks against
their box, a Settings that read as a sun). Lucide is an open set (ISC licence, with a
part from Feather under MIT; `static/vendor/lucide/LICENSE`) drawn on one 24-unit grid
with one stroke width and round ends, so every icon in the app has the same weight.
Only the icons the app uses are shipped, copied unchanged from lucide-static 0.460.0.

Rejected: an icon font (a request and a flash of letters before it loads), a sprite
read by `<use href>` (one more request, and an icon missing until it arrives), the
whole set (1,500 files nobody asked for). Inline, an icon is part of the page and is
there without a script, under the Content-Security-Policy, offline and in print.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

from markupsafe import Markup

from cps import service

ICONS = Path(__file__).resolve().parent / "static" / "vendor" / "lucide"

# Each place's picture (D99) by the Lucide icon drawn on its tile (D139).
ART: dict[str, str] = {
    # Under You (D99), and the explaining pages (D113, D116).
    "progress": "chart-column",
    "cards": "layers",
    "tasks": "list-checks",
    "focus": "timer",
    "tips": "lightbulb",
    "place": "map-pin",
    "spark": "sparkles",  # the first day on Today (D171)  # D169: the study map's row on Focus
    "science": "flask-conical",
    "garden": "sprout",
    "code": "code",
    "settings": "settings",
    "calendar": "calendar",
    "feedback": "message-square-heart",
    "connections": "link",  # D175
    "rewards": "gift",  # D206
    # Settings' rows (D103).
    "week": "calendar-days",
    "key": "key-round",
    "bell": "bell",
    "palette": "palette",
    "shield": "shield-check",
    # The insights (D80).
    "pace": "gauge",
    "alarm": "alarm-clock",
    "target": "target",
    "daytime": "sun-moon",
    "crystal": "telescope",
    # Study tips' topics, the coach and the weekly note (D91, D65, D64).
    "group": "users-round",
    "cap": "graduation-cap",
    "brain": "brain",
    "trophy": "trophy",
    "repeat": "repeat",
    "hourglass": "hourglass",
    "moon": "moon",
    "coach": "message-circle-question",
    "letter": "mail",
    # What a diary session was, read from its title (D100).
    "paper": "file-text",
    "exercises": "pencil-line",
    "summary": "notebook-pen",
    "reading": "book-open-text",
    "lecture": "presentation",
    "project": "folder-kanban",
    "study": "book-marked",
}

# The crests' pictures (D189): each badge's own icon, as `service` lists them.
CRESTS: frozenset[str] = service.CREST_ICONS

# The bars, the sidebar and the buttons.
PLACES: tuple[str, ...] = (
    "sun",
    "calendar",
    "timer",
    "layers",
    "list-checks",
    "chart-column",
    "book-open",
    "users",
    "map-pin",
    "lightbulb",
    "circle-user",
    "settings",
    "plus",
    "chevron-left",
    "chevron-right",
    "thumbs-up",
    "message-circle",
    "upload",
    "calendar-range",
    "play",
    "circle-check",
    "graduation-cap",
    "globe",  # the front page's language (D193)
    "trash-2",  # a to-do line's bin (D202)
    "grip-vertical",  # a to-do line's grip, to drag it
    "table",  # the to-do page's views
    "kanban",
    "layout-template",  # the to-do page's templates
    "lock",  # a reward still locked (D206)
)

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_INNER = re.compile(r"<svg\b[^>]*>(.*)</svg>", re.S)


@functools.cache
def _drawing(name: str) -> str:
    """The inside of one shipped icon: its paths, circles and rects, as Lucide wrote them."""
    if not _NAME.fullmatch(name):
        raise ValueError(f"not an icon name: {name!r}")
    found = _INNER.search((ICONS / f"{name}.svg").read_text(encoding="utf-8"))
    if found is None:
        raise ValueError(f"not an SVG: {name}")
    return re.sub(r"\s*\n\s*", "", re.sub(r"<!--.*?-->", "", found.group(1), flags=re.S)).strip()


def icon(name: str, cls: str = "") -> Markup:
    """One icon, inline. Decoration: hidden from screen readers, the words beside it say
    the same. Stroked in the text's colour, 2 units wide on Lucide's 24-unit grid."""
    css = f' class="{cls}"' if cls else ""
    return Markup(
        f'<svg{css} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
        f"{_drawing(name)}</svg>"
    )


# D174: each kind of a course's materials, as the page it came from says it.
MATERIAL: dict[str, str] = {
    "file": "file-text",
    "folder": "folder",
    "page": "book-open-text",
    "link": "link",
    "quiz": "list-checks",
}


def shipped() -> set[str]:
    """Every icon the app may draw."""
    return set(ART.values()) | set(CRESTS) | set(PLACES) | set(MATERIAL.values())
