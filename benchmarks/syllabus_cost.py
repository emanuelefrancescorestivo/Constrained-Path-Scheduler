"""
What reading a syllabus with a model would cost (docs/CURRICULUM.md §3a),
flashcards made from notes by the model at most (DECISIONS.md D84), and flashcards
from a whole course's materials (D101), and an answer of the coach (D176).

Nothing is called: this multiplies documented token counts by the price list the
app already keeps (`cps.ai.PRICES`, Anthropic's list as cached on 2026-10-06 and
checked against https://platform.claude.com/docs/en/about-claude/pricing on
2026-10-21). The token counts come from Anthropic's documentation, read on
2026-10-21:

- PDF support (build-with-claude/pdf-support): "Each page typically uses
  1,500-3,000 tokens per page depending on content density", and "because each
  page is converted into an image, the same image-based cost calculations are
  applied".
- Vision (build-with-claude/vision): an image costs at most 4,784 visual tokens on
  Claude 4.7 and later models (the high-resolution tier). The size at which a PDF
  page is drawn is not documented, so the PDF route below is an upper bound.
- Pricing: the Batch API is half price on input and output.

The instructions' allowance and the answer's cap are this project's choices, not
measurements; the answer cap is billed in full, as `cps.ai` already counts the
worst case against the monthly cap before a call.

    python benchmarks/syllabus_cost.py

No model answer has been measured: what a syllabus really costs, and how well
each model reads one, wait for real syllabi (CURRICULUM.md §3a).

For a course's materials (D101) the token count of a character is not documented;
two to four characters a token is this project's assumption (dense formulas near
two, prose near four), shown as a range, beside the worst case the cap reserves.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cps import ai, cardmaker, service, tips  # noqa: E402
from cps.console import ensure_utf8_output  # noqa: E402

TEXT_PER_PAGE = (1_500, 3_000)  # documented range, text of one PDF page
IMAGE_PER_PAGE = 4_784  # documented ceiling, one page as an image
INSTRUCTIONS = 2_000  # tokens: the instructions and the answer's schema (a choice)
ANSWER = 4_000  # max_tokens: thinking and up to about 40 topics (a choice)
PAGES = (2, 5, 10)
COURSES_A_SEMESTER = 6  # a full-time semester of 30 ECTS in 5-ECTS courses (an example)
CHARS_PER_TOKEN = (2.0, 4.0)  # an assumption, not a documented figure (see above)


def cost(model: str, input_tokens: int, batch: bool = False) -> float:
    """US dollars for one call that uses its whole answer cap."""
    dollars = ai.cost(model, input_tokens, ANSWER)
    return dollars / 2 if batch else dollars


def routes(pages: int) -> dict[str, tuple[int, int]]:
    """Input tokens, lowest and highest, of each way of giving the model a syllabus."""
    low, high = (INSTRUCTIONS + pages * t for t in TEXT_PER_PAGE)
    return {
        "text (pasted)": (low, high),
        "PDF (at most)": (INSTRUCTIONS + pages * (TEXT_PER_PAGE[1] + IMAGE_PER_PAGE),) * 2,
    }


def table() -> list[tuple[str, int, str, float, float]]:
    rows = []
    for model in ai.PRICES:
        for pages in PAGES:
            for route, (low, high) in routes(pages).items():
                rows.append((model, pages, route, cost(model, low), cost(model, high)))
    return rows


def main() -> None:
    ensure_utf8_output()
    print("One syllabus, one call, the answer cap billed in full (US dollars)\n")
    print(f"{'model':<20}{'pages':>6}  {'route':<15}{'lowest':>9}{'highest':>9}")
    for model, pages, route, low, high in table():
        print(f"{model:<20}{pages:>6}  {route:<15}{low:>9.4f}{high:>9.4f}")
    print("\nThe Batch API halves each figure.")
    print(f"\nWith the default monthly cap ({ai.DEFAULT_MONTHLY_CAP:.0f} USD), 10-page PDFs, at most:")
    worst = routes(10)["PDF (at most)"][1]
    for model in ai.PRICES:
        each = cost(model, worst)
        semester = COURSES_A_SEMESTER * each
        print(
            f"  {model:<20} {each:.4f} USD each: {int(ai.DEFAULT_MONTHLY_CAP // each)} syllabi a month;"
            f" a class's {COURSES_A_SEMESTER} courses, read once and shared, {semester:.2f} USD"
        )
    cards()
    materials()
    coach()


def materials_cost(model: str) -> tuple[float, float, float]:
    """A course's materials at their cut (D101): the lowest and highest estimate, at
    `CHARS_PER_TOKEN`, with the answer's whole cap; and the worst case the app counts
    against the monthly cap before the call (every byte a token)."""
    topics = [(f"t{n}", "x" * 60) for n in range(1, 25)]
    system, prompt = ai.materials_prompt(
        "x" * service.MATERIALS_MOST, "English", "x" * 80, topics, only_topics=False
    )
    chars = len(system + prompt)
    low, high = (
        ai.cost(model, round(chars / per), ai.MATERIALS_MAX_TOKENS) for per in reversed(CHARS_PER_TOKEN)
    )
    return low, high, ai.cost(model, len((system + prompt).encode()), ai.MATERIALS_MAX_TOKENS)


def materials() -> None:
    print(
        f"\nFlashcards from a course's materials (D101), one call of {service.MATERIALS_MOST:,} characters"
        f" and up to {ai.MATERIALS_CARDS} cards (US dollars):"
    )
    print(f"  {'model':<20}{'lowest':>9}{'highest':>9}{'cap reserves':>14}")
    for model in ai.PRICES:
        low, high, worst = materials_cost(model)
        mark = "  (the cards' model)" if model == ai.CARDS_MODEL else ""
        print(f"  {model:<20}{low:>9.4f}{high:>9.4f}{worst:>14.4f}{mark}")


def cards_worst(model: str) -> float:
    """Flashcards from notes by the model (D84), as the app counts a call against the
    monthly cap before making it: a full box of notes, every byte a token, and the
    answer's whole cap."""
    system, prompt = ai.cards_prompt("x" * cardmaker.MAX_TEXT, "English", "x" * 80)
    return ai.cost(model, len((system + prompt).encode()), ai.CARDS_MAX_TOKENS)


