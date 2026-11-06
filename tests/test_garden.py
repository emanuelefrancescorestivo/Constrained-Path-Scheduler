"""How it works (D116, D117, D121): the plain page, a garden told as one story scrolled
like time, and a month the student plans. Its numbers are the memory model's, its
sentences written as a person says them; its glasses are drawn in WebGL from the scene's
data, labelled as they change, over a flat drawing that stands still without motion; the
month is scored three ways against the app's, so that cramming wins what it wins; and
the technical pages are for the curious again."""

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
    # Chapter 1: one pot drying, never refilling; at the target on the day the app would
    # revise it (D121's steps).
    assert view["one"][0] == 20 and view["one"] == sorted(view["one"], reverse=True) and view["one"][-1] < 20
    assert view["one_due"] == round(model.interval(2.0, target))
    assert (view["one_due"], view["one_due_recall"], view["one_end"]) == (2, 90, 79)
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
    # The opening shelf and exam day: three topics, on Monday and after the app's week;
    # every root longer on exam day, every glass at its exam-day water.
    app = service._garden_week(model, service._garden_app_plan(model))["plants"]
    assert view["exam"] == app
    assert all(e["roots"] > t["roots"] for t, e in zip(view["start"], view["exam"], strict=True))
    # The numbers DECISIONS.md D117 quotes.
    assert (s["roots_from"], s["roots_to"]) == (5, 16)
    assert [(p["z"], p["roots"]) for p in view["exam"]] == [(19, 5), (19, 10), (20, 18)]
    # The numbers DECISIONS.md D116 quotes.
    assert (s["soon_end"], s["just_end"], s["grown"], r["shrunk_days"]) == (85, 96, "23", 1)
    # Chapter 4's week is the code page's: five evenings free.
    assert view["week"]["free"] == len(view["week"]["blocks"]) == view["evenings"] == 5


def test_cramming_wins_the_next_day_and_loses_the_rest():
    """D121, the owner: "one can easily say that cramming works best". The cramming
    chapter counts that a revision may not work (its chance is the recall then) and
    scores both ways three times: exam day, the days below the target before it, and a
    month on. Cramming wins the first, and the chapter says so."""
    model, target = FSRS_6, flashcards.RETENTION
    c = service.garden_view()["cram"]
    crammed, spaced = c["pots"]
    assert crammed["exam"] > spaced["exam"] >= round(100 * target)  # cramming wins exam day
    assert crammed["later"] < spaced["later"]  # and loses a month on
    assert c["shaky"][0] > c["shaky"][1]  # and the month before it
    # Its chance: the recall of a new topic after 27 days untouched.
    fresh = model.initial_state(Grade.GOOD)
    assert c["chance"] == round(100 * model.retrievability(27.0, fresh.stability))
    # The numbers DECISIONS.md D121 quotes.
    assert (crammed["exam"], spaced["exam"], crammed["later"], spaced["later"]) == (96, 91, 80, 85)
    assert (c["shaky"], c["chance"], c["revisions"]) == ([25, 4], 68, 2)
    # Each step holds what it says: the spaced revisions in the first half, the cram in
    # the third quarter, the exam where the last begins.
    assert all(d[1] <= 0.5 for d in spaced["drops"]) and len(spaced["drops"]) == c["revisions"]
    assert [0.5 <= d[0] < d[1] <= c["split"] for d in crammed["drops"]] == [True]
    assert [b[1] for b in c["badge"] if b[0] == c["split"]] == ["Exam day"]


