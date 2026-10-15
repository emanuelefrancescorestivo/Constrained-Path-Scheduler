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


def render() -> str:
    rng = random.Random(SEED)
    nodes: list[tuple[float, float]] = []
    for _ in range(20000):
        if len(nodes) >= 64:
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

    synapses = [curve(points[i], points[j]) for i, j in sorted(edges)]
    dendrites = []
    for x, y in points:
        for _ in range(rng.choice((2, 3))):
            angle = rng.uniform(0, 2 * math.pi)
            reach = rng.uniform(24, 56)
            ex, ey = x + math.cos(angle) * reach, y + math.sin(angle) * reach
            bx = (x + ex) / 2 + math.cos(angle + math.pi / 2) * rng.uniform(-10, 10)
            by = (y + ey) / 2 + math.sin(angle + math.pi / 2) * rng.uniform(-10, 10)
            dendrites.append(f"M{x} {y}Q{round(bx)} {round(by)} {round(ex)} {round(ey)}")
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


def main() -> int:
    PARTIAL.write_bytes(render().encode("utf-8"))
    print(f"wrote {PARTIAL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
