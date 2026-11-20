"""
D151: mathematics in flashcards made from a LaTeX PDF (the owner, 2026-11-19: "the maths
need to be properly rendered in the flashcards or else it is impossible to learn anything
from them"). What must hold: a page of notes keeps its words apart from the mathematics
beside them, marks what is raised (`^{...}`) or lowered (`_{...}`), and writes TeX's "not"
over a sign as one sign; a syllabus is read as before; an equation is not a term; a
statement is cut at a sentence or a word, never inside a mark; a card draws its marks and
escapes everything else.

`data/latex-notes.pdf` is `data/latex-notes.tex` made by pdfTeX 1.40.25 (TeX Live 2023):
`pdflatex latex-notes.tex` makes it again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pypdf")
from pypdf import PdfReader

from cps import cardmaker, notefiles, pdftext

PDF = Path(__file__).resolve().parent / "data" / "latex-notes.pdf"


def test_a_latex_page_keeps_its_words_apart_and_its_mathematics_marked():
    page = PdfReader(PDF).pages[0]
    plain = page.extract_text()
    # What pypdf alone gives: words run into the mathematics, powers flattened.
    assert "functionf" in plain and "classC1" in plain and "^{" not in plain
    text = " ".join(pdftext.maths_text(page).split())
    for part in (
        "consider a function f : I → R of class C^{1}, where I ⊆ R",
        "If f′(c) ≠ 0, c ∈ I, then f is strictly monotone",
        "neighbourhood of c. Consequently",
        "Let A ⊆ R^{n} be open, let c ∈ A",
        "such that J_{f}(c) ≠ 0",
        "the restriction f|_{U} : U → W",
        "whose inverse (f|_{U})^{−1} : W → U is of class C^{1}",
        "D(f|_{U})^{−1}(y) = Df(x)^{−1}",
        "(g_{1}, h_{1})(g_{2}, h_{2}) = (g_{1}g_{2}, h_{1}h_{2})",
        "x^{2}_{i}",  # x with i below and 2 above
        "at most \\frac{1}{2}∥x∥^{2}",  # a fraction, its 1 over its 2 (D166; D151 stacked them)
        "∑",  # TeX's sum, written with a letter's code in its own font
    ):
        assert part in text, part
    assert not text.rstrip().endswith(" 1")  # the page's number is not part of the notes


def test_notes_are_read_with_their_mathematics_and_a_syllabus_as_before():
    data = PDF.read_bytes()
    assert "R^{n}" in notefiles.text("notes.pdf", data).text
    assert "^{" not in pdftext.text(data).text  # a program's PDF (D90): "1^{er}" would break its dates


def test_an_equation_is_not_a_term_and_a_theorem_is_one_card():
    found = cardmaker.read(notefiles.text("notes.pdf", PDF.read_bytes()).text)
    fronts = [c.front for c in found]
    assert not any(f.startswith("In dimension") for f in fronts)  # "n = 1" is an equation
    theorem = next(c for c in found if c.front == "Theorem: Inverse Function Theorem")
    assert theorem.back.startswith("Let A ⊆ R^{n} be open")
    assert "(f|_{U})^{−1} : W → U is of class C^{1}" in theorem.back
    # "Vitamin C = ascorbic acid" stays a term: what follows its "=" is words, not mathematics.
    assert [c.kind for c in cardmaker.read("Vitamin C = ascorbic acid")] == ["term"]


def test_a_long_statement_is_cut_at_a_sentence_and_never_inside_a_mark():
    first = "The first sentence of the statement holds for every open set of the plane we study."
    text = first + " " + "Then R^{n} is open and " * 30
    cut = cardmaker._trim(text, 120)
    assert cut == first  # a sentence's end past half the length is where it stops
    words = "word " * 20 + "R^{nnnnnn}"
    cut = cardmaker._trim(words, 104)
    assert "^{" not in cut and cut.endswith(" …") and len(cut) <= 106


def test_a_card_draws_its_marks_and_escapes_the_rest():
    pytest.importorskip("fastapi")
    from cps.web.app import _maths

    j = "\u2060"  # no line break between a sign and its script
    drawn = str(_maths("R^{n} and (f|_{U})^{−1}"))
    assert drawn == f"R{j}<sup>n</sup> and (f|{j}<sub>U</sub>){j}<sup>−1</sup>"
    stacked = str(_maths("x^{2}_{i}"))
    assert stacked == f'x{j}<span class="msub-sup"><sup>2</sup><sub>i</sub></span>'
    assert str(_maths("<b>^{<i>}</b>")) == f"&lt;b&gt;{j}<sup>&lt;i&gt;</sup>&lt;/b&gt;"
    assert str(_maths("an open ^{ mark")) == "an open ^{ mark"