def test_the_month_is_scored_three_ways_against_the_apps():
    """D121: the month the student plans. The app's plan follows its rule; any plan is
    scored on the weakest course on exam day, the days below the target before it and a
    month later, each revision working only as often as the course is remembered then."""
    model, target = FSRS_6, flashcards.RETENTION
    app = service.garden_month_view()
    assert app["is_app"] and app["count"] == app["app_count"] == 6 and len(app["verdict"]) == 1
    assert service._garden_month_app() == ((2, 6, 15), (3, 12), (13,))  # as D121 says
    assert (app["app"]["exam"], app["app"]["shaky"], app["app"]["later"]) == (91, 11, 83)
    # The app's rule: each evening the course that has faded most, once it is down to the
    # target; so each of its revisions comes when its course is at or just under it.
    chances = [cell["chance"] for course in app["courses"] for cell in course["cells"] if cell["on"]]
    assert len(chances) == 6 and all(85 <= c <= round(100 * target) for c in chances)
    # A course's first revision: its chance is its recall that evening.
    hard = service._garden_month_starts(model)[0]
    first = model.retrievability(service._month_evening(2), hard.stability)
    assert app["courses"][0]["cells"][1]["chance"] == round(100 * first)
    # The best cram, one course an evening, the hardest last: it wins exam day, and the
    # page says so before what it costs.
    cram = service.garden_month_view(preset="cram")
    assert [[d["day"] for d in c["cells"] if d["on"]] for c in cram["courses"]] == [[27], [26], [25]]
    assert (cram["lowest"]["exam"], cram["lowest"]["shaky"], cram["lowest"]["later"]) == (94, 63, 74)
    assert cram["verdict"][0].startswith("You win exam day") and "next day" in cram["verdict"][1]
    assert "63 days below 90" in cram["verdict"][2] and "74" in cram["verdict"][3]
    # More revisions win more, and the page counts them.
    every = service.garden_month_view([f"{c}-{d}" for c in range(3) for d in range(1, 28)])
    assert every["lowest"]["exam"] > app["app"]["exam"] and every["count"] == 81
    assert every["lowest"]["shaky"] < app["app"]["shaky"] and every["lowest"]["later"] > app["app"]["later"]
    assert "81 revisions" in every["verdict"][-1]
    # Nothing ticked: every course left alone, the app ahead on all three.
    clear = service.garden_month_view(preset="clear")
    assert clear["count"] == 0 and clear["lowest"]["exam"] < app["app"]["exam"]
    assert service.garden_month_view([])["count"] == 0
    for ticks, preset in ((["0-28"], None), (["3-1"], None), (["0-0"], None), (["x"], None), ([], "nope")):
        with pytest.raises(service.InvalidInput):
            service.garden_month_view(ticks, preset)


def test_every_label_says_its_number_and_warns_below_the_target():
    """D121: the laptop's labels are the server's: from a point of the scene on, a number,
    a line under it, and whether it is below the target (drawn as a warning)."""
    view, month = service.garden_view(), service.garden_month_view(preset="cram")
    fine = view["fine"]
    lists = [fine["one"]["tags"], *(p["tags"] for p in fine["pace"]["pots"])]
    lists += [p["tags"] for p in fine["time"]["pots"]] + [p["tags"] for p in view["cram"]["pots"]]
    lists += [c["tags"] for c in month["courses"]]
    for tags in lists:
        assert [t[0] for t in tags] == sorted(t[0] for t in tags) and tags[0][0] == 0.0
        for progress, big, small, low in tags:
            number = int(big.split(" ")[0])
            assert 0 <= progress <= 1 and small and low == (number < 90)
    for badge in (fine["one"]["badge"], view["cram"]["badge"], month["badge"]):
        assert all(len(b) == 3 and b[1] for b in badge)
    assert month["badge"][0][1] == "Day 1" and month["badge"][-1][1] == "Exam day"


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.?!])\s+|:\s+", text) if s.strip()]


def test_the_story_is_told_in_short_sentences():
    """The writing rule: D116's sentences of 15 words or fewer read as written by a
    machine (the owner, D121), so they are written again as a person says them, in
    sentences of up to 25 words and paragraphs of up to 60."""
    source = (TEMPLATES / "garden.html").read_text(encoding="utf-8")
    source += (TEMPLATES / "_garden_month_part.html").read_text(encoding="utf-8")
    strings = re.findall(r'_n?\(\s*"((?:[^"\\]|\\.)*)"(?:\s*,\s*"((?:[^"\\]|\\.)*)")?', source)
    texts = [t for pair in strings for t in pair if t]
    assert len(texts) > 60
    for text in texts:
        words = re.findall(r"[\w{}'’]+", text)
        assert len(words) <= 60, text
        for sentence in _sentences(text):
            assert len(re.findall(r"[\w{}'’]+", sentence)) <= 25, sentence


