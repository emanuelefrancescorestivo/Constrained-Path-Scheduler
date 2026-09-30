#!/usr/bin/env python3
"""
The README's figures, drawn from the code that produces their numbers.

    python benchmarks/figures.py                # all three, a few minutes
    python benchmarks/figures.py --only spacing # the quick one

Writes docs/figures/<name>-light.svg and -dark.svg: plain SVG, no plotting
library, so no new dependency. Every number drawn is computed here or by the
benchmark it comes from, and printed, so a figure can be checked against a run
(CLAUDE.md invariant 4).

1. spacing: what the memory model predicts for one week of lectures under three
   ways of using the same effort. A model's prediction (FSRS-4.5 with population
   weights), not a measurement of students.
2. planner-vs-schedulers: benchmarks/replanning.py, 100 seeds, with standard errors.
3. search-vs-rules: benchmarks/rule_vs_planner.py, the semester, one run.
4. pipeline: how the pieces fit together; a diagram, no numbers.

Colours are the first three slots of a categorical palette checked for colour-blind
separation in both themes; every series is also named in text, never by colour alone.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks"))

from cps.console import ensure_utf8_output  # noqa: E402
from cps.memory import Grade, MemoryState, initial_state, retrievability, review  # noqa: E402

OUT = ROOT / "docs" / "figures"
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str
    text: str
    muted: str
    grid: str
    series: tuple[str, str, str]


THEMES = (
    Theme("light", "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", ("#2a78d6", "#eb6834", "#1baf7a")),
    Theme("dark", "#1a1a19", "#ffffff", "#c3c2b7", "#383835", ("#3987e5", "#d95926", "#199e70")),
)


class Svg:
    """Just enough SVG: shapes and text, escaped, in one theme."""

    def __init__(self, width: int, height: int, theme: Theme, title: str) -> None:
        self.width, self.height, self.theme = width, height, theme
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}" '
            f'font-family="{FONT}">',
            f"<title>{escape(title)}</title>",
            f'<rect width="{width}" height="{height}" rx="12" fill="{theme.surface}"/>',
        ]

    def add(self, markup: str) -> None:
        self.parts.append(markup)

    def text(
        self,
        x: float,
        y: float,
        value: str,
        *,
        size: int = 12,
        colour: str | None = None,
        weight: int = 400,
        anchor: str = "start",
    ) -> None:
        self.add(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
            f'fill="{colour or self.theme.text}" text-anchor="{anchor}">{escape(value)}</text>'
        )

    def line(self, x1: float, y1: float, x2: float, y2: float, colour: str, width: float = 1) -> None:
        self.add(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{colour}" '
            f'stroke-width="{width}" stroke-linecap="round"/>'
        )

    def path(self, points: Sequence[tuple[float, float]], colour: str, width: float = 2) -> None:
        d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(points))
        self.add(
            f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{width}" '
            'stroke-linejoin="round" stroke-linecap="round"/>'
        )

    def dot(self, x: float, y: float, colour: str, r: float = 4.5) -> None:
        # A ring in the surface colour keeps a marker legible where it sits on a line.
        self.add(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{colour}" '
            f'stroke="{self.theme.surface}" stroke-width="2"/>'
        )

    def bar(self, x: float, y: float, length: float, thickness: float, colour: str) -> None:
        """A horizontal bar from the baseline at x: square at the baseline, a 4 px
        rounded end."""
        if length <= 0:
            return
        r = min(4.0, length / 2, thickness / 2)
        self.add(
            f'<path d="M{x:.1f},{y:.1f} h{length - r:.1f} a{r},{r} 0 0 1 {r},{r} '
            f'v{thickness - 2 * r:.1f} a{r},{r} 0 0 1 {-r},{r} h{-(length - r):.1f} z" fill="{colour}"/>'
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join([*self.parts, "</svg>", ""]), encoding="utf-8")


def header(svg: Svg, title: str, subtitle: str, second: str = "") -> None:
    svg.text(24, 34, title, size=16, weight=650)
    svg.text(24, 54, subtitle, size=12, colour=svg.theme.muted)
    if second:
        svg.text(24, 70, second, size=12, colour=svg.theme.muted)


def footer(svg: Svg, note: str) -> None:
    svg.text(24, svg.height - 16, note, size=11, colour=svg.theme.muted)


# --------------------------------------------------------------------------- #
# 1. Spacing and self-testing, as the memory model predicts them
# --------------------------------------------------------------------------- #

EXAM = 28.0  # the exam four weeks after the lectures
HORIZON = 112.0  # twelve weeks after the exam: when a next course builds on it


def spacing_data() -> dict:
    """One week of lectures, "seen once and shaky" (FSRS-4.5 after a first review
    graded Hard, as the product assumes of every week of lectures), then three ways
    of preparing an exam four weeks later. Spaced self-tests follow the assistant's
    rule: test yourself when predicted recall falls to 90%."""
    start = initial_state(Grade.HARD)

    def spaced(below: float = 0.9) -> list[float]:
        memory, last, days, t = start, 0.0, [], 0.0
        while t < EXAM - 1:
            t += 0.25
            if retrievability(t - last, memory.stability) <= below:
                memory = review(memory, t - last, Grade.GOOD)
                last = t
                days.append(t)
        return days

    strategies = {
        "spaced": spaced(),
        "crammed": [24.0, 25.0, 26.0, 27.0],
        "once": [],
    }
    curves = {}
    for name, days in strategies.items():
        memory, last = start, 0.0
        points, reviews = [], []
        steps = [i / 4 for i in range(int(HORIZON * 4) + 1)]
        pending = list(days)
        for t in steps:
            while pending and pending[0] <= t:
                memory = review(memory, pending[0] - last, Grade.GOOD)
                last = pending.pop(0)
                reviews.append((last, 1.0))
            points.append((t, retrievability(t - last, memory.stability)))
        final: MemoryState = memory
        curves[name] = {
            "days": days,
            "points": points,
            "reviews": reviews,
            "exam": retrievability(EXAM - last, final.stability),
            "end": retrievability(HORIZON - last, final.stability),
            "stability": final.stability,
        }
    return curves


def draw_spacing(curves: dict, theme: Theme) -> Svg:
    width, height = 760, 456
    svg = Svg(width, height, theme, "Predicted recall of one week of lectures under three study strategies")
    header(
        svg,
        "Spread-out self-testing keeps more, with fewer sessions",
        "Predicted recall of one week of lectures, examined four weeks later (FSRS-4.5 memory model).",
        f"Cramming is marginally ahead on exam day: {curves['crammed']['exam']:.1%} against "
        f"{curves['spaced']['exam']:.1%}.",
    )
    left, right, top, bottom = 64, width - 170, 112, height - 64
    lo, hi = 0.2, 1.0

    def x(day: float) -> float:
        return left + (right - left) * day / HORIZON

    def y(r: float) -> float:
        return bottom - (bottom - top) * (r - lo) / (hi - lo)

    for r in (0.2, 0.4, 0.6, 0.8, 1.0):
        svg.line(left, y(r), right, y(r), theme.grid)
        svg.text(left - 10, y(r) + 4, f"{r:.0%}", size=11, colour=theme.muted, anchor="end")
    for week in range(0, int(HORIZON) + 1, 14):
        svg.text(x(week), bottom + 20, f"week {week // 7}", size=11, colour=theme.muted, anchor="middle")
    svg.line(x(EXAM), top - 6, x(EXAM), bottom, theme.muted, 1)
    svg.text(x(EXAM) + 6, top - 4, "exam", size=11, colour=theme.muted)

    order = (
        ("spaced", theme.series[0], f"Spaced self-tests ({len(curves['spaced']['days'])} sessions)"),
        ("crammed", theme.series[1], f"Crammed ({len(curves['crammed']['days'])} sessions)"),
        ("once", theme.series[2], "Studied once"),
    )
    # The legend: identity in text, beside a key in the series colour.
    lx = left
    for _, colour, label in order:
        svg.line(lx, 92, lx + 18, 92, colour, 2.5)
        svg.text(lx + 24, 96, label, size=12)
        lx += 34 + 7.2 * len(label)
    ends = []
    for name, colour, _ in order:
        curve = curves[name]
        svg.path([(x(d), y(max(r, lo))) for d, r in curve["points"]], colour)
        for day, _ in curve["reviews"]:
            svg.dot(x(day), y(1.0), colour, 4)
        ends.append((y(curve["end"]), curve["end"], colour, name))
    # End labels: the value twelve weeks after the exam, pushed apart when close.
    ends.sort()
    placed: list[float] = []
    for ey, value, colour, name in ends:
        ly = max(ey, placed[-1] + 16) if placed else ey
        placed.append(ly)
        svg.dot(right, ey, colour, 4)
        label = {"spaced": "spaced", "crammed": "crammed", "once": "once"}[name]
        svg.text(right + 10, ly + 4, f"{value:.0%} {label}", size=12, weight=600)
    svg.text(right + 10, top - 4, "12 weeks after the exam", size=11, colour=theme.muted)
    footer(
        svg,
        "A model's prediction with population-default weights, not a measurement of students. "
        "Dots: study sessions. benchmarks/figures.py",
    )
    return svg


# --------------------------------------------------------------------------- #
# 2. The research planner against the schedulers students use
# --------------------------------------------------------------------------- #


def planner_data(seeds: int = 100) -> list[dict]:
    """benchmarks/replanning.py's comparison, its defaults: window 4, 100 seeds."""
    from datetime import date

    import numpy as np
    import replanning as rp

    from cps.calendar_io import load_availability
    from cps.plan import tile_free_time
    from cps.rolling import solve_deadlines

    grid, _ = load_availability(rp.TIMETABLE, date(2026, 3, 2), int(rp.EXAM_DAYS), "Europe/London")
    blocks = tile_free_time(grid, block_slots=3, max_blocks_per_day=2)
    continuation = solve_deadlines(rp.SUBJECTS, rp.RETENTION, rp.PENALTY)
    targets = continuation.targets
    runs = {
        "planner": [rp.planner(blocks, continuation, 4, np.random.default_rng(s)) for s in range(seeds)],
        "fixed-0.90": [
            rp.simulate_rule(blocks, targets, rp.greedy_fixed(0.90), np.random.default_rng(s))
            for s in range(seeds)
        ],
    }
    scan = {
        k: [rp.simulate_rule(blocks, targets, rp.every_k(k), np.random.default_rng(s)) for s in range(seeds)]
        for k in range(1, 8)
    }
    best = max(
        scan, key=lambda k: (sum(o.ready for o in scan[k]), -np.mean([len(o.sessions) for o in scan[k]]))
    )
    runs[f"every {best} day{'s' if best > 1 else ''}"] = scan[best]

    def stats(values: list[float]) -> tuple[float, float]:
        a = np.array(values, dtype=float)
        return float(a.mean()), float(a.std(ddof=1) / math.sqrt(len(a)))

    rows = []
    for name, outcomes in runs.items():
        p = sum(o.ready for o in outcomes) / len(outcomes)
        rows.append(
            {
                "name": name,
                "ready": (p, math.sqrt(p * (1 - p) / len(outcomes))),
                "blocks": stats([len(o.sessions) for o in outcomes]),
                "recall": stats([o.recall for o in outcomes]),
                "stability": stats([o.stability for o in outcomes]),
            }
        )
    return rows


