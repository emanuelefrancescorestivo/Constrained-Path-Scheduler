"""How it works (D116, D117): the plain page, a garden told as one story scrolled like
time, with a game where the free evenings come in. Its numbers are the memory model's,
its sentences short; its glasses are drawn in WebGL from the scene's data, over a flat
drawing that stands still without motion; and the technical pages are for the curious
again."""

import html
import json
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
    # The roots, on one scale for the page (D117): a month reaches the bottom of the glass.
    assert (s["roots_from"], s["roots_to"]) == (round(20 * 7.0 / 30), round(20 * grown / 30))
    assert view["pace_roots"] == [max(1, round(20 * s / 30)) for s in (2.0, 7.0, 30.0)] == [1, 5, 20]
    # Chapter 5: remembered, the roots grow; forgotten, they shrink below where they were.
    r = view["report"]
    assert r["grown"] == s["grown"] and r["shrunk_days"] < 7 < r["grown_days"]
    assert r["roots_shrunk"] < r["roots_from"] < r["roots_to"] < 20
    # The opening shelf and exam day: the game's three topics, on Monday and after the
    # app's week; every root longer on exam day, every glass at its exam-day water.
    app = service.garden_view(service._garden_app_plan(model))["game"]["app"]["plants"]
    assert view["exam"] == app
    assert all(e["roots"] > t["roots"] for t, e in zip(view["start"], view["exam"], strict=True))
    # The numbers DECISIONS.md D117 quotes.
    assert (s["roots_from"], s["roots_to"]) == (5, 16)
    assert [(p["z"], p["roots"]) for p in view["exam"]] == [(19, 5), (19, 10), (20, 18)]
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
    # A pinned chapter's words are in its stage, before its picture (D117, AUDIT 95).
    for chapter in ("pace", "time", "evenings", "morning"):
        part = page.split(f'id="{chapter}"', 1)[1]
        assert part.index('class="code-pin-stage"') < part.index('class="code-copy"') < part.index("<figure")
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


def _scenes(page: str) -> list[dict]:
    return [json.loads(html.unescape(m)) for m in re.findall(r"data-scene='([^']*)'", page)]


def test_each_scene_carries_the_numbers_it_draws(web):
    """D117: garden.js draws each scene from its data, the service's numbers, as the flat
    drawing under it does; the ranges in which a drop falls, roots grow and a plant
    wilts are the stylesheet's, and the steps' (a quarter or a third of the scene)."""
    view = service.garden_view()
    page = web.get("/garden").text
    assert '<script type="module" src="/static/garden.js">' in page or "/garden.js" in page
    hero, one, pace, time, morning, exam = _scenes(page)
    assert (hero["t"], one["t"], pace["t"], time["t"], morning["t"], exam["t"]) == (
        "exit",
        "cover",
        "pin",
        "pin",
        "pin",
        "cover",
    )
    assert [p["rt"] for p in hero["pots"]] == [t["roots"] for t in view["start"]]
    assert one["pots"][0]["z"] == view["one"] and one["pots"][0]["rt"] == view["one_roots"]
    columns = view["fade"]["columns"]
    assert [p["z"] for p in pace["pots"]] == [c["zq"] for c in columns]
    assert [p["rt"] for p in pace["pots"]] == view["pace_roots"]
    s, r = view["spacing"], view["report"]
    soon, just = time["pots"]
    assert (soon["z"], just["z"]) == (s["soon"], s["just"])
    assert soon["rt"] == s["roots_from"] and (just["rf"], just["rt"]) == (s["roots_from"], s["roots_to"])
    # Each glass is watered in the step that says so: the second step, then the third.
    assert soon["drop"] == [0.25, 0.36] and just["drop"] == just["ra"] == [0.6, 0.72]
    assert 0.25 <= soon["drop"][0] < soon["drop"][1] <= 0.5 <= just["drop"][0] < just["drop"][1] <= 0.75
    yes, no = morning["pots"]
    assert (yes["rf"], yes["rt"]) == (r["roots_from"], r["roots_to"])
    assert (no["rf"], no["rt"]) == (r["roots_from"], r["roots_shrunk"])
    assert yes["ra"][0] >= 1 / 3 and yes["ra"][1] <= 2 / 3 and no["wilt"][0] >= 2 / 3 and no["ra"][0] >= 2 / 3
    assert [p["z"] for p in exam["pots"]] == [p["z"] for p in view["exam"]]
    assert all(p["bloom"] == [0, 1] for p in exam["pots"]) and not any(p["bloom"] for p in pace["pots"])
    # The flat drawing's ranges, in the stylesheet, are the same.
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    for selector, (a, b) in (
        (".code-pin .gd-soon .gd-drop", soon["drop"]),
        (".code-pin .gd-just .gd-drop", just["drop"]),
        (".code-pin .gd-grow .gd-roots", just["ra"]),
        (".code-pin .gd-yes-pot .gd-roots", yes["ra"]),
        (".code-pin .gd-no-pot .gd-roots", no["ra"]),
        (".code-pin .gd-wilt", no["wilt"]),
    ):
        rule = re.search(re.escape(selector) + r" \{[^}]*animation-range: contain (\d+)% contain (\d+)%", css)
        assert rule and (int(rule[1]), int(rule[2])) == (round(100 * a), round(100 * b)), selector
    # The game's two shelves, still, each glass at its exam-day water and roots.
    played = web.get("/garden?e1=t0&e2=t0&e3=t0&e4=t0&e5=t0").text
    game = service.garden_view(["t0"] * 5)["game"]
    yours, apps = _scenes(played)[4:6]  # chapter 4 comes before chapter 5
    assert yours["t"] == apps["t"] == "still"
    for drawn, week in ((yours, game["yours"]), (apps, game["app"])):
        assert [(p["z"], p["rt"]) for p in drawn["pots"]] == [(p["z"], p["roots"]) for p in week["plants"]]


