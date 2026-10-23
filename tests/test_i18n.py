"""
English, French (DECISIONS.md, D10) and Italian (D34): every sentence the code
marks for translation has an entry in each language, in the server's catalogues
(cps/locales/fr.py, it.py) and in the scripts' (static/i18n.js); entries keep their
{placeholders}; and a French or Italian plan's pages are in its language.
"""

from __future__ import annotations

import ast
import re
from datetime import date
from pathlib import Path

import pytest

from cps import i18n, service, tips, usage, weekly
from cps.locales.fr import MESSAGES
from cps.locales.it import MESSAGES as ITALIAN

CATALOGUES = {"fr": MESSAGES, "it": ITALIAN}

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "cps"
CALLS = {"_", "_n", "translate", "translate_plural", "_h"}


def _strings(node: ast.AST) -> list[str]:
    return [
        a.value
        for a in getattr(node, "args", [])[:2]
        if isinstance(a, ast.Constant) and isinstance(a.value, str)
    ]


def python_sentences() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        if "locales" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in CALLS:
                strings = _strings(node)
                found.update(strings if name in ("_n", "translate_plural") else strings[:1])
    return found


_LITERAL = r"""("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')"""
TEMPLATE_CALL = re.compile(r"\b(_n|_h|_)\(\s*" + _LITERAL + r"(?:\s*,\s*" + _LITERAL + r")?")


def template_sentences() -> set[str]:
    found: set[str] = set()
    for path in (SRC / "web" / "templates").glob("*.html"):
        for name, first, second in TEMPLATE_CALL.findall(path.read_text(encoding="utf-8")):
            found.add(ast.literal_eval(first))
            if name == "_n" and second:
                found.add(ast.literal_eval(second))
    return found


def indirect_sentences() -> set[str]:
    """Words kept in tables and translated where they are shown (`_(word)`)."""
    from cps.web import app as web

    return {
        *service.KIND_LABELS.values(),
        *(v for v in service._SESSION_VERBS.values()),
        *(label for _, label in service.ACTIVITY_KINDS),
        *service.DAY_WORDS.values(),
        *(label for _, label in service.MILESTONES),
        *service.WEEKDAY_NAMES,
        *web.OUTCOME_WORDS.values(),
        *web.RECALL_WORDS.values(),
        *(word for _, word in web.FAMILIARITY),
        *web.TASK_NOTICES.values(),
        *web.NETWORK_NOTICES.values(),
        *web.CARD_WORDS.values(),
        *web.CARD_NOTICES.values(),
        *service.REPORT_REASONS.values(),
        *service.EFFORT_WORDS.values(),
        *service.PROGRESS_WORDS.values(),
        *tips.sentences(),  # the tips' text, translated where it is shown (D62)
        *weekly.sentences(),  # the weekly notes (D64)
        *service.ROUTINE_SAYS.values(),  # what Spark says about the week (D70)
        *service._INSIGHT_TITLES.values(),  # the insights (D80)
        *service._INSIGHT_HOW.values(),
        *service._BEST_PART.values(),
        *service._PDF_ERRORS.values(),  # why a PDF gave no text (D90)
        *(label for _, label in usage.PARTS),  # the parts of the app counted (D96)
    }


@pytest.mark.parametrize("lang", sorted(CATALOGUES))
def test_every_marked_sentence_has_an_entry_in_each_language(lang):
    pytest.importorskip("fastapi")
    wanted = python_sentences() | template_sentences() | indirect_sentences()
    assert len(wanted) > 300  # the extraction found the app, not nothing
    missing = sorted(wanted - CATALOGUES[lang].keys())
    assert not missing, f"no {lang} for:\n" + "\n".join(missing)


@pytest.mark.parametrize("lang", sorted(CATALOGUES))
def test_translations_keep_their_placeholders_and_add_none(lang):
    for english, said in CATALOGUES[lang].items():
        assert set(re.findall(r"\{(\w+)\}", english)) == set(re.findall(r"\{(\w+)\}", said)), english
        assert said.strip() and said == said.strip(), english
        # Markup survives translation: the same tags, in the same number.
        assert sorted(re.findall(r"</?\w+", english)) == sorted(re.findall(r"</?\w+", said)), english


SCRIPT_CALL = re.compile(r"\bt\(\s*" + _LITERAL)


@pytest.mark.parametrize("lang", ["FR", "IT"])
def test_the_scripts_words_are_in_each_language(lang):
    static = SRC / "web" / "static"
    script = (static / "i18n.js").read_text(encoding="utf-8")
    catalogue = script.split(f"const {lang} = {{", 1)[1].split("\n};", 1)[0]
    keys = {ast.literal_eval(k) for k in re.findall(r'^\s*("(?:[^"\\]|\\.)*"):', catalogue, re.M)}
    used = set()
    for path in static.glob("*.js"):
        used |= {ast.literal_eval(s) for s in SCRIPT_CALL.findall(path.read_text(encoding="utf-8"))}
    # Words the calendar keeps in tables and passes through t().
    calendar = (static / "calendar.js").read_text(encoding="utf-8")
    for table in ("KIND_WORDS", "OUTCOMES"):
        line = next(x for x in calendar.splitlines() if x.startswith(f"const {table} ="))
        used |= {ast.literal_eval(s) for s in re.findall(r'"[A-Z][^"]*"', line)}
    used |= {"Study"}
    assert used and not sorted(used - keys), sorted(used - keys)


def test_dates_numbers_and_plurals_in_each_language():
    day = date(2026, 10, 5)
    assert i18n.format_date(day) == "Monday 5 October" and i18n.format_date(day, "day_short") == "5 Oct"
    with i18n.use("fr"):
        assert i18n.format_date(day) == "lundi 5 octobre" and i18n.format_date(day, "short") == "lun. 5 oct."
        assert (
            i18n._n("{n} day", "{n} days", 0) == "0 jour" and i18n._n("{n} day", "{n} days", 2) == "2 jours"
        )
        assert i18n._("A sentence nobody translated") == "A sentence nobody translated"
    with i18n.use("it-IT"):
        assert i18n.current() == "it"
        assert i18n.format_date(day) == "lunedì 5 ottobre" and i18n.format_date(day, "short") == "lun 5 ott"
        assert i18n.format_date(date(2026, 11, 1), "month") == "novembre 2026"
        assert i18n._n("{n} day", "{n} days", 0) == "0 giorni"
        assert i18n._n("{n} day", "{n} days", 1) == "1 giorno"
        assert i18n.number(1.5) == "1,5" and i18n.number(3.0) == "3"
    assert i18n._n("{n} day", "{n} days", 0) == "0 days"
    assert i18n.current() == "en"  # the language was put back


def test_topic_names_stay_identifiers_and_are_shown_in_the_language():
    assert service.topic_words("Algebra 3 · week of 05 Oct") == "Algebra 3 · week of 5 Oct"
    with i18n.use("fr"):
        assert service.topic_words("Algebra 3 · week of 05 Oct") == "Algebra 3 · semaine du 5 oct."
        assert service.topic_words("taught before 29 Sep") == "vu avant le 29 sept."


def test_topic_names_are_shown_in_italian_too():
    with i18n.use("it"):
        assert service.topic_words("Algebra 3 · week of 05 Oct") == "Algebra 3 · settimana del 5 ott"
        assert service.topic_words("taught before 29 Sep") == "spiegato prima del 29 set"