PLANNER_LABELS = {
    "planner": "This planner (AO* search)",
    "fixed-0.90": "Review at 90% recall (Anki)",
}


def draw_planner(rows: list[dict], theme: Theme) -> Svg:
    width, height = 760, 400
    svg = Svg(
        width, height, theme, "The research planner against two common schedulers, 100 simulated students"
    )
    header(
        svg,
        "The planner's advantage: memory that lasts past the exam",
        "Not higher recall on exam day. Two subjects, a 21-day calendar, 100 simulated students; "
        "means ± 1 standard error",
    )
    panels = (
        ("ready", "Both subjects at target", lambda v: f"{v:.0%}", 1.0),
        ("stability", "Stability at the exam (days)", lambda v: f"{v:.1f}", None),
        ("recall", "Recall on exam day", lambda v: f"{v:.3f}", 1.0),
        ("blocks", "Study blocks used", lambda v: f"{v:.1f}", None),
    )
    label_w, pw, ph = 214, 238, 96
    positions = [
        (label_w + 24, 92),
        (label_w + 24 + pw + 40, 92),
        (label_w + 24, 92 + ph + 56),
        (label_w + 24 + pw + 40, 92 + ph + 56),
    ]
    thickness, gap = 18, 12
    for (key, title, fmt, fixed_max), (px, py) in zip(panels, positions, strict=True):
        svg.text(px, py - 12, title, size=12, weight=650)
        top = max(m + se for m, se in (r[key] for r in rows))
        scale_max = fixed_max or _nice(top * 1.15)
        svg.line(px, py, px, py + len(rows) * (thickness + gap) - gap + 2, theme.grid)
        for i, row in enumerate(rows):
            mean, se = row[key]
            y0 = py + 2 + i * (thickness + gap)
            length = (pw - 60) * mean / scale_max
            colour = theme.series[i]
            svg.bar(px, y0, length, thickness, colour)
            if se > 0:
                a = px + (pw - 60) * (mean - se) / scale_max
                b = px + (pw - 60) * (mean + se) / scale_max
                svg.line(a, y0 + thickness / 2, b, y0 + thickness / 2, theme.text, 1.2)
                svg.line(a, y0 + 4, a, y0 + thickness - 4, theme.text, 1.2)
                svg.line(b, y0 + 4, b, y0 + thickness - 4, theme.text, 1.2)
            end = px + (pw - 60) * (mean + se) / scale_max
            svg.text(end + 6, y0 + thickness / 2 + 4, fmt(mean), size=11)
        if px == label_w + 24:
            for i, row in enumerate(rows):
                y0 = py + 2 + i * (thickness + gap)
                svg.dot(24 + 5, y0 + thickness / 2, theme.series[i], 4.5)
                name = PLANNER_LABELS.get(row["name"], f"Review {row['name']}".replace("1 day", "day"))
                if row["name"] not in PLANNER_LABELS:
                    name += " (best interval)"
                svg.text(24 + 16, y0 + thickness / 2 + 4, name, size=12)
    footer(
        svg,
        "Stability target 21 days: recall of 90% if the preparation time passed again. "
        "benchmarks/replanning.py, benchmarks/figures.py",
    )
    return svg