def test_the_page_opens_for_anyone_and_inside_a_plan(web, token):
    page = web.get("/garden").text
    assert page.count('class="code-scene') == 7 and len(re.findall(r'<div class="code-pin[ "]', page)) == 6
    for chapter in ("plant", "pace", "time", "cram", "evenings", "morning", "exam", "month"):
        assert f'id="{chapter}"' in page
    # A pinned chapter's words are in its stage, before its picture (D117, AUDIT 95).
    for chapter in ("plant", "pace", "time", "cram", "evenings", "morning"):
        part = page.split(f'id="{chapter}"', 1)[1]
        assert part.index('class="code-pin-stage"') < part.index('class="code-copy"') < part.index("<figure")
    assert page.count('class="chat scroll-fig"') == 1 and "av-ghost" in page  # the reader, not made yet
    # The month (D121): a form the server answers, three courses of 27 evenings, the
    # app's plan ticked until the student changes it; the chapters' line for a laptop.
    assert re.search(r'<form class="gd-month-form" method="get" action="/garden#month">', page)
    assert page.count('type="checkbox" name="r"') == 81 and page.count(" checked>") == 6
    assert '<nav class="gd-timeline"' in page and page.count("data-chapter=") == 7
    played = web.get("/garden?month=1&r=0-27&r=1-26&r=2-25")
    assert played.status_code == 200 and "You win exam day" in played.text and "63 days below" in played.text
    assert played.text.count(" checked>") == 3
    part = web.get("/garden?month=1&r=0-27&part=month")
    assert part.status_code == 200 and '<section class="code-row gd-month" id="month">' in part.text
    assert "<html" not in part.text and part.text.count('type="checkbox" name="r"') == 81
    assert web.get("/garden?month=1").text.count(" checked>") == 0  # nothing ticked is a plan too
    refused = web.get("/garden?r=0-99")
    assert refused.status_code == 400 and 'class="notice"' in refused.text
    inside = web.get(f"/p/{token}/garden")
    assert inside.status_code == 200 and f'action="/p/{token}/garden#month"' in inside.text
    assert f'href="/p/{token}/science"' in inside.text and f'href="/p/{token}/how"' in inside.text
    assert web.get("/p/not-a-plan/garden").status_code == 404
    assert "Tes cours sont des plantes" in web.get("/garden?lang=fr").text
    assert "I tuoi corsi sono piante" in web.get("/garden?lang=it").text


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
    hero, one, pace, time, cram, morning, exam, month = _scenes(page)
    assert [x["t"] for x in (hero, one, pace, time, cram, morning, exam, month)] == [
        "exit",
        "pin",
        "pin",
        "pin",
        "pin",
        "pin",
        "cover",
        "cursor",
    ]
    assert [p["rt"] for p in hero["pots"]] == [t["roots"] for t in view["start"]]
    fine = view["fine"]
    assert one["pots"][0]["z"] == view["one"] and one["pots"][0]["rt"] == view["one_roots"]
    # D121: the laptop's finer stops, labels and day, beside the flat drawing's eight.
    assert one["pots"][0]["zf"] == fine["one"]["zf"] and len(fine["one"]["zf"]) == service.GARDEN_FINE
    assert one["pots"][0]["tags"] == fine["one"]["tags"] and one["badge"] == fine["one"]["badge"]
    columns = view["fade"]["columns"]
    assert [p["z"] for p in pace["pots"]] == [c["zq"] for c in columns]
    assert [p["zf"] for p in pace["pots"]] == [p["zf"] for p in fine["pace"]["pots"]]
    assert [p["rt"] for p in pace["pots"]] == view["pace_roots"]
    s, r = view["spacing"], view["report"]
    soon, just = time["pots"]
    assert (soon["z"], just["z"]) == (s["soon"], s["just"])
    assert soon["rt"] == s["roots_from"] and (just["rf"], just["rt"]) == (s["roots_from"], s["roots_to"])
    assert (just["rtf"][0], just["rtf"][-1]) == (s["roots_from"], s["roots_to"])
    # Each glass is watered in the step that says so: the second step, then the third.
    assert soon["drop"] == [0.25, 0.36] and just["drop"] == just["ra"] == [0.6, 0.72]
    assert 0.25 <= soon["drop"][0] < soon["drop"][1] <= 0.5 <= just["drop"][0] < just["drop"][1] <= 0.75
    assert [p["z"] for p in cram["pots"]] == [p["w"] for p in view["cram"]["pots"]]
    assert [p["drops"] for p in cram["pots"]] == [p["drops"] for p in view["cram"]["pots"]]
    assert cram["badge"] == view["cram"]["badge"]
    yes, no = morning["pots"]
    assert (yes["rf"], yes["rt"]) == (r["roots_from"], r["roots_to"])
    assert (no["rf"], no["rt"]) == (r["roots_from"], r["roots_shrunk"])
    assert (yes["tags"], no["tags"]) == (fine["morning"]["yes"], fine["morning"]["no"])
    assert yes["ra"][0] >= 1 / 3 and yes["ra"][1] <= 2 / 3 and no["wilt"][0] >= 2 / 3 and no["ra"][0] >= 2 / 3
    assert [p["z"] for p in exam["pots"]] == [p["z"] for p in view["exam"]]
    assert all(p["bloom"] == [0, 1] for p in exam["pots"]) and not any(p["bloom"] for p in pace["pots"])
    # The month: the garden under the slider, standing on exam day without a script.
    planned = service.garden_month_view()
    assert month["rest"] == 1 and month["badge"] == planned["badge"]
    drawn = [(p["z"], p["rt"], p["zf"]) for p in month["pots"]]
    assert drawn == [(c["z"], c["roots"], c["zf"]) for c in planned["courses"]]
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


