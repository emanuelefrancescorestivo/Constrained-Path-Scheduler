"""
The tips' covers (DECISIONS.md D164, redrawn under D168): one small scene per tip across the
top of its card, of what the advice asks. The owner chose illustrated covers on 2026-11-19
("2C") and on 2026-11-20 found D164's drawings careless ("bro just pay attention when you
make these drawings it's not that hard to draw a sun with all its beams"): a sun with six
beams, white outlines, one colour. D168 draws them again as illustrations: whole objects in
filled colours, shaded by a darker tone of their own colour and set on a soft shadow, never
outlined, and every one checked by eye at three times its size in light and dark.

Each scene is drawn on a 300 by 112 grid and scaled to the card's width; where the card is
wider than the scene, the cover's tinted ground fills the sides (`preserveAspectRatio`
centres it). Colours are classes, never inline styles or fill attributes (the Content-
Security-Policy, and one palette to change in `style.css`):

    g    the ground, the accent faint        sh   a soft shadow
    p    paper                               p2   paper's shaded side
    ln   a grey line (stroke)                ink  dark ink, filled
    a    the accent the student chose        a2   the accent, darker    a3  lighter
    al   a line in the accent                wl   a white line         wf  white, filled
    y    sun yellow   y2 its shade           yl   a yellow line (a sun's beams)
    r    coral red    r2 its shade           gr   leaf green   gr2 its shade
    pu   violet       sk sky blue            pk   pink         bd  a chalkboard's green
    br   wood         br2 its shade          sn   skin         hr  hair
    nt   the night's blue                    m    faded        h   half faded

They are decoration: the card's words say the same, so each is hidden from screen readers.
A study's number is not drawn here: it is the cover's caption in the page, translated.
"""

from __future__ import annotations

import math

from markupsafe import Markup

# --------------------------------------------------------------------------- #
# Pieces drawn the same way in every scene
# --------------------------------------------------------------------------- #


def _n(v: float) -> str:
    """A coordinate, short: 12.0 is "12", 12.345 is "12.35"."""
    return f"{round(v, 2):g}"


def _shadow(cx: float, cy: float, rx: float, ry: float = 3.5) -> str:
    return f'<ellipse cx="{_n(cx)}" cy="{_n(cy)}" rx="{_n(rx)}" ry="{_n(ry)}" class="sh"/>'


def _blobs(*circles: tuple[float, float, float]) -> str:
    """Soft rounds of the ground behind a scene, for depth."""
    return "".join(f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{_n(r)}" class="g"/>' for x, y, r in circles)


def _sun(cx: float, cy: float, r: float = 9, beams: int = 8, reach: float = 6, gap: float = 4) -> str:
    """A sun: a disc and all its beams, evenly round it (eight unless asked)."""
    out = [f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}" class="y"/>']
    rays = []
    for k in range(beams):
        t = 2 * math.pi * k / beams
        x0, y0 = cx + (r + gap) * math.cos(t), cy + (r + gap) * math.sin(t)
        x1, y1 = cx + (r + gap + reach) * math.cos(t), cy + (r + gap + reach) * math.sin(t)
        rays.append(f"M{_n(x0)} {_n(y0)}L{_n(x1)} {_n(y1)}")
    out.append(f'<path d="{"".join(rays)}" class="yl" stroke-width="2.6"/>')
    return "".join(out)


def _moon(cx: float, cy: float, r: float = 11) -> str:
    """A crescent: a disc of radius r at (cx, cy) less a disc shifted up and to the right."""
    r2 = r * 0.82
    ox, oy = cx + r * 0.5, cy - r * 0.32  # the cut's centre
    d = math.hypot(ox - cx, oy - cy)
    a = (r * r - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(r * r - a * a, 0))
    mx, my = cx + a * (ox - cx) / d, cy + a * (oy - cy) / d
    p1 = (mx + h * (oy - cy) / d, my - h * (ox - cx) / d)
    p2 = (mx - h * (oy - cy) / d, my + h * (ox - cx) / d)
    return (
        f'<path d="M{_n(p1[0])} {_n(p1[1])}A{_n(r)} {_n(r)} 0 1 0 {_n(p2[0])} {_n(p2[1])}'
        f'A{_n(r2)} {_n(r2)} 0 0 1 {_n(p1[0])} {_n(p1[1])}Z" class="y"/>'
    )


def _star(cx: float, cy: float, r: float, cls: str = "y") -> str:
    """A five-pointed star, a point up."""
    pts = []
    for k in range(10):
        t = -math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * 0.45
        pts.append(f"{_n(cx + rr * math.cos(t))} {_n(cy + rr * math.sin(t))}")
    return f'<path d="M{"L".join(pts)}Z" class="{cls}"/>'


def _sparkle(cx: float, cy: float, r: float, cls: str = "y") -> str:
    """A four-pointed twinkle."""
    q = r * 0.28
    return (
        f'<path d="M{_n(cx)} {_n(cy - r)}Q{_n(cx + q)} {_n(cy - q)} {_n(cx + r)} {_n(cy)}'
        f"Q{_n(cx + q)} {_n(cy + q)} {_n(cx)} {_n(cy + r)}Q{_n(cx - q)} {_n(cy + q)} {_n(cx - r)} {_n(cy)}"
        f'Q{_n(cx - q)} {_n(cy - q)} {_n(cx)} {_n(cy - r)}Z" class="{cls}"/>'
    )


def _sheet(x: float, y: float, w: float, h: float, rx: float = 8, cls: str = "p") -> str:
    """A sheet or a card: its shaded edge under it, then its face."""
    return (
        f'<rect x="{_n(x)}" y="{_n(y + 3)}" width="{_n(w)}" height="{_n(h)}" rx="{_n(rx)}" class="p2"/>'
        f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" rx="{_n(rx)}" class="{cls}"/>'
    )


def _lines(x: float, y: float, widths: list[float], gap: float = 9, cls: str = "ln", width: float = 3) -> str:
    d = "".join(f"M{_n(x)} {_n(y + i * gap)}h{_n(w)}" for i, w in enumerate(widths))
    return f'<path d="{d}" class="{cls}" stroke-width="{_n(width)}"/>'


def _check(cx: float, cy: float, r: float = 11) -> str:
    """A green badge with a white tick."""
    s = r / 11
    return (
        f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}" class="gr"/>'
        f'<path d="M{_n(cx - 5 * s)} {_n(cy + 0.5 * s)}l{_n(3.6 * s)} {_n(3.6 * s)} {_n(6.6 * s)}-{_n(7.4 * s)}" '
        f'class="wl" stroke-width="{_n(2.8 * s)}"/>'
    )


