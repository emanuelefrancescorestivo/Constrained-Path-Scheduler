"""
The tips' covers (DECISIONS.md D164): one small scene per tip, in filled shapes across the
top of its card, of what the advice asks. The owner chose them on 2026-11-19 ("2C", from
"Finishing Choices", part 2) over D160's line drawings on a tile, which left the text a
narrow column on a phone and let a study's number spill out of its tile.

Each scene is drawn on a 300 by 112 grid and scaled to the card's width; where the card is
wider than the scene, the cover's tinted ground fills the sides (`preserveAspectRatio`
centres it). Colours are classes, never inline styles (the Content-Security-Policy), so a
scene follows light and dark and the accent the student chose (D156):

    g   a soft ground, the accent faint      c   paper: the card's surface, outlined
    a   the accent, filled and outlined      l   a line in the accent
    w   a mark drawn on the accent           k   ink, filled
    m   quieter (faded)                      h   half-quiet

A shape with no class is outlined in ink. They are decoration: the card's words say the
same, so each is hidden from screen readers. A study's number is not drawn here: it is
the cover's caption in the page, translated, so it can never spill out.
"""

from __future__ import annotations

from markupsafe import Markup

GROUND = '<ellipse cx="150" cy="99" rx="122" ry="8" class="g"/>'


def _book(x: float, cls: str = "c") -> str:
    """An open book, its left edge at x, 60 wide."""
    return (
        f'<path d="M{x} 44c10-6 22-6 30 0v44c-8-6-20-6-30 0zM{x + 30} 44c8-6 20-6 30 0v44c-10-6-22-6-30 0z" class="{cls}"/>'
        f'<path d="M{x + 6} 56h16M{x + 6} 64h16M{x + 6} 72h12M{x + 36} 56h16M{x + 36} 64h16" class="m"/>'
    )


def _arrow(x: float, y: float, length: float = 28) -> str:
    end = x + length
    return f'<path d="M{x} {y}h{length}M{end - 7} {y - 7}l7 7-7 7" class="l"/>'


def _question(cx: float, cy: float, size: float = 1.0, width: float = 3.5) -> str:
    """A question mark: its hook's centre at (cx, cy)."""
    s = size
    return (
        f'<path d="M{cx - 8 * s} {cy}a{8 * s} {8 * s} 0 1 1 {11 * s} {7.4 * s}c{-2.4 * s} {1 * s} {-3 * s} {2.6 * s} {-3 * s} {5 * s}" '
        f'class="l" stroke-width="{width}"/>'
        f'<path d="M{cx} {cy + 21 * s}v.5" class="l" stroke-width="{width + 1.5}"/>'
    )


def _tick(x: float, y: float, cls: str = "w") -> str:
    return f'<path d="M{x - 6.5} {y}l4.5 4.5 8-9" class="{cls}" stroke-width="3"/>'


def _moon(x: float, y: float, r: float = 13) -> str:
    """A crescent whose outer circle has its top at (x, y)."""
    return f'<path d="M{x} {y}a{r} {r} 0 1 0 {r * 1.15} {r * 1.2} {r * 0.78} {r * 0.78} 0 0 1 {-r * 1.15}-{r * 1.2}z" class="a"/>'


def _row(xs: list[float], y: float, r: float, classes: list[str]) -> str:
    return "".join(
        f'<circle cx="{x}" cy="{y}" r="{r}" class="{c}"/>' for x, c in zip(xs, classes, strict=True)
    )


def _arc_y(x: float, x0: float, x1: float, y: float, rise: float) -> float:
    """The height of a row of seats curving up by `rise` at its middle."""
    t = (x - x0) / (x1 - x0)
    return round(y - 4 * rise * t * (1 - t), 1)


def _seats() -> str:
    rows = [(78, 222, 52, 7, 5), (58, 242, 72, 9, 6), (38, 262, 92, 11, 7)]
    out = []
    for x0, x1, y, rise, count in rows:
        step = (x1 - x0) / (count - 1)
        for i in range(count):
            x = round(x0 + i * step, 1)
            you = y == 72 and i == 3
            out.append(
                f'<circle cx="{x}" cy="{_arc_y(x, x0, x1, y, rise)}" r="6" class="{"a" if you else "k m"}"/>'
            )
    return "".join(out)


