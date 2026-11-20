"""
D166: fractions, limits and words in a LaTeX PDF of notes. The owner's card, 2026-11-20:
"Recall that a functionf" on its front, "lim x→c f(x)–f(c) x–c" flattened on its back
("we still have not fixed the latex rendering in the flashcards?"). What must hold: a page
is read from where each piece of text is drawn, so words are spaced by the gaps the page
leaves; a thin line with text alone just above and below it is a fraction, inline or
displayed, one inside another too; a limit under "lim" is its subscript; TeX's big
parentheses and sums are named; an overline, an underline and a table's rules are not
fractions; a map's colon makes no card; a card draws a fraction as one and is never cut
inside it; and if pypdf's reading changes, the page is read as D151 read it.
"""

from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

from cps import cardmaker, notefiles, pdftext
from cps.web.app import _maths

PDF = Path(__file__).resolve().parent / "data" / "latex-fractions.pdf"


def _page_text() -> str:
    return pdftext.maths_text(PdfReader(PDF).pages[0])


def test_words_stay_apart_from_the_mathematics_beside_them():
    text = " ".join(_page_text().split())
    assert "Recall that a function f : (a, b) → R is differentiable at c ∈ (a, b) if" in text
    assert "the tangent line to the graph at (c, f(c))." in text
    assert "denoted by f′(c), is the slope" in text


def test_a_fraction_is_read_as_one_inline_displayed_and_inside_another():
    text = " ".join(_page_text().split())
    assert "if lim_{x→c} \\frac{f(x)−f(c)}{x−c} exists." in text  # inline, under its limit
    assert "(\\frac{u}{v})′ = \\frac{u′v − uv′}{v^{2}}" in text  # TeX's big parentheses, the primes kept
    assert "\\frac{1}{1 + \\frac{1}{x}} = \\frac{x}{x + 1}" in text
    assert "if |u_{n+1}/u_{n}| → ℓ < 1, the series ∑ u_{n} converges." in text


def test_an_overline_an_underline_and_a_table_are_not_fractions():
    text = _page_text()
    assert text.count("\\frac") == 6
    assert "The conjugate z of z has the same modulus, and this word is underlined." in " ".join(text.split())
    assert "Alpha\nBeta" in text


def test_the_derivative_s_paragraph_makes_no_card_cut_at_the_map_s_colon():
    notes = notefiles.text("notes.pdf", PDF.read_bytes()).text
    assert "if lim_{x→c} \\frac{f(x)−f(c)}{x−c} exists." in notes  # one line once joined
    fronts = [c.front for c in cardmaker.read(notes)]
    assert not any(f.startswith("Recall that a function") for f in fronts), fronts
    assert [c.kind for c in cardmaker.read("Vitamin C: ascorbic acid")] == ["term"]
    assert cardmaker.read("A map f : X → Y sends each x to one y") == []


def test_a_long_statement_is_never_cut_inside_a_fraction():
    long = "The derivative is " + "\\frac{f(x+h)−f(x)}{h} " * 6 + "as h tends to zero, and so on " * 4
    cut = cardmaker._trim(long, 60)
    assert cut.count("{") == cut.count("}") and not cut.rstrip(" …").endswith("\\frac"), cut


def test_a_card_draws_a_fraction_over_its_rule_and_escapes_the_rest():
    drawn = str(_maths("lim_{x→c} \\frac{f(x)−f(c)}{x−c} <b>"))
    fraction = '<span class="mfrac"><span class="mnum">f(x)−f(c)</span><span class="mden">x−c</span></span>'
    assert fraction in drawn
    assert "<sub>x→c</sub>" in drawn and "&lt;b&gt;" in drawn
    nested = str(_maths("\\frac{1}{1 + \\frac{1}{x}}"))
    assert nested.count('class="mfrac"') == 2 and "\\frac" not in nested
    assert str(_maths("\\frac{open")) == "\\frac{open"  # a mark left open stays as typed


def test_if_pypdf_s_layout_reading_fails_the_page_is_read_as_before(monkeypatch):
    def broken(page):
        raise AttributeError("a pypdf without its layout reading")

    monkeypatch.setattr(pdftext, "_runs", broken)
    text = _page_text()
    assert "differentiable" in text and "\\frac" not in text
    assert re.search(r"_\{x→c", text)  # D151's marks
