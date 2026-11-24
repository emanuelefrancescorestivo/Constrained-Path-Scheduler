"""
The home page's field of neurons (DECISIONS.md D58), drawn once and kept as a
template partial, `templates/_neural.html`.

Decoration only: neurons scattered over a square (Poisson-disc points, at least 170
apart, so they never clump), each joined to its two nearest neighbours by a curved
synapse, with a few short dendrites, and fourteen synapses chosen to carry a signal.
The stylesheet colours it, makes the signals travel and stops them under reduced
motion. A square, because a laptop's wide hero shows its middle band and a phone's
tall one its middle columns, so both see neurons.

Seeded, so the markup changes only when this file does; `tests/test_web.py` checks
that the partial is this function's output. To draw it again:

    python -m cps.web.neural
"""

from __future__ import annotations

import math
import random
from pathlib import Path

SEED = 58
SIZE = 1440
SPACING = 170
SIGNALS = 14
PARTIAL = Path(__file__).resolve().parent / "templates" / "_neural.html"


def _geometry(
    rng: random.Random, count: int = 64
) -> tuple[list[tuple[int, int]], list[tuple[int, int, str]], list[str]]:
    """The field's neurons (Poisson-disc points), its synapses (each joined to its two
    nearest neighbours, a curve from the first to the second) and its dendrites."""
    nodes: list[tuple[float, float]] = []
    for _ in range(20000):
        if len(nodes) >= count:
            break
        p = (rng.uniform(40, SIZE - 40), rng.uniform(40, SIZE - 40))
        if all(math.hypot(p[0] - q[0], p[1] - q[1]) >= SPACING for q in nodes):
            nodes.append(p)
    points = [(round(x), round(y)) for x, y in nodes]

    edges: set[tuple[int, int]] = set()
    for i, a in enumerate(points):
        near = sorted((math.hypot(a[0] - b[0], a[1] - b[1]), j) for j, b in enumerate(points) if j != i)[:2]
        for _, j in near:
            edges.add((min(i, j), max(i, j)))

    def curve(a: tuple[int, int], b: tuple[int, int], bend: float = 0.22) -> str:
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        nx, ny = -dy / length, dx / length
        o1, o2 = (rng.uniform(-bend, bend) * length for _ in range(2))
        c1 = (a[0] + dx * 0.33 + nx * o1, a[1] + dy * 0.33 + ny * o1)
        c2 = (a[0] + dx * 0.66 + nx * o2, a[1] + dy * 0.66 + ny * o2)
        return "M{} {}C{} {} {} {} {} {}".format(*(round(v) for p in (a, c1, c2, b) for v in p))

    synapses = [(i, j, curve(points[i], points[j])) for i, j in sorted(edges)]
    dendrites = []
    for x, y in points:
        for _ in range(rng.choice((2, 3))):
            angle = rng.uniform(0, 2 * math.pi)
            reach = rng.uniform(24, 56)
            ex, ey = x + math.cos(angle) * reach, y + math.sin(angle) * reach
            bx = (x + ex) / 2 + math.cos(angle + math.pi / 2) * rng.uniform(-10, 10)
            by = (y + ey) / 2 + math.sin(angle + math.pi / 2) * rng.uniform(-10, 10)
            dendrites.append(f"M{x} {y}Q{round(bx)} {round(by)} {round(ex)} {round(ey)}")
    return points, synapses, dendrites


