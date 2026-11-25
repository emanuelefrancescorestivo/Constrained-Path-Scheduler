"""
A card's mathematics typeset by the browser (DECISIONS.md D199): LaTeX read into MathML.

D151, D166 and D197 drew a card's mathematics with spans: a superscript was a `<sup>`, a
fraction two stacked boxes. It read, but it did not look like mathematics: the letters were
upright, a sum's limits sat at its side, a root's bar was a border. The browsers the app
supports typeset MathML Core themselves (Chrome and Edge since 109, Safari, Firefox), with the
device's mathematics font, as a LaTeX document is typeset. So the server reads the LaTeX of a
card into MathML: `x^{2}` a `<msup>`, `\\frac{a}{b}` a `<mfrac>`, `\\sum_{i=1}^{n}` a
`<munderover>` with its limits above and below, `\\begin{pmatrix}` a `<mtable>` in brackets.

What is mathematics in a card's text: what `$…$`, `$$…$$`, `\\(…\\)` or `\\[…\\]` hold, and, without
them, a run of signs around a mark of LaTeX (`\\`, `^`, `_`) that no word of two letters or more
interrupts, as notes read from a LaTeX PDF mark it (`R^{n} → R^{m}`). A formula alone on a card is
set apart, as LaTeX's display mathematics. What cannot be read (a brace left open) is left to the
spans of D197, and everything is escaped: no markup gets through.

Nothing to install and nothing run in the page: MathML is markup, under the app's
Content-Security-Policy, and it reads without a script.
"""

from __future__ import annotations

import re
from html import escape

# Letters as LaTeX's fonts set them: blackboard bold, script.
_DOUBLE = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ", strict=True))
_SCRIPT = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "𝒜ℬ𝒞𝒟ℰℱ𝒢ℋℐ𝒥𝒦ℒℳ𝒩𝒪𝒫𝒬ℛ𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵", strict=True))
_FRAKTUR = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "𝔄𝔅ℭ𝔇𝔈𝔉𝔊ℌℑ𝔍𝔎𝔏𝔐𝔑𝔒𝔓𝔔ℜ𝔖𝔗𝔘𝔙𝔚𝔛𝔜ℨ", strict=True))


def _pairs(words: str) -> dict[str, str]:
    parts = words.split()
    return dict(zip(parts[::2], parts[1::2], strict=True))


