"""
The universities a student can choose from (DECISIONS.md D39).

`data/universities.tsv` is trimmed from Hipo's university-domains-list (MIT, the
notice in `data/UNIVERSITIES-LICENSE.txt`): about ten thousand universities, each
with its country and web domains. Names are mostly in English ("University of
Bologna", "Polytechnic Institute of Milan"), so a search also matches the domains
("unibo", "polimi"), and a student may always type a name the list does not have:
choosing from the list only makes it likelier that classmates write it the same way.

The student's own country comes first, from the plan's time zone (the time-zone
database's own table, in `tzdata`, a dependency already).
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass
from importlib import resources


@dataclass(frozen=True)
class University:
    name: str
    country: str  # ISO 3166 code, "IT"
    domains: tuple[str, ...]

    @property
    def flag(self) -> str:
        """The country's flag, as two regional-indicator letters (no words to translate)."""
        return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in self.country if "A" <= c <= "Z")


def fold(text: str) -> str:
    """Lower case, without accents or punctuation: "Université Lyon-1" is "universite lyon 1"."""
    plain = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in plain if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", plain).split())


@functools.cache
def _all() -> tuple[tuple[University, frozenset[str], str], ...]:
    """Every university, with the words a search matches (name and domain labels)."""
    text = (resources.files("cps") / "data" / "universities.tsv").read_text(encoding="utf-8")
    out = []
    for line in text.splitlines():
        country, name, domains = [*line.split("\t"), "", ""][:3]
        uni = University(name, country, tuple(d for d in domains.split(",") if d))
        labels = {d.split(".")[0] for d in uni.domains}
        out.append((uni, frozenset(fold(name).split()) | {fold(x) for x in labels}, fold(name)))
    return tuple(out)


def search(query: str, country: str | None = None, limit: int = 8) -> list[University]:
    """Universities whose name or domain has every word of `query` as a word's
    start; the student's country first, then whole-word matches, then names
    starting with the query."""
    terms = fold(query).split()
    if not terms or len("".join(terms)) < 2:
        return []
    whole = " ".join(terms)
    found = []
    for uni, words, folded in _all():
        if all(any(w.startswith(t) for w in words) for t in terms):
            exact = all(t in words for t in terms)  # "unibo" is Bologna's own domain, not Bocconi's start
            found.append(
                ((uni.country != country, not exact, not folded.startswith(whole), len(uni.name)), uni)
            )
    found.sort(key=lambda x: x[0])
    return [uni for _key, uni in found[:limit]]


def canonical(name: str, country: str | None = None) -> University | None:
    """The list's university with this name (ignoring case and accents), if any;
    one in the student's country first."""
    folded = fold(name)
    matches = [uni for uni, _words, f in _all() if f == folded]
    matches.sort(key=lambda u: u.country != country)
    return matches[0] if matches else None


@functools.cache
def _zones() -> dict[str, str]:
    text = (resources.files("tzdata") / "zoneinfo" / "zone.tab").read_text(encoding="utf-8")
    zones = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            parts = line.split("\t")
            if len(parts) >= 3:
                zones[parts[2]] = parts[0]
    return zones


def country_of_zone(tz: str) -> str | None:
    """The country a time zone belongs to ("Europe/Rome" is "IT"); None for zones
    such as "UTC"."""
    return _zones().get(tz)
