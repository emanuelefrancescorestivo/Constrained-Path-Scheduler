"""
Flashcards from a student's own text (DECISIONS.md D84): notes typed or pasted, or
a text note of theirs or of their study group, read by rules into cards the student
picks from before any is kept. No model, no network, no cost; the same text always
gives the same cards.

Why cards from notes: practice testing is one of the two techniques rated of high
utility in a review of ten [ref:dunlosky2013], and left alone most students reread
instead [ref:karpicke2009]. Writing cards is the step that stops them; reading the
cards out of notes they already have removes most of it. The student still chooses
each card: a card they would not ask themselves is noise in their reviews.

Five shapes are read, in the languages the app speaks and a few more:

- **Question and answer**: "Q: … / A: …" (Question/Answer, Question/Réponse,
  Domanda/Risposta, D:/R:, Pregunta/Respuesta, Frage/Antwort, Vraag/Antwoord), or
  a line ending in "?" followed by its answer.
- **Term and definition**: "Term: definition", "Term – definition", "Term =
  definition", "Term :: definition", "Term → definition".
- **A list**: a line ending in ":" (or a Markdown heading) with two to eight items
  under it; the card asks for the items, and says how many.
- **A blank to fill**: a word in bold (`**…**`, `__…__`), highlighted (`==…==`) or
  an Anki cloze (`{{c1::…}}`): the sentence with a gap, the word as the answer.
- **A definition sentence**: "An eigenvalue is a number…", "Une valeur propre est
  un…", "Un autovalore è un…", "Ein Eigenwert ist ein…": the term on the front,
  the sentence on the back.

What is not read: tables, formulas set as images, and anything a rule would only
guess at. Pure functions of the text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import syllabus

MAX_TEXT = 20_000  # characters read at once, as the course topics' box
MAX_CARDS = 200  # candidates offered at once: the flashcards' own paste limit
FRONT_MOST, BACK_MOST = 300, 600  # the flashcards' own limits (cards.LIMITS)
TERM_WORDS = 8  # a term is a name, not a sentence
LIST_ITEMS = (2, 8)  # a list card asks for two to eight items
KINDS = ("qa", "term", "list", "cloze", "definition")


@dataclass(frozen=True)
class Candidate:
    front: str
    back: str
    kind: str  # one of KINDS


_Q = re.compile(
    r"^(?:q|question|domanda|pregunta|frage|vraag|pergunta|d)\s*\d*\s*[:.)]\s*(?P<rest>.+)$",
    re.IGNORECASE,
)
_A = re.compile(
    r"^(?:a|answer|ans|r|reponse|réponse|risposta|respuesta|antwort|antwoord|resposta)\s*\d*\s*[:.)]\s*"
    r"(?P<rest>.*)$",
    re.IGNORECASE,
)
_SEPARATOR = re.compile(r"^(?P<term>[^:=→]{1,80}?)\s*(?:::|:|\s[–—-]\s|\s=\s|\s?→\s?|\s->\s)\s*(?P<back>.+)$")
_MARK = (
    r"\{\{c\d+::(?P<a>[^}]+?)(?:::[^}]*)?\}\}"  # an Anki cloze
    r"|\*\*(?P<b>[^*]+?)\*\*|__(?P<c>[^_]+?)__"  # bold
    r"|==(?P<d>[^=]+?)=="  # highlighted
)
_CLOZE = re.compile(_MARK)
_HEADING = re.compile(r"^#{1,6}\s+(?P<title>.+)$")
_ITEM = re.compile(r"^\s*(?:[-*•▪◦●■➢►–—·+]|\d{1,2}[.)]|[a-z][.)])\s+(?P<item>.+)$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Þ])")
# A definition sentence: the term, then "is a" in one of the languages read. The
# term may start with an article, which is dropped.
_ARTICLES = (
    r"(?:(?:an?|the|un|une|le|la|les|il|lo|gli|uno|el|los|las|ein|eine|der|die|das|een|de|het"
    r"|o|um|uma)\s+|l')?"
)
_TERM = r"[^\s,;:()][^,;:()]{1,60}?"
_IS_A = (
    r"(?:is|are)\s+(?:an?|the|defined as|called)\s",  # English
    r"(?:est|désigne|sont)\s+(?:un|une|le|la|l'|les|des|défini)",  # French
    r"(?:è|sono)\s+(?:un|uno|una|un'|il|lo|la|l'|i|gli|le)\b",  # Italian
    r"(?:es|son)\s+(?:un|una|el|la|lo|los|las)\s",  # Spanish
    r"(?:ist|sind|bezeichnet)\s+(?:ein|eine|einen|der|die|das)\s",  # German
    r"(?:is|zijn)\s+(?:een|de|het)\s",  # Dutch
    r"(?:é|são)\s+(?:um|uma|o|a|os|as)\s",  # Portuguese
)
_DEFINES = re.compile(
    "|".join(f"^{_ARTICLES}(?P<t{n}>{_TERM})\\s+{verb}" for n, verb in enumerate(_IS_A)), re.IGNORECASE
)
# Subjects that are not terms ("It is a…", "C'est un…", "Questo è un…"), and words a
# note writes before a colon that are not terms ("Note: …", "Date: …").
_NOT_TERM_LINES = (
    "it this that there here which what he she they we you i one each",  # English
    "il elle ce c' cela ceci ça on qui",  # French
    "esso essa questo questa quello quella ciò chi",  # Italian
    "ello ella esto esta eso esa que",  # Spanish
    "es er sie dies dieses das der die",  # German
    "het dit dat",  # Dutch
    "isso isto ele ela",  # Portuguese
    "note notes nb n.b. ps p.s. date time page pages see source sources example examples e.g. eg ex",
    "todo remember important attention warning tip remark",
    "remarque rq exemple voir nota esempio vedi ejemplo véase beispiel siehe voorbeeld zie exemplo",
)
_NOT_TERMS = frozenset(word for line in _NOT_TERM_LINES for word in line.split())


def _clean(text: str) -> str:
    return " ".join(text.split()).strip(" \t-–—:;,.·|")


def _unmark(text: str) -> str:
    """The text without its bold, highlight or cloze marks."""
    return _CLOZE.sub(lambda m: next(g for g in m.groups() if g), text)


def _is_term(term: str) -> bool:
    words = term.split()
    if not 1 <= len(words) <= TERM_WORDS or not any(c.isalpha() for c in term):
        return False
    if term.casefold() in _NOT_TERMS or term.endswith("?"):
        return False
    if syllabus._MARKER.match(term) or syllabus._heading(term) is not None or syllabus._LINK.search(term):
        return False
    return not term[0].isdigit()


def _card(front: str, back: str, kind: str) -> Candidate | None:
    front, back = _clean(front), _clean(back)
    if not front or not back or len(front) > FRONT_MOST or len(back) > BACK_MOST:
        return None
    if not any(c.isalnum() for c in back):
        return None
    if kind == "definition":  # a term taken from inside a sentence: "a basis" -> "Basis"
        front = front[0].upper() + front[1:]
    return Candidate(front, back, kind)


def _clozes(line: str) -> list[Candidate]:
    """A card per marked word: the line with a gap where the word was."""
    out = []
    for m in list(_CLOZE.finditer(line))[:3]:
        answer = next(g for g in m.groups() if g)
        gap = line[: m.start()] + "[…]" + line[m.end() :]
        sentence = _unmark(gap)
        if len(sentence.split()) >= 4:
            card = _card(sentence, answer, "cloze")
            if card is not None:
                out.append(card)
    return out


def _definitions(line: str) -> list[Candidate]:
    out = []
    for sentence in _SENTENCE_END.split(line):
        m = _DEFINES.match(sentence.strip())
        if m is None:
            continue
        term = next(g for g in m.groups() if g).strip()
        if _is_term(term) and len(sentence.split()) >= 4:
            card = _card(term, sentence, "definition")
            if card is not None:
                out.append(card)
    return out


def read(text: str) -> list[Candidate]:
    """The cards a text gives, in its order, each once, at most `MAX_CARDS`."""
    lines = [line.rstrip() for line in str(text or "")[:MAX_TEXT].replace("\r\n", "\n").split("\n")]
    out: list[Candidate] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if not line:
            i += 1
            continue
        # A question and its answer.
        q = _Q.match(line)
        if q is not None and nxt and _A.match(nxt):
            answer = [_A.match(nxt)["rest"]]  # type: ignore[index]
            j = i + 2
            while j < len(lines) and (item := _ITEM.match(lines[j])) is not None:  # an answer's own list
                answer.append(item["item"].strip())
                j += 1
            back = answer[0]
            if len(answer) > 1:  # "four:" and its list, or the list alone
                back = (answer[0].rstrip(": ") + ": " if answer[0].strip() else "") + "; ".join(answer[1:])
            card = _card(_unmark(q["rest"]), _unmark(back), "qa")
            out += [card] if card else []
            i = j
            continue
        if (
            line.endswith("?")
            and len(line.split()) >= 3
            and nxt
            and not nxt.endswith("?")
            and not _Q.match(nxt)
        ):
            card = _card(_unmark(line), _unmark(nxt), "qa")
            out += [card] if card else []
            i += 2
            continue
        # A list under its heading.
        heading = _HEADING.match(line)
        title = heading["title"] if heading else ""
        if not heading and line.endswith(":") and not _ITEM.match(line):
            title = line[:-1]
        if title:
            items = []
            j = i + 1
            while j < len(lines) and (item := _ITEM.match(lines[j])) is not None:
                items.append(_clean(_unmark(item["item"])))
                j += 1
            low, high = LIST_ITEMS
            if low <= len(items) <= high and _is_term(_clean(_unmark(title))):
                card = _card(f"{_clean(_unmark(title))} ({len(items)})", "; ".join(items), "list")
                out += [card] if card else []
            if heading or items:
                # The items are read again on their own: each may be a term and its definition.
                i += 1
                continue
        # A line's own shapes: a marked word, a term and its definition, a sentence.
        body = _ITEM.match(line)
        line = body["item"] if body else line
        clozes = _clozes(line)
        if clozes:
            out += clozes
            i += 1
            continue
        # A definition sentence first: "Ein Eigenwert ist eine Zahl λ mit Av = λv" has
        # an "=" too, and it is not what separates its term.
        found = _definitions(_unmark(line))
        sep = _SEPARATOR.match(_unmark(line)) if not found else None
        if sep is not None and _is_term(_clean(sep["term"])) and not sep["back"].strip().startswith("//"):
            card = _card(sep["term"], sep["back"], "term")
            found = [card] if card else []
        out += found
        i += 1
    seen: set[tuple[str, str]] = set()
    unique = []
    for card in out:
        key = (card.front.casefold(), card.back.casefold())
        if key not in seen:
            seen.add(key)
            unique.append(card)
    return unique[:MAX_CARDS]


def both_ways(cards: list[Candidate]) -> list[Candidate]:
    """Each term and definition card, and its reverse: the definition asks for the
    term. Questions, lists and gaps keep one way."""
    out = []
    for card in cards:
        out.append(card)
        if card.kind in ("term", "definition") and len(card.back) <= FRONT_MOST:
            out.append(Candidate(card.back, card.front, card.kind))
    return out