def check_worst(model: str) -> float:
    """D138: the check of the cards written, at most: the same notes, every card at the
    flashcards' longest, and the check's whole answer."""
    notes = "x" * cardmaker.MAX_TEXT
    cards = [("q" * cardmaker.FRONT_MOST, "a" * cardmaker.BACK_MOST)] * ai.CARDS_MOST
    system, prompt = ai.check_prompt(notes, cards, "English")
    return ai.cost(model, len((system + prompt).encode()), ai.CHECK_MAX_TOKENS)


def cards() -> None:
    print(
        f"\nFlashcards from notes (D84), one call at most"
        f" ({cardmaker.MAX_TEXT:,} characters, counted as the cap does):"
    )
    for model in ai.PRICES:
        each = cards_worst(model)
        print(
            f"  {model:<20} {each:.4f} USD; {int(ai.DEFAULT_MONTHLY_CAP // each)} calls under the monthly cap"
        )
    print(
        f"\nFlashcards from a file of notes, written and then checked (D138), at most, {ai.CARDS_MOST} cards:"
    )
    for model in ai.PRICES:
        both = cards_worst(model) + check_worst(model)
        print(
            f"  {model:<20} {both:.4f} USD; {int(ai.DEFAULT_MONTHLY_CAP // both)} files under the monthly cap"
        )


def coach_prompt() -> tuple[str, str]:
    """The coach's longest call (D176): a question at the box's limit, every tip's
    summary as the app sends them, and the plan's facts at their most (the next exams,
    the weakest topics, each with four of its program's topics, and six sessions)."""
    library = [{"key": t.key, "advice": t.advice, "why": t.why, "limits": t.limits} for t in tips.TIPS]
    plan = {
        "today": "2026-10-01 (Thursday)",
        "exams": [
            {"course": "x" * 40, "date": "2027-01-26", "days_left": 117}
            for _ in range(service.COACH_PLAN_EXAMS)
        ],
        "weakest_topics": [
            {"course": "x" * 40, "topic": "week of 05 Oct", "covers": ["x" * 60] * 4, "recall_now": "70 %"}
            for _ in range(service.COACH_PLAN_TOPICS)
        ],
        "today_sessions": [
            {"time": "08:00-09:30", "kind": "First review", "of": "x" * 40, "over": False} for _ in range(6)
        ],
    }
    return ai.coach_prompt("x" * service.COACH_MAX, "English", library, plan)


def coach_cost(model: str) -> tuple[float, float, float]:
    """One answer of the coach: the lowest and highest estimate at `CHARS_PER_TOKEN`,
    with the answer's whole cap; and the worst case counted against the monthly cap."""
    system, prompt = coach_prompt()
    chars = len(system + prompt)
    low, high = (ai.cost(model, round(chars / per), ai.COACH_MAX_TOKENS) for per in reversed(CHARS_PER_TOKEN))
    return low, high, ai.cost(model, len((system + prompt).encode()), ai.COACH_MAX_TOKENS)


def coach() -> None:
    system, prompt = coach_prompt()
    print(
        f"\nAn answer of the coach (D176), at its longest ({len(system + prompt):,} characters sent,"
        f" up to {ai.COACH_MAX_TOKENS:,} tokens back), US dollars:"
    )
    print(f"  {'model':<20}{'lowest':>9}{'highest':>9}{'cap reserves':>14}")
    for model in ai.PRICES:
        low, high, worst = coach_cost(model)
        mark = "  (the coach's model)" if model == ai.COACH_MODEL else ""
        print(f"  {model:<20}{low:>9.4f}{high:>9.4f}{worst:>14.4f}{mark}")
    _low, high, worst = coach_cost(ai.COACH_MODEL)
    month = 30 * service.COACH_FREE_A_DAY
    print(
        f"  Under the {ai.DEFAULT_MONTHLY_CAP:.0f} USD monthly cap, at the highest estimate:"
        f" {int(ai.DEFAULT_MONTHLY_CAP // high):,} answers, or"
        f" {int(ai.DEFAULT_MONTHLY_CAP // (high * month))} students asking all"
        f" {service.COACH_FREE_A_DAY} free answers every day of a month"
        f" ({int(ai.DEFAULT_MONTHLY_CAP // (worst * month))} at the worst case the cap reserves)."
    )


if __name__ == "__main__":
    main()