# Letters: identifiers, set in italic when they are one letter, as LaTeX does.
_LETTERS = _pairs(
    "alpha α beta β gamma γ delta δ epsilon ϵ varepsilon ε zeta ζ eta η theta θ vartheta ϑ iota ι "
    "kappa κ lambda λ mu μ nu ν xi ξ pi π varpi ϖ rho ρ varrho ϱ sigma σ varsigma ς tau τ upsilon υ "
    "phi ϕ varphi φ chi χ psi ψ omega ω Gamma Γ Delta Δ Theta Θ Lambda Λ Xi Ξ Pi Π Sigma Σ "
    "Upsilon Υ Phi Φ Psi Ψ Omega Ω infty ∞ partial ∂ nabla ∇ hbar ℏ ell ℓ aleph ℵ emptyset ∅ "
    "varnothing ∅ Re ℜ Im ℑ wp ℘ imath ı jmath ȷ"
)
# Operators and relations: set upright, spaced by the browser as their kind asks.
_OPERATORS = _pairs(
    "cdot ⋅ times × div ÷ pm ± mp ∓ ast ∗ star ⋆ circ ∘ bullet ∙ le ≤ leq ≤ ge ≥ geq ≥ ne ≠ neq ≠ "
    "approx ≈ equiv ≡ sim ∼ simeq ≃ cong ≅ propto ∝ ll ≪ gg ≫ to → rightarrow → leftarrow ← gets ← "
    "Rightarrow ⇒ Leftarrow ⇐ implies ⟹ impliedby ⟸ iff ⟺ Leftrightarrow ⇔ leftrightarrow ↔ "
    "longrightarrow ⟶ longmapsto ⟼ mapsto ↦ uparrow ↑ downarrow ↓ hookrightarrow ↪ in ∈ notin ∉ "
    "ni ∋ subset ⊂ subseteq ⊆ subsetneq ⊊ supset ⊃ supseteq ⊇ cup ∪ cap ∩ setminus ∖ forall ∀ "
    "exists ∃ nexists ∄ neg ¬ lnot ¬ land ∧ wedge ∧ lor ∨ vee ∨ oplus ⊕ otimes ⊗ perp ⊥ "
    "parallel ∥ mid ∣ nmid ∤ angle ∠ ldots … dots … cdots ⋯ vdots ⋮ ddots ⋱ prime ′ "
    "langle ⟨ rangle ⟩ lfloor ⌊ rfloor ⌋ lceil ⌈ rceil ⌉ vert | Vert ‖ lvert | rvert | lVert ‖ "
    "rVert ‖ colon : models ⊨ vdash ⊢ triangle △ square □ degree ° backslash ∖ top ⊤ bot ⊥ "
    "sqcup ⊔ sqcap ⊓ uplus ⊎ preceq ⪯ succeq ⪰ prec ≺ succ ≻ lesssim ≲ gtrsim ≳ dagger † "
    "therefore ∴ because ∵"
)
# Big operators: their limits go above and below, in display; at their side, in a line.
_BIG = _pairs("sum ∑ prod ∏ coprod ∐ bigcup ⋃ bigcap ⋂ bigoplus ⨁ bigotimes ⨂ bigvee ⋁ bigwedge ⋀")
_INTEGRALS = _pairs("int ∫ iint ∬ iiint ∭ oint ∮")
# Functions, set upright; those whose limits go under them.
_FUNCTIONS = frozenset(
    (
        "sin",
        "cos",
        "tan",
        "cot",
        "sec",
        "csc",
        "arcsin",
        "arccos",
        "arctan",
        "sinh",
        "cosh",
        "tanh",
        "coth",
        "log",
        "ln",
        "lg",
        "exp",
        "det",
        "dim",
        "ker",
        "deg",
        "gcd",
        "lcm",
        "arg",
        "hom",
        "Pr",
        "mod",
        "sgn",
        "tr",
        "rank",
        "span",
        "diag",
    )
)
_LIMITS = frozenset(("lim", "liminf", "limsup", "max", "min", "sup", "inf", "argmax", "argmin"))
_ACCENTS = {"hat": "^", "widehat": "^", "bar": "‾", "overline": "‾", "vec": "→", "overrightarrow": "→",
            "tilde": "~", "widetilde": "~", "dot": "˙", "ddot": "¨", "check": "ˇ", "breve": "˘"}  # fmt: skip
_SPACES = {",": "0.1667em", ";": "0.2778em", ":": "0.2222em", " ": "0.25em", "quad": "1em",
           "qquad": "2em", "enspace": "0.5em", "thinspace": "0.1667em", "medspace": "0.2222em",
           "thickspace": "0.2778em"}  # fmt: skip
_FENCES = {"matrix": ("", ""), "pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "Bmatrix": ("{", "}"),
           "vmatrix": ("|", "|"), "Vmatrix": ("‖", "‖"), "smallmatrix": ("", ""), "cases": ("{", ""),
           "aligned": ("", ""), "align": ("", ""), "align*": ("", ""), "gathered": ("", ""),
           "array": ("", ""), "split": ("", "")}  # fmt: skip
_DELIMS = {"(": "(", ")": ")", "[": "[", "]": "]", "\\{": "{", "\\}": "}", "|": "|", "\\|": "‖", ".": "",
           "\\langle": "⟨", "\\rangle": "⟩", "\\lvert": "|", "\\rvert": "|", "\\lVert": "‖", "\\rVert": "‖",
           "\\lfloor": "⌊", "\\rfloor": "⌋", "\\lceil": "⌈", "\\rceil": "⌉", "/": "/"}  # fmt: skip

_TOKEN = re.compile(
    r"\\[A-Za-z]+\*?|\\.|\d+(?:\.\d+)?|[A-Za-z]|\s+|.",
    re.S,
)
# Characters set as identifiers rather than operators when met bare.
_LETTERISH = re.compile(r"[A-Za-zα-ωΑ-Ωϵϑϕϖϱς∞∂∇ℏℓℵ∅ℝℕℤℚℂℙℍ𝔼]")


class Unreadable(ValueError):
    """The LaTeX could not be read (a brace or an environment left open)."""


