"""
D109: how the code works. The page runs the research planner on demo.py's worked
example as it is drawn, so its numbers are the code's own; its sentences claim only
what those numbers say; its figures draw themselves as the page scrolls, and are
complete without that. The science page's charts do the same, with one more: a topic
kept up by self-tests against the same topic left alone.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest

from cps import cards as flashcards
from cps import plan, service, ssp
from cps.memory import FSRS_6, MemoryState, retrievability, stability_for_interval
from cps.timegrid import TimeGrid

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from cps.web import Config, create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src/cps/web/static"
EXAMPLES = ROOT / "examples"
NOW = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def view() -> dict:
    return service.code_view()


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    config = Config(
        store=tmp_path_factory.mktemp("how"), clock=lambda: NOW, background=False, sweep_every=None
    )
    with TestClient(create_app(config)) as client:
        yield client


def test_the_worked_example_is_demo_py_part_3_solved_again():
    """Built here as demo.py builds it (a student's week of five days, the first two
    topics, one block an evening), the exhaustive optimum and AO*'s agree with the page."""
    weekdays = [d for d in range(5) if d % 7 < 5]
    grid = TimeGrid(days=5).block_daily(23, 7).block_daily(8, 18, days=weekdays)
    target = stability_for_interval(21.0, 0.9)
    topics = [("Analysis", MemoryState(2.0, 7.0)), ("Algebra", MemoryState(4.0, 5.0))]
    continuation = ssp.solve(ssp.SSPConfig.for_heuristic(target))
    instance = plan.Instance.build(grid, topics, target, continuation, max_blocks_per_day=1)
    exact = plan.solve_exact(instance)
    solved = service._code_solved()
    assert solved["exact"] == (pytest.approx(exact.value, abs=1e-12), exact.nodes)
    assert len(solved["instance"].blocks) == len(instance.blocks) == 5
    for key, h, nodes, value in solved["runs"]:
        assert value == pytest.approx(exact.value, abs=1e-9), key  # every guess reaches the optimum
        assert h <= exact.value + 1e-9 and nodes < exact.nodes


def test_the_numbers_on_the_page_are_the_codes(view):
    assert view["slots"] == 5 * 48 and view["busy_slots"] + view["free_slots"] == view["slots"]
    for day in view["days"]:
        assert len(day["bits"]) == 48 and "".join(day["groups"]) == day["bits"]
        # sleep until 07:00 and classes 08:00 to 18:00 are taken; 07:00 and 18:00 are free
        assert day["bits"][:14] == "1" * 14 and day["bits"][14:16] == "00" and day["bits"][36] == "0"
    assert [b["hour"] for b in view["blocks"]] == ["18:00"] * 5 and view["block_hours"] == "1.5"
    assert view["factor"] == "19/81" and view["decay"] == "-0.5" and view["target"] == "21"
    assert view["penalty"] == "5"  # the number of blocks: plan.Instance.build's default
    analysis = MemoryState(2.0, 7.0)
    assert view["review"]["recall"] == round(100 * retrievability(0.75, analysis.stability))
    assert view["curves"][0]["at_exam"] == round(100 * retrievability(21.0, 2.0))
    policy = service._code_solved()["policy"]
    assert view["h_sum"] == "3.221" and view["sweeps"] == policy.sweeps and policy.residual < 1e-6
    assert view["exact"] == {"value": "13.22073", "states": "3906"}
    assert [r["nodes"] for r in view["runs"]] == ["184", "118", "151", "114"]
    assert all(r["optimal"] for r in view["runs"]) and view["admissible"] is True
    # The value table falls with stability along each row and grows with difficulty
    # down each column, as the cost to reach a target must.
    values = [[float(c["value"]) for c in row["cells"]] for row in view["table"]]
    assert all(row == sorted(row, reverse=True) for row in values)
    assert all(list(col) == sorted(col) for col in zip(*values, strict=True))
    assert view["columns"] == ["1", "2", "4", "8", "16"]