def test_the_3d_draws_only_when_something_changed():
    """D93 and D117: the WebGL draws a frame when the page scrolled, a scene came into
    view, the page was resized or its theme changed, and eases to where the page is and
    stops; with reduced motion it draws each scene once. It sets no style and says no
    words (the flat drawing and the page's text say what it shows)."""
    js = (STATIC / "garden.js").read_text(encoding="utf-8")
    assert "setInterval" not in js and "setTimeout" not in js and ".style" not in js
    assert js.count("requestAnimationFrame(tick)") == 2  # on a wake, and again only while easing
    assert "if (again) frame = requestAnimationFrame(tick)" in js
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in js and 'root.dataset.motion === "reduce"' in js
    assert 'CSS.supports("animation-timeline: view()")' in js
    assert 'addEventListener("scroll", wake, { passive: true })' in js and "if (moving)" in js
    assert not re.search(r"\bt\(\s*[\"']", js)  # no sentence for the reader


def test_the_garden_moves_only_with_motion_and_stands_still_without():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    opening = "@supports (animation-timeline: view()) {\n  @media (prefers-reduced-motion: no-preference) {\n"
    start = css.index(opening + '    :root:not([data-motion="reduce"]) .scroll-fig')
    end = css.index("\n}\n", start)
    block, outside = css[start:end], css[:start] + css[end:]
    for name in ("gd-drop", "gd-roots", "gd-wilt", "gd-phone", "gd-press"):
        assert f"animation-name: {name}" in block and f"animation-name: {name}" not in outside
        assert re.search(rf"@keyframes {name} \{{", css)
    # Still, every pot shows its water and every root its length.
    for n in range(21):
        assert f".gz{n} {{ --zq: {n}; }}" in css and f".rt{n} {{" in css and f".rf{n} {{" in css
    assert "@property --wilt" in css and "@property --turn" not in css  # the flat drawing does not turn
    assert ".gd-3d .gd-flat { visibility: hidden; }" in css  # the flat one under the 3D, once drawn
    assert ".cap1of3 { animation-range: contain 0% contain 33.3%; }" in block
    assert not re.search(r"\.gd-[^{}]*\{[^{}]*infinite", css)
    # No style attribute and no inline script (the Content-Security-Policy): the flat
    # drawing is CSS alone, and the one script is garden.js, from its file (D117).
    for name in ("garden.html", "_garden.html"):
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        scripts = re.findall(r"<script[^>]*>", source)
        assert "style=" not in source and all(" src=" in tag for tag in scripts)
        own = ['<script type="module" src="{{ asset(\'garden.js\') }}">']
        assert scripts == (own if name == "garden.html" else [])