def _week_tiles() -> str:
    tiles, dots = [], []
    for i in range(7):
        x = 28 + i * 36
        tiles.append(f'<rect x="{x}" y="54" width="28" height="30" rx="7" class="c"/>')
        if i in (0, 1, 3, 6):
            dots.append(f'<circle cx="{x + 14}" cy="69" r="6" class="a"/>')
    return "".join(tiles + dots)


def _nights() -> str:
    out = []
    for i in range(7):
        x = 46 + i * 31
        out.append(f'<rect x="{x}" y="24" width="23" height="70" rx="7" class="c"/>')
        out.append(
            f'<rect x="{x + 7}" y="38" width="9" height="44" rx="4.5" class="a{" h" if i in (5, 6) else ""}"/>'
        )
    return "".join(out)


def _shapes(y: float, kinds: str, classes: list[str]) -> str:
    """A row of circles (o), squares (s) and triangles (t), 28 apart from x 80."""
    out = []
    for i, (kind, cls) in enumerate(zip(kinds, classes, strict=True)):
        x = 80 + i * 28
        if kind == "o":
            out.append(f'<circle cx="{x}" cy="{y}" r="9" class="{cls}"/>')
        elif kind == "s":
            out.append(f'<rect x="{x - 9}" y="{y - 9}" width="18" height="18" rx="3" class="{cls}"/>')
        else:
            out.append(f'<path d="M{x} {y - 10}l10 18h-20z" class="{cls}"/>')
    return "".join(out)


