"""How it works (D116): the plain page, a garden told as one story scrolled like time,
with a game where the free evenings come in. Its numbers are the memory model's, its
sentences short, its 3D still without motion, and the technical pages are for the
curious again."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cps import cards as flashcards
from cps import service, usage
from cps.memory import FSRS_6, Grade, MemoryState
from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src/cps/web/static"
TEMPLATES = ROOT / "src/cps/web/templates"
EXAMPLES = ROOT / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    config = Config(
        store=tmp_path_factory.mktemp("garden"), clock=lambda: NOW, background=False, sweep_every=None
    )
    with TestClient(create_app(config)) as client:
        yield client


@pytest.fixture(scope="module")
def token(web) -> str:
    files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
    start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    return start.headers["location"].split("/")[2]


def test_the_story_says_what_the_model_says():
    """Every number the story tells comes from FSRS-6, the model the app runs."""
    view = service.garden_view()
    model, target = FSRS_6, flashcards.RETENTION
    # Chapter 1: one pot drying, never refilling.
    assert view["one"][0] == 20 and view["one"] == sorted(view["one"], reverse=True) and view["one"][-1] < 20
    # Chapter 2: two weeks and a month, each topic at its own pace, the shakiest lowest.
    for day, recalls in view["pace"].items():
        assert recalls == sorted(recalls)
        assert recalls == [round(100 * model.retrievability(float(day), s)) for s in (2.0, 7.0, 30.0)]
    # Chapter 3: watered at once, the roots stay; watered as it droops, they grow; a
    # week later the second has more water than the first.
    week = MemoryState(stability=7.0, difficulty=5.0)
    wait = model.interval(7.0, target)
    grown = model.review(week, wait, Grade.GOOD).stability
    s = view["spacing"]
    assert s["holds"] == "7" and s["wait"] == "7" and s["grown"] == service.format_number(grown, 0)
    assert model.review(week, 0.0, Grade.GOOD).stability == pytest.approx(7.0)
    assert s["soon_end"] < round(100 * target) < s["just_end"]
    assert s["just"][s["just_at"]] == 20 and s["just"][s["just_at"] - 1] == round(20 * target)
    assert s["roots_from"] == round(20 * 7.0 / grown)
    # Chapter 5: remembered, the roots grow; forgotten, they shrink below where they were.
    r = view["report"]
    assert r["grown"] == s["grown"] and r["shrunk_days"] < 7 < r["grown_days"]
    assert r["roots_shrunk"] < r["roots_from"] < 20
    # The numbers DECISIONS.md D116 quotes.
    assert (s["soon_end"], s["just_end"], s["grown"], r["shrunk_days"]) == (85, 96, "23", 1)
    # Chapter 4's week is the code page's: five evenings free.
    assert view["week"]["free"] == len(view["week"]["blocks"]) == view["evenings"] == 5


def test_the_game_scores_both_weeks_honestly():
    """The app's week follows its rule; your week is scored the same way, on the lowest
    plant on exam day and the evenings it took."""
    app = service._garden_app_plan(FSRS_6)
    assert app == ["t0", "t1", "rest", "rest", "t2"]  # the driest first, once it is down to 90 %
    same = service.garden_view(app)["game"]
    assert same["verdict"] == "same" and same["yours"] == same["app"] and same["app"]["used"] == 3
    assert same["app"]["lowest"] >= round(100 * flashcards.RETENTION)
    drooped = service.garden_view(["t0"] * 5)["game"]
    assert (
        drooped["verdict"] == "drooped" and drooped["yours"]["lowest"] < 90 and drooped["yours"]["used"] == 5
    )
    assert (same["app"]["lowest"], drooped["yours"]["lowest"]) == (93, 85)  # as D116 says
    every = service.garden_view(["t0", "t1", "t2", "t0", "t1"])["game"]
    assert every["yours"]["lowest"] == 98
    # Revising every evening reaches more, with every evening: the page says so.
    assert every["verdict"] == "more" and every["yours"]["lowest"] > every["app"]["lowest"]
    for bad in (["t0"] * 4, ["t0", "t1", "t2", "t9", "rest"], ["", "", "", "", ""]):
        with pytest.raises(service.InvalidInput):
            service.garden_view(bad)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?!])\s+|:\s+", text) if s.strip()]


def test_the_story_is_told_in_short_sentences():
    """The writing rule of the proposal (D116): sentences of 15 words or fewer, steps of
    45 words or fewer, so a first-year reads the page without stopping."""
    source = (TEMPLATES / "garden.html").read_text(encoding="utf-8")
    strings = re.findall(r'_n?\(\s*"((?:[^"\\]|\\.)*)"(?:\s*,\s*"((?:[^"\\]|\\.)*)")?', source)
    texts = [t for pair in strings for t in pair if t]
    assert len(texts) > 60
    for text in texts:
        words = re.findall(r"[\w{}'’]+", text)
        assert len(words) <= 45, text
        for sentence in _sentences(text):
            assert len(re.findall(r"[\w{}'’]+", sentence)) <= 15, sentence


def test_the_page_opens_for_anyone_and_inside_a_plan(web, token):
    page = web.get("/garden").text
    assert page.count('class="code-scene') == 6 and len(re.findall(r'<div class="code-pin[ "]', page)) == 4
    for chapter in ("plant", "pace", "time", "evenings", "morning", "exam"):
        assert f'id="{chapter}"' in page
    assert page.count('class="chat scroll-fig"') == 1 and "av-ghost" in page  # the reader, not made yet
    # The game: a form the server answers, five evenings of four choices.
    assert re.search(r'<form class="gd-picks" method="get" action="/garden#game">', page)
    assert page.count('class="gd-evening"') == 5 and page.count('class="gd-pick" type="radio"') == 20
    played = web.get("/garden?e1=t0&e2=t0&e3=t0&e4=t0&e5=t0")
    assert (
        played.status_code == 200 and "fell below 90" in played.text and played.text.count("gd-result") >= 2
    )
    refused = web.get("/garden?e1=t0&e2=nope")
    assert (
        refused.status_code == 400 and 'class="notice"' in refused.text and 'gd-result"' not in refused.text
    )
    inside = web.get(f"/p/{token}/garden")
    assert inside.status_code == 200 and f'action="/p/{token}/garden#game"' in inside.text
    assert f'href="/p/{token}/science"' in inside.text and f'href="/p/{token}/how"' in inside.text
    assert web.get("/p/not-a-plan/garden").status_code == 404
    assert "Ton plan de révision est un jardin" in web.get("/garden?lang=fr").text
    assert "Il tuo piano di studio è un giardino" in web.get("/garden?lang=it").text


def test_the_plain_page_is_first_and_the_technical_pages_are_technical(web, token):
    """Two pages, as the owner chose: the plain one for everyone, linked first; the
    science and the code for the curious, without the conversations of D114."""
    you = web.get(f"/p/{token}/you").text
    garden, science, how = (you.index(f'href="/p/{token}/{p}"') for p in ("garden", "science", "how"))
    assert garden < science < how and 'class="art art-garden"' in you
    assert '/garden?lang=en">How it works</a>' in web.get("/science").text  # the footer
    for path in ("/how", "/science"):
        text = web.get(path).text
        assert 'class="card gd-start" href="/garden"' in text
        assert "chat scroll-fig" not in text and "metaphor-limit" not in text and "cap-plain" not in text
    assert usage.part_of("GET", "/garden") == "garden"


def test_the_garden_moves_only_with_motion_and_stands_still_without():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    opening = "@supports (animation-timeline: view()) {\n  @media (prefers-reduced-motion: no-preference) {\n"
    start = css.index(opening + '    :root:not([data-motion="reduce"]) .scroll-fig')
    end = css.index("\n}\n", start)
    block, outside = css[start:end], css[:start] + css[end:]
    for name in ("gd-turn", "gd-drop", "gd-roots", "gd-wilt", "gd-phone", "gd-press"):
        assert f"animation-name: {name}" in block and f"animation-name: {name}" not in outside
        assert re.search(rf"@keyframes {name} \{{", css)
    # Still, every pot shows its water and every root its length.
    for n in range(21):
        assert f".gz{n} {{ --zq: {n}; }}" in css and f".rt{n} {{" in css and f".rf{n} {{" in css
    assert "@property --turn" in css and "@property --wilt" in css
    assert ".cap1of3 { animation-range: contain 0% contain 33.3%; }" in block
    assert not re.search(r"\.gd-[^{}]*\{[^{}]*infinite", css)
    # The 3D is drawn without a style attribute or a script.
    for name in ("garden.html", "_garden.html"):
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "style=" not in source and "<script" not in source