def _nice(value: float) -> float:
    step = 10 ** math.floor(math.log10(value))
    for m in (1, 2, 2.5, 5, 10):
        if value <= m * step:
            return m * step
    return 10 * step


# --------------------------------------------------------------------------- #
# 3. Does the search earn its keep?
# --------------------------------------------------------------------------- #


def rules_data() -> dict:
    import rule_vs_planner

    return rule_vs_planner.results()


RULE_LABELS = (
    "Research planner (AO* search)",
    "Rule: least remembered topic",
    "Same rule, once recall ≤ 93%",
    "Review at 90% recall (Anki)",
)


def draw_rules(out: dict, theme: Theme) -> Svg:
    rows = out["rows"]
    width, height = 760, 290
    svg = Svg(width, height, theme, "The research planner against one-line rules on a semester")
    header(
        svg,
        "On a semester, a one-line rule matches the search",
        f"Six courses, {out['topics']} weekly topics, the same memory model; the product uses the rule, "
        "in a hundredth of a second",
    )
    label_w, pw = 210, 230
    thickness, gap = 18, 12
    panels = (
        ("sessions", "Study sessions", None, lambda r: f"{r['sessions']}"),
        ("ready", f"Topics at target (of {out['topics']})", out["topics"], lambda r: f"{r['ready']}"),
    )
    colour = theme.series[0]
    for p, (key, title, fixed, fmt) in enumerate(panels):
        px, py = label_w + 24 + p * (pw + 40), 100
        svg.text(px, py - 12, title, size=12, weight=650)
        scale_max = fixed or _nice(max(r[key] for r in rows) * 1.1)
        svg.line(px, py, px, py + len(rows) * (thickness + gap) - gap + 2, theme.grid)
        for i, row in enumerate(rows):
            y0 = py + 2 + i * (thickness + gap)
            length = (pw - 44) * row[key] / scale_max
            svg.bar(px, y0, length, thickness, colour)
            svg.text(px + length + 6, y0 + thickness / 2 + 4, fmt(row), size=11)
            if p == 0:
                svg.text(24, y0 + thickness / 2 + 4, RULE_LABELS[i], size=12)
    # Like for like: the rule variant that reaches as many topics as the planner.
    planner = rows[0]
    peer = min(
        (r for r in rows[1:] if r["ready"] == planner["ready"]),
        key=lambda r: r["sessions"],
        default=rows[1],
    )
    saved = peer["sessions"] - planner["sessions"]
    svg.text(
        24,
        py + len(rows) * (thickness + gap) + 22,
        f"For the same {planner['ready']} topics at target, the search saves {saved} of {peer['sessions']} "
        f"sessions (about one in {round(peer['sessions'] / saved)}) and takes {planner['seconds']:.0f} s "
        f"instead of {peer['seconds']:.2f} s.",
        size=12,
        colour=theme.muted,
    )
    footer(svg, "Every review assumed to succeed. benchmarks/rule_vs_planner.py, benchmarks/figures.py")
    return svg


