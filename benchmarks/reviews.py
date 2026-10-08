"""
Review mining: what students say about the apps this project competes with.

    python benchmarks/reviews.py                      # the default apps, France and the US
    python benchmarks/reviews.py --country fr --app 866450515
    python benchmarks/reviews.py --save reviews/ ...  # keep the raw answers to re-run offline
    python benchmarks/reviews.py --load reviews/      # code saved answers again, no network

It reads the most recent App Store reviews of each app (Apple's public customer
review feed, up to ten pages of fifty), sorts every review into themes with a
codebook of English and French keywords, and prints, per app and per theme, how
many reviews mention it, their mean rating, and the share among one- and two-star
reviews. docs/MARKET.md says what to read into the result and what not to.

Method, stated so it can be argued with:

* A review can carry several themes; a review that matches none counts as
  "other". Keywords are matched on whole words, case-insensitive, accents kept.
* Recent reviews are not all reviews: the feed returns the newest ones, so a
  theme that followed one bad update is over-represented. The date range is
  printed with the counts.
* Reviewers' names are never stored or printed; a quote is cut to 140
  characters.
* The development sandbox this was written in cannot reach Apple's servers, so
  the parser is tested on a sample in the feed's documented shape
  (tests/test_reviews.py), and the counts in docs/MARKET.md stay empty until the
  script is run on a machine that can.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

# App Store ids, as they appear in each app's store address (checked by web search
# on 2026-10-08). Motion is left out: its store id could not be confirmed.
APPS = {
    "My Study Life": "910639339",
    "Forest": "866450515",
    "YPT (Yeolpumta)": "1441909643",
    "Vaia (StudySmarter)": "1439949520",
    "Tiimo": "1480220328",
    "Structured": "1499198946",
    "Partielo": "1639502571",
}
COUNTRIES = ("fr", "us")
FEED = "https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app}/sortby=mostrecent/json"

# Each theme: the words that put a review in it, in English and French. Kept short
# on purpose; a theme is a question the market analysis asks, not a sentiment.
CODEBOOK: dict[str, tuple[str, ...]] = {
    theme: tuple(words.split("|"))
    for theme, words in {
        "price and paywall": "price|expensive|subscription|paywall|premium|pay|paid|money|refund|charged"
        "|prix|cher|abonnement|payant|payer|rembours",
        "bugs and data loss": "bug|bugs|crash|crashes|glitch|lost|deleted|broken|freeze|freezes"
        "|plante|beug|perdu|supprimé|marche pas|fonctionne pas",
        "sync and calendar": "sync|synced|calendar|google calendar|outlook|icloud|import|timetable"
        "|synchro|synchronisation|calendrier|emploi du temps|agenda",
        "setup and manual entry": "manually|manual|set up|setup|tedious|enter|typing|time-consuming"
        "|manuellement|saisir|fastidieux|configurer",
        "reminders and notifications": "notification|notifications|reminder|reminders|alert|alarm"
        "|rappel|rappels|notif|alerte",
        "motivation and streaks": "motivation|motivated|motivating|streak|streaks|habit|consistent"
        "|procrastinate|procrastination|motivé|habitude|régulier|série",
        "friends and social": "friend|friends|group|groups|together|leaderboard|ranking|compete"
        "|ami|amis|groupe|ensemble|classement",
        "AI": "ai|chatgpt|gpt|artificial intelligence|ia|intelligence artificielle",
        "ads": "ad|ads|advert|adverts|advertising|pub|pubs|publicité",
        "design and ease": "interface|ui|design|intuitive|confusing|cluttered|easy|simple|clean"
        "|intuitif|compliqué|facile|épuré",
    }.items()
}
OTHER = "other"


@dataclass(frozen=True)
class Review:
    app: str
    country: str
    rating: int
    title: str
    text: str
    date: str  # ISO date, as the feed gives it
    version: str = ""


def _patterns(codebook: dict[str, tuple[str, ...]]) -> dict[str, re.Pattern[str]]:
    return {
        theme: re.compile(r"(?<!\w)(?:" + "|".join(re.escape(w) for w in words) + r")(?!\w)", re.IGNORECASE)
        for theme, words in codebook.items()
    }


PATTERNS = _patterns(CODEBOOK)


def themes(review: Review) -> list[str]:
    """The themes a review mentions, in codebook order; ["other"] if none."""
    text = f"{review.title}\n{review.text}"
    found = [theme for theme, pattern in PATTERNS.items() if pattern.search(text)]
    return found or [OTHER]


def parse_feed(data: dict, app: str, country: str) -> list[Review]:
    """The reviews in one page of Apple's customer review feed (JSON). The first
    entry of a page is sometimes the app itself, without a rating: skipped."""
    entries = data.get("feed", {}).get("entry", [])
    if isinstance(entries, dict):  # a page with one entry is not a list
        entries = [entries]
    out = []
    for entry in entries:
        rating = entry.get("im:rating", {}).get("label")
        if rating is None:
            continue
        out.append(
            Review(
                app=app,
                country=country,
                rating=int(rating),
                title=entry.get("title", {}).get("label", ""),
                text=entry.get("content", {}).get("label", ""),
                date=entry.get("updated", {}).get("label", "")[:10],
                version=entry.get("im:version", {}).get("label", ""),
            )
        )
    return out


def fetch(app_id: str, country: str, pages: int = 10, timeout: float = 20.0) -> list[dict]:
    """The raw pages of the feed, newest first; stops at the first empty page."""
    answers = []
    for page in range(1, pages + 1):
        url = FEED.format(country=country, page=page, app=app_id)
        request = urllib.request.Request(url, headers={"User-Agent": "cps-review-mining/1"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not data.get("feed", {}).get("entry"):
            break
        answers.append(data)
    return answers


@dataclass(frozen=True)
class ThemeCount:
    theme: str
    reviews: int
    share: float  # of the app's reviews
    mean_rating: float
    share_of_low: float  # of the app's one- and two-star reviews
    quotes: tuple[str, ...]


def summarise(reviews: Sequence[Review], quotes: int = 2) -> dict[str, list[ThemeCount]]:
    """Per app: each theme's count, share, mean rating and share among low ratings,
    most frequent first."""
    by_app: dict[str, list[Review]] = defaultdict(list)
    for r in reviews:
        by_app[r.app].append(r)
    out = {}
    for app, items in by_app.items():
        low = [r for r in items if r.rating <= 2]
        tagged: dict[str, list[Review]] = defaultdict(list)
        for r in items:
            for theme in themes(r):
                tagged[theme].append(r)
        rows = []
        for theme, hits in tagged.items():
            low_hits = sum(1 for r in hits if r.rating <= 2)
            chosen = sorted(hits, key=lambda r: (r.rating, -len(r.text)))[:quotes]
            rows.append(
                ThemeCount(
                    theme=theme,
                    reviews=len(hits),
                    share=len(hits) / len(items),
                    mean_rating=sum(r.rating for r in hits) / len(hits),
                    share_of_low=low_hits / len(low) if low else 0.0,
                    quotes=tuple(_quote(r) for r in chosen),
                )
            )
        out[app] = sorted(rows, key=lambda c: (-c.reviews, c.theme))
    return out


def _anonymous(page: dict) -> dict:
    """A page without the reviewers' names and profile links, before it is saved."""
    entries = page.get("feed", {}).get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    kept = [{k: v for k, v in e.items() if k != "author"} for e in entries]
    return {"feed": {"entry": kept}}