class _Reader:
    """A small recursive reader for the LaTeX a card holds."""

    def __init__(self, latex: str) -> None:
        self.tokens = [t for t in _TOKEN.findall(latex)]
        self.at = 0
        self.depth = 0

    # -- tokens ---------------------------------------------------------------------------

    def peek(self, skip_space: bool = True) -> str | None:
        at = self.at
        while skip_space and at < len(self.tokens) and self.tokens[at].isspace():
            at += 1
        return self.tokens[at] if at < len(self.tokens) else None

    def take(self, skip_space: bool = True) -> str:
        while skip_space and self.at < len(self.tokens) and self.tokens[self.at].isspace():
            self.at += 1
        if self.at >= len(self.tokens):
            raise Unreadable("ended early")
        token = self.tokens[self.at]
        self.at += 1
        return token

    def raw_group(self) -> str:
        """The text of the next `{…}` as written (for `\\text`, environments' names)."""
        if self.take() != "{":
            raise Unreadable("a group was expected")
        depth, out = 1, list[str]()
        while True:
            token = self.take(skip_space=False)
            if token == "{":
                depth += 1
            elif token == "}":
                depth -= 1
                if depth == 0:
                    return "".join(out)
            out.append(token)

    # -- structure ------------------------------------------------------------------------

    def row(self, stops: tuple[str, ...] = ()) -> str:
        """Nodes until the end, or one of `stops` (left for the caller)."""
        self.depth += 1
        if self.depth > 40:
            raise Unreadable("nested too deep")
        out: list[str] = []
        while True:
            token = self.peek()
            if token is None or token in stops or (token == "\\end" and "\\end" in stops):
                break
            if token == "}":
                raise Unreadable("a brace closed that was not open")
            node = self.node()
            if node:
                out.append(node)
        self.depth -= 1
        return "".join(out)

    def argument(self) -> str:
        """One argument: a group, or the next single token."""
        token = self.peek()
        if token == "{":
            self.take()
            inner = self.row(("}",))
            self.take()
            return f"<mrow>{inner}</mrow>"
        return self.atom()

    def node(self) -> str:
        base, kind = self.atom_with_kind()
        sub = sup = None
        while True:
            token = self.peek()
            if token == "^" and sup is None:
                self.take()
                sup = self.argument()
            elif token == "_" and sub is None:
                self.take()
                sub = self.argument()
            elif token == "'" and sup is None:
                primes = ""
                while self.peek() == "'":
                    self.take()
                    primes += "′"
                sup = f"<mo>{primes}</mo>"
            else:
                break
        if sub is None and sup is None:
            return base
        base = base or "<mrow></mrow>"
        under = kind in ("big", "limit")
        if sub is not None and sup is not None:
            return (
                f"<munderover>{base}{sub}{sup}</munderover>"
                if under
                else f"<msubsup>{base}{sub}{sup}</msubsup>"
            )
        if sub is not None:
            return f"<munder>{base}{sub}</munder>" if under else f"<msub>{base}{sub}</msub>"
        return f"<mover>{base}{sup}</mover>" if under else f"<msup>{base}{sup}</msup>"

    def atom(self) -> str:
        return self.atom_with_kind()[0]

    def atom_with_kind(self) -> tuple[str, str]:
        token = self.take()
        if token == "{":
            inner = self.row(("}",))
            self.take()
            return f"<mrow>{inner}</mrow>", ""
        if token in ("^", "_"):
            # A script with nothing before it: on an empty base.
            self.at -= 1
            return "<mrow></mrow>", ""
        if token.startswith("\\"):
            return self.command(token)
        if token.isdigit() or re.fullmatch(r"\d+\.\d+", token):
            return f"<mn>{token}</mn>", ""
        if _LETTERISH.fullmatch(token):
            return f"<mi>{escape(token)}</mi>", ""
        if token == "-":
            return "<mo>−</mo>", ""
        if token == "&":
            raise Unreadable("a column outside a table")
        return f"<mo>{escape(token)}</mo>", ""

    def command(self, token: str) -> tuple[str, str]:
        name = token[1:]
        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            top, bottom = self.argument(), self.argument()
            return f"<mfrac>{top}{bottom}</mfrac>", ""
        if name == "binom":
            top, bottom = self.argument(), self.argument()
            return f'<mrow><mo>(</mo><mfrac linethickness="0">{top}{bottom}</mfrac><mo>)</mo></mrow>', ""
        if name == "sqrt":
            index = None
            if self.peek() == "[":
                self.take()
                index = self.row(("]",))
                self.take()
            body = self.argument()
            return (f"<mroot>{body}<mrow>{index}</mrow></mroot>" if index else f"<msqrt>{body}</msqrt>"), ""
        if name in ("text", "textrm", "mbox", "textit", "textnormal"):
            return f"<mtext>{escape(self.raw_group())}</mtext>", ""
        if name in ("mathrm", "operatorname", "mathsf", "mathtt"):
            words = re.sub(r"\\,|\\ |\s+", " ", self.raw_group()).strip()
            return f"<mi>{escape(words)}</mi>", ("limit" if words in _LIMITS else "")
        if name in ("mathbf", "boldsymbol", "bm", "textbf"):
            return f'<mrow class="mbf">{self.argument()}</mrow>', ""
        if name in ("mathit",):
            return self.argument(), ""
        if name in ("mathbb", "mathcal", "mathscr", "mathfrak"):
            table = {"mathbb": _DOUBLE, "mathcal": _SCRIPT, "mathscr": _SCRIPT, "mathfrak": _FRAKTUR}[name]
            letters = self.raw_group().strip()
            return "".join(f"<mi>{escape(table.get(c, c))}</mi>" for c in letters if not c.isspace()), ""
        if name in _ACCENTS:
            body = self.argument()
            stretch = (
                ' stretchy="true"' if name in ("overline", "widehat", "widetilde", "overrightarrow") else ""
            )
            return f'<mover accent="true">{body}<mo{stretch}>{_ACCENTS[name]}</mo></mover>', ""
        if name == "underline":
            return f'<munder accentunder="true">{self.argument()}<mo stretchy="true">_</mo></munder>', ""
        if name in ("left", "right", "bigl", "bigr", "Bigl", "Bigr", "big", "Big", "bigg", "Bigg", "middle"):
            delim = self.take()
            sign = _DELIMS.get(delim, delim.lstrip("\\"))
            if name == "left":
                inner = self.row(("\\right",))
                if self.peek() != "\\right":
                    raise Unreadable("\\left without \\right")
                self.take()
                closing = _DELIMS.get(self.take(), "")
                left = f'<mo fence="true" stretchy="true">{escape(sign)}</mo>' if sign else ""
                right = f'<mo fence="true" stretchy="true">{escape(closing)}</mo>' if closing else ""
                return f"<mrow>{left}{inner}{right}</mrow>", ""
            if name == "right":
                raise Unreadable("\\right without \\left")
            return (f'<mo stretchy="false">{escape(sign)}</mo>' if sign else ""), ""
        if name == "begin":
            return self.environment(self.raw_group().strip()), ""
        if name in _SPACES:
            return f'<mspace width="{_SPACES[name]}"></mspace>', ""
        if name in ("!", "displaystyle", "textstyle", "limits", "nolimits", "\\", "label", "nonumber"):
            if name == "label":
                self.raw_group()
            return "", ""
        if name in _BIG:
            return f'<mo largeop="true" movablelimits="true">{_BIG[name]}</mo>', "big"
        if name in _INTEGRALS:
            return f'<mo largeop="true">{_INTEGRALS[name]}</mo>', ""
        if name in _LIMITS:
            return f"<mi>{name}</mi>", "limit"
        if name in _FUNCTIONS:
            return f"<mi>{name}</mi><mo>&#x2061;</mo>", ""
        if name in _LETTERS:
            return f"<mi>{_LETTERS[name]}</mi>", ""
        if name in _OPERATORS:
            return f"<mo>{_OPERATORS[name]}</mo>", ""
        if len(name) == 1 and not name.isalpha():
            return f"<mo>{escape(name)}</mo>", ""
        # A command this reader does not know is shown as it was typed.
        return f"<mtext>{escape(token)}</mtext>", ""

    def environment(self, name: str) -> str:
        if name not in _FENCES:
            raise Unreadable(f"environment {name}")
        if name == "array" and self.peek() == "{":
            self.raw_group()  # its columns' alignment
        rows: list[list[str]] = [[]]
        while True:
            cell = self.row(("&", "\\\\", "\\end"))
            rows[-1].append(cell)
            token = self.take()
            if token == "&":
                continue
            if token == "\\\\":
                rows.append([])
                continue
            if self.raw_group().strip() != name:
                raise Unreadable("an environment closed under another name")
            break
        if rows and rows[-1] in ([], [""]):
            rows.pop()
        left_align = name in ("cases", "aligned", "align", "align*", "split")
        align = (
            ' columnalign="left"' if name == "cases" else (' columnalign="right left"' if left_align else "")
        )
        table = "".join("<mtr>" + "".join(f"<mtd>{cell}</mtd>" for cell in row) + "</mtr>" for row in rows)
        opening, closing = _FENCES[name]
        fence = f'<mo fence="true" stretchy="true">{escape(opening)}</mo>' if opening else ""
        end = f'<mo fence="true" stretchy="true">{escape(closing)}</mo>' if closing else ""
        return f"<mrow>{fence}<mtable{align}>{table}</mtable>{end}</mrow>"