# --------------------------------------------------------------------------- #
# 4. How the pieces fit together (a diagram: no numbers)
# --------------------------------------------------------------------------- #

PIPELINE = (
    (
        "Your timetable",
        ("a .ics file or its link:", "ADE, Hyperplanning, Google"),
        "calendar_io.py, sources.py",
    ),
    (
        "What it says",
        ("courses, lectures, exams,", "and the free time left"),
        "calendar_io.py, timegrid.py",
    ),
    (
        "The assistant plans",
        ("deadlines, exam practice,", "self-tests as recall fades"),
        "assistant.py, memory.py",
    ),
    ("In your calendar", ("a feed to subscribe to,", "a week you can drag"), "feed.py, web/"),
)


def draw_pipeline(theme: Theme) -> Svg:
    width, height = 800, 300
    svg = Svg(width, height, theme, "How the study planner works, from timetable to calendar")
    header(svg, "From your timetable back to your calendar", "Every front end calls one API, cps.service.")
    box_w, box_h, gap, top = 176, 96, 24, 96
    left = (width - 4 * box_w - 3 * gap) / 2
    accent = theme.series[0]
    centres = []
    for i, (title, lines, modules) in enumerate(PIPELINE):
        x0 = left + i * (box_w + gap)
        colour = accent if i == 2 else theme.muted
        svg.add(
            f'<rect x="{x0:.1f}" y="{top}" width="{box_w}" height="{box_h}" rx="10" '
            f'fill="{theme.surface}" stroke="{colour}" stroke-width="{2 if i == 2 else 1.2}"/>'
        )
        svg.text(x0 + 12, top + 24, title, size=13, weight=650)
        for k, line in enumerate(lines):
            svg.text(x0 + 12, top + 44 + 16 * k, line, size=11, colour=theme.muted)
        svg.text(x0 + 12, top + box_h - 10, modules, size=10, colour=theme.muted)
        centres.append(x0 + box_w / 2)
        if i:
            ax = x0 - gap + 4
            svg.line(ax, top + box_h / 2, x0 - 6, top + box_h / 2, theme.muted, 1.5)
            svg.add(f'<path d="M{x0 - 4:.1f},{top + box_h / 2:.1f} l-7,-4.5 v9 z" fill="{theme.muted}"/>')
    # The report loop: a session marked done, skipped or hard replans at once.
    y_loop = top + box_h + 26
    x_from, x_to = centres[3], centres[2]
    svg.path(
        [(x_from, top + box_h + 2), (x_from, y_loop), (x_to, y_loop), (x_to, top + box_h + 8)],
        accent,
        1.5,
    )
    svg.add(f'<path d="M{x_to:.1f},{top + box_h + 3:.1f} l-4.5,7 h9 z" fill="{accent}"/>')
    svg.text(
        (x_from + x_to) / 2,
        y_loop + 16,
        "one tap after each session: done, skipped, hard",
        size=11,
        colour=theme.muted,
        anchor="middle",
    )
    # The research reference sits beside the product, not inside it.
    rx, ry, rw, rh = left, y_loop + 34, 2 * box_w + gap, 34
    svg.add(
        f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{rw}" height="{rh}" rx="8" fill="none" '
        f'stroke="{theme.muted}" stroke-width="1" stroke-dasharray="4 4"/>'
    )
    svg.text(rx + 12, ry + 21, "Research reference: FSRS + AO* search (plan.py, rolling.py)", size=11)
    svg.text(
        rx + rw + 12,
        ry + 21,
        "checks that the assistant's rules are good enough",
        size=11,
        colour=theme.muted,
    )
    return svg


# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--only", choices=("spacing", "planner", "rules", "pipeline"), default=None)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    ensure_utf8_output()

    if args.only in (None, "spacing"):
        curves = spacing_data()
        for name, c in curves.items():
            print(
                f"spacing {name:8} sessions on days {', '.join(f'{d:g}' for d in c['days']) or '-'}; "
                f"recall on exam day {c['exam']:.3f}, 12 weeks after {c['end']:.3f}, "
                f"stability {c['stability']:.1f}"
            )
        for theme in THEMES:
            draw_spacing(curves, theme).save(args.out / f"spacing-{theme.name}.svg")
    if args.only in (None, "planner"):
        rows = planner_data()
        for r in rows:
            print(
                f"planner  {r['name']:12} ready {r['ready'][0]:.0%} ± {r['ready'][1]:.0%}, "
                f"blocks {r['blocks'][0]:.2f} ± {r['blocks'][1]:.2f}, recall {r['recall'][0]:.3f}, "
                f"stability {r['stability'][0]:.1f} ± {r['stability'][1]:.1f}"
            )
        for theme in THEMES:
            draw_planner(rows, theme).save(args.out / f"planner-vs-schedulers-{theme.name}.svg")
    if args.only in (None, "rules"):
        out = rules_data()
        for r in out["rows"]:
            print(
                f"rules    {r['label']:44} {r['sessions']:>4} sessions, "
                f"{r['ready']}/{out['topics']} at target"
            )
        for theme in THEMES:
            draw_rules(out, theme).save(args.out / f"search-vs-rules-{theme.name}.svg")
    if args.only in (None, "pipeline"):
        for theme in THEMES:
            draw_pipeline(theme).save(args.out / f"pipeline-{theme.name}.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