def _quote(review: Review, limit: int = 140) -> str:
    text = " ".join(f"{review.title}. {review.text}".split())
    return (text[: limit - 1] + "…" if len(text) > limit else text) + f" ({review.rating}★, {review.country})"


def report(reviews: Sequence[Review]) -> str:
    """The summary as Markdown, with the date range each app's sample covers."""
    lines = []
    for app, rows in summarise(reviews).items():
        items = [r for r in reviews if r.app == app]
        dates = sorted(r.date for r in items if r.date)
        span = f"{dates[0]} to {dates[-1]}" if dates else "dates unknown"
        mean = sum(r.rating for r in items) / len(items)
        lines += [
            f"## {app}",
            "",
            f"{len(items)} reviews, {span}, mean rating {mean:.2f}.",
            "",
            "| theme | reviews | share | mean rating | share of 1-2★ |",
            "|---|---:|---:|---:|---:|",
        ]
        lines += [
            f"| {c.theme} | {c.reviews} | {c.share:.0%} | {c.mean_rating:.2f} | {c.share_of_low:.0%} |"
            for c in rows
        ]
        lines.append("")
        for c in rows[:5]:
            for q in c.quotes:
                lines.append(f"- *{c.theme}*: {q}")
        lines.append("")
    return "\n".join(lines)


def load(directory: Path) -> list[Review]:
    """Reviews saved by `--save`: one JSON file per app and country."""
    out = []
    for path in sorted(directory.glob("*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        for page in saved["pages"]:
            out += parse_feed(page, saved["app"], saved["country"])
    return out


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", action="append", help="an App Store id (default: the apps in APPS)")
    parser.add_argument("--country", action="append", help="a store country code (default: fr and us)")
    parser.add_argument("--pages", type=int, default=10)
    parser.add_argument("--save", type=Path, help="keep the raw answers in this directory")
    parser.add_argument("--load", type=Path, help="code answers saved earlier instead of fetching")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.load:
        reviews = load(args.load)
    else:
        names = {v: k for k, v in APPS.items()}
        apps = args.app or list(APPS.values())
        reviews = []
        for app_id in apps:
            for country in args.country or COUNTRIES:
                pages = [_anonymous(p) for p in fetch(app_id, country, args.pages)]
                name = names.get(app_id, app_id)
                if args.save:
                    args.save.mkdir(parents=True, exist_ok=True)
                    target = args.save / f"{app_id}-{country}.json"
                    target.write_text(json.dumps({"app": name, "country": country, "pages": pages}), "utf-8")
                for page in pages:
                    reviews += parse_feed(page, name, country)
    if not reviews:
        print("no reviews read", file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(report(reviews))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
