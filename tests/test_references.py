"""
Citation integrity, as a regression test.

The January 2026 bibliography had two entries with the same title under different
authors, attributed FSRS to the wrong people, and left one author list as
"[Authors]" (AUDIT.md item 9). Each of those is a check here, against the single
bibliography in docs/REFERENCES.md. A citation is `[ref:key]`; a sentence of the
form "Surname et al. (KDD 2022)" must also match an entry's first author and year,
which is the check that would have caught "Reddy et al., KDD 2022".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BIBLIOGRAPHY = ROOT / "docs" / "REFERENCES.md"
FIELDS = ("authors", "title", "year", "venue", "status", "checked against")


def entries() -> dict[str, dict[str, str]]:
    text = BIBLIOGRAPHY.read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {}
    for block in re.split(r"^### ", text, flags=re.M)[1:]:
        key, _, body = block.partition("\n")
        fields = dict(re.findall(r"^- ([a-z ]+): (.+)$", body, flags=re.M))
        out[key.strip()] = fields
    return out


def cited_files() -> list[Path]:
    """Everything a reader of the repository reads, except the bibliography, the
    archived December prototype, and the working prompt that leaves in M5."""
    skip = {BIBLIOGRAPHY, ROOT / "docs" / "CLAUDE_CODE_PROMPT.md", Path(__file__).resolve()}
    files = [
        *ROOT.glob("*.md"),
        *(ROOT / "docs").glob("*.md"),
        *ROOT.glob("*.py"),
        *(ROOT / "src").rglob("*.py"),
        *(ROOT / "benchmarks").rglob("*.py"),
        *(ROOT / "tests").rglob("*.py"),
    ]
    return [f for f in files if f not in skip and "archive" not in f.parts]


def citations() -> list[tuple[Path, str]]:
    return [
        (f, key)
        for f in cited_files()
        for key in re.findall(r"\[ref:([a-z0-9.\-]+)\]", f.read_text(encoding="utf-8"))
    ]


def test_every_entry_is_complete_and_says_how_it_was_checked():
    found = entries()
    assert len(found) >= 5
    for key, fields in found.items():
        for name in FIELDS:
            assert fields.get(name, "").strip(), f"{key}: missing {name}"
        assert fields["status"] in ("checked", "unchecked"), key
        assert re.fullmatch(r"(19|20)\d{2}", fields["year"]), key


def test_no_author_list_is_a_placeholder():
    for key, fields in entries().items():
        authors = fields["authors"]
        assert "[" not in authors and "et al" not in authors.lower(), f"{key}: {authors!r}"


def test_no_title_appears_twice():
    """Two entries, one title, two author lists: the January bibliography's error."""
    seen: dict[str, str] = {}
    for key, fields in entries().items():
        title = re.sub(r"\W+", " ", fields["title"].lower()).strip()
        assert title not in seen, f"{key} and {seen.get(title)} share the title {fields['title']!r}"
        seen[title] = key


def test_every_citation_resolves_and_every_entry_is_cited():
    found = entries()
    cited = citations()
    for path, key in cited:
        assert key in found, f"{path.relative_to(ROOT)} cites unknown [ref:{key}]"
    unused = set(found) - {key for _, key in cited}
    assert not unused, f"entries nobody cites: {sorted(unused)}"


ET_AL = re.compile(r"\b([A-Z][A-Za-z'\-]+) et al\.?,?\s*\(?(?:[A-Z][A-Za-z']*\s+)?((?:19|20)\d{2})\)?")


def test_every_named_citation_matches_an_entry():
    """ "Ye et al. (KDD 2022)" must be an entry whose first author is Ye and whose
    year is 2022. The January report's "Reddy et al." for the KDD 2022 paper would
    fail here."""
    index = {(fields["authors"].split(";")[0].split()[-1], fields["year"]) for fields in entries().values()}
    checked = 0
    for path in cited_files():
        for surname, year in ET_AL.findall(path.read_text(encoding="utf-8")):
            assert (surname, year) in index, f"{path.relative_to(ROOT)}: {surname} et al. ({year})"
            checked += 1
    assert checked >= 5


def test_the_check_would_have_caught_the_january_error():
    """The proposal credited the KDD 2022 paper to Reddy et al."""
    index = {(f["authors"].split(";")[0].split()[-1], f["year"]) for f in entries().values()}
    surname, year = ET_AL.search("as shown by Reddy et al. (KDD 2022)").groups()
    assert (surname, year) not in index


@pytest.mark.parametrize("key", ["reddy2016", "balkanski2023", "nilsson1980", "hansen2001"])
def test_unchecked_entries_say_why(key):
    fields = entries()[key]
    assert fields["status"] == "unchecked"
    assert "blocked" in fields["checked against"] or "not reachable" in fields["checked against"]