def _grid() -> str:
    done = "110111110111000"
    out = []
    for k, d in enumerate(done):
        x, y = 166 + (k % 5) * 20, 30 + (k // 5) * 20
        cls = "a" if d == "1" else ("c m" if k >= 12 else "c")
        out.append(f'<rect x="{x}" y="{y}" width="14" height="14" rx="3.5" class="{cls}"/>')
    return "".join(out)


def _days() -> str:
    return "".join(
        f'<circle cx="{40 + i * 24}" cy="96" r="6" class="{"c" if i == 4 else "a"}"'
        + (' stroke-dasharray="2.5 3"' if i == 4 else "")
        + "/>"
        for i in range(10)
    )


COVERS: dict[str, str] = {
    # How to study
    "self-test": GROUND
    + _book(46, "c m")
    + _arrow(122, 66)
    + '<rect x="166" y="28" width="76" height="58" rx="9" class="c"/>'
    + _question(204, 48)
    + '<circle cx="240" cy="84" r="13" class="a"/>'
    + _tick(240, 84),
    "spacing": '<ellipse cx="150" cy="99" rx="128" ry="7" class="g"/>'
    + _week_tiles()
    + '<path d="M42 50q18-16 36 0M78 50q36-30 72 0M150 50q54-46 108 0" class="l"/>',
    "class-quizzes": GROUND
    + '<rect x="40" y="16" width="96" height="58" rx="6" class="c m"/>'
    + '<path d="M52 32h48M52 44h62M52 56h38" class="m"/>'
    + '<rect x="118" y="30" width="80" height="66" rx="8" class="c"/>'
    + '<rect x="130" y="42" width="10" height="10" rx="2.5"/><path d="M148 47h36"/>'
    + '<rect x="130" y="58" width="10" height="10" rx="2.5" class="a"/>'
    + '<path d="M132 63l2.5 2.5 4.5-5" class="w" stroke-width="2"/><path d="M148 63h30"/>'
    + '<rect x="130" y="74" width="10" height="10" rx="2.5"/><path d="M148 79h24"/>'
    + '<path d="M214 90l34-50 9 6-34 50-11 5z" class="c"/><path d="M248 40l9 6"/>'
    + '<path d="M214 90l-2 6 7-2z" class="a"/>',
    "interleave": _shapes(32, "oosstt", ["c m", "c m", "c m", "c m", "c m", "c m"])
    + '<path d="M232 32c22 0 22 44 0 44" class="l"/><path d="M240 70l-8 6 8 6" class="l"/>'
    + _shapes(76, "ostost", ["a", "c", "a", "c", "a", "c"]),
    "pretest": GROUND
    + '<path d="M48 22h68a10 10 0 0 1 10 10v28a10 10 0 0 1-10 10H76l-12 12V70H48a10 10 0 0 1-10-10V32a10 10 0 0 1 10-10z" class="c"/>'
    + _question(82, 38, 0.9, 3.2)
    + _arrow(134, 50, 18)
    + '<rect x="160" y="16" width="110" height="62" rx="6" class="c"/>'
    + '<path d="M174 34h62M174 46h78M174 58h46" class="m"/><path d="M186 78l-8 16M244 78l8 16"/>',
    "attend": '<rect x="100" y="10" width="100" height="24" rx="4" class="c"/>'
    + '<path d="M114 22h46" class="m"/>'
    + _seats(),
    # Sleep, movement and coffee
    "sleep-after": GROUND
    + '<rect x="38" y="70" width="34" height="24" rx="3" class="c"/><path d="M42 64h26v6H42z" class="a"/>'
    + '<path d="M86 94V48M236 72v22"/>'
    + '<rect x="86" y="62" width="150" height="14" rx="4" class="c"/>'
    + '<rect x="94" y="48" width="36" height="14" rx="7" class="c"/>'
    + '<circle cx="114" cy="44" r="9" class="c"/>'
    + '<path d="M124 64c24-16 76-16 112 0v8H124z" class="a"/>'
    + '<path d="M146 32h8l-8 8h8M162 18h6l-6 6h6" class="l"/>'
    + _moon(262, 14),
    "sleep-regular": '<path d="M38 38h228M38 82h228" class="l m" stroke-dasharray="3 6"/>'
    + _nights()
    + _moon(272, 8, 10),
    "bedtime-list": GROUND
    + '<rect x="70" y="16" width="88" height="80" rx="8" class="c"/>'
    + '<path d="M86 16v-6M104 16v-6M122 16v-6M140 16v-6"/>'
    + '<circle cx="84" cy="38" r="3.2" class="a"/><circle cx="84" cy="56" r="3.2" class="a"/>'
    + '<circle cx="84" cy="74" r="3.2" class="a"/>'
    + '<path d="M94 38h50M94 56h44M94 74h34"/>'
    + '<path d="M176 88l40-48 8 6-40 48-10 4z" class="c"/><path d="M176 88l-2 6 6-2z" class="a"/>'
    + _moon(236, 18, 14)
    + '<path d="M232 66v10M227 71h10M262 52v8M258 56h8" class="l"/>',
    "exercise": GROUND
    + '<path d="M20 64h70l10-22 14 44 12-30 8 8h62" class="l" stroke-width="3"/>'
    + '<rect x="34" y="80" width="10" height="18" rx="3" class="a"/><rect x="88" y="80" width="10" height="18" rx="3" class="a"/>'
    + '<path d="M44 89h44" stroke-width="3"/>'
    + '<path d="M238 26a20 20 0 0 0-12 36v8h24v-8a20 20 0 0 0-12-36z" class="c"/>'
    + '<path d="M230 78h16M232 85h12"/><path d="M238 12v-6M262 24l6-4M214 24l-6-4" class="l"/>',
    "caffeine": '<path d="M28 88h244" class="m"/>'
    + '<circle cx="46" cy="38" r="10" class="a"/>'
    + '<path d="M46 20v-6M46 62v-6M28 38h-6M70 38h-6M33 25l-4-4M59 25l4-4" class="l"/>'
    + '<path d="M84 50h40v18a14 14 0 0 1-14 14H98a14 14 0 0 1-14-14z" class="c"/>'
    + '<path d="M124 56h5a7 7 0 0 1 0 14h-5"/><path d="M96 42c-3-4 3-6 0-10M110 42c-3-4 3-6 0-10" class="l"/>'
    + '<path d="M144 72v8h100v-8" class="l" stroke-dasharray="3 5"/>'
    + _moon(258, 26),
    # Breaks
    "breaks": '<circle cx="64" cy="62" r="30" class="c"/>'
    + '<path d="M64 62V32a30 30 0 0 1 21.2 8.8z" class="a"/>'
    + '<path d="M64 62l12 8"/><path d="M58 24h12M64 24v8"/>'
    + '<rect x="112" y="52" width="50" height="20" rx="6" class="c"/><rect x="166" y="52" width="12" height="20" rx="6" class="a"/>'
    + '<rect x="182" y="52" width="50" height="20" rx="6" class="c"/><rect x="236" y="52" width="12" height="20" rx="6" class="a"/>'
    + '<path d="M112 84h136" class="m" stroke-dasharray="2 6"/>',
    # How the brain learns
    "recall-strengthens": GROUND
    + _book(34, "c m")
    + '<rect x="40" y="94" width="48" height="2" rx="1" class="m"/>'
    + '<rect x="150" y="72" width="22" height="20" rx="4" class="a m"/>'
    + '<rect x="188" y="52" width="22" height="40" rx="4" class="a h"/>'
    + '<rect x="226" y="26" width="22" height="66" rx="4" class="a"/>'
    + '<path d="M174 62l12-8M212 42l12-8" class="l"/>',
    "surprise": '<path d="M58 72a14 14 0 0 1 4-27 16 16 0 0 1 30-6 14 14 0 0 1 20 13 12 12 0 0 1-6 22z" class="c"/>'
    + '<circle cx="66" cy="84" r="4" class="c"/><circle cx="58" cy="94" r="2.5" class="c"/>'
    + _question(84, 48, 0.75, 3)
    + _arrow(132, 58, 30)
    + '<path d="M218 18l8 16 17-6-5 17 16 9-16 9 5 17-17-6-8 16-8-16-17 6 5-17-16-9 16-9-5-17 17 6z" class="a"/>'
    + '<path d="M218 44v12" class="w" stroke-width="4.5"/><path d="M218 67v.5" class="w" stroke-width="5.5"/>',
    "spacing-cells": '<path d="M24 58h170" class="m"/>'
    + _row([36, 70, 110, 162], 58, 6, ["a", "a", "a", "a"])
    + '<path d="M36 46q17-12 34 0M70 46q20-14 40 0M110 46q26-18 52 0" class="l"/>'
    + '<circle cx="232" cy="56" r="36" class="c"/><circle cx="238" cy="52" r="13" class="a h"/>'
    + '<path d="M206 36a36 36 0 0 1 18-14" class="l"/>',
    "curiosity": GROUND
    + '<circle cx="150" cy="48" r="32" class="c"/>'
    + '<path d="M173 71l26 24" class="l" stroke-width="9"/>'
    + _question(150, 40, 1.1, 4)
    + '<path d="M92 24v12M86 30h12M214 20v12M208 26h12M226 56v8M222 60h8" class="l"/>',
    "reward-memory": GROUND
    + '<rect x="78" y="18" width="114" height="76" rx="8" class="c"/>'
    + '<path d="M88 84l26-30 18 18 12-12 38 24" class="m"/><circle cx="108" cy="38" r="6" class="m"/>'
    + '<circle cx="196" cy="30" r="20" class="a"/>'
    + '<path d="M196 19l3.3 6.8 7.5 1.1-5.4 5.3 1.3 7.4-6.7-3.5-6.7 3.5 1.3-7.4-5.4-5.3 7.5-1.1z" class="w" stroke-width="2"/>',
    # Motivation and reward
    "rewards": '<rect x="34" y="60" width="200" height="16" rx="8" class="c"/>'
    + '<rect x="34" y="60" width="140" height="16" rx="8" class="a"/>'
    + '<path d="M174 60V22"/><path d="M174 22h30l-7 8 7 8h-30" class="a"/>'
    + '<path d="M74 86v6M114 86v6M154 86v6" class="m"/>'
    + '<rect x="248" y="62" width="30" height="24" rx="3" class="c m"/>'
    + '<path d="M248 70h30M263 62v24M263 62c-6-8-14-4-8 0M263 62c6-8 14-4 8 0" class="m"/>',
    "choice": '<path d="M150 106V74" stroke-width="3"/>'
    + '<path d="M150 74C150 50 80 50 64 24M150 74C150 50 220 50 236 24" class="m" stroke-width="3"/>'
    + '<path d="M150 74V26" class="l" stroke-width="4"/>'
    + '<circle cx="64" cy="20" r="8" class="c m"/><circle cx="236" cy="20" r="8" class="c m"/>'
    + '<circle cx="150" cy="18" r="10" class="a"/>',
    "effort-feels": '<path d="M40 96h220" class="m"/>'
    + '<rect x="80" y="64" width="44" height="32" rx="5" stroke-dasharray="4 4"/>'
    + '<path d="M88 50a8 8 0 0 1 2-15 9 9 0 0 1 17-2 8 8 0 0 1 9 13z" class="c m"/>'
    + '<rect x="176" y="22" width="44" height="74" rx="5" class="a"/>'
    + _tick(198, 46),
    "effort-reward": GROUND
    + '<path d="M40 98L120 26l34 38 22-20 84 54z" class="c"/>'
    + '<path d="M70 92l30-12-16-12 34-14-14-12" class="l" stroke-dasharray="3 5"/>'
    + '<path d="M120 26V8"/><path d="M120 8h18l-5 5 5 5h-18" class="a"/>'
    + '<circle cx="238" cy="28" r="10" class="a h"/>',
    # A balance at rest: believing intelligence grows did little for grades.
    "mindset": '<path d="M150 22v66M120 96h60" stroke-width="3"/>'
    + '<path d="M72 30h156" stroke-width="3"/>'
    + '<path d="M80 30l-14 30M80 30l14 30M220 30l-14 30M220 30l14 30" class="m"/>'
    + '<path d="M60 60a20 7 0 0 0 40 0zM200 60a20 7 0 0 0 40 0z" class="a"/>'
    + '<circle cx="150" cy="22" r="5" class="a"/>'
    + '<path d="M80 34a9 9 0 0 0-5 16v4h10v-4a9 9 0 0 0-5-16z" class="c"/>'
    + '<rect x="210" y="38" width="20" height="18" rx="2" class="c"/><path d="M214 44h12M214 49h8" class="m"/>',
    # Habits
    "track": '<rect x="54" y="16" width="86" height="80" rx="8" class="c"/>'
    + _tick(72, 36, "l")
    + _tick(72, 56, "l")
    + _tick(72, 76, "l")
    + '<path d="M86 36h40M86 56h40M86 76h28"/>'
    + _grid(),
    "if-then": GROUND
    + '<rect x="40" y="24" width="70" height="66" rx="8" class="c"/><path d="M40 40h70M56 18v12M94 18v12"/>'
    + '<rect x="54" y="52" width="16" height="13" rx="3" class="a"/>'
    + _arrow(116, 58, 12)
    + '<circle cx="160" cy="58" r="22" class="c"/><path d="M160 58V44M160 58l10 6" class="l"/>'
    + _arrow(190, 58, 12)
    + '<path d="M240 90s-20-18-20-34a20 20 0 0 1 40 0c0 16-20 34-20 34z" class="a"/>'
    + '<circle cx="240" cy="56" r="7" class="c"/>',
    "plan-it": '<rect x="40" y="22" width="88" height="70" rx="8" class="c"/>'
    + '<path d="M54 40h50M54 56h40"/><path d="M54 72h44" class="l" stroke-width="3"/>'
    + '<path d="M104 72c30 0 48-10 70-26" class="l"/><path d="M164 41l10 4-4 10" class="l"/>'
    + '<rect x="182" y="26" width="84" height="66" rx="8" class="c"/><path d="M182 42h84"/>'
    + '<rect x="192" y="52" width="16" height="12" rx="3" class="m"/><rect x="214" y="52" width="22" height="12" rx="3" class="a"/>'
    + '<rect x="242" y="52" width="16" height="12" rx="3" class="m"/><rect x="192" y="70" width="16" height="12" rx="3" class="m"/>'
    + '<rect x="214" y="70" width="16" height="12" rx="3" class="m"/>',
    "habit": '<path d="M28 84C96 82 128 46 272 26" class="l" stroke-width="3"/>' + _days(),
}


def cover(key: str) -> Markup:
    """A tip's cover, inline; empty for a tip without one."""
    inner = COVERS.get(key)
    if inner is None:
        return Markup("")
    return Markup(
        '<svg class="tip-cover" viewBox="0 0 300 112" preserveAspectRatio="xMidYMid meet" fill="none" '
        'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true" focusable="false">{inner}</svg>'
    )