def _question(cx: float, cy: float, s: float = 1.0, cls: str = "al", width: float = 4) -> str:
    """A question mark whose hook is centred on (cx, cy)."""
    return (
        f'<path d="M{_n(cx - 7 * s)} {_n(cy - 2 * s)}a{_n(7.5 * s)} {_n(7.5 * s)} 0 1 1 {_n(10.6 * s)} {_n(6.8 * s)}'
        f'c{_n(-2.6 * s)} {_n(1.2 * s)} {_n(-3.6 * s)} {_n(2.8 * s)} {_n(-3.6 * s)} {_n(5.6 * s)}" '
        f'class="{cls}" stroke-width="{_n(width)}"/>'
        f'<circle cx="{_n(cx)}" cy="{_n(cy + 17.5 * s)}" r="{_n(width * 0.62)}" class="{cls[0] if cls != "wl" else "wf"}"/>'
    )


def _arrow(x0: float, y0: float, x1: float, y1: float, bend: float = -14, cls: str = "al") -> str:
    """A curved arrow from (x0, y0) to (x1, y1), bowed by `bend`, its head along its end."""
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2 + bend
    tx, ty = x1 - mx, y1 - my  # the end's direction
    k = math.hypot(tx, ty) or 1
    ux, uy = tx / k, ty / k
    left = (x1 - 7 * ux - 5 * uy, y1 - 7 * uy + 5 * ux)
    right = (x1 - 7 * ux + 5 * uy, y1 - 7 * uy - 5 * ux)
    return (
        f'<path d="M{_n(x0)} {_n(y0)}Q{_n(mx)} {_n(my)} {_n(x1)} {_n(y1)}" class="{cls}" stroke-width="3"/>'
        f'<path d="M{_n(left[0])} {_n(left[1])}L{_n(x1)} {_n(y1)}L{_n(right[0])} {_n(right[1])}" '
        f'class="{cls}" stroke-width="3"/>'
    )


def _book_open(x: float, y: float, w: float = 64, cls: str = "") -> str:
    """An open book seen from the front, its left edge at x, its top at y; `cls` "m" fades it."""
    h = w * 0.62
    half = w / 2
    fade = f' class="{cls}"' if cls else ""
    return (
        f"<g{fade}>"
        f'<path d="M{_n(x - 3)} {_n(y + 6)}V{_n(y + h + 5)}H{_n(x + w + 3)}V{_n(y + 6)}Z" class="a2"/>'
        f'<path d="M{_n(x)} {_n(y + 4)}Q{_n(x + half * 0.5)} {_n(y - 3)} {_n(x + half)} {_n(y + 4)}V{_n(y + h + 2)}'
        f'Q{_n(x + half * 0.5)} {_n(y + h - 4)} {_n(x)} {_n(y + h + 2)}Z" class="p"/>'
        f'<path d="M{_n(x + w)} {_n(y + 4)}Q{_n(x + half * 1.5)} {_n(y - 3)} {_n(x + half)} {_n(y + 4)}V{_n(y + h + 2)}'
        f'Q{_n(x + half * 1.5)} {_n(y + h - 4)} {_n(x + w)} {_n(y + h + 2)}Z" class="p2"/>'
        + _lines(x + 6, y + 12, [half - 14, half - 18, half - 14, half - 22], gap=7, width=2.2)
        + _lines(x + half + 8, y + 12, [half - 14, half - 18, half - 16], gap=7, width=2.2)
        + "</g>"
    )


def _book_closed(x: float, y: float, w: float, h: float, cls: str = "r", shade: str = "r2") -> str:
    """A closed book lying flat, seen from the side: its cover and its pages."""
    return (
        f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" rx="2" class="{shade}"/>'
        f'<rect x="{_n(x + 3)}" y="{_n(y + 2)}" width="{_n(w - 6)}" height="{_n(h - 4)}" rx="1" class="p"/>'
        f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h * 0.34)}" rx="2" class="{cls}"/>'
    )


def _pencil(x: float, y: float, angle: float, length: float = 58) -> str:
    """A yellow pencil, its point at (x, y), turned `angle` degrees from pointing left."""
    body = length - 16
    return (
        f'<g transform="rotate({_n(angle)} {_n(x)} {_n(y)})">'
        f'<path d="M{_n(x)} {_n(y)}l12-6v12z" class="br"/>'
        f'<path d="M{_n(x)} {_n(y)}l4.2-2.1v4.2z" class="ink"/>'
        f'<rect x="{_n(x + 12)}" y="{_n(y - 6)}" width="{_n(body)}" height="12" class="y"/>'
        f'<rect x="{_n(x + 12)}" y="{_n(y + 1.5)}" width="{_n(body)}" height="4.5" class="y2"/>'
        f'<rect x="{_n(x + 12 + body)}" y="{_n(y - 6)}" width="5" height="12" class="p2"/>'
        f'<rect x="{_n(x + 17 + body)}" y="{_n(y - 6)}" width="9" height="12" rx="3" class="pk"/>'
        "</g>"
    )


def _calendar(x: float, y: float, w: float, h: float, head: str = "a") -> str:
    """A calendar page: its coloured head with two rings, its face."""
    return (
        _sheet(x, y, w, h, 9)
        + f'<path d="M{_n(x)} {_n(y + 9)}a9 9 0 0 1 9-9h{_n(w - 18)}a9 9 0 0 1 9 9v7H{_n(x)}z" class="{head}"/>'
        + f'<rect x="{_n(x + w * 0.25 - 2.5)}" y="{_n(y - 6)}" width="5" height="12" rx="2.5" class="tf"/>'
        + f'<rect x="{_n(x + w * 0.75 - 2.5)}" y="{_n(y - 6)}" width="5" height="12" rx="2.5" class="tf"/>'
    )


