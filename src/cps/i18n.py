"""
The app's two languages (DECISIONS.md, D10).

The English sentence is the key: `_("Add")` is "Add" in English and whatever
`cps/locales/fr.py` says in French, or "Add" again if it says nothing, so a
missing translation shows English rather than a blank. Values go in by name,
after translation, so a translator can move them: `_("{n} days", n=4)`.

The language is held per request in a context variable (`use`, or `activate` for
a web framework's middleware), not passed to every function: service code keeps
its signatures, and a test that asks for nothing gets English.

Dates are written here with each language's own names, never the operating
system's locale, which differs between Windows and Linux.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import date
from functools import cache

LANGUAGES = {"en": "English", "fr": "Français"}
DEFAULT = "en"

_current: ContextVar[str] = ContextVar("cps_language", default=DEFAULT)


def pick(*candidates: str | None) -> str:
    """The first supported language among `candidates`, each a code ("fr"), a
    regional code ("fr-CA") or an Accept-Language header; English otherwise."""
    for candidate in candidates:
        for part in (candidate or "").split(","):
            code = part.split(";")[0].strip().lower()[:2]
            if code in LANGUAGES:
                return code
    return DEFAULT


def current() -> str:
    return _current.get()


def activate(lang: str | None) -> Token[str]:
    """Set the language until `deactivate` (a middleware's pair of calls)."""
    return _current.set(pick(lang))


def deactivate(token: Token[str]) -> None:
    _current.reset(token)


@contextmanager
def use(lang: str | None) -> Iterator[str]:
    token = activate(lang)
    try:
        yield current()
    finally:
        deactivate(token)


@cache
def catalogue(lang: str) -> dict[str, str]:
    if lang == DEFAULT:
        return {}
    module = importlib.import_module(f"cps.locales.{lang}")
    return dict(module.MESSAGES)


def _(text: str, /, **values: object) -> str:
    """`text` in the current language, with `values` filled in."""
    lang = _current.get()
    if lang != DEFAULT:
        text = catalogue(lang).get(text, text)
    return text.format(**values) if values else text


def _n(singular: str, plural: str, n: int | float, /, **values: object) -> str:
    """The singular or plural sentence for `n`, by the language's rule: English
    says "1 day, 0 days", French "1 jour, 0 jour"."""
    one = n in (0, 1) if _current.get() == "fr" else n == 1
    return _(singular if one else plural, n=n, **values)


_DAYS = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "fr": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
}
_MONTHS = {
    "en": (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ),
    "fr": (
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ),
}  # fmt: skip
_SHORT_MONTHS = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."),
}
_SHORT_DAYS = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "fr": ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."),
}


def weekday_name(day: date, short: bool = False) -> str:
    return (_SHORT_DAYS if short else _DAYS)[current()][day.weekday()]


def format_date(day: date, style: str = "long") -> str:
    """A date in words: "long" is "Monday 12 October" (lundi 12 octobre), "short"
    is "Mon 12 Oct", "day" is "12 October", "month" is "October 2026"."""
    lang = current()
    if style == "short":
        return f"{_SHORT_DAYS[lang][day.weekday()]} {day.day} {_SHORT_MONTHS[lang][day.month - 1]}"
    if style == "day":
        return f"{day.day} {_MONTHS[lang][day.month - 1]}"
    if style == "month":
        return f"{_MONTHS[lang][day.month - 1]} {day.year}"
    first = "1er" if lang == "fr" and day.day == 1 else str(day.day)
    return f"{_DAYS[lang][day.weekday()]} {first} {_MONTHS[lang][day.month - 1]}"


def number(value: float, digits: int = 1) -> str:
    """A number as the language writes it: 1.5 in English, 1,5 in French; no
    trailing zero (3, not 3.0)."""
    text = f"{round(value, digits):.{digits}f}".rstrip("0").rstrip(".") if digits else str(round(value))
    return text.replace(".", ",") if current() == "fr" else text
