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
    from cps.web.app import _spans as _maths  # the drawing of D151, now the fallback (D199)

    j = "\u2060"  # no line break between a sign and its script
    drawn = str(_maths("R^{n} and (f|_{U})^{−1}"))
    assert drawn == f"R{j}<sup>n</sup> and (f|{j}<sub>U</sub>){j}<sup>−1</sup>"
    stacked = str(_maths("x^{2}_{i}"))
    assert stacked == f'x{j}<span class="msub-sup"><sup>2</sup><sub>i</sub></span>'
    assert str(_maths("<b>^{<i>}</b>")) == f"&lt;b&gt;{j}<sup>&lt;i&gt;</sup>&lt;/b&gt;"
    assert str(_maths("an open ^{ mark")) == "an open ^{ mark"


@pytest.mark.parametrize(
    "typed, drawn",
    [
        # D197: delimiters go; money stays money.
        ("$f(x)=x^2$", "f(x)=x⁠<sup>2</sup>"),
        (r"\(a \cdot b\)", "a · b"),
        ("costs $5 and $10", "costs $5 and $10"),
        ("$1$. And $n$", "1. And n"),
        # Symbols, Greek letters, sets and arrows become the characters they name.
        (r"\alpha + \beta \le \gamma", "α + β ≤ γ"),
        (r"x \in \mathbb{R} \Rightarrow x^2 \geq 0", "x ∈ ℝ ⇒ x⁠<sup>2</sup> ≥ 0"),
        (r"\forall \epsilon > 0 \; \exists \delta", "∀ ϵ &gt; 0   ∃ δ"),
        # A root over its radicand; a function's name upright, its limits under it.
        (r"\sqrt{2}", '⁠<span class="msqrt">√<span class="mrad">2</span></span>'),
        (r"\lim_{x \to 0} x", "lim⁠<sub>x → 0</sub> x"),
        (r"\sin x", "sin x"),
        # Bare scripts as braced ones, a sum's and an integral's limits stacked.
        ("v_1 + a_n", "v⁠<sub>1</sub> + a⁠<sub>n</sub>"),
        (r"\int_0^1 x", '∫⁠<span class="msub-sup"><sup>1</sup><sub>0</sub></span> x'),
        # Words with underscores and unknown commands stay as typed.
        ("snake_case file_name", "snake_case file_name"),
        (r"\weird{x}", r"\weird{x}"),
        (
            r"\left( \frac{a}{b} \right)",
            '( ⁠<span class="mfrac"><span class="mnum">a</span><span class="mden">b</span></span>⁠ )',
        ),
    ],
)
def test_a_card_reads_the_latex_it_is_likely_to_hold(typed, drawn):
    from cps.web.app import _spans as _maths  # D197, now the fallback (D199)

    assert str(_maths(typed)) == drawn


def test_latex_on_a_card_never_lets_markup_through():
    from cps.web.app import _spans as _maths  # D197, now the fallback (D199)

    html = str(_maths(r"$\sqrt{<script>}$ \text{<b>x</b>} \mathbf{<i>}"))
    assert "<script>" not in html and "<i>" not in html and "&lt;script&gt;" in html


# D199: the mathematics typeset by the browser, as MathML.


def test_a_formula_alone_on_a_card_is_typeset_apart():
    from cps.web.app import _maths

    html = str(_maths(r"\sum_{i=1}^{n} i = \frac{n(n+1)}{2}"))
    assert html.startswith('<math display="block">') and html.endswith("</math>")
    assert '<munderover><mo largeop="true" movablelimits="true">∑</mo>' in html  # limits above and below
    assert "<mfrac><mrow><mi>n</mi><mo>(</mo>" in html
    # In a list of cards, in its line.
    assert str(_maths(r"\frac{a}{b}", block=False)).startswith("<math><mfrac>")


@pytest.mark.parametrize(
    "typed, parts",
    [
        # Notes read from a LaTeX PDF: the formulas in a sentence, the words around them.
        (
            "the map f: R^{n} → R^{m} is of class C^{1}.",
            [
                "the map ",
                "<math><mi>f</mi><mo>:</mo><msup><mi>R</mi>",
                "</math> is of class <math><msup><mi>C</mi>",
            ],
        ),
        ("Let $x \\in \\mathbb{R}$ be real", ["Let <math><mi>x</mi><mo>∈</mo><mi>ℝ</mi></math> be real"]),
        (r"\sqrt{2} \approx 1.414", ["<msqrt><mrow><mn>2</mn></mrow></msqrt><mo>≈</mo><mn>1.414</mn>"]),
        (r"\sqrt[3]{x}", ["<mroot><mrow><mi>x</mi></mrow><mrow><mn>3</mn></mrow></mroot>"]),
        (r"\lim_{x \to 0} \frac{\sin x}{x} = 1", ["<munder><mi>lim</mi>", "<mi>sin</mi><mo>&#x2061;</mo>"]),
        (
            r"\int_0^1 x^2 \, dx",
            ['<msubsup><mo largeop="true">∫</mo><mn>0</mn><mn>1</mn></msubsup>', "<mspace"],
        ),
        (
            r"\vec{v} \cdot \hat{n}",
            ['<mover accent="true"><mrow><mi>v</mi></mrow><mo>→</mo></mover><mo>⋅</mo>'],
        ),
        (
            r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}",
            [
                '<mo fence="true" stretchy="true">(</mo><mtable><mtr>'
                "<mtd><mi>a</mi></mtd><mtd><mi>b</mi></mtd></mtr>"
            ],
        ),
        (
            r"|x| = \begin{cases} x & x \ge 0 \\ -x & \text{otherwise} \end{cases}",
            ['<mtable columnalign="left">', "<mtext>otherwise</mtext>", "<mo>−</mo><mi>x</mi>"],
        ),
        (r"\left( \frac{a}{b} \right)^2", ['<msup><mrow><mo fence="true" stretchy="true">(</mo><mfrac>']),
        # Words with underscores, money and unknown commands stay as typed.
        ("snake_case and file_name", ["snake_case and file_name"]),
        ("costs $5 and $10", ["costs $5 and $10"]),
    ],
)
def test_a_card_s_latex_is_typeset_as_mathml(typed, parts):
    from cps.web.app import _maths

    html = str(_maths(typed))
    for part in parts:
        assert part in html, (part, html)


def test_mathml_never_lets_markup_through_and_falls_back_when_unreadable():
    from cps.web.app import _maths

    html = str(_maths(r"$\text{<script>x</script>} + \mathrm{<i>}$"))
    assert "<script>" not in html and "<i>" not in html and "&lt;script&gt;" in html
    # A brace left open cannot be typeset: the spans draw what they can (D197).
    assert "<math" not in str(_maths(r"\frac{1}{2")) and "\\frac{1}{2" in str(_maths(r"\frac{1}{2"))
    assert str(_maths(r"\weird{x} + y^2")).count("<mtext>\\weird</mtext>") == 1