def _clock(cx: float, cy: float, r: float, hour: float = 60, minute: float = 0) -> str:
    """A clock face: its rim, its marks and its hands (angles in degrees from twelve)."""

    def hand(deg: float, length: float) -> str:
        t = math.radians(deg - 90)
        return f"M{_n(cx)} {_n(cy)}L{_n(cx + length * math.cos(t))} {_n(cy + length * math.sin(t))}"

    marks = "".join(
        f"M{_n(cx + (r - 6) * math.cos(math.radians(a)))} {_n(cy + (r - 6) * math.sin(math.radians(a)))}"
        f"L{_n(cx + (r - 3) * math.cos(math.radians(a)))} {_n(cy + (r - 3) * math.sin(math.radians(a)))}"
        for a in range(0, 360, 90)
    )
    return (
        f'<circle cx="{_n(cx)}" cy="{_n(cy + 3)}" r="{_n(r)}" class="p2"/>'
        f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}" class="p"/>'
        f'<path d="{marks}" class="ln" stroke-width="2"/>'
        f'<path d="{hand(hour, r * 0.5)}{hand(minute, r * 0.72)}" class="inkl" stroke-width="2.6"/>'
        f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="2.4" class="a"/>'
    )


def _cloud(x: float, y: float, s: float = 1.0, cls: str = "p") -> str:
    """A thought's cloud, its left at x and its bottom at y."""
    return (
        f'<path d="M{_n(x + 8 * s)} {_n(y)}a{_n(10 * s)} {_n(10 * s)} 0 0 1 {_n(2 * s)}-{_n(19.5 * s)}'
        f"a{_n(15 * s)} {_n(15 * s)} 0 0 1 {_n(27 * s)}-{_n(8 * s)}a{_n(13 * s)} {_n(13 * s)} 0 0 1 {_n(22 * s)} {_n(5 * s)}"
        f'a{_n(11 * s)} {_n(11 * s)} 0 0 1 {_n(5 * s)} {_n(22.5 * s)}z" class="{cls}"/>'
    )


def _plant(cx: float, base: float, s: float = 1.0) -> str:
    """A small plant in a pot, the pot's bottom at `base`."""
    return (
        f'<path d="M{_n(cx)} {_n(base - 18 * s)}V{_n(base - 34 * s)}" class="gr2l" stroke-width="{_n(2.4 * s)}"/>'
        f'<path d="M{_n(cx)} {_n(base - 24 * s)}c{_n(-9 * s)} {_n(1 * s)} {_n(-14 * s)}-{_n(5 * s)} {_n(-14 * s)}-{_n(12 * s)}'
        f'c{_n(8 * s)}-{_n(0.5 * s)} {_n(13 * s)} {_n(4 * s)} {_n(14 * s)} {_n(12 * s)}z" class="gr"/>'
        f'<path d="M{_n(cx)} {_n(base - 29 * s)}c{_n(1 * s)}-{_n(8 * s)} {_n(6 * s)}-{_n(12.5 * s)} {_n(14 * s)}-{_n(12 * s)}'
        f'c0 {_n(7 * s)}-{_n(5 * s)} {_n(13 * s)}-{_n(14 * s)} {_n(12 * s)}z" class="gr2"/>'
        f'<path d="M{_n(cx - 11 * s)} {_n(base - 19 * s)}h{_n(22 * s)}l{_n(-3 * s)} {_n(19 * s)}h{_n(-16 * s)}z" class="br"/>'
        f'<rect x="{_n(cx - 12.5 * s)}" y="{_n(base - 21 * s)}" width="{_n(25 * s)}" height="{_n(6 * s)}" rx="2" class="br2"/>'
    )


def _person(cx: float, cy: float, top: str, s: float = 1.0) -> str:
    """Someone seen from the front, head and shoulders: the head's centre at (cx, cy)."""
    return (
        f'<path d="M{_n(cx - 11 * s)} {_n(cy + 19 * s)}a{_n(11 * s)} {_n(10 * s)} 0 0 1 {_n(22 * s)} 0z" class="{top}"/>'
        f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(6.5 * s)}" class="sn"/>'
        f'<path d="M{_n(cx - 6.5 * s)} {_n(cy - 0.5 * s)}a{_n(6.5 * s)} {_n(6.5 * s)} 0 0 1 {_n(13 * s)} 0'
        f'c{_n(-3 * s)}-{_n(1.5 * s)}-{_n(8 * s)}-{_n(1.5 * s)}-{_n(13 * s)} 0z" class="hr"/>'
    )


# --------------------------------------------------------------------------- #
# The scenes, in the order of the tips' page
# --------------------------------------------------------------------------- #


def _self_test() -> str:
    """Test yourself instead of rereading: the book left faded, a card asked and ticked."""
    return (
        _blobs((70, 40, 42), (226, 64, 52))
        + _shadow(72, 96, 40)
        + _book_open(40, 48, 64, "m")
        + _arrow(116, 66, 156, 60, -16)
        + _shadow(206, 99, 46)
        + '<rect x="172" y="26" width="76" height="56" rx="9" transform="rotate(8 210 54)" class="a3"/>'
        + _sheet(164, 32, 80, 60, 9)
        + _question(204, 52, 1.05)
        + _check(240, 88, 12)
    )


def _spacing() -> str:
    """Spread your study over days: a week's calendar, sessions on days apart, the gaps growing."""
    cells = []
    for i in range(7):
        x = 60 + i * 26
        cells.append(f'<rect x="{x}" y="60" width="20" height="28" rx="5" class="p2"/>')
        if i in (0, 1, 3, 6):
            cells.append(f'<circle cx="{x + 10}" cy="74" r="6.5" class="a"/>')
    arcs = '<path d="M70 66Q83 54 96 66M96 66Q122 46 148 66M148 66Q187 40 226 66" class="al" stroke-width="2.4"/>'
    return (
        _blobs((60, 30, 34), (250, 84, 40))
        + _shadow(150, 103, 100)
        + _calendar(50, 32, 200, 66)
        + "".join(cells)
        + arcs
        + _sparkle(270, 26, 7)
        + _sparkle(32, 82, 5, "a3")
    )