def to_mathml(latex: str, display: bool = False) -> str:
    """`latex` as one `<math>` element; `Unreadable` when it cannot be read."""
    reader = _Reader(latex)
    body = reader.row()
    if reader.peek() is not None:
        raise Unreadable("left over")
    mode = ' display="block"' if display else ""
    return f"<math{mode}>{body}</math>"


# What is mathematics in a card's text.
_DELIMITED = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|\$([^$\n]{1,400}?)\$", re.S)
_MARK = re.compile(r"[\\^_]")
# A run of signs around marks: commands with their groups, scripts, letters alone, digits, signs.
_RUN_TOKEN = re.compile(
    r"\\begin\{(\w+\*?)\}.*?\\end\{\1\}"  # an environment, whole
    # a command and its groups
    r"|\\[A-Za-z]+\*?(?:\[[^\]\n]{0,20}\])?(?:\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})*"
    r"|[\^_](?:\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}|\\[A-Za-z]+|.)"  # a script
    r"|\\[^A-Za-z]"  # \, \{ and the like
    r"|\{(?:[^{}]|\{[^{}]*\})*\}"  # a group
    r"|(?<![A-Za-z])(?:d[xyzt]|[A-Za-z])(?![A-Za-z])"  # a letter alone, or an integral's dx
    r"|[0-9]+(?:[.,][0-9]+)?"
    r"|[ \t]+"
    r"|[=+\-−*/<>|()\[\]:,.'′″!≤≥≠≈≡∈∉⊂⊆⊃⊇∪∩→←↦⇒⇔×·∘∑∏∫∞∂∇±∓αβγδεϵζηθϑικλμνξπρσςτυφϕχψωΓΔΘΛΞΠΣΦΨΩℝℕℤℚℂ]",
    re.S,
)