def test_what_the_sentences_say_stays_true(view):
    """The page's words are fixed where its numbers are not; these are the claims."""
    assert [c["topic"] for c in view["curves"]] == ["Analysis", "Algebra"]  # "{a} and {b}"
    # "Neither is there yet."
    assert all(float(c["stability"]) < float(view["target"]) for c in view["curves"])
    # "Here the best is to leave tonight free": the empty topic is the block left free.
    assert view["best"] == "" and view["best_q"] == "13.22"
    q = {c["topic"]: float(c["q"]) for c in view["choices"]}
    assert q[""] < q["Analysis"] and q[""] < q["Algebra"]
    # "this week cannot win the exam alone": neither topic reaches the target even
    # with every block on it and every recall a success.
    assert view["reachable"] is False and all(float(r["stability"]) < 21 for r in view["reach"])
    # "recalled ... S becomes", "forgotten ... S falls to"
    assert float(view["review"]["recalled"]) > 2.0 > float(view["review"]["forgot"])


def test_the_page_opens_for_anyone_and_inside_a_plan(web, view):
    page = web.get("/how")
    assert page.status_code == 200
    text = page.text
    assert text.count("<h1") == 1 and "How the code works" in text
    ids = re.findall(r'\sid="([^"]+)"', text)
    assert len(ids) == len(set(ids))
    assert " style=" not in text and "<script>" not in text  # nothing the CSP would block
    for number in (view["exact"]["value"], view["exact"]["states"], view["h_sum"], view["best_q"], "19/81"):
        assert number in text
    # Ten scenes: four run while scrolled through (D112), the rest draw themselves.
    assert text.count('class="code-scene') == 10
    assert len(re.findall(r'<div class="code-pin[ "]', text)) == 4
    assert len(re.findall(r'class="[^"]*\bscroll-fig\b', text)) == 7
    assert 'href="/science"' in text
    assert "Comment marche le code" in web.get("/how?lang=fr").text
    assert "Come funziona il codice" in web.get("/how?lang=it").text
    files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
    start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    token = start.headers["location"].split("/")[2]
    inside = web.get(f"/p/{token}/how")
    assert inside.status_code == 200 and f'href="/p/{token}/science"' in inside.text
    # The science page leads to it, inside a plan and outside.
    assert f'href="/p/{token}/how"' in web.get(f"/p/{token}/science").text
    assert 'href="/how"' in web.get("/science").text
    assert web.get("/p/not-a-plan/how").status_code == 404


def test_the_science_page_shows_a_topic_kept_up_and_left_alone(web):
    kept = service.science_view()["kept"]
    target = round(100 * flashcards.RETENTION)
    assert kept["target"] == target and kept["reviews"] == len(kept["marks"]) == len(kept["days"]) >= 2
    gaps = [b - a for a, b in zip([0, *kept["days"]], kept["days"], strict=False)]
    assert gaps == sorted(gaps) and len(set(gaps)) == len(gaps)  # "each gap longer than the last"
    assert kept["alone_end"] == round(100 * FSRS_6.retrievability(60.0, 2.0)) < target
    assert float(kept["last"]) > 2 * 30  # it ends holding months, not days
    page = web.get("/science").text
    assert 'id="kept"' in page and kept["alone"] in page and kept["points"] in page
    assert page.count('class="sci-dot sci-pop eb') == len(kept["marks"])  # each test on its step (D113)


def test_figures_draw_themselves_only_where_motion_is_wanted():
    """The stages, the drawn lines, the bars and the marks animate only inside the block
    that asks for scroll-driven animations and for motion; elsewhere every figure is
    complete. Nothing loops."""
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # what the comments say is not a rule
    opening = "@supports (animation-timeline: view()) {\n  @media (prefers-reduced-motion: no-preference) {\n"
    start = css.index(opening + '    :root:not([data-motion="reduce"]) .scroll-fig')
    end = css.index("\n}\n", start)
    block, outside = css[start:end], css[:start] + css[end:]
    for name in (
        "view-timeline: --fig",
        "animation-timeline: --fig",
        "stroke-dasharray: 1",
        "code-draw",
        "code-bar",
        "code-pop",
    ):
        assert name in block
    assert "animation-timeline: --fig" not in outside and ".st-draw" not in outside
    assert not re.search(r"\.scroll-fig[^{}]*\{[^{}]*infinite", css)
    for template in ("how.html", "science.html"):
        text = (ROOT / "src/cps/web/templates" / template).read_text(encoding="utf-8")
        # a drawn line carries its length as 1, so the dash that draws it is the line
        for line in re.findall(r"<(?:polyline|line|rect)[^>]*st-draw[^>]*>", text):
            assert 'pathLength="1"' in line, line


