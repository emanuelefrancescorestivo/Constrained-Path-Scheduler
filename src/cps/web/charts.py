"""
The geometry of the Trends page's charts (DECISIONS.md D22): numbers in, SVG
coordinates out. Layout only; what the numbers are is `cps.service`'s.

The plots are drawn in a fixed box and stretched to the page's width
(`preserveAspectRatio="none"`); lines keep their width through the stylesheet's
`vector-effect: non-scaling-stroke`, and every text (axis, values, legend) is HTML
beside the SVG, so nothing written is stretched. The Content-Security-Policy allows
no inline style: positions are SVG attributes, colours are classes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

WIDTH = 600
HEIGHT = 160
MAX_BAR = 24  # the widest column, in the box's units (marks-and-anatomy: <= 24 px)
RADIUS = 4  # the rounded end of a column; its baseline end stays square


def nice_top(value: float) -> float:
    """The axis's top: the value rounded up to 1, 2 or 5 times a power of ten."""
    if value <= 0:
        return 1.0
    power = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 5, 10):
        if value <= step * power:
            return float(step * power)
    return float(10 * power)


def _column(x: float, width: float, height: float) -> str:
    """A column from the baseline up, its top corners rounded."""
    top = HEIGHT - height
    r = min(RADIUS, width / 2, height)
    return (
        f"M{x:.1f},{HEIGHT} V{top + r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} "
        f"H{x + width - r:.1f} Q{x + width:.1f},{top:.1f} {x + width:.1f},{top + r:.1f} V{HEIGHT} Z"
    )


def _y(value: float, top: float) -> float:
    return HEIGHT - HEIGHT * max(0.0, value) / top


def _path(points: Sequence[tuple[float, float | None]], top: float) -> str:
    """A line through the points, broken where a value is missing."""
    parts, pen = [], "M"
    for x, value in points:
        if value is None:
            pen = "M"
            continue
        parts.append(f"{pen}{x:.1f},{_y(value, top):.1f}")
        pen = "L"
    return " ".join(parts)


def _labels(labels: Sequence[str]) -> list[str]:
    """At most about seven labels under the axis; the others left blank."""
    every = max(1, math.ceil(len(labels) / 7))
    last = len(labels) - 1
    return [x if (last - i) % every == 0 else "" for i, x in enumerate(labels)]


def columns(
    values: Sequence[float],
    labels: Sequence[str],
    *,
    current: Sequence[bool] = (),
    average: Sequence[float] | None = None,
    limit: float | None = None,
) -> dict:
    """Columns per week, the current one marked, with a trailing average and a limit
    on the same axis (never a second axis)."""
    n = max(len(values), 1)
    top = nice_top(max([*values, *(average or ()), limit or 0.0, 0.0]) * 1.08)
    slot = WIDTH / n
    width = min(MAX_BAR, slot * 0.62)
    bars = []
    for i, value in enumerate(values):
        x = slot * i + (slot - width) / 2
        height = HEIGHT * max(0.0, value) / top
        bars.append(
            {
                "d": _column(x, width, height) if height > 0.5 else "",
                "slot_x": round(slot * i, 1),
                "slot_w": round(slot, 1),
                "current": bool(current[i]) if i < len(current) else False,
            }
        )
    centres = [slot * i + slot / 2 for i in range(len(values))]
    return {
        "w": WIDTH,
        "h": HEIGHT,
        "top": top,
        "bars": bars,
        "average": _path(list(zip(centres, average, strict=True)), top) if average else "",
        "limit": round(_y(limit, top), 1) if limit else None,
        "labels": _labels(labels),
        "grid": [0.0, HEIGHT / 2, float(HEIGHT)],
    }


def line(values: Sequence[float | None], labels: Sequence[str], *, top: float = 100.0) -> dict:
    """A line over weeks, broken where a week has no value, with a wash under it."""
    n = max(len(values), 1)
    slot = WIDTH / n
    centres = [slot * i + slot / 2 for i in range(len(values))]
    d = _path(list(zip(centres, values, strict=True)), top)
    area = ""
    if len(values) >= 2 and all(v is not None for v in values):  # a wash only under an unbroken line
        area = f"{d} L{centres[-1]:.1f},{HEIGHT} L{centres[0]:.1f},{HEIGHT} Z"
    return {
        "w": WIDTH,
        "h": HEIGHT,
        "top": top,
        "d": d,
        "area": area,
        "slots": [{"x": round(slot * i, 1), "w": round(slot, 1)} for i in range(len(values))],
        "labels": _labels(labels),
        "grid": [0.0, HEIGHT / 2, float(HEIGHT)],
    }


def sparkline(values: Sequence[float], *, top: float = 100.0) -> dict:
    """A small line of a few points, for the exam forecast's path."""
    n = len(values)
    if n == 0:
        return {"d": "", "w": WIDTH, "h": HEIGHT}
    if n == 1:
        y = _y(values[0], top)
        return {"d": f"M0,{y:.1f} L{WIDTH},{y:.1f}", "w": WIDTH, "h": HEIGHT}
    step = WIDTH / (n - 1)
    return {"d": _path([(step * i, v) for i, v in enumerate(values)], top), "w": WIDTH, "h": HEIGHT}


# D100: the climb on Progress. Its line is drawn in a box stretched to the card's width
# only: the height is in pixels, so flags and words placed at a share of the width and
# a number of pixels down keep their shape at every width.
CLIMB_W = 1000
CLIMB_H = 150
CLIMB_TOP = 40  # room above the highest point for a flag
CLIMB_BASE = 142
FLAGS_SHOWN = 4  # the latest flags; older ones are counted, not drawn
FLAG_GAP = 9.0  # per cent of the width: a flag closer than this to the next is left out


def climb(totals: Sequence[float], passed: Sequence[tuple[int, float]], next_flag: int | None) -> dict:
    """The running total rising from nothing at the start, a wash under it, the latest
    flags passed where they were passed, the next flag's level and the summit, now."""
    n = max(len(totals), 1)
    top = float(max([next_flag or 0, *totals, 1]))

    def y(value: float) -> float:
        return round(CLIMB_BASE - (CLIMB_BASE - CLIMB_TOP) * max(0.0, value) / top, 1)

    points = [(0.0, y(0.0)), *((CLIMB_W * (i + 1) / n, y(t)) for i, t in enumerate(totals))]
    d = "M" + " L".join(f"{x:.1f},{v}" for x, v in points)
    shown: list[dict] = []
    for m, at in reversed(passed[-FLAGS_SHOWN * 2 :]):
        x = round(100 * at / n, 1)
        if len(shown) == FLAGS_SHOWN or (shown and shown[-1]["x"] - x < FLAG_GAP):
            continue
        shown.append({"m": m, "x": x, "y": y(m), "end": x > 82})
    return {
        "w": CLIMB_W,
        "h": CLIMB_H,
        "base": CLIMB_BASE,
        "d": d,
        "area": f"{d} L{CLIMB_W},{CLIMB_BASE} L0,{CLIMB_BASE} Z",
        "flags": list(reversed(shown)),
        "summit": y(totals[-1]) if totals else y(0.0),
        "next": {"m": next_flag, "y": y(next_flag)} if next_flag else None,
        "hits": [{"x": round(CLIMB_W * i / n, 1), "w": round(CLIMB_W / n, 1)} for i in range(len(totals))],
    }
