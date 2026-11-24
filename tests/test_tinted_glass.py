"""
Calm glass (DECISIONS.md D132): the glass is tinted unless the student chooses
See-through, and nothing on the app's own pages moves as a page scrolls; only the
explaining pages keep their scenes, and only with motion on. Movement tied to scrolling
is what brings on dizziness and nausea (WCAG 2.3.3).
"""

from __future__ import annotations

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "src/cps/web/static/style.css").read_text(encoding="utf-8")
PLAIN = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
# The explaining pages' figures and scenes (D109, D112, D113), and the front page's
# story, which explains the app as it is scrolled (D188): the only things that may
# follow the scroll, each under :root:not([data-motion="reduce"]).
EXPLAINING = (".scroll-fig", ".code-", ".science-block", ".sci-figure", ".landing-")


def _rules() -> list[tuple[str, str]]:
    """Every innermost rule of the style sheet: its selector and its declarations."""
    return [(" ".join(sel.split()), body) for sel, body in re.findall(r"([^{}]*)\{([^{}]*)\}", PLAIN)]


def test_only_the_explaining_pages_move_with_the_scroll():
    tied = [sel for sel, body in _rules() if re.search(r"(animation|view)-timeline\s*:\s*(?!none)", body)]
    assert tied
    for selector in tied:
        if selector == ".topbar":
            # The top bar's colour comes in as the page scrolls under it (D37): a colour,
            # nothing that moves.
            frames = re.search(r"@keyframes bar-solid\s*\{(.*?)\}\s*\}", PLAIN, flags=re.S).group(1)
            assert "transform" not in frames and "translate" not in frames
            continue
        assert any(name in selector for name in EXPLAINING), selector
        assert ':root:not([data-motion="reduce"])' in selector, selector


def test_pages_fade_into_each_other_without_moving():
    frames = re.search(r"@keyframes vt-in\s*\{([^}]*)\}", PLAIN).group(1)
    assert "opacity" in frames and "transform" not in frames and "translate" not in frames


def _alpha(value: str) -> float:
    return float(re.search(r"/\s*([\d.]+)%\s*\)", value).group(1)) / 100


def test_the_glass_is_tinted_unless_see_through_is_chosen():
    blocks = re.findall(
        r':root(?::not\(\[data-theme="light"\]\))?(?:\[data-theme="dark"\])?:not\(\[data-glass="clear"\]\)\s*\{([^{}]*)\}',
        PLAIN,
    )
    assert len(blocks) == 3  # light, dark by the device, dark by the plan's choice
    for block in blocks:
        tokens = dict(re.findall(r"(--glass-[a-z-]+)\s*:\s*([^;]+);", block))
        for name in ("--glass-top", "--glass-bottom", "--glass-bar", "--glass-board"):
            assert _alpha(tokens[name]) >= 0.74, (name, tokens[name])
    # D73's clear glass is still there, for those who choose See-through.
    clear = re.search(r":root\s*\{[^{}]*--glass-top:\s*([^;]+);", PLAIN).group(1)
    assert _alpha(clear) < 0.6
