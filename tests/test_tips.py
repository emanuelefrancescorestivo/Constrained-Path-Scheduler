"""
Tips with their sources (DECISIONS.md D62): every tip rests on sources that were
read at the source, says how strong its evidence is and what it does not show, and
states no number its sources do not; the tips page reads in each language without
its scripts, Today shows one tip a day, and opening the page is counted for the
pilot's measures.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest

from cps import i18n, service, tips
from cps.locales.fr import MESSAGES as FRENCH
from cps.locales.it import MESSAGES as ITALIAN

ROOT = Path(__file__).resolve().parent.parent
BIBLIOGRAPHY = ROOT / "docs" / "REFERENCES.md"
SEMESTER = ROOT / "examples" / "sample-semester.ics"
MORNING = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


def entries() -> dict[str, dict[str, str]]:
    """docs/REFERENCES.md, as `tests/test_references.py` reads it."""
    text = BIBLIOGRAPHY.read_text(encoding="utf-8")
    out = {}
    for block in re.split(r"^### ", text, flags=re.M)[1:]:
        key, _, body = block.partition("\n")
        out[key.strip()] = dict(re.findall(r"^- ([a-z ]+): (.+)$", body, flags=re.M))
    return out


def test_every_tip_has_advice_evidence_limits_and_sources():
    keys = [t.key for t in tips.TIPS]
    assert len(keys) == len(set(keys)) >= 10
    for tip in tips.TIPS:
        assert re.fullmatch(r"[a-z]+(-[a-z]+)*", tip.key), tip.key  # an anchor on the page
        assert tip.topic in tips.TOPICS and tip.strength in tips.STRENGTHS, tip.key
        assert tip.advice.endswith(".") and tip.why.strip() and tip.limits.strip(), tip.key
        assert tip.sources and all(key in tips.SOURCES for key in tip.sources), tip.key
    assert {t.topic for t in tips.TIPS} == set(tips.TOPICS)  # no empty topic
    assert {key for t in tips.TIPS for key in t.sources} == set(tips.SOURCES)  # no source unused
    assert {t.strength for t in tips.TIPS} == set(tips.STRENGTHS)  # every level is used


def test_every_source_is_a_checked_entry_read_on_the_day_shown():
    """A tip may cite only what was read at the source, never from memory or from a
    search engine's summary (docs/REFERENCES.md: "checked")."""
    found = entries()
    for key, source in tips.SOURCES.items():
        entry = found[key]
        assert entry["status"] == "checked", key
        assert source.checked.isoformat() in entry["checked against"], key
        assert entry["year"] in source.cite, key
        surname = entry["authors"].split(";")[0].split()[-1]
        assert surname in source.cite or key.startswith("efsa"), key
        if "doi" in entry:
            assert source.url.lower() == f"https://doi.org/{entry['doi']}".lower(), key
        assert source.url.startswith("https://"), key


NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[float]:
    return [float(n.rstrip(",").replace(",", "")) for n in NUMBER.findall(text)]


def test_every_number_in_a_tip_is_one_its_sources_state():
    """Each figure a tip gives (in digits) is in what was read of its sources, or
    within 5 % of one of them ("over 21,000" for 21,195): no number from memory."""
    found = entries()
    for tip in tips.TIPS:
        read = " ".join(
            f"{found[key]['year']} {found[key]['title']} "
            + re.sub(r"\d{4}-\d{2}-\d{2}", " ", found[key]["checked against"])
            for key in tip.sources
        )
        stated = _numbers(read)
        for n in _numbers(f"{tip.why} {tip.limits}"):
            assert any(abs(x - n) <= 0.05 * n for x in stated), (tip.key, n)


def test_the_check_would_catch_a_number_from_memory():
    stated = _numbers("222 independent studies, 48,478 students")
    assert not any(abs(x - 66) <= 0.05 * 66 for x in stated)  # "a median of 66 days" is not there


def test_every_tip_reads_in_french_and_italian():
    for catalogue in (FRENCH, ITALIAN):
        assert not tips.sentences() - catalogue.keys()
    with i18n.use("it"):
        view = service.tips_view()
    first = view["topics"][0]["tips"][0]
    assert first["advice"] == ITALIAN[tips.TIPS[0].advice]
    assert first["strength_label"] == "Prove solide"
    assert first["sources"][0]["checked"] == "15 ott 2026"


def test_one_tip_a_day_and_every_tip_in_turn():
    start = date(2026, 10, 15)
    days = [tips.of_the_day(start + timedelta(days=n)).key for n in range(len(tips.TIPS))]
    assert sorted(days) == sorted(t.key for t in tips.TIPS)
    assert tips.of_the_day(start) == tips.of_the_day(start + timedelta(days=len(tips.TIPS)))


pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cps.web import Config, create_app  # noqa: E402


@pytest.mark.parametrize(("lang", "catalogue"), [("en", None), ("fr", FRENCH), ("it", ITALIAN)])
def test_the_tips_page_without_its_scripts_and_today_s_tip(tmp_path, lang, catalogue):
    config = Config(store=tmp_path, clock=lambda: MORNING, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        files = {"file": ("timetable.ics", SEMESTER.read_bytes(), "text/calendar")}
        start = web.post(
            "/start", data={"tz": "Europe/Paris", "lang": lang}, files=files, follow_redirects=False
        )
        token = start.headers["location"].split("/")[2]

        page = web.get(f"/p/{token}/tips")
        assert page.status_code == 200
        text = unescape(page.text)
        for tip in tips.TIPS:
            assert f'id="tip-{tip.key}"' in text
            assert (catalogue or {}).get(tip.advice, tip.advice) in text
            assert (catalogue or {}).get(tip.limits, tip.limits) in text
        for source in tips.SOURCES.values():
            assert f'href="{source.url}"' in text and source.cite in text
        web.get(f"/p/{token}/tips")  # twice the same day: counted once

        today = unescape(web.get(f"/p/{token}").text)
        tip = tips.of_the_day(date(2026, 9, 29))  # 08:00 in Paris
        assert f"/p/{token}/tips#tip-{tip.key}" in today
        assert (catalogue or {}).get(tip.advice, tip.advice) in today
        assert f'href="/p/{token}/tips"' in web.get(f"/p/{token}/focus").text

    measures = service.engagement(tmp_path)
    assert measures["tips_7"] == measures["tips_30"] == 1 and measures["plans"] == 1
