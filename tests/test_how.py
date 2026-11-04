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
    assert len(re.findall(r'class="[^"]*\bscroll-fig\b', text)) == 9  # one per scene
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
    assert page.count('class="sci-dot st-pop') == len(kept["marks"]) + 4  # with the spacing chart's four


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
