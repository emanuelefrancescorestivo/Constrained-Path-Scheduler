"""
The app's icon (DECISIONS.md D74): the week as a ring of seven days, Monday at the
top and the days going clockwise. The days before today are filled, today ends in
a gold light, the days to come are faint.

A plan's pages show it live, in the top bar and in the browser's tab: today's
weekday, in the colours of the sky of the hour (D73), and `ui.js` swaps it as the
day goes on. What a phone keeps on its home screen cannot change once installed, so
the files in static/icons/ are this drawing made once, on a Friday: white for light
mode, graphite for dark. `python -c "from cps.web import icon; icon.main()"` writes
the SVGs there; the PNGs are those drawings rendered by a browser at their sizes.

Drawn with SVG attributes only: an SVG shown as an image runs no script and loads
nothing. The one style element is icon.svg's, which picks the light or the dark
drawing by the device's mode.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

DAYS = range(1, 8)  # Monday 1 ... Sunday 7, as `date.isoweekday()`
STATIC_DAY = 5  # the installed icon is a Friday: four days done, today, two to come
CORNER = 114.5  # the rounded tile's radius on 512, Apple's 22.37 %


@dataclass(frozen=True)
class Look:
    top: str  # the tile, from its top
    bottom: str  # to its bottom
    glow: str  # a soft light in the tile's upper left
    glow_strength: float
    ring: tuple[tuple[float, str], ...]  # the days' colours, Monday (0) to Sunday (1)
    faint: str  # the days to come
    faint_strength: float
    shadow: str  # under the days
    shadow_strength: float


NIGHT_RING = ((0.0, "#4f7bff"), (0.45, "#8e6bff"), (0.78, "#ff7fa8"), (1.0, "#ffc451"))

# A look per sky of the hour (service.SKIES, D73), and the installed icon's two.
LOOKS = {
    "morning": Look("#2b86d9", "#0b3c74", "#a6e4ff", 0.35,
                    ((0.0, "#6fd6ff"), (0.5, "#4fe3c4"), (0.85, "#c2f27a"), (1.0, "#ffd25c")),
                    "#ffffff", 0.12, "#000000", 0.45),
    "afternoon": Look("#5566ff", "#2a1d9e", "#c0caff", 0.4,
                      ((0.0, "#e2e8ff"), (0.6, "#ffffff"), (1.0, "#ffe39a")),
                      "#ffffff", 0.13, "#000000", 0.45),
    "evening": Look("#4a1a7e", "#16062a", "#ff7ab8", 0.3,
                    ((0.0, "#8a6bff"), (0.4, "#d65cff"), (0.75, "#ff6f8e"), (1.0, "#ffb23f")),
                    "#ffffff", 0.15, "#000000", 0.45),
    "night": Look("#1b2457", "#070a1e", "#6f8cff", 0.3, NIGHT_RING, "#ffffff", 0.15, "#000000", 0.45),
    "light": Look("#ffffff", "#eceef6", "#ffffff", 0.0, NIGHT_RING, "#1d2550", 0.12, "#2a2a60", 0.22),
    "dark": Look("#2c2c30", "#0b0b0d", "#ffffff", 0.12,
                 ((0.0, "#b4b4bc"), (0.6, "#f2f2f5"), (1.0, "#ffe39a")),
                 "#ffffff", 0.12, "#000000", 0.45),
}  # fmt: skip

CENTRE, RADIUS, WIDTH = 256, 150, 50
SLOT = 360 / 7
GAP = 13  # degrees cut from each end of a day's slot; the round caps take most of it back


def _colour(ring: tuple[tuple[float, str], ...], t: float) -> str:
    """The colour at `t` along the ring's stops."""
    for (t0, c0), (t1, c1) in pairwise(ring):
        if t <= t1:
            u = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            a = [int(c0[i : i + 2], 16) for i in (1, 3, 5)]
            b = [int(c1[i : i + 2], 16) for i in (1, 3, 5)]
            return "#" + "".join(f"{round(x + (y - x) * u):02x}" for x, y in zip(a, b, strict=True))
    return ring[-1][1]


def _point(degrees: float) -> tuple[float, float]:
    a = math.radians(degrees)
    return CENTRE + RADIUS * math.cos(a), CENTRE + RADIUS * math.sin(a)