def test_the_score_is_shown_term_by_term_and_adds_up_to_the_optimum(view):
    """D110: the receipt of scene 3 is the plan the search chose, every evening free."""
    blocks, owed, unready, total = service._code_solved()["all_free"]
    exact_value, _ = service._code_solved()["exact"]
    assert total == pytest.approx(exact_value, abs=1e-12) and blocks == 0
    penalty = service._code_solved()["instance"].lateness_penalty
    assert owed + penalty * unready == pytest.approx(total, abs=1e-12)
    assert view["score"] == {"blocks": "0", "owed": "3.22", "unready": 2, "penalties": "10", "total": "13.22"}
    assert view["overshoots"] == "0" and view["admissible"] is True
    assert view["futures"] == 5 and view["opened_share"] == 3  # 114 of 3906 states, rounded


def test_the_calendar_drawn_is_the_bits(view):
    """Each day's blocks of sleep and classes cover exactly its 1s."""
    for day in view["days"]:
        drawn = ["0"] * 48
        for stretch in day["runs"]:
            assert stretch["kind"] in ("sleep", "classes")
            for k in range(stretch["start"], stretch["end"]):
                drawn[k] = "1"
        assert "".join(drawn) == day["bits"]
        assert [s["kind"] for s in day["runs"]] == ["sleep", "classes", "sleep"]  # weekdays


def test_the_pages_wear_the_apps_look(web, view):
    """The topics in the first two course colours, the exam in red. The dusk sky of D110
    is gone since D120: the explaining pages are read on the app's plain grey."""
    page = web.get("/how").text
    assert "data-sky" not in page and "/sky/" not in page
    assert 'class="dot c0"' in page and 'class="dot c1"' in page
    assert 'class="code-exam"' in page and 'class="code-exam-line"' in page
    assert 'class="code-progress"' in page
    assert "data-sky" not in web.get("/science").text
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".code-progress { display: none; }" in css  # a bar only where scrolling fills it
    assert "animation-timeline: scroll(root);" in css


def test_the_landscape_and_the_layers_are_the_numbers(web, view):
    """D111: a tower per cell of the value table, as tall as its value (the largest
    twenty steps), the topics' towers marked in their colours; the week in three
    layers, sleep, classes and the study blocks, that are one calendar when flat."""
    values = [float(c["value"]) for row in view["table"] for c in row["cells"]]
    heights = [c["z"] for row in view["table"] for c in row["cells"]]
    assert max(heights) == 20 and all(1 <= z <= 20 for z in heights)
    assert all(
        (a > b) <= (za >= zb)
        for a, za in zip(values, heights, strict=True)
        for b, zb in zip(values, heights, strict=True)
    )  # taller where costlier
    page = web.get("/how").text
    assert page.count('class="code-tower ') == len(values) == 25
    assert 'on-c0"' in page and 'on-c1"' in page
    for layer in ("code-layer-base", "code-layer-classes", "code-layer-study"):
        assert f"code-layer {layer}" in page
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert all(f".tz{n} {{ --h: calc({n} * var(--u)); }}" in css for n in range(1, 21))
    # A tower never fades while it grows (D112): opacity below 1 would flatten it.
    sweep = re.search(r"@keyframes code-sweep \{(.*?)\n\}", css, re.S)
    assert sweep and "opacity" not in sweep[1] and "--zq: var(--q7)" in sweep[1]


# --------------------------------------------------------------------------- #
# D112: the engine as it runs
# --------------------------------------------------------------------------- #


def test_the_traces_record_the_search_without_changing_it():
    """AO* and value iteration take an optional trace; with it they return the same
    answer, and the trace is the run: one entry per expansion or sweep, in order."""
    solved = service._code_solved()
    instance = solved["instance"]
    for key, heuristic in solved["heuristics"].items():
        trace: list[plan.Expansion] = []
        traced = plan.solve_ao_star(instance, heuristic, trace=trace)
        plain = plan.solve_ao_star(instance, heuristic)
        assert (traced.value, traced.nodes, traced.policy) == (plain.value, plain.nodes, plain.policy)
        assert len(trace) == traced.nodes
        assert [e.state for e in trace] == [e.state for e in solved["traces"][key]]
    sweeps: list = []
    config = solved["policy"].config
    traced_policy = ssp.solve(config, trace=sweeps)
    plain_policy = ssp.solve(config)
    assert len(sweeps) == traced_policy.sweeps == plain_policy.sweeps
    assert (sweeps[-1] == plain_policy.expected_cost).all()
    assert (traced_policy.expected_cost == plain_policy.expected_cost).all()


