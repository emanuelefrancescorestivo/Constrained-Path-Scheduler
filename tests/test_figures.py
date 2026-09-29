"""
The README's figures: every number the README quotes from them is what the code
computes (CLAUDE.md invariant 4), and every figure it shows exists, in both themes,
as SVG a browser can read.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def _figures():
    spec = importlib.util.spec_from_file_location("figures", ROOT / "benchmarks" / "figures.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["figures"] = module  # its dataclasses look their module up there
    spec.loader.exec_module(module)
    return module


def test_the_spacing_numbers_in_the_readme_are_the_models():
    curves = _figures().spacing_data()
    spaced, crammed, once = curves["spaced"], curves["crammed"], curves["once"]
    assert len(spaced["days"]) == 3 and len(crammed["days"]) == 4
    quoted = {
        f"keep {spaced['end']:.0%}": "spaced, twelve weeks after",
        f"keep {crammed['end']:.0%}": "crammed, twelve weeks after",
        f"({crammed['exam']:.1%} against {spaced['exam']:.1%})": "exam day",
        f"never again, {once['end']:.0%}": "studied once",
    }
    for text, what in quoted.items():
        assert text in README, f"README's {what} number is not {text!r}"
    # The honest part of the picture: cramming is ahead on exam day, behind later.
    assert crammed["exam"] > spaced["exam"] and spaced["end"] > crammed["end"]


def test_the_spacing_figure_draws_in_both_themes(tmp_path):
    figures = _figures()
    assert figures.main(["--only", "spacing", "--out", str(tmp_path)]) == 0
    for theme in ("light", "dark"):
        svg = ET.parse(tmp_path / f"spacing-{theme}.svg").getroot()
        text = " ".join(t.text or "" for t in svg.iter("{http://www.w3.org/2000/svg}text"))
        assert "Spaced self-tests (3 sessions)" in text and "Crammed (4 sessions)" in text
        assert "not a measurement of students" in text


def test_every_figure_the_readme_shows_exists_and_parses():
    shown = set(re.findall(r"docs/figures/([\w-]+)\.svg", README))
    assert {"spacing-light", "spacing-dark", "planner-vs-schedulers-light", "search-vs-rules-dark"} <= shown
    for name in shown:
        path = ROOT / "docs" / "figures" / f"{name}.svg"
        assert path.exists(), name
        root = ET.parse(path).getroot()
        assert root.tag.endswith("svg") and root.find("{http://www.w3.org/2000/svg}title") is not None
        assert "<script" not in path.read_text(encoding="utf-8")
