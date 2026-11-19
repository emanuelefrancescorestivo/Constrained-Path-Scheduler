"""
The tips' pictures (DECISIONS.md D160): one small line drawing per tip, of what the advice
asks, in the weight of the app's icons (Lucide's round ends, D139), drawn inline by the
server. The owner chose them from a page of three options ("a mix of A and B, especially
B", 2026-11-19): B's drawings, with A's number under them where the study gives one
(`tips.FIGURES`).

Each drawing is a few shapes on a 64-unit square, stroked in the text's colour. Classes,
never inline styles (the Content-Security-Policy): `a` strokes in the accent, `f` fills
and strokes in it, `w` strokes in the colour on the accent, `s` fills with the card behind
(to hide a line under a shape), `m` is quieter. They are decoration: the card's words
say the same, so each is hidden from screen readers.
"""

from __future__ import annotations

from markupsafe import Markup

_HABIT = "".join(
    f'<circle cx="{x}" cy="32" r="5" class="{cls}"/>'
    for x, cls in ((8, "f"), (20, "f"), (32, "s m"), (44, "f"), (56, "f"))
)
_REGULAR = "".join(
    f'<rect x="{6 + k * 7.5}" y="26" width="4.5" height="28" rx="2" class="f"/>' for k in range(7)
)