def _class_quizzes() -> str:
    """Treat quizzes in class as study: a quiz with its boxes ticked, a pencil, a star."""
    rows = []
    for i, (done, w) in enumerate(((True, 46), (True, 38), (False, 42))):
        y = 44 + i * 16
        if done:
            rows.append(f'<rect x="114" y="{y - 6}" width="12" height="12" rx="3" class="a"/>')
            rows.append(f'<path d="M116.6 {y}l2.6 2.6 4.6-5.2" class="wl" stroke-width="2"/>')
        else:
            rows.append(f'<rect x="114" y="{y - 6}" width="12" height="12" rx="3" class="p2"/>')
        rows.append(_lines(134, y, [w], width=3))
    return (
        _blobs((76, 44, 40), (232, 70, 44))
        + _shadow(150, 102, 52)
        + _sheet(104, 16, 88, 82, 8)
        + '<rect x="114" y="25" width="44" height="5" rx="2.5" class="a3"/>'
        + "".join(rows)
        + _pencil(196, 92, -52, 62)
        + _star(72, 44, 15)
        + _sparkle(48, 70, 5)
        + _sparkle(96, 22, 4, "a3")
    )


def _interleave() -> str:
    """Mix different kinds of problems: shapes sorted by kind above, mixed below."""

    def shape(kind: str, x: float, y: float) -> str:
        if kind == "o":
            return f'<circle cx="{x}" cy="{y}" r="9" class="a"/>'
        if kind == "s":
            return f'<rect x="{x - 8.5}" y="{y - 8.5}" width="17" height="17" rx="3.5" class="r"/>'
        return f'<path d="M{x} {y - 10}l10.5 18h-21z" class="y"/>'

    top = "".join(shape(k, 72 + i * 28, 28) for i, k in enumerate("oosstt"))
    bottom = "".join(shape(k, 72 + i * 28, 80) for i, k in enumerate("ostsot"))
    return (
        _blobs((150, 56, 70))
        + f'<g class="m">{top}</g>'
        + _shadow(142, 96, 86, 3)
        + bottom
        + '<path d="M236 30q20 0 20 23t-20 23" class="al" stroke-width="3"/>'
        + '<path d="M243 70l-7 6 7 6" class="al" stroke-width="3"/>'
    )


def _pretest() -> str:
    """Try a few questions before the lecture: a question first, then the lecture's screen."""
    return (
        _blobs((72, 46, 40), (220, 50, 50))
        + _shadow(72, 98, 34)
        + '<path d="M44 22h56a10 10 0 0 1 10 10v30a10 10 0 0 1-10 10H78l-13 13V72H44a10 10 0 0 1-10-10V32a10 10 0 0 1 10-10z" class="p2" transform="translate(0 3)"/>'
        + '<path d="M44 22h56a10 10 0 0 1 10 10v30a10 10 0 0 1-10 10H78l-13 13V72H44a10 10 0 0 1-10-10V32a10 10 0 0 1 10-10z" class="p"/>'
        + _question(72, 40, 0.95)
        + _arrow(120, 52, 150, 52, -10)
        + '<path d="M196 82l-10 18M228 82l10 18" class="tl" stroke-width="3"/>'
        + '<rect x="156" y="14" width="112" height="6" rx="3" class="tf"/>'
        + _sheet(160, 20, 104, 62, 4)
        + _lines(172, 34, [56, 72, 44], gap=10)
        + '<path d="M236 70v-8h7v8M247 70V56h7v14" class="a3"/>'
        + '<rect x="236" y="56" width="7" height="14" rx="1.5" class="a3"/><rect x="247" y="48" width="7" height="22" rx="1.5" class="a"/>'
        + _sparkle(118, 26, 6)
    )


def _attend() -> str:
    """Go to class: a board in front, two rows of students at their desks, you among them."""
    back = "".join(
        _person(x, 46, top, 0.82) for x, top in zip((84, 117, 183, 216), ("pu", "sk", "gr", "r"), strict=True)
    )
    front = "".join(
        _person(x, 70, top, 0.95)
        for x, top in zip((66, 102, 198, 234), ("y2", "gr", "pu", "sk"), strict=True)
    )
    return (
        _blobs((36, 34, 28), (264, 34, 28))
        + '<rect x="100" y="4" width="100" height="26" rx="4" class="br"/>'
        + '<rect x="104" y="8" width="92" height="18" rx="2" class="bd"/>'
        + '<path d="M112 14h30M112 20h44M170 13l7 7m0-7l-7 7" class="wl m" stroke-width="1.8"/>'
        + _person(150, 46, "a3", 0.82)
        + back
        + '<rect x="62" y="58" width="176" height="7" rx="3.5" class="br"/>'
        + _shadow(150, 104, 112, 3)
        + front
        + _person(150, 70, "a", 1.05)
        + '<rect x="40" y="86" width="220" height="9" rx="4.5" class="br"/>'
        + '<rect x="40" y="93" width="220" height="4" rx="2" class="br2"/>'
        + _sparkle(167, 64, 5)
    )


