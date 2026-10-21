"""
D89: the science behind it. Every number the page shows is computed from the model
the app runs, or stated by a checked source in docs/REFERENCES.md; the page opens
for anyone, and from the logo inside a plan.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from cps import cards, service
from cps.memory import FSRS_6, Grade, MemoryState
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


def _entries() -> dict[str, dict[str, str]]:
    """docs/REFERENCES.md as {key: {field: value}}."""
    out: dict[str, dict[str, str]] = {}
    key = None
    for line in (ROOT / "docs/REFERENCES.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("### "):
            key = line[4:].strip()
            out[key] = {}
        elif key and line.startswith("- ") and ": " in line:
            field, value = line[2:].split(": ", 1)
            out[key][field] = value
    return out


def test_the_page_s_numbers_are_the_model_s():
    view = service.science_view()
    assert view["week"] == {d: round(100 * FSRS_6.retrievability(float(d), 7.0)) for d in (7, 14, 30)}
    assert view["week"][7] == 90  # stability is the 90 % interval, by definition
    # A longer stability holds better after a month, in the order the chart draws them.
    assert [c["at_30"] for c in view["curves"]] == sorted(c["at_30"] for c in view["curves"])
    state = MemoryState(stability=service.SCIENCE_SPACING, difficulty=5.0)
    gains = [FSRS_6.review(state, float(g), Grade.GOOD).stability for g in (0, 5, 15, 30)]
    assert gains == sorted(gains)  # the later a recall that succeeds, the more it adds
    assert round(gains[0], 1) == service.SCIENCE_SPACING  # crammed at once: nothing
    assert view["target"] == round(100 * cards.RETENTION) and view["new_per_day"] == cards.NEW_PER_DAY
    assert view["credit_hours"] == "25–30"


def test_every_source_is_a_checked_reference_with_its_own_address():
    found = _entries()
    for source in service.science_view()["sources"]:
        entry = found[source.key]
        assert entry["status"] == "checked", source.key
        assert source.checked.isoformat() in entry["checked against"], source.key
        assert entry["year"] in source.cite, source.key
        if "doi" in entry:
            assert source.url.lower() == f"https://doi.org/{entry['doi']}".lower(), source.key


# Each number the page quotes from research, and the checked text it comes from.
QUOTED = [
    ("yang2021", "222 studies and 48,478 students", ("222", "48,478", "0.499")),
    ("cepeda2006", "317 experiments", ("317",)),
    ("cepeda2008", "more than 1,350 people", ("1,350", "20 to 40", "5 to 10")),
    ("karpicke2009", "177 students", ("177",)),
    ("rohrer2020", "62 against 38", ("62.17", "37.60", "43 days")),
    ("rohrer2020", "787 pupils", ("787",)),
    ("srs-benchmark", "350 million", ("350 million", "0.3460", "0.3625")),
]


def test_every_research_number_on_the_page_is_its_source_s():
    found = _entries()
    page = (ROOT / "src/cps/web/templates/science.html").read_text(encoding="utf-8")
    for key, shown, stated in QUOTED:
        assert shown in page, shown
        checked = found[key]["checked against"]
        for number in stated:
            assert number in checked, (key, number)
    # No number from research on the page that this list does not account for.
    research = re.findall(
        r"\(g = [\d.]+\)|\d[\d,]* (?:studies|students|experiments|people|pupils|million)", page
    )
    assert research and all(
        any(r in shown or shown in r for _, shown, _ in QUOTED) or "g = 0.499" in r for r in research
    )


def test_the_page_opens_for_anyone_and_from_the_logo_inside_a_plan(tmp_path):
    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None)
    with TestClient(create_app(config)) as web:
        public = web.get("/science")
        assert public.status_code == 200
        assert "The science behind it" in public.text and "Why the wait matters" in public.text
        assert (
            "Ye, Su and Cao, 2022" in public.text
            and 'href="https://doi.org/10.1145/3534678.3539081"' in public.text
        )
        assert "La science derrière" in web.get("/science?lang=fr").text
        assert 'href="/science?lang=en"' in web.get("/").text
        files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
        start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
        token = start.headers["location"].split("/")[2]
        today = web.get(f"/p/{token}").text
        assert 'popovertarget="brand-menu"' in today and f'href="/p/{token}/science"' in today
        inside = web.get(f"/p/{token}/science")
        assert inside.status_code == 200 and f'href="/p/{token}"' in inside.text
        assert web.get("/p/not-a-plan/science").status_code == 404