DRAWINGS: dict[str, str] = {
    # How to study
    "self-test": (
        '<rect x="12" y="18" width="30" height="22" rx="4" class="m"/>'
        '<rect x="18" y="12" width="30" height="22" rx="4" class="s"/>'
        '<path d="M29 19a4 4 0 1 1 5.5 3.7c-1.3.6-1.5 1.4-1.5 2.8"/><circle cx="33" cy="30" r="0.8" class="f"/>'
        '<circle cx="46" cy="46" r="8" class="f"/><path d="M42.5 46l2.5 2.5 4.5-5" class="w"/>'
    ),
    "spacing": (
        '<path d="M6 46h52" class="m"/>'
        '<path d="M9 42c2-6 6-6 8 0M17 42c3-10 11-10 14 0M31 42c5-17 18-17 23 0" class="a"/>'
        '<circle cx="9" cy="46" r="3.5" class="f"/><circle cx="17" cy="46" r="3.5" class="f"/>'
        '<circle cx="31" cy="46" r="3.5" class="f"/><circle cx="54" cy="46" r="3.5" class="f"/>'
    ),
    "class-quizzes": (
        '<rect x="12" y="10" width="30" height="44" rx="4"/>'
        '<rect x="17" y="18" width="6" height="6" rx="1.5"/><path d="M27 21h10"/>'
        '<rect x="17" y="29" width="6" height="6" rx="1.5" class="f"/><path d="M18.5 32l1.5 1.5 2.5-3" class="w"/>'
        '<path d="M27 32h10"/><rect x="17" y="40" width="6" height="6" rx="1.5"/><path d="M27 43h7"/>'
        '<path d="M44 48l10-24 4 2-10 24-5 2z" class="a s"/>'
    ),
    "interleave": (
        '<circle cx="10" cy="18" r="4" class="m"/><circle cx="21" cy="18" r="4" class="m"/>'
        '<rect x="28" y="14" width="8" height="8" rx="1.5" class="m"/><rect x="39" y="14" width="8" height="8" rx="1.5" class="m"/>'
        '<path d="M54 14l4.5 8h-9z" class="m"/>'
        '<circle cx="10" cy="44" r="4" class="f"/><rect x="17" y="40" width="8" height="8" rx="1.5"/>'
        '<path d="M32 40l4.5 8h-9z" class="f"/><circle cx="43" cy="44" r="4"/>'
        '<rect x="50" y="40" width="8" height="8" rx="1.5" class="f"/>'
    ),
    "pretest": (
        '<rect x="6" y="24" width="34" height="28" rx="3"/><path d="M12 32h18M12 38h22M12 44h12" class="m"/>'
        '<path d="M40 8h16a4 4 0 0 1 4 4v10a4 4 0 0 1-4 4h-8l-5 5v-5h-3a4 4 0 0 1-4-4V12a4 4 0 0 1 4-4z" class="a s"/>'
        '<path d="M45 14a3 3 0 1 1 4.2 2.8c-1 .5-1.2 1-1.2 2" class="a"/><circle cx="48" cy="22" r="0.8" class="f"/>'
    ),
    "attend": (
        '<rect x="16" y="8" width="32" height="14" rx="2"/>'
        '<path d="M10 34c7-4 37-4 44 0M8 44c8-5 40-5 48 0M6 54c9-6 43-6 52 0" class="m"/>'
        '<circle cx="32" cy="40" r="3.5" class="f"/>'
    ),
    # Sleep, movement and coffee
    "sleep-after": (
        '<path d="M40 8a12 12 0 1 0 14 15 9.5 9.5 0 0 1-14-15z" class="a"/>'
        '<path d="M8 42c6-3 12-3 18 0v14c-6-3-12-3-18 0zM26 42c6-3 12-3 18 0v14c-6-3-12-3-18 0z"/>'
    ),
    "sleep-regular": _REGULAR + '<path d="M50 6a7 7 0 1 0 8 9 5.5 5.5 0 0 1-8-9z"/>',
    "bedtime-list": (
        '<rect x="8" y="12" width="32" height="44" rx="4"/>'
        '<circle cx="15" cy="22" r="1.4" class="f"/><path d="M20 22h13"/>'
        '<circle cx="15" cy="31" r="1.4" class="f"/><path d="M20 31h13"/>'
        '<circle cx="15" cy="40" r="1.4" class="f"/><path d="M20 40h10"/>'
        '<path d="M50 8a8 8 0 1 0 9 10 6 6 0 0 1-9-10z" class="a"/>'
    ),
    "exercise": (
        '<path d="M6 44h12l4-10 6 20 6-16 4 6h20" class="a"/>'
        '<path d="M18 16h28M8 13v6M56 13v6"/><rect x="12" y="10" width="6" height="12" rx="2"/>'
        '<rect x="46" y="10" width="6" height="12" rx="2"/>'
    ),
    "caffeine": (
        '<path d="M8 28h28v14a10 10 0 0 1-10 10h-8A10 10 0 0 1 8 42z"/><path d="M36 32h3a5 5 0 0 1 0 10h-3"/>'
        '<path d="M16 22c-2-3 2-5 0-8M25 22c-2-3 2-5 0-8" class="a"/>'
        '<circle cx="51" cy="16" r="8"/><path d="M51 12v4l3 2"/>'
    ),
    # Breaks
    "breaks": (
        '<circle cx="32" cy="36" r="20"/><path d="M32 36V16a20 20 0 0 1 17.3 10z" class="f"/>'
        '<path d="M32 36l-8 6M28 8h8M32 8v8"/>'
    ),
    # How the brain learns
    "recall-strengthens": (
        '<rect x="10" y="38" width="9" height="16" rx="2" class="m"/>'
        '<rect x="27" y="28" width="9" height="26" rx="2" class="m"/>'
        '<rect x="44" y="14" width="9" height="40" rx="2" class="f"/>'
        '<path d="M19 33l6-4M36 22l6-4" class="a"/>'
    ),
    "surprise": (
        '<path d="M32 6l5 10 11-4-4 11 10 5-10 5 4 11-11-4-5 10-5-10-11 4 4-11-10-5 10-5-4-11 11 4z" class="a"/>'
        '<path d="M32 22v10"/><circle cx="32" cy="38" r="1.2" class="f"/>'
    ),
    "spacing-cells": (
        '<circle cx="44" cy="32" r="14"/><circle cx="46" cy="30" r="5" class="m"/>'
        '<circle cx="6" cy="32" r="2.2" class="f"/><circle cx="13" cy="32" r="2.2" class="f"/>'
        '<circle cx="20" cy="32" r="2.2" class="f"/><circle cx="27" cy="32" r="2.2" class="f"/>'
    ),
    "curiosity": (
        '<circle cx="26" cy="26" r="15"/><path d="M37 37l15 15" stroke-width="4"/>'
        '<path d="M22 21a4.5 4.5 0 1 1 6 4.2c-1.2.5-1.6 1.3-1.6 2.6" class="a"/>'
        '<circle cx="26.4" cy="32" r="1" class="f"/>'
    ),
    "reward-memory": (
        '<rect x="8" y="22" width="36" height="30" rx="3"/>'
        '<path d="M12 48l9-11 7 7 5-5 8 9" class="m"/><circle cx="18" cy="30" r="2.5" class="m"/>'
        '<path d="M50 6l2.6 5.4 5.9.8-4.3 4.1 1 5.8L50 19.3l-5.2 2.8 1-5.8-4.3-4.1 5.9-.8z" class="f"/>'
    ),
    # Motivation and reward
    "rewards": (
        '<rect x="8" y="40" width="48" height="8" rx="4" class="m"/>'
        '<rect x="8" y="40" width="34" height="8" rx="4" class="f"/>'
        '<path d="M42 40V14"/><path d="M42 14h12l-3 4 3 4H42" class="a"/>'
    ),
    "choice": (
        '<path d="M32 58V38"/><path d="M32 38L14 18M32 38l18-20" class="m"/><path d="M32 38V15" class="a"/>'
        '<circle cx="14" cy="16" r="3" class="m"/><circle cx="32" cy="11" r="3.5" class="f"/>'
        '<circle cx="50" cy="16" r="3" class="m"/>'
    ),
    "effort-feels": (
        '<path d="M6 54h52" class="m"/>'
        '<rect x="14" y="38" width="12" height="16" rx="2" stroke-dasharray="3 3"/>'
        '<rect x="38" y="14" width="12" height="40" rx="2" class="f"/>'
    ),
    "effort-reward": (
        '<path d="M6 56L26 22l10 14 6-8 16 28z"/>'
        '<path d="M26 22V7" class="a"/><path d="M26 7h10l-2.5 3.5L36 14H26" class="f"/>'
    ),
    # A balance at rest: believing intelligence grows did little for grades.
    "mindset": (
        '<path d="M32 10v40M22 54h20M10 20h44"/>'
        '<path d="M10 20L5 32M10 20l5 12M54 20l-5 12M54 20l5 12" class="m"/>'
        '<path d="M4 32a6 3.5 0 0 0 12 0zM48 32a6 3.5 0 0 0 12 0z" class="a"/>'
        '<circle cx="32" cy="10" r="2.5" class="f"/>'
    ),
    # Habits
    "track": (
        '<rect x="14" y="8" width="32" height="44" rx="4"/>'
        '<path d="M20 19l2 2 4-4M30 19h10M20 30l2 2 4-4M30 30h10M20 41l2 2 4-4M30 41h7"/>'
        '<circle cx="46" cy="50" r="8" class="f"/><path d="M42.5 50l2.5 2.5 4.5-5" class="w"/>'
    ),
    "if-then": (
        '<rect x="6" y="14" width="32" height="32" rx="4"/><path d="M6 23h32M14 9v8M30 9v8"/>'
        '<path d="M13 31h6M13 38h12" class="m"/>'
        '<circle cx="40" cy="46" r="10" class="s"/><path d="M40 41v5l3.5 2"/>'
        '<path d="M58 20c0 5-6 10-6 10s-6-5-6-10a6 6 0 0 1 12 0z" class="a"/><circle cx="52" cy="20" r="2" class="a"/>'
    ),
    "plan-it": (
        '<path d="M8 16h24M8 25h18M8 34h20"/>'
        '<path d="M30 34c8 0 12 3 14 9" class="a"/><path d="M40 42l4 2 1-4.5" class="a"/>'
        '<rect x="36" y="46" width="22" height="14" rx="3"/><path d="M36 51h22"/>'
    ),
    "habit": '<path d="M8 32h48" class="m"/>'
    + _HABIT.replace('class="s m"', 'class="s" stroke-dasharray="3 3"'),
}


def drawing(key: str) -> Markup:
    """A tip's picture, inline; empty for a tip without one."""
    inner = DRAWINGS.get(key)
    if inner is None:
        return Markup("")
    return Markup(
        '<svg class="tip-art" viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="2.2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
        f"{inner}</svg>"
    )
