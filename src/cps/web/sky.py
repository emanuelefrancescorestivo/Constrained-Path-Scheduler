"""
The sky's colours along the day (DECISIONS.md D76): a base and four soft lights for
each moment, in light and in dark mode, drawn along `cps.daylight.day_position` (0
deep night, 1 sunrise, 2 the highest sun, 3 sunset, 4 night again) and blended
between moments, so that the sky turns as slowly as the real one.

A page asks for its sky as a small style sheet, `/sky/<step>.css`, one of `STEPS`
along the day (a step is a few minutes), the same for everyone and kept a day; its
variables are what the calm-glass rules in style.css paint (D73). The live icon
(`icon.py`) takes its colours from the same moment, so the two always agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

STEPS = 400  # 0.01 of the day's position each: a few minutes of a summer's day

Colour = tuple[int, int, int]
Sky = tuple[Colour, Colour, Colour, Colour, Colour]  # the base, then the four lights


@dataclass(frozen=True)
class Moment:
    at: float  # the day's position, 0 to 4
    light: tuple[str, Colour, Colour, Colour, Colour]
    dark: tuple[str, Colour, Colour, Colour, Colour]


# Each moment: the base, then the four lights (upper left, upper right, lower left,
# lower right). Daytime is blue sky with a little sun in it; the hours around
# sunrise and sunset are warm; twilight is violet and indigo; night is deep blue.
MOMENTS = (
    Moment(0.00, ("#e9e8f6", (176, 178, 236), (200, 186, 240), (156, 190, 222), (214, 202, 240)),
           ("#070b1d", (36, 50, 130), (76, 46, 136), (14, 80, 96), (34, 42, 108))),
    Moment(0.55, ("#eceefb", (168, 182, 242), (206, 190, 246), (188, 214, 246), (234, 200, 232)),
           ("#0a0e26", (48, 62, 156), (102, 62, 152), (36, 84, 146), (118, 62, 112))),
    Moment(0.92, ("#fbeff3", (214, 186, 246), (255, 180, 200), (190, 200, 250), (255, 200, 170)),
           ("#140f22", (96, 64, 160), (150, 62, 98), (52, 70, 150), (150, 84, 60))),
    Moment(1.05, ("#fff2ec", (255, 192, 168), (255, 178, 204), (255, 222, 160), (200, 206, 255)),
           ("#170f1c", (156, 74, 78), (122, 56, 112), (146, 102, 42), (52, 72, 142))),
    Moment(1.25, ("#fff7ee", (255, 224, 168), (182, 214, 255), (255, 204, 184), (198, 236, 224)),
           ("#101a2c", (58, 112, 196), (168, 126, 56), (36, 122, 140), (108, 92, 158))),
    Moment(1.55, ("#eef6ff", (160, 206, 255), (176, 236, 222), (206, 214, 255), (190, 232, 255)),
           ("#0b1830", (32, 104, 196), (20, 132, 150), (70, 88, 184), (40, 120, 170))),
    Moment(2.45, ("#eef6ff", (160, 206, 255), (190, 232, 255), (176, 236, 222), (255, 230, 186)),
           ("#0b1830", (32, 104, 196), (40, 120, 170), (20, 132, 150), (120, 108, 60))),
    Moment(2.70, ("#f6f6fb", (170, 206, 255), (255, 222, 168), (190, 222, 246), (255, 206, 176)),
           ("#0f182c", (40, 98, 190), (176, 122, 46), (30, 116, 152), (150, 92, 60))),
    Moment(2.90, ("#fff4e6", (255, 208, 140), (170, 204, 255), (255, 186, 158), (220, 202, 255)),
           ("#1a1420", (196, 124, 38), (52, 84, 176), (186, 84, 58), (104, 62, 162))),
    Moment(3.03, ("#fdeeef", (255, 164, 128), (255, 170, 200), (190, 166, 255), (255, 204, 138)),
           ("#180e1d", (176, 72, 58), (142, 52, 112), (84, 52, 172), (166, 102, 40))),
    Moment(3.35, ("#efe9fa", (188, 168, 252), (250, 174, 210), (148, 164, 242), (212, 184, 242)),
           ("#0f0c25", (92, 60, 172), (142, 60, 112), (40, 60, 152), (112, 50, 122))),
    Moment(3.70, ("#eae9f8", (172, 172, 240), (204, 184, 244), (150, 176, 232), (216, 196, 242)),
           ("#090c22", (40, 52, 146), (88, 50, 144), (22, 70, 116), (60, 44, 120))),
    Moment(4.00, ("#e9e8f6", (176, 178, 236), (200, 186, 240), (156, 190, 222), (214, 202, 240)),
           ("#070b1d", (36, 50, 130), (76, 46, 136), (14, 80, 96), (34, 42, 108))),
)  # fmt: skip


def step_of(position: float) -> int:
    """The step of the day a position falls in (0 .. STEPS - 1)."""
    return round(position / 4 * STEPS) % STEPS


def _hex(color: str) -> Colour:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _mix(a: Colour, b: Colour, u: float) -> Colour:
    return (round(a[0] + (b[0] - a[0]) * u), round(a[1] + (b[1] - a[1]) * u), round(a[2] + (b[2] - a[2]) * u))


def colours(step: int, mode: str) -> Sky:
    """The base and the four lights at a step, in "light" or "dark" mode, blended
    between the two moments around it."""
    if not 0 <= step < STEPS:
        raise ValueError(f"a step of the sky is 0 to {STEPS - 1}, not {step!r}")
    if mode not in ("light", "dark"):
        raise ValueError(f"no sky for mode {mode!r}")
    position = step / STEPS * 4
    for before, after in pairwise(MOMENTS):
        if before.at <= position <= after.at:
            u = (position - before.at) / (after.at - before.at)
            a, b = getattr(before, mode), getattr(after, mode)
            return (
                _mix(_hex(a[0]), _hex(b[0]), u),
                _mix(a[1], b[1], u),
                _mix(a[2], b[2], u),
                _mix(a[3], b[3], u),
                _mix(a[4], b[4], u),
            )
    raise AssertionError("the moments cover the whole day")


def _rules(step: int, mode: str) -> str:
    base, *lights = colours(step, mode)
    values = [f"--sky-base: rgb({base[0]} {base[1]} {base[2]});"]
    values += [f"--sky-{i}: {c[0]} {c[1]} {c[2]};" for i, c in enumerate(lights, 1)]
    return " ".join(values)


def stylesheet(step: int) -> str:
    """The sky at a step, for both modes, in the same selectors as the app's own
    light and dark rules (the device's mode, or the plan's own setting)."""
    light, dark = _rules(step, "light"), _rules(step, "dark")
    device = ':root:not([data-theme="light"]) body[data-sky]'
    return (
        f"/* the sky at step {step} of {STEPS} (cps.web.sky, D76) */\n"
        f"body[data-sky] {{ {light} }}\n"
        f"@media (prefers-color-scheme: dark) {{ {device} {{ {dark} }} }}\n"
        f':root[data-theme="dark"] body[data-sky] {{ {dark} }}\n'
    )