def test_the_search_drawn_is_the_search_that_ran(web, view):
    """The disc's nodes are the states AO* opened (and those its best plan reached), each
    shown when it was produced and filled when it was opened; the best plan of each
    step is the marked graph then; the last is every evening left free."""
    solved = service._code_solved()
    trace = solved["traces"]["capacity"]
    optimum = solved["exact"][0]
    search = view["search"]
    # "The estimate at the root rises toward the true cost and never passes it."
    values = [solved["heuristics"]["capacity"](solved["instance"], solved["instance"].initial)]
    values += [e.root_value for e in trace]
    assert all(b >= a - 1e-12 for a, b in pairwise(values))
    assert all(v <= optimum + 1e-9 for v in values) and values[-1] == pytest.approx(optimum, abs=1e-9)
    assert search["start"] == "4"  # "the best plan costs at least {h}"
    # "Here, every evening left free": the last best plan leaves each block free.
    final = trace[-1].marked
    assert final and all(action == plan.SKIP for _, action, _ in final) and len(final) == 5
    assert [p["final"] for p in search["plans"]] == [False] * (len(search["plans"]) - 1) + [True]
    assert all(e["tone"] == "skip" for e in search["plans"][-1]["edges"])
    opened = [d for d in search["dots"] if d["opened"] is not None]
    assert len(opened) == len(trace) == int(view["runs"][-1]["nodes"]) == 114
    assert 175 <= len(search["dots"]) <= 185  # "about 180 drawn" (D112)
    assert all(d["made"] <= d["opened"] for d in opened)
    assert [c["opened"] for c in search["counts"]] == sorted(c["opened"] for c in search["counts"])
    assert search["counts"][search["last"]]["opened"] == 114
    # The steps of the scroll are cut over the longest run, the one with no guess.
    assert search["last"] == (114 - 1) * service.CODE_SEARCH_STEPS // 184
    # Each node on the ring of its evening, tonight in the centre.
    rings = [0.0] + [r["r"] for r in search["rings"]]
    for d in search["dots"]:
        assert round((d["x"] ** 2 + d["y"] ** 2) ** 0.5) in {round(r) for r in rings}
    page = web.get("/how").text
    assert page.count('class="code-gdot ') == len(search["dots"])
    assert page.count('class="code-odot ') == len(opened)
    assert page.count('<g class="code-plan ') == len(search["plans"])
    assert page.count(" code-final") == 1
    assert search["guided"] in page and search["blind"] in page


def test_the_value_table_fills_sweep_by_sweep(view):
    """The towers' heights at each stop are the table after that many sweeps: all 0,
    then 1 everywhere (one review), rising to the settled table; never falling."""
    stops = view["sweep_stops"]
    expected = [*service.CODE_SWEEP_STOPS, view["sweeps"]]
    assert [s["sweep"] for s in stops] == expected == [0, 1, 2, 3, 4, 6, 10, 51]
    sweeps = service._code_solved()["sweeps"]
    assert len(sweeps) == view["sweeps"]
    # "A sweep gives every cell its cheapest review: 1 for the review ..." from 0.
    first = sweeps[0]
    assert set(first.ravel().tolist()) == {0.0, 1.0}  # 1 everywhere, 0 where the target is already met
    assert float(first[:, 0].max()) == 1.0
    assert stops[-1]["change"] == view["residual"]  # "no cell moves by more than {e}"
    assert [s["change"] for s in stops] == ["", "1", "1", "1", "0.88", "0.48", "0.09", "9e-10"]  # D112's text
    for row in view["table"]:
        for cell in row["cells"]:
            heights = cell["zq"]
            assert len(heights) == 8 and heights[0] == 0 and heights[-1] == cell["z"]
            assert heights == sorted(heights)