def _runs(text: str) -> list[tuple[str, bool]]:
    """The text cut into prose and runs of mathematics (each holding a mark of LaTeX)."""
    out: list[tuple[str, bool]] = []
    at, n = 0, len(text)
    prose_from = 0
    while at < n:
        m = _RUN_TOKEN.match(text, at)
        # A run never begins inside a word: "snake_case" is not a subscript.
        if not m or (at > 0 and text[at - 1].isalnum() and m.group(0)[:1] in "^_{"):
            at += 1
            continue
        # Extend a run over consecutive tokens.
        start, end, marked = at, at, False
        while True:
            m = _RUN_TOKEN.match(text, end)
            if not m or m.end() == end:
                break
            if _MARK.search(m.group(0)):
                marked = True
            end = m.end()
        if marked:
            # Trim spaces and a sentence's punctuation off the run's ends.
            s, e = start, end
            while s < e and text[s] in " \t,.;:":
                s += 1
            while e > s and text[e - 1] in " \t,.;:" and text[e - 2 : e - 1] != "\\":
                e -= 1
            if s > prose_from:
                out.append((text[prose_from:s], False))
            out.append((text[s:e], True))
            prose_from = e
        at = max(end, at + 1)
    if prose_from < n:
        out.append((text[prose_from:], False))
    return out


def segments(text: str) -> list[tuple[str, str]]:
    """The text as ("prose", words), ("inline", latex) and ("block", latex) parts."""
    parts: list[tuple[str, str]] = []
    at = 0
    for m in _DELIMITED.finditer(text):
        body = next(g for g in m.groups() if g is not None)
        dollar = m.group(4) is not None
        if (
            dollar
            and not _MARK.search(body)
            and not re.search(r"[=+<>]", body)
            and (body[:1].isspace() or body[-1:].isspace())
        ):
            continue  # "$5 and $10" is money
        if m.start() > at:
            parts.extend(_undelimited(text[at : m.start()]))
        parts.append(("block" if m.group(1) is not None or m.group(2) is not None else "inline", body))
        at = m.end()
    if at < len(text):
        parts.extend(_undelimited(text[at:]))
    # A formula alone on a card is set apart.
    content = [p for p in parts if not (p[0] == "prose" and not p[1].strip())]
    if len(content) == 1 and content[0][0] == "inline":
        parts = [("block", content[0][1])]
    return parts


def _undelimited(text: str) -> list[tuple[str, str]]:
    return [("inline" if maths else "prose", part) for part, maths in _runs(text)]