def _sleep_after() -> str:
    """Sleep after studying: a bed, someone asleep under a blanket, the books on the side."""
    return (
        _blobs((252, 28, 34), (60, 70, 34))
        + _moon(254, 28, 13)
        + _star(222, 18, 4)
        + _star(280, 52, 3.5)
        + _shadow(146, 100, 104)
        + '<rect x="28" y="70" width="34" height="28" rx="3" class="br"/>'
        + '<rect x="28" y="70" width="34" height="5" rx="2" class="br2"/>'
        + _book_closed(32, 61, 26, 9, "r", "r2")
        + _book_closed(35, 53, 21, 8, "a", "a2")
        + '<rect x="72" y="42" width="12" height="58" rx="4" class="br"/>'
        + '<rect x="244" y="64" width="10" height="36" rx="3" class="br"/>'
        + '<rect x="78" y="76" width="172" height="14" rx="4" class="br2"/>'
        + '<rect x="82" y="64" width="164" height="14" rx="6" class="p"/>'
        + '<ellipse cx="104" cy="60" rx="17" ry="7.5" class="p2"/>'
        + '<circle cx="108" cy="52" r="8.5" class="sn"/>'
        + '<path d="M99.6 51a8.5 8.5 0 0 1 16.6-3c-5 .8-11 1.8-16.6 3z" class="hr"/>'
        + '<path d="M118 60c18-12 54-12 86-6 14 2.6 30 4 42 6v14c0 4-3 6-7 6H122c-4 0-6-3-6-6z" class="a"/>'
        + '<path d="M118 66h128v8c0 4-3 6-7 6H122c-4 0-6-3-6-6z" class="a2"/>'
        + '<path d="M128 40h9l-9 9h9M144 26h7l-7 7h7" class="al" stroke-width="2.6"/>'
    )


def _sleep_regular() -> str:
    """Keep your sleep regular: seven nights, each from the same bedtime to the same morning."""
    nights = []
    for i in range(7):
        x = 66 + i * 26
        nights.append(f'<rect x="{x}" y="34" width="18" height="52" rx="9" class="nt"/>')
        nights.append(
            f'<circle cx="{x + 6}" cy="46" r="1.4" class="wf"/><circle cx="{x + 12}" cy="58" r="1.1" class="wf"/>'
        )
    return (
        _blobs((150, 60, 72))
        + '<path d="M58 34h192M58 86h192" class="ln" stroke-width="2" stroke-dasharray="3 5"/>'
        + "".join(nights)
        + _moon(36, 34, 12)
        + _sun(270, 86, 8, reach=5, gap=3.5)
    )


def _bedtime_list() -> str:
    """Before bed, write tomorrow's list: a pad of tomorrow's tasks, a pencil, the night."""
    rings = "".join(
        f'<rect x="{x}" y="12" width="5" height="12" rx="2.5" class="tf"/>' for x in (98, 116, 134, 152)
    )
    items = "".join(f'<circle cx="104" cy="{40 + i * 16}" r="4" class="a"/>' for i in range(3))
    return (
        _blobs((126, 54, 52), (250, 40, 36))
        + _shadow(128, 102, 46)
        + _sheet(86, 18, 84, 80, 9)
        + rings
        + items
        + _lines(114, 40, [42, 36, 28], gap=16)
        + _pencil(178, 92, -50, 60)
        + _moon(246, 34, 14)
        + _star(272, 60, 4.5)
        + _star(218, 64, 3.5)
        + _sparkle(282, 22, 4)
    )


def _exercise() -> str:
    """Move: a workout can help you remember: a running shoe, a heartbeat, a lit bulb."""
    return (
        _blobs((80, 60, 46), (238, 46, 40))
        + _shadow(78, 98, 46)
        + '<path d="M36 92c0-6 2-10 8-12l20-6 10-16c2-3 6-4 9-2l7 5c4 3 8 4 12 2l6-2c4 6 6 14 6 22v9z" class="a"/>'
        + '<path d="M36 88h78v4c0 3-2 5-5 5H40c-2 0-4-2-4-4z" class="p"/>'
        + '<path d="M36 92h78" class="ln" stroke-width="2"/>'
        + '<path d="M72 66l8 3M68 72l8 3M64 78l8 3" class="wl" stroke-width="2.2"/>'
        + '<path d="M20 66h10M14 76h12M22 86h8" class="al m" stroke-width="2.6"/>'
        + '<path d="M128 62h22l7-16 10 32 8-22 5 6h16" class="rl" stroke-width="3"/>'
        + '<path d="M238 22a18 18 0 0 0-11 32c3 2.4 4 5 4 8h14c0-3 1-5.6 4-8a18 18 0 0 0-11-32z" class="y"/>'
        + '<path d="M238 22a18 18 0 0 1 11 32c-3 2.4-4 5-4 8h-4c0-3 1.2-6 4-9a15 15 0 0 0-7-31z" class="y2"/>'
        + '<rect x="231" y="64" width="14" height="5" rx="2" class="p2"/><rect x="232" y="70" width="12" height="5" rx="2" class="p2"/>'
        + '<path d="M238 12V5M258 20l5-5M218 20l-5-5M266 38h7M210 38h-7" class="yl" stroke-width="2.6"/>'
    )


def _caffeine() -> str:
    """Coffee or tea can sharpen attention; keep it far from bedtime: the morning's cup, the night far."""
    return (
        _blobs((52, 40, 36), (250, 40, 36))
        + _sun(46, 34, 10)
        + _shadow(116, 98, 30)
        + '<path d="M128 62h6a9 9 0 0 1 0 18h-6" class="p2l" stroke-width="5"/>'
        + '<path d="M94 54h36v26a14 14 0 0 1-14 14h-8a14 14 0 0 1-14-14z" class="p"/>'
        + '<path d="M94 64h36v6H94z" class="a"/>'
        + '<ellipse cx="112" cy="54" rx="18" ry="4" class="br2"/>'
        + '<path d="M104 46c-3-4 3-7 0-11M114 46c-3-4 3-7 0-11M124 46c-3-4 3-7 0-11" class="ln" stroke-width="2.4"/>'
        + '<path d="M30 102h240" class="ln" stroke-width="2"/>'
        + '<path d="M150 102h36" class="al" stroke-width="5"/>'
        + '<path d="M196 102h70" class="rl" stroke-width="5" stroke-dasharray="6 6"/>'
        + _moon(252, 40, 14)
        + _star(224, 26, 4)
        + _star(280, 64, 3.5)
    )