def test_the_scenes_that_run_have_their_steps_and_stand_still_without_motion(web, view):
    """Each pinned scene lists its steps; without scroll-driven animations or with
    motion off, the figure is complete and the steps are a list; with them, the track
    and its timeline exist only in the motion block. The replanning strip's CSS holds
    the page's numbers."""
    page = web.get("/how").text
    for scene, steps in (("bits", 4), ("value", 4), ("search", 5), ("rolling", 4)):
        part = page.split(f'id="{scene}"', 1)[1].split("</section>", 1)[0]
        assert '<div class="code-pin' in part
        caps = re.findall(r'<li class="cap(\d)of(\d)">', part)
        assert [int(a) for a, _ in caps] == list(range(1, steps + 1)) and {int(b) for _, b in caps} == {steps}
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    opening = "@supports (animation-timeline: view()) {\n  @media (prefers-reduced-motion: no-preference) {\n"
    start = css.index(opening + '    :root:not([data-motion="reduce"]) .scroll-fig')
    end = css.index("\n}\n", start)
    block, outside = css[start:end], css[:start] + css[end:]
    assert "view-timeline: --pin block" in block and "position: sticky" in block
    assert "--pin" not in outside and "position: sticky; top: var(--topbar); height" not in outside
    for k in (4, 5):
        assert all(f".cap{i}of{k} {{ animation-range:" in block for i in range(1, k + 1))
    ranges = (f".eb{s} {{ animation-range: contain {4 * s}% contain {4 * s + 4}%; }}" for s in range(25))
    assert all(r in block for r in ranges)
    # Without motion: the last best plan and the last readout only, the steps a list.
    for rule in (
        ".code-plan { display: none; }",
        ".code-plan.code-final { display: inline; }",
        ".code-readout .code-was { display: none; }",
        ".code-cover { fill: var(--surface); display: none; }",
    ):
        assert rule in outside, rule
    assert all(f".q{i}z{z} {{ --q{i}: {z}; }}" in css for i in range(8) for z in range(21))
    # The strip: 21 days, a window of 5, 17 plans (16 moves of a day).
    assert (view["exam_days"], view["day_count"], view["replans"]) == ("21", 5, 17)
    assert "repeat(21, minmax(0, 1fr)) minmax(0, 3fr)" in css and "width: calc(100% * 5 / 24)" in css
    assert "translateX(320%)" in css and "steps(17, jump-none)" in block
    assert not re.search(r"@keyframes code-[a-z-]+ \{[^@]*infinite", css)


def test_the_technical_pages_are_solid_not_glass(web):
    """The owner's report (AUDIT 94): see-through panes made the figures hard to follow.
    Since D120 the code and science pages have no sky at all, so no glass rule (each
    is under `body[data-sky]`) reaches their panels, and their bars are solid."""
    mains = (
        ("/how", '<main class="site code-page read tech">'),
        ("/science", '<main class="site read tech">'),
    )
    for path, main in mains:
        page = web.get(path).text
        assert main in page and "data-sky" not in page and 'class=" reading"' in page
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    glass = re.findall(r"^([^{}\n]*)\{[^}]*backdrop-filter: blur", css, flags=re.M)
    assert not [rule for rule in glass if "reading" in rule or "main.tech" in rule]
    bars = re.search(r"body\.reading \.topbar, body\.reading \.tabbar \{([^}]*)\}", css)
    assert bars and "background: var(--bg)" in bars[1] and "backdrop-filter: none" in bars[1]


# --------------------------------------------------------------------------- #
# D113: the science page in motion
# --------------------------------------------------------------------------- #


