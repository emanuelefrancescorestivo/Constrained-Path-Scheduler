"""
What reading a syllabus with a model would cost (docs/CURRICULUM.md §3a), and
flashcards made from notes by the model at most (DECISIONS.md D84).

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
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cps import ai, cardmaker  # noqa: E402
from cps.console import ensure_utf8_output  # noqa: E402

TEXT_PER_PAGE = (1_500, 3_000)  # documented range, text of one PDF page
IMAGE_PER_PAGE = 4_784  # documented ceiling, one page as an image
INSTRUCTIONS = 2_000  # tokens: the instructions and the answer's schema (a choice)
ANSWER = 4_000  # max_tokens: thinking and up to about 40 topics (a choice)
PAGES = (2, 5, 10)
COURSES_A_SEMESTER = 6  # a full-time semester of 30 ECTS in 5-ECTS courses (an example)


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


def cards_worst(model: str) -> float:
    """Flashcards from notes by the model (D84), as the app counts a call against the
    monthly cap before making it: a full box of notes, every byte a token, and the
    answer's whole cap."""
    system, prompt = ai.cards_prompt("x" * cardmaker.MAX_TEXT, "English", "x" * 80)
    return ai.cost(model, len((system + prompt).encode()), ai.CARDS_MAX_TOKENS)


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


if __name__ == "__main__":
    main()