def _breaks() -> str:
    """Take short breaks, decided before you start: a timer, blocks of work and short breaks."""
    return (
        _blobs((72, 56, 44), (220, 56, 60))
        + _shadow(70, 100, 32)
        + '<rect x="65" y="18" width="10" height="7" rx="2" class="tf"/>'
        + '<path d="M92 34l5-5" class="tl" stroke-width="4"/>'
        + '<circle cx="70" cy="63" r="32" class="p2"/>'
        + '<circle cx="70" cy="60" r="32" class="p"/>'
        + '<path d="M70 60V34a26 26 0 0 1 22.5 13z" class="a"/>'
        + '<path d="M70 60l12 9" class="inkl" stroke-width="3"/>'
        + '<circle cx="70" cy="60" r="3" class="ink"/>'
        + _shadow(196, 84, 82, 3)
        + '<rect x="120" y="50" width="56" height="24" rx="8" class="a"/>'
        + '<rect x="180" y="50" width="16" height="24" rx="8" class="gr"/>'
        + '<rect x="200" y="50" width="56" height="24" rx="8" class="a"/>'
        + '<rect x="260" y="50" width="16" height="24" rx="8" class="gr"/>'
        + '<path d="M188 46v-6M268 46v-6" class="gr2l" stroke-width="2.4"/>'
    )


def _recall_strengthens() -> str:
    """Remembering strengthens the memory; rereading does little: the book faded, recall growing."""
    return (
        _blobs((70, 44, 40), (220, 56, 56))
        + _shadow(70, 96, 38)
        + _book_open(38, 48, 64, "m")
        + _shadow(206, 100, 66)
        + '<rect x="150" y="72" width="26" height="24" rx="5" class="a3"/>'
        + '<rect x="190" y="50" width="26" height="46" rx="5" class="a"/>'
        + '<rect x="230" y="24" width="26" height="72" rx="5" class="a2"/>'
        + _arrow(164, 62, 200, 40, -10)
        + _arrow(204, 40, 240, 16, -10)
        + _sparkle(270, 20, 6)
    )


def _surprise() -> str:
    """Guess before you check: a guess, then the surprise that teaches."""
    burst = []
    for k in range(16):
        t = -math.pi / 2 + k * math.pi / 8
        rr = 30 if k % 2 == 0 else 19
        burst.append(f"{_n(222 + rr * math.cos(t))} {_n(58 + rr * math.sin(t))}")
    return (
        _blobs((80, 50, 44), (222, 58, 46))
        + _shadow(82, 100, 34)
        + _cloud(46, 72, 1.15)
        + '<circle cx="66" cy="84" r="5" class="p"/><circle cx="58" cy="96" r="3" class="p"/>'
        + _question(84, 44, 0.85)
        + _arrow(134, 56, 176, 56, -12)
        + f'<path d="M{"L".join(burst)}Z" class="y"/>'
        + f'<path d="M{"L".join(burst)}Z" class="y2" transform="translate(222 58) scale(.62) translate(-222 -58)"/>'
        + '<path d="M222 44v13" class="wl" stroke-width="5"/><circle cx="222" cy="68" r="3.2" class="wf"/>'
        + _sparkle(266, 22, 6)
        + _sparkle(180, 92, 4)
    )


def _spacing_cells() -> str:
    """Spacing is built into how cells remember: signals arriving apart at a neuron."""
    cx, cy = 238, 56
    arms, twigs = [], []
    for deg, length in ((-80, 30), (-35, 32), (10, 30), (55, 30), (105, 26), (-130, 26)):
        t = math.radians(deg)
        end = (cx + length * math.cos(t), cy + length * math.sin(t))
        arms.append(f"M{_n(cx)} {_n(cy)}L{_n(end[0])} {_n(end[1])}")
        for turn in (-28, 28):
            u = math.radians(deg + turn)
            twigs.append(f"M{_n(end[0])} {_n(end[1])}l{_n(9 * math.cos(u))} {_n(9 * math.sin(u))}")
    return (
        _blobs((120, 56, 60), (238, 56, 46))
        + '<path d="M26 60C80 60 150 60 222 57" class="pkl" stroke-width="6"/>'
        + f'<path d="{"".join(arms)}" class="pkl" stroke-width="6"/>'
        + f'<path d="{"".join(twigs)}" class="pkl" stroke-width="3.4"/>'
        + f'<circle cx="{cx}" cy="{cy}" r="17" class="pk"/>'
        + f'<circle cx="{cx + 2}" cy="{cy - 1}" r="7.5" class="a2"/>'
        + "".join(f'<circle cx="{x}" cy="60" r="6.5" class="a"/>' for x in (44, 74, 116, 174))
        + '<path d="M44 48q15-12 30 0M74 48q21-16 42 0M116 48q29-20 58 0" class="al" stroke-width="2.4"/>'
        + _sparkle(204, 30, 6)
        + _sparkle(200, 88, 4)
    )


def _curiosity() -> str:
    """Start with a question you want answered: the question, then the book that answers it."""
    return (
        _blobs((86, 48, 44), (214, 60, 52))
        + _shadow(86, 98, 30)
        + '<path d="M60 18h52a12 12 0 0 1 12 12v28a12 12 0 0 1-12 12H96l-12 14V70H60a12 12 0 0 1-12-12V30a12 12 0 0 1 12-12z" class="a"/>'
        + _question(86, 36, 0.95, "wl", 4.2)
        + _arrow(134, 56, 166, 58, -10)
        + '<path d="M214 44V16M196 46l-12-16M232 46l12-16M184 48l-18-8M244 48l18-8" class="yl" stroke-width="3"/>'
        + _shadow(214, 100, 44)
        + _book_open(182, 56, 64)
        + _sparkle(262, 26, 5)
    )


def _reward_memory() -> str:
    """Knowing something is worth remembering helps: a picture kept, with a medal on it."""
    return (
        _blobs((132, 56, 60))
        + _shadow(134, 102, 56)
        + '<rect x="78" y="14" width="112" height="84" rx="6" class="br"/>'
        + '<rect x="86" y="22" width="96" height="68" rx="2" class="sk"/>'
        + '<path d="M86 90V72l22-22 18 18 14-12 42 34z" class="gr"/>'
        + '<path d="M86 90V80l22-16 18 14 14-9 42 21z" class="gr2"/>'
        + _sun(112, 40, 7, reach=4, gap=3)
        + '<path d="M196 54l-8 26 10-4 6 9 6-24zM212 54l8 26-10-4-6 9-6-24z" class="r"/>'
        + '<circle cx="204" cy="42" r="19" class="y2"/>'
        + '<circle cx="204" cy="40" r="19" class="y"/>'
        + _star(204, 40, 9, "wf")
    )