def test_the_science_scenes_say_what_the_model_says():
    """Each sentence of the science page's scrolled scenes, held to FSRS-6's numbers."""
    view = service.science_view()
    fade, land, kept = view["fade"], view["land"], view["kept"]
    # Memory fading: each column drains, never fills; the readouts walk the days.
    for column in fade["columns"]:
        assert column["zq"][0] == 20 and column["zq"] == sorted(column["zq"], reverse=True)
    days = [r["day"] for r in fade["readouts"]]
    assert days == sorted(days) and days[-1] == view["days"] == 60 and len(days) == service.SCIENCE_STEPS
    on = {r["day"]: r["recall"] for r in fade["readouts"]}
    assert fade["shaky_at_90"] == "2" and on[2][0] == 90  # "the shaky one is at 90 % on day 2"
    assert on[7][1] == 90 and on[7][2] == fade["solid_at_week"] == 97  # "the week-long one ... day 7"
    assert on[60] == fade["end"] == [59, 71, 85]  # "after 60 days: 59 %, 71 % and 85 %"
    # The spacing effect: a later test gains more (along a row); a solid topic gains
    # less from the same wait (down a column); at once, nothing.
    times = land["times"]
    for s in service.SCIENCE_LAND_STABILITIES:
        row = [times[(s, r)] for r in service.SCIENCE_LAND_RECALLS]
        assert row[0] == pytest.approx(1.0, abs=1e-9) and row == sorted(row)
    for r in service.SCIENCE_LAND_RECALLS[1:]:
        column = [times[(s, r)] for s in service.SCIENCE_LAND_STABILITIES]
        assert column == sorted(column, reverse=True)  # "the front rows stay lower"
    week = {c["recall"]: c for c in land["columns"]}
    assert 1.8 <= times[(7.0, 0.95)] <= 2.4  # "about twice as long"
    assert week[90]["after"] == "23" and week[100]["after"] == "7" and week[70]["risk"] == 30
    assert week[95]["after"] == "15" and week[70]["after"] == "60"  # D113's text
    assert kept["days"] == [2, 10, 35] and kept["last"] == "74" and kept["alone_end"] == 59
    assert [c["risk"] for c in land["columns"]] == sorted(c["risk"] for c in land["columns"])
    # Kept up: never below the target between tests, held longer after each one; the
    # topic alone only fades; the last step is the page's ending.
    steps = kept["readouts"]
    assert all(r["recall"] >= kept["target"] for r in steps)
    holds = [float(r["holds"]) for r in steps]
    assert (
        holds == sorted(holds)
        and steps[-1]["holds"] == kept["last"]
        and steps[-1]["alone"] == kept["alone_end"]
    )
    alone = [r["alone"] for r in steps]
    assert alone == sorted(alone, reverse=True)
    assert kept["steps"] == sorted(kept["steps"]) and len(kept["steps"]) == kept["reviews"]


def test_the_science_page_runs_as_it_is_scrolled_and_stands_still_without(web):
    page = web.get("/science").text
    assert len(re.findall(r'<div class="code-pin[ "]', page)) == 3
    assert page.count('class="code-tower sci-drain ') == 3 and page.count('class="code-tower sci-rise ') == 25
    for scene, steps in (("forgetting", 4), ("spacing", 5)):
        part = page.split(f'id="{scene}"', 1)[1]
        caps = re.findall(r'<li class="cap(\d)of(\d)">', part.split("</ol>", 1)[0])
        assert [int(a) for a, _ in caps] == list(range(1, steps + 1))
    assert page.count('class="sci-cursor"') == 2 and page.count('class="code-cover"') == 2
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    opening = "@supports (animation-timeline: view()) {\n  @media (prefers-reduced-motion: no-preference) {\n"
    start = css.index(opening + '    :root:not([data-motion="reduce"]) .scroll-fig')
    end = css.index("\n}\n", start)
    block, outside = css[start:end], css[:start] + css[end:]
    for rule in ("animation-name: sci-cursor", "animation-name: sci-drain", "animation-name: sci-rise"):
        assert rule in block and rule not in outside
    assert ".sci-cursor { display: none;" in outside  # no cursor without motion
    drain = re.search(r"@keyframes sci-drain \{(.*?)\n\}", css, re.S)
    assert drain and "opacity" not in drain[1] and drain[1].count("--zq") == 8


def test_how_the_code_works_is_under_you_beside_the_science(web):
    files = {"file": ("timetable.ics", (EXAMPLES / "sample-semester.ics").read_bytes(), "text/calendar")}
    start = web.post("/start", data={"tz": "Europe/Paris"}, files=files, follow_redirects=False)
    token = start.headers["location"].split("/")[2]
    you = web.get(f"/p/{token}/you").text
    science = you.index(f'href="/p/{token}/science"')
    how = you.index(f'href="/p/{token}/how"')
    assert science < how and 'class="art art-code"' in you[how:]
    assert web.get(f"/p/{token}/how").status_code == 200