def _drawing(day: int, look: Look, rounded: bool, ids: str) -> tuple[str, str]:
    """The defs and the body of one drawing; `ids` keeps two drawings in one file apart."""
    rx = f' rx="{CORNER}"' if rounded else ""
    defs = (
        f'<linearGradient id="{ids}bg" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{look.top}"/>'
        f'<stop offset="1" stop-color="{look.bottom}"/></linearGradient>'
        f'<radialGradient id="{ids}hl" cx=".3" cy=".08" r=".75">'
        f'<stop offset="0" stop-color="{look.glow}" stop-opacity="{look.glow_strength}"/>'
        f'<stop offset="1" stop-color="{look.glow}" stop-opacity="0"/></radialGradient>'
        f'<radialGradient id="{ids}gold" cx=".38" cy=".32" r=".75"><stop offset="0" stop-color="#fff6d6"/>'
        f'<stop offset=".45" stop-color="#ffd25c"/><stop offset="1" stop-color="#ff9d1c"/></radialGradient>'
        f'<radialGradient id="{ids}halo"><stop offset="0" stop-color="#ffc94a" stop-opacity=".75"/>'
        f'<stop offset=".45" stop-color="#ffb02e" stop-opacity=".28"/>'
        f'<stop offset="1" stop-color="#ff9d1c" stop-opacity="0"/></radialGradient>'
        f'<filter id="{ids}sh" x="-30%" y="-30%" width="160%" height="170%">'
        f'<feDropShadow dx="0" dy="10" stdDeviation="{12 if look.shadow_strength < 0.3 else 14}" '
        f'flood-color="{look.shadow}" flood-opacity="{look.shadow_strength}"/></filter>'
    )
    days = []
    for i in range(7):
        start = -90 + SLOT * i + GAP
        (x0, y0), (x1, y1) = _point(start), _point(start + SLOT - 2 * GAP)
        arc = f"M{x0:.1f} {y0:.1f}A{RADIUS} {RADIUS} 0 0 1 {x1:.1f} {y1:.1f}"
        if i < day:
            days.append(f'<path d="{arc}" stroke="{_colour(look.ring, i / 6)}"/>')
        else:
            days.append(f'<path d="{arc}" stroke="{look.faint}" stroke-opacity="{look.faint_strength}"/>')
    hx, hy = _point(-90 + SLOT * (day - 1) + SLOT - GAP)
    halo = 2.3 if look.glow_strength else 1.9  # less halo on the white tile, where it reads as a smudge
    body = (
        f'<rect width="512" height="512"{rx} fill="url(#{ids}bg)"/>'
        f'<rect width="512" height="512"{rx} fill="url(#{ids}hl)"/>'
        f'<g filter="url(#{ids}sh)" fill="none" stroke-width="{WIDTH}" stroke-linecap="round">'
        f"{''.join(days)}</g>"
        f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="{27 * halo:.1f}" fill="url(#{ids}halo)"/>'
        f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="27" fill="url(#{ids}gold)"/>'
        f'<circle cx="{hx - 8.1:.1f}" cy="{hy - 8.6:.1f}" r="8.6" fill="#fff" opacity=".85"/>'
    )
    return defs, body


def _check(day: int, look: str) -> Look:
    if day not in DAYS:
        raise ValueError(f"a day of the week is 1 to 7, not {day!r}")
    if look not in LOOKS:
        raise ValueError(f"no look named {look!r}")
    return LOOKS[look]


def week(day: int, look: str, *, rounded: bool = True) -> str:
    """The icon for a day (1 Monday ... 7 Sunday) in a look: a sky (D73), "light" or
    "dark". `rounded` draws the tile's corners (a tab, the top bar); a phone's home
    screen cuts its own, so the icons it is given are square."""
    defs, body = _drawing(day, _check(day, look), rounded, "k")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" '
        f'aria-label="{_label(day)}"><defs>{defs}</defs>{body}</svg>\n'
    )


def _label(day: int) -> str:
    return f"The week as a ring of seven days, day {day} lit"


def switching() -> str:
    """icon.svg: the installed icon's drawing, white in light mode and graphite in
    dark mode (where an SVG icon is used: a tab, the home page's top bar)."""
    light_defs, light = _drawing(STATIC_DAY, LOOKS["light"], True, "l")
    dark_defs, dark = _drawing(STATIC_DAY, LOOKS["dark"], True, "d")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" '
        f'aria-label="{_label(STATIC_DAY)}">'
        "<style>.dark{display:none}"
        "@media (prefers-color-scheme: dark){.light{display:none}.dark{display:inline}}</style>"
        f'<defs>{light_defs}{dark_defs}</defs><g class="light">{light}</g><g class="dark">{dark}</g></svg>\n'
    )


# The files in static/icons/ this module writes; the PNGs are rendered from these
# drawings (see the module's docstring).
STATIC = {
    "icon.svg": switching,
    "maskable.svg": lambda: week(STATIC_DAY, "light", rounded=False),
}


def main(folder: Path | None = None) -> None:
    """Write the static SVGs (into static/icons/ unless told otherwise)."""
    folder = folder or Path(__file__).parent / "static" / "icons"
    for name, draw in STATIC.items():
        (folder / name).write_text(draw(), encoding="utf-8")