def test_the_3d_draws_only_when_something_changed():
    """D93 and D117: the WebGL draws a frame when the page scrolled, a scene came into
    view, the page was resized or its theme changed, and eases to where the page is and
    stops; with reduced motion it draws each scene once. It sets no style and has no
    words of its own: D121's labels and days are the server's, from the scene's data."""
    js = (STATIC / "garden.js").read_text(encoding="utf-8")
    assert "setInterval" not in js and "setTimeout" not in js and ".style" not in js
    assert js.count("requestAnimationFrame(tick)") == 2  # on a wake, and again only while easing
    assert "if (again) frame = requestAnimationFrame(tick)" in js
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in js and 'root.dataset.motion === "reduce"' in js
    assert 'CSS.supports("animation-timeline: view()")' in js
    assert 'addEventListener("scroll", wake, { passive: true })' in js and "if (moving)" in js
    assert not re.search(r"\bt\(\s*[\"']", js)  # no sentence for the reader
    # D121: the month's Play goes a frame at a time and stops at exam day or on a tap; a
    # tick asks the server for the month again.
    assert "playing = requestAnimationFrame(play)" in js and "if (next >= 1) return stop();" in js
    assert "fetch(`${url}?${query}&part=month`" in js


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


def test_the_plant_follows_the_theme_and_its_light_the_memory():
    """D119, the owner's choice: a rosette when the page is light, a plant of light when it
    is dark. Each glows as brightly as its topic is remembered, the light fading as the
    water falls and when it wilts; the flat drawing's pool of light follows --zq too."""
    js = (STATIC / "garden.js").read_text(encoding="utf-8")
    assert "const kit = isDark() ? GLOW : ROSETTE;" in js
    assert "shine = clamp((s.w - 0.5) / 0.5) * (1 - s.wilt)" in js  # the rosette's
    assert "shine = clamp((s.w - 0.45) / 0.55) * (1 - 0.7 * s.wilt)" in js  # the plant of light's
    assert "const w = clamp(z / 20)" in js  # the water is the server's, in twentieths
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.gd-f-pool \{[^}]*opacity: clamp\(0, calc\(\(var\(--zq\) - 10\) / 10", css)
    flat = (TEMPLATES / "_garden.html").read_text(encoding="utf-8")
    drooping = flat.count('class="gd-leaf gd-lr"') + flat.count('class="gd-leaf gd-ll"')
    assert drooping >= 10  # the leaves that droop to either side