def _rewards() -> str:
    """Reward yourself with progress you can see: a bar filling, a flag at its tip, a cup at the end."""
    marks = "".join(f'<circle cx="{x}" cy="68" r="2.4" class="wf"/>' for x in (66, 98, 130))
    return (
        _blobs((130, 60, 70), (252, 60, 34))
        + _shadow(140, 88, 112, 3)
        + '<rect x="34" y="58" width="190" height="20" rx="10" class="p2"/>'
        + '<rect x="34" y="58" width="132" height="20" rx="10" class="a"/>'
        + marks
        + '<path d="M166 58V20" class="tl" stroke-width="3"/>'
        + '<path d="M167 20h28l-7 8 7 8h-28z" class="r"/>'
        + _shadow(256, 98, 22)
        + '<path d="M238 30h36v14a18 18 0 0 1-36 0z" class="y"/>'
        + '<path d="M238 34h-6a8 8 0 0 0 8 12M274 34h6a8 8 0 0 1-8 12" class="yl" stroke-width="3.5"/>'
        + '<path d="M256 30h18v14a18 18 0 0 1-12 17V30z" class="y2"/>'
        + '<rect x="252" y="61" width="8" height="12" class="y2"/>'
        + '<rect x="242" y="73" width="28" height="9" rx="2" class="br2"/>'
        + _sparkle(286, 22, 5)
    )


def _choice() -> str:
    """Choose for yourself: a signpost where the path splits."""
    return (
        _blobs((150, 60, 70))
        + '<path d="M118 112c8-20 18-34 32-42 14 8 24 22 32 42z" class="p2"/>'
        + '<path d="M150 70C130 58 98 54 60 56l-2 10c34-2 64 2 84 14zM150 70c20-12 52-16 90-14l2 10c-34-2-64 2-84 14z" class="p2"/>'
        + '<path d="M150 96v-8M150 80v-6" class="wl" stroke-width="2.4"/>'
        + _shadow(150, 102, 18, 3)
        + '<rect x="146" y="20" width="8" height="82" rx="3" class="br2"/>'
        + '<path d="M150 24h46l12 9-12 9h-46z" class="a"/>'
        + '<path d="M150 46h-44l-12 9 12 9h44z" class="r"/>'
        + '<path d="M150 68h40l10 8-10 8h-40z" class="y"/>'
        + '<path d="M160 33h28M114 55h28M160 76h22" class="wl" stroke-width="2.4"/>'
        + '<circle cx="54" cy="98" r="9" class="gr2"/><circle cx="66" cy="96" r="11" class="gr"/><circle cx="78" cy="99" r="7" class="gr2"/>'
        + '<circle cx="230" cy="99" r="8" class="gr2"/><circle cx="242" cy="96" r="11" class="gr"/><circle cx="254" cy="99" r="8" class="gr2"/>'
    )


def _effort_feels() -> str:
    """Feeling you learn less is not proof: what it feels like, small; what it is, tall and ticked."""
    return (
        _blobs((92, 50, 44), (206, 56, 50))
        + '<path d="M40 98h220" class="ln" stroke-width="2"/>'
        + f'<g class="h">{_cloud(56, 62, 1.15)}</g>'
        + '<rect x="78" y="44" width="11" height="10" rx="2" class="a3"/><rect x="93" y="38" width="11" height="16" rx="2" class="a3"/>'
        + '<circle cx="86" cy="76" r="4.5" class="p h"/><circle cx="80" cy="88" r="3" class="p h"/>'
        + _shadow(206, 99, 30, 3)
        + '<rect x="184" y="20" width="44" height="78" rx="6" class="a"/>'
        + '<rect x="210" y="20" width="18" height="78" rx="6" class="a2"/>'
        + '<path d="M184 40h8M184 56h8M184 72h8M184 88h8" class="wl" stroke-width="2"/>'
        + _check(226, 22, 12)
    )


def _effort_reward() -> str:
    """Effort can become rewarding: a mountain climbed, its flag on top, the sun out."""
    return (
        _blobs((150, 70, 80))
        + _sun(250, 30, 11)
        + '<path d="M150 100L206 46l52 54z" class="a3"/>'
        + '<path d="M28 100L112 22l84 78z" class="a"/>'
        + '<path d="M112 22l84 78h-38z" class="a2"/>'
        + '<path d="M112 22l-18 17 7 6 11-8 10 8 7-6z" class="wf"/>'
        + '<path d="M54 94l26-10-14-10 26-12-10-12" class="inkl m" stroke-width="2.4" stroke-dasharray="3 5"/>'
        + '<path d="M112 22V4" class="tl" stroke-width="2.6"/>'
        + '<path d="M113 4h18l-5 5 5 5h-18z" class="r"/>'
    )


def _mindset() -> str:
    """Don't count on a mindset alone: a balance at rest, a mind on one side, books on the other."""
    return (
        _blobs((150, 60, 72))
        + _shadow(150, 102, 34)
        + '<rect x="128" y="94" width="44" height="8" rx="3" class="br2"/>'
        + '<rect x="146" y="30" width="8" height="66" rx="3" class="y2"/>'
        + '<rect x="68" y="26" width="164" height="7" rx="3.5" class="y"/>'
        + '<circle cx="150" cy="29" r="7" class="y2"/>'
        + '<path d="M84 33l-16 32M84 33l16 32M216 33l-16 32M216 33l16 32" class="ln" stroke-width="2"/>'
        + '<path d="M60 65h48a24 9 0 0 1-48 0z" class="a3"/><path d="M192 65h48a24 9 0 0 1-48 0z" class="a3"/>'
        + '<path d="M70 64c-6-2-8-12-1-15 0-7 8-10 14-6 5-4 13-1 13 5 7 2 7 12 1 16z" class="pk"/>'
        + '<path d="M83 43v20M76 52c3 1 5 0 7-3M90 52c-3 1-5 0-7-3" class="pk2l" stroke-width="2"/>'
        + _book_closed(198, 56, 36, 9, "r", "r2")
        + _book_closed(201, 47, 30, 9, "gr", "gr2")
        + _book_closed(199, 38, 34, 9, "a", "a2")
    )


