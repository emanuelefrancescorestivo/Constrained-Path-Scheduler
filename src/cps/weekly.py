"""
The weekly note on the science of learning (DECISIONS.md D64): one study a week,
told in plain words, with what it means for a student, what it does not show, and
its source. A plan's first note is there from its first day; one more each Monday.

The rules are the tips' (D62), and `tests/test_tips.py` checks them here too: a
note cites only sources of `tips.SOURCES` (entries of docs/REFERENCES.md marked
checked), every figure it writes in digits is in what was read of them, and every
sentence has a French and an Italian entry.

Pure data and date arithmetic; `service` translates and keeps what was read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Note:
    key: str  # its address, kept stable
    title: str
    paragraphs: tuple[str, ...]
    for_you: str
    limits: str
    sources: tuple[str, ...]


NOTES = (
    Note(
        "rereading",
        "Why rereading feels better than it works",
        (
            "When 177 college students were asked how they study, most said they reread their notes or "
            "textbooks again and again; relatively few tested themselves.",
            "Rereading feels productive because the answer is in front of you. In experiments on how people "
            "judge their own learning, judging while the answer was there gave a sense of knowing it that "
            "the test, where the answer was missing, did not bear out.",
        ),
        "Close the book and try to recall before you check: what you can say without it is what you know.",
        "The survey asked what students do, not how well it worked; the experiments on judging used pairs "
        "of words.",
        ("karpicke2009", "koriat2005"),
    ),
    Note(
        "ridgeline",
        "The best gap between two reviews",
        (
            "More than 1,350 people learned a set of facts, reviewed them after a gap of up to three and a "
            "half months, and were tested up to a year later.",
            "For each test date, a longer gap first helped and then slowly hurt. The best gap grew with the "
            "test's distance: about 20 to 40 % of the time to a test a week away, and about 5 to "
            "10 % for a test a year away.",
        ),
        "Cramming the night before sets the gap to zero. Your plan spreads each topic's reviews out instead.",
        "Facts to remember, not problems to solve; one study, though a large one.",
        ("cepeda2008",),
    ),
    Note(
        "sleep-between",
        "A night's sleep between two sessions",
        (
            "Forty people practised foreign vocabulary until they knew it perfectly, in two sessions 12 "
            "hours apart. Half learned in the morning and relearned in the evening; the other half "
            "learned in the evening, slept, and relearned the next morning.",
            "Those who slept between the sessions needed half as much practice to relearn, and remembered "
            "much more a week and six months later.",
        ),
        "When a topic is hard, split it across an evening and the next morning, with a night's sleep "
        "between.",
        "One small study, with words in a foreign language.",
        ("mazza2016",),
    ),
    Note(
        "classroom-quizzes",
        "What 222 classroom studies say about quizzes",
        (
            "A review gathered 222 studies of quizzing in real classes, with 48,478 students. Quizzing "
            "raised achievement by a medium amount, compared with restudying and other strategies.",
            "The benefit was larger or smaller with, among other things, whether students got corrective "
            "feedback, how many times they were tested, and whether the quiz matched the final test's "
            "format.",
        ),
        "When a quiz comes back, look at what you missed: the correction is part of the benefit.",
        "The effect varied between studies, with the conditions above among the reasons.",
        ("yang2021",),
    ),
    Note(
        "mixed-practice",
        "Mixed practice in maths",
        (
            "In a randomised trial in 787 seventh-graders' classes, mixed maths practice, with problems of "
            "different kinds together, scored 62 against 38 for the usual practice on the authors' test 43 "
            "days later.",
            "A meta-analysis of 59 studies found that mixing helps overall, most when the kinds of problem "
            "look alike, with a smaller effect for maths than for paintings.",
        ),
        "When you practise for an exam, mix problems from different chapters instead of doing one "
        "chapter's at a time.",
        "For learning words, one kind at a time did better, and for reading texts the result was unclear.",
        ("rohrer2020", "brunmair2019"),
    ),
    Note(
        "planned-breaks",
        "Breaks you plan, and breaks you take",
        (
            "For one day of self-study, 87 students of a Dutch university either took breaks when they "
            "liked, or stopped for 6 minutes every 24, or for 3 minutes every 12.",
            "Those who chose their own breaks studied and rested for longer stretches, and felt more tired "
            "and distracted and less focused and motivated. All groups got about as much done; the set "
            "breaks seemed to do it in less time.",
        ),
        "Before a session, decide when you will stop. On Focus, the app asks after 24 minutes.",
        "One day of study, at one university.",
        ("biwer2023",),
    ),
    Note(
        "sleep-term",
        "Sleep across the term, not the night before",
        (
            "In a college chemistry class, 88 students wore sleep trackers through the term. Sleep on the "
            "night before a test showed no link to its result.",
            "Sleep over the week and the month before tests did: longer, better and more regular sleep went "
            "with better grades, and sleep accounted for nearly 25 % of the differences in grades.",
        ),
        "Keep regular hours in the weeks before exams; one good night at the end is not what the study "
        "found mattered.",
        "Correlations in one class: a link, not proof that the sleep made the grades.",
        ("okano2019",),
    ),
    Note(
        "cells",
        "Spacing works even in cells",
        (
            "Researchers gave two kinds of human cells that are not nerve cells the same stimulation, "
            "either as four pulses spaced out in time or as one massed pulse.",
            "The spaced pulses switched on a reporter of a memory-related gene more strongly and for "
            "longer, and blocking either of two molecules involved removed the difference.",
        ),
        "Not a study method in itself, but one more piece of evidence that spaced repetition beats massed "
        "repetition, here even in cells outside the brain.",
        "Cells in a dish, not people learning: what it means for studying was not tested.",
        ("kukushkin2024",),
    ),
    Note(
        "if-then",
        "If it is Tuesday at nine",
        (
            "Across 94 tests, people who wrote their goal as a plan of the form “if this situation "
            "comes, I will do this” reached it more often than those who only intended to, a "
            "medium-to-large effect.",
            "The plans helped people start, keep going despite distractions, and let go of courses of "
            "action that were failing.",
        ),
        "Give each study session a when and a where: your plan gives the when; add the where.",
        "The tests covered all kinds of goals, not studying in particular.",
        ("gollwitzer2006",),
    ),
    Note(
        "habits",
        "How long a habit takes",
        (
            "In one study, 96 volunteers chose something to do every day in the same situation, such as "
            "after breakfast, and said each day how automatic it felt, for 12 weeks.",
            "Reaching their limit of automaticity took from 18 to 254 days, and missing one day did not "
            "materially affect how the habit formed.",
        ),
        "Studying at the same time each day may take a while to become automatic; one missed day does "
        "not reset it.",
        "One study, with habits of eating, drinking or exercise, not studying.",
        ("lally2010",),
    ),
)


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def unlocked(start: date, today: date) -> int:
    """How many notes a plan started on `start` has on `today`: the first from its
    first day, one more each Monday, until there are no more."""
    weeks = (monday(today) - monday(start)).days // 7
    return max(0, min(len(NOTES), weeks + 1)) if today >= start else 0


def sentences() -> set[str]:
    """Every sentence of this module a student reads, for the translation check."""
    out: set[str] = set()
    for note in NOTES:
        out |= {note.title, note.for_you, note.limits, *note.paragraphs}
    return out