def render() -> str:
    rng = random.Random(SEED)
    points, edges, dendrites = _geometry(rng)
    synapses = [d for _i, _j, d in edges]
    firing = sorted(rng.sample(range(len(synapses)), SIGNALS))

    lines = [
        "{# The home page's field of neurons (D58): decoration only, written by",
        "   `python -m cps.web.neural` (seeded); CSS gives it its colours and its signals,",
        "   and stops them under reduced motion. Hidden from screen readers. #}",
        f'<svg class="neural" viewBox="0 0 {SIZE} {SIZE}" preserveAspectRatio="xMidYMid slice" '
        'aria-hidden="true" focusable="false">',
        "  <defs>",
        '    <linearGradient id="nf-line" gradientUnits="userSpaceOnUse" '
        f'x1="0" y1="0" x2="{SIZE}" y2="{SIZE}">'
        '<stop offset="0" class="nf-a"/><stop offset="0.5" class="nf-b"/><stop offset="1" class="nf-c"/>'
        "</linearGradient>",
        '    <radialGradient id="nf-glow"><stop offset="0" class="nf-glow-in"/>'
        '<stop offset="1" class="nf-glow-out"/></radialGradient>',
        "  </defs>",
        '  <g class="nf-dendrites">' + "".join(f'<path d="{d}"/>' for d in dendrites) + "</g>",
        '  <g class="nf-edges">' + "".join(f'<path d="{d}"/>' for d in synapses) + "</g>",
        '  <g class="nf-signals">'
        + "".join(
            f'<path class="s{k + 1}" d="{synapses[i]}" pathLength="100"/>' for k, i in enumerate(firing)
        )
        + "</g>",
        '  <g class="nf-nodes">'
        + "".join(
            f'<circle class="glow n{k % 5}" cx="{x}" cy="{y}" r="22"/>'
            f'<circle class="soma n{k % 5}" cx="{x}" cy="{y}" r="3.5"/>'
            for k, (x, y) in enumerate(points)
        )
        + "</g>",
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


# D191: the field that runs the whole length of the front page's story, behind it. The
# same drawing, another seed; its signals follow the scroll instead of a clock: each of
# `STORY_WINDOWS` stretches of the story fires its own signals, which run from one neuron
# to the next as the page is scrolled through it, and light the neuron they reach.
STORY_SEED = 191
STORY_NODES = 52
STORY_SIGNALS = 36
STORY_WINDOWS = 12
STORY_PARTIAL = Path(__file__).resolve().parent / "templates" / "_neural_story.html"


def render_story() -> str:
    rng = random.Random(STORY_SEED)
    points, edges, dendrites = _geometry(rng, STORY_NODES)
    firing = sorted(rng.sample(range(len(edges)), STORY_SIGNALS))
    windows = {k: 1 + n % STORY_WINDOWS for n, k in enumerate(firing)}
    # A neuron a signal reaches lights up in its window; the others glow faintly, still.
    lit = {edges[k][1]: windows[k] for k in firing}
    lines = [
        "{# The front page's story's field of neurons (D191): decoration only, written by",
        "   `python -m cps.web.neural` (seeded). CSS keeps it in view behind the story and",
        "   fires its signals as the page is scrolled; still under reduced motion. #}",
        f'<svg class="neurons" viewBox="0 0 {SIZE} {SIZE}" preserveAspectRatio="xMidYMid slice" '
        'aria-hidden="true" focusable="false">',
        "  <defs>",
        '    <linearGradient id="ns-line" gradientUnits="userSpaceOnUse" '
        f'x1="0" y1="0" x2="{SIZE}" y2="{SIZE}">'
        '<stop offset="0" class="nf-a"/><stop offset="0.5" class="nf-b"/><stop offset="1" class="nf-c"/>'
        "</linearGradient>",
        '    <radialGradient id="ns-glow"><stop offset="0" class="nf-glow-in"/>'
        '<stop offset="1" class="nf-glow-out"/></radialGradient>',
        "  </defs>",
        '  <g class="ns-dendrites">' + "".join(f'<path d="{d}"/>' for d in dendrites) + "</g>",
        '  <g class="ns-edges">' + "".join(f'<path d="{d}"/>' for _i, _j, d in edges) + "</g>",
        '  <g class="ns-signals">'
        + "".join(f'<path class="k{windows[k]}" d="{edges[k][2]}" pathLength="100"/>' for k in firing)
        + "</g>",
        '  <g class="ns-nodes">'
        + "".join(
            (f'<circle class="glow k{lit[k]}" cx="{x}" cy="{y}" r="26"/>' if k in lit else "")
            + f'<circle class="soma n{k % 3}" cx="{x}" cy="{y}" r="3.5"/>'
            for k, (x, y) in enumerate(points)
        )
        + "</g>",
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    PARTIAL.write_bytes(render().encode("utf-8"))
    STORY_PARTIAL.write_bytes(render_story().encode("utf-8"))
    print(f"wrote {PARTIAL} and {STORY_PARTIAL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