def _track() -> str:
    """Keep track of what you did: a list ticked off, a grid of days filling in."""
    done = "110111110111000"
    grid = []
    for k, d in enumerate(done):
        x, y = 164 + (k % 5) * 20, 30 + (k // 5) * 20
        if d == "1":
            grid.append(f'<rect x="{x}" y="{y}" width="15" height="15" rx="4" class="a"/>')
        elif k >= 12:
            grid.append(
                f'<rect x="{x + 0.5}" y="{y + 0.5}" width="14" height="14" rx="4" class="lnd" stroke-width="1.6" stroke-dasharray="2.5 2.5"/>'
            )
        else:
            grid.append(f'<rect x="{x}" y="{y}" width="15" height="15" rx="4" class="p2"/>')
    return (
        _blobs((98, 56, 48), (210, 54, 48))
        + _shadow(98, 102, 44)
        + _sheet(56, 18, 84, 80, 9)
        + _check(76, 38, 7)
        + _check(76, 58, 7)
        + '<circle cx="76" cy="78" r="7" class="p2"/>'
        + _lines(90, 38, [36, 30, 24], gap=20)
        + _shadow(213, 96, 52, 3)
        + "".join(grid)
    )


def _if_then() -> str:
    """Decide when and where: a day on the calendar, its hour, its place."""
    return (
        _blobs((150, 58, 80))
        + _shadow(76, 100, 36)
        + _calendar(42, 28, 68, 66, "r")
        + "".join(
            f'<rect x="{50 + (k % 3) * 20}" y="{52 + (k // 3) * 18}" width="14" height="12" rx="3" class="{c}"/>'
            for k, c in enumerate(("p2", "a", "p2", "p2", "p2", "p2"))
        )
        + _arrow(116, 60, 130, 60, 0)
        + _shadow(160, 98, 22)
        + _clock(160, 60, 24, 120, 0)
        + _arrow(190, 60, 204, 60, 0)
        + _shadow(240, 100, 16)
        + '<path d="M240 96s-22-20-22-38a22 22 0 0 1 44 0c0 18-22 38-22 38z" class="r"/>'
        + '<path d="M240 96s22-20 22-38a22 22 0 0 0-22-22z" class="r2"/>'
        + '<circle cx="240" cy="58" r="8" class="wf"/>'
    )


def _plan_it() -> str:
    """Write down what is left, with when: the list's last item drawn into the week."""
    cells = "".join(
        f'<rect x="{192 + (k % 4) * 18}" y="{52 + (k // 4) * 18}" width="13" height="12" rx="3" class="{c}"/>'
        for k, c in enumerate(("p2", "p2", "a", "p2", "p2", "p2", "p2", "p2"))
    )
    return (
        _blobs((86, 56, 48), (226, 56, 48))
        + _shadow(86, 100, 44)
        + _sheet(44, 20, 86, 76, 9)
        + _lines(58, 40, [52, 44], gap=14)
        + '<path d="M58 68h48" class="al" stroke-width="4"/>'
        + '<circle cx="114" cy="68" r="4" class="a"/>'
        + '<path d="M118 68C140 68 156 40 176 50" class="al" stroke-width="3" stroke-dasharray="5 5"/>'
        + '<path d="M170 44l7 6-8 4" class="al" stroke-width="3"/>'
        + _shadow(226, 100, 42)
        + _calendar(184, 26, 82, 70)
        + cells
    )


def _habit() -> str:
    """Missing one day does not undo a habit: the curve still rises past the day missed."""
    dots = "".join(
        f'<circle cx="{40 + i * 22}" cy="98" r="6" class="gr"/>'
        if i != 4
        else f'<circle cx="{40 + i * 22}" cy="98" r="5.4" class="lnd" stroke-width="2" stroke-dasharray="2.6 2.6"/>'
        for i in range(10)
    )
    return (
        _blobs((150, 56, 80))
        + '<path d="M30 80C96 78 126 40 236 30" class="al" stroke-width="4"/>'
        + '<path d="M30 80C96 78 126 40 236 30V88H30z" class="a3 m"/>'
        + dots
        + _plant(262, 104, 1.05)
        + _sparkle(284, 40, 5)
        + _sparkle(244, 52, 3.5)
    )


COVERS: dict[str, str] = {
    # How to study
    "self-test": _self_test(),
    "spacing": _spacing(),
    "class-quizzes": _class_quizzes(),
    "interleave": _interleave(),
    "pretest": _pretest(),
    "attend": _attend(),
    # Sleep, movement and coffee
    "sleep-after": _sleep_after(),
    "sleep-regular": _sleep_regular(),
    "bedtime-list": _bedtime_list(),
    "exercise": _exercise(),
    "caffeine": _caffeine(),
    # Breaks
    "breaks": _breaks(),
    # How the brain learns
    "recall-strengthens": _recall_strengthens(),
    "surprise": _surprise(),
    "spacing-cells": _spacing_cells(),
    "curiosity": _curiosity(),
    "reward-memory": _reward_memory(),
    # Motivation and reward
    "rewards": _rewards(),
    "choice": _choice(),
    "effort-feels": _effort_feels(),
    "effort-reward": _effort_reward(),
    "mindset": _mindset(),
    # Habits
    "track": _track(),
    "if-then": _if_then(),
    "plan-it": _plan_it(),
    "habit": _habit(),
}


def cover(key: str) -> Markup:
    """A tip's cover, inline; empty for a tip without one."""
    inner = COVERS.get(key)
    if inner is None:
        return Markup("")
    return Markup(
        '<svg class="tip-cover" viewBox="0 0 300 112" preserveAspectRatio="xMidYMid meet" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true" focusable="false">{inner}</svg>'
    )
