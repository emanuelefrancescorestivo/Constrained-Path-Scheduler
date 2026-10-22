"""
Tips with their sources (DECISIONS.md D62): what research says about studying
well, each tip with how strong its evidence is, what the evidence does not show,
and the studies it rests on, each read at its source on the date given.

The rules, which `tests/test_tips.py` checks:

- every tip cites at least one source, and every source is an entry of
  docs/REFERENCES.md marked **checked**, read on the date the tip shows, with the
  same first author, year and DOI;
- every sentence a student reads has a French and an Italian entry
  (`tests/test_i18n.py` reads `sentences()`);
- a number in a tip is one its sources state;
- nothing is prescribed: the caffeine tip gives the EU's safety limits as limits,
  and says it is general information.

The text here is English; `service.tips_view` translates it where it is shown.
Pure data, no imports from the app.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

STRENGTHS = {
    "strong": ("Strong evidence", "many studies agree"),
    "moderate": ("Moderate evidence", "good studies agree, with conditions or open questions"),
    "early": ("Early evidence", "one or a few studies, or correlations only"),
}

TOPICS = {
    "studying": "How to study",
    "brain": "How the brain learns",
    "motivation": "Motivation and reward",
    "habits": "Habits",
    "breaks": "Breaks",
    "body": "Sleep, movement and coffee",
}
# What each topic is about, on the tips page's list of topics (D91).
TOPIC_NOTES = {
    "studying": "Ways to study that research shows work.",
    "brain": "What happens in the brain and in its cells when you learn.",
    "motivation": "Effort, reward and choice: what keeps you going.",
    "habits": "Keeping it going week after week.",
    "breaks": "When to stop, and for how long.",
    "body": "What sleep, exercise and caffeine do for learning.",
}


@dataclass(frozen=True)
class Source:
    key: str  # the entry in docs/REFERENCES.md
    cite: str  # as shown: "Dunlosky et al., 2013"
    venue: str
    url: str
    checked: date  # read at the source on this day (its entry says how)


@dataclass(frozen=True)
class Tip:
    key: str  # the tip's anchor on the page, kept stable for links
    topic: str
    strength: str
    advice: str
    why: str
    limits: str
    sources: tuple[str, ...]
    in_app: str = ""


CHECKED = date(2026, 10, 15)
READ = date(2026, 10, 22)  # the brain and motivation sources (D91)

SOURCES = {
    s.key: s
    for s in (
        Source(
            "dunlosky2013",
            "Dunlosky et al., 2013",
            "Psychological Science in the Public Interest",
            "https://doi.org/10.1177/1529100612453266",
            CHECKED,
        ),
        Source(
            "adesope2017",
            "Adesope et al., 2017",
            "Review of Educational Research",
            "https://doi.org/10.3102/0034654316689306",
            CHECKED,
        ),
        Source(
            "cepeda2006",
            "Cepeda et al., 2006",
            "Psychological Bulletin",
            "https://doi.org/10.1037/0033-2909.132.3.354",
            CHECKED,
        ),
        Source(
            "cepeda2008",
            "Cepeda et al., 2008",
            "Psychological Science",
            "https://doi.org/10.1111/j.1467-9280.2008.02209.x",
            CHECKED,
        ),
        Source(
            "yang2021",
            "Yang et al., 2021",
            "Psychological Bulletin",
            "https://doi.org/10.1037/bul0000309",
            CHECKED,
        ),
        Source(
            "brunmair2019",
            "Brunmair & Richter, 2019",
            "Psychological Bulletin",
            "https://doi.org/10.1037/bul0000209",
            CHECKED,
        ),
        Source(
            "rohrer2020",
            "Rohrer et al., 2020",
            "Journal of Educational Psychology",
            "https://doi.org/10.1037/edu0000367",
            CHECKED,
        ),
        Source(
            "pan2023",
            "Pan & Carpenter, 2023",
            "Educational Psychology Review",
            "https://doi.org/10.1007/s10648-023-09814-5",
            CHECKED,
        ),
        Source(
            "crede2010",
            "Credé et al., 2010",
            "Review of Educational Research",
            "https://doi.org/10.3102/0034654310362998",
            CHECKED,
        ),
        Source(
            "rasch2013",
            "Rasch & Born, 2013",
            "Physiological Reviews",
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC3768102/",
            date(2026, 10, 13),
        ),
        Source(
            "mazza2016",
            "Mazza et al., 2016",
            "Psychological Science",
            "https://doi.org/10.1177/0956797616659930",
            CHECKED,
        ),
        Source(
            "okano2019",
            "Okano et al., 2019",
            "npj Science of Learning",
            "https://doi.org/10.1038/s41539-019-0055-z",
            CHECKED,
        ),
        Source(
            "roig2013",
            "Roig et al., 2013",
            "Neuroscience & Biobehavioral Reviews",
            "https://doi.org/10.1016/j.neubiorev.2013.06.012",
            CHECKED,
        ),
        Source(
            "vandongen2016",
            "van Dongen et al., 2016",
            "Current Biology",
            "https://doi.org/10.1016/j.cub.2016.04.071",
            CHECKED,
        ),
        Source(
            "camfield2014",
            "Camfield et al., 2014",
            "Nutrition Reviews",
            "https://doi.org/10.1111/nure.12120",
            CHECKED,
        ),
        Source(
            "drake2013",
            "Drake et al., 2013",
            "Journal of Clinical Sleep Medicine",
            "https://doi.org/10.5664/jcsm.3170",
            CHECKED,
        ),
        Source(
            "efsa2015",
            "EFSA, 2015",
            "EFSA Journal",
            "https://doi.org/10.2903/j.efsa.2015.4102",
            CHECKED,
        ),
        Source(
            "albulescu2022",
            "Albulescu et al., 2022",
            "PLOS ONE",
            "https://doi.org/10.1371/journal.pone.0272460",
            CHECKED,
        ),
        Source(
            "biwer2023",
            "Biwer et al., 2023",
            "British Journal of Educational Psychology",
            "https://doi.org/10.1111/bjep.12593",
            CHECKED,
        ),
        Source(
            "harkin2016",
            "Harkin et al., 2016",
            "Psychological Bulletin",
            "https://doi.org/10.1037/bul0000025",
            CHECKED,
        ),
        Source(
            "gollwitzer2006",
            "Gollwitzer & Sheeran, 2006",
            "Advances in Experimental Social Psychology",
            "https://doi.org/10.1016/S0065-2601(06)38002-1",
            CHECKED,
        ),
        Source(
            "lally2010",
            "Lally et al., 2010",
            "European Journal of Social Psychology",
            "https://doi.org/10.1002/ejsp.674",
            CHECKED,
        ),
        # How the brain learns, and motivation and reward (D91).
        Source(
            "karpicke2008",
            "Karpicke & Roediger, 2008",
            "Science",
            "https://doi.org/10.1126/science.1152408",
            READ,
        ),
        Source(
            "schultz1997",
            "Schultz, Dayan & Montague, 1997",
            "Science",
            "https://doi.org/10.1126/science.275.5306.1593",
            READ,
        ),
        Source(
            "gruber2014",
            "Gruber et al., 2014",
            "Neuron",
            "https://doi.org/10.1016/j.neuron.2014.08.060",
            READ,
        ),
        Source(
            "adcock2006",
            "Adcock et al., 2006",
            "Neuron",
            "https://doi.org/10.1016/j.neuron.2006.03.036",
            READ,
        ),
        Source(
            "deci1999",
            "Deci, Koestner & Ryan, 1999",
            "Psychological Bulletin",
            "https://doi.org/10.1037/0033-2909.125.6.627",
            READ,
        ),
        Source(
            "patall2008",
            "Patall, Cooper & Robinson, 2008",
            "Psychological Bulletin",
            "https://doi.org/10.1037/0033-2909.134.2.270",
            READ,
        ),
        Source(
            "deslauriers2019",
            "Deslauriers et al., 2019",
            "Proceedings of the National Academy of Sciences",
            "https://doi.org/10.1073/pnas.1821936116",
            READ,
        ),
        Source(
            "kornell2008",
            "Kornell & Bjork, 2008",
            "Psychological Science",
            "https://doi.org/10.1111/j.1467-9280.2008.02127.x",
            READ,
        ),
        Source(
            "eisenberger1992",
            "Eisenberger, 1992",
            "Psychological Review",
            "https://doi.org/10.1037/0033-295X.99.2.248",
            READ,
        ),
        Source(
            "inzlicht2018",
            "Inzlicht, Shenhav & Olivola, 2018",
            "Trends in Cognitive Sciences",
            "https://doi.org/10.1016/j.tics.2018.01.007",
            READ,
        ),
        Source(
            "sisk2018",
            "Sisk et al., 2018",
            "Psychological Science",
            "https://doi.org/10.1177/0956797617739704",
            READ,
        ),
        # Cited by the weekly notes (D64) only.
        Source(
            "karpicke2009",
            "Karpicke et al., 2009",
            "Memory",
            "https://doi.org/10.1080/09658210802647009",
            date(2026, 10, 13),
        ),
        Source(
            "koriat2005",
            "Koriat & Bjork, 2005",
            "Journal of Experimental Psychology: Learning, Memory, and Cognition",
            "https://doi.org/10.1037/0278-7393.31.2.187",
            CHECKED,
        ),
        Source(
            "kukushkin2024",
            "Kukushkin et al., 2024",
            "Nature Communications",
            "https://doi.org/10.1038/s41467-024-53922-x",
            date(2026, 10, 13),
        ),
    )
}

TIPS = (
    Tip(
        "self-test",
        "studying",
        "strong",
        "Test yourself instead of rereading.",
        "A review of ten common study techniques rated practice testing among the two most useful, and "
        "rereading and highlighting among the least. A meta-analysis of practice tests found that they "
        "beat restudying and every other method they were compared with.",
        "How much testing helped varied with the kind of test, the students and what was measured.",
        ("dunlosky2013", "adesope2017"),
        "Your plan's self-tests and your flashcards are practice tests.",
    ),
    Tip(
        "spacing",
        "studying",
        "strong",
        "Spread your study over days instead of cramming.",
        "Spreading study out is the other technique that review rated most useful. A meta-analysis of "
        "317 experiments found that the best gap between sessions grows with how long you need to "
        "remember: in a study of over 1,350 people, it was about 20 to 40\u00a0% of the time until a test a "
        "week away, and 5 to 10\u00a0% for a test a year away.",
        "Most of these experiments taught facts or words to remember.",
        ("dunlosky2013", "cepeda2006", "cepeda2008"),
        "Your plan comes back to each topic at gaps that grow, timed to its exam.",
    ),
    Tip(
        "class-quizzes",
        "studying",
        "strong",
        "Treat quizzes in class as study, not only as marks.",
        "Across 222 classroom studies with 48,478 students, quizzing raised achievement by a medium "
        "amount, compared with restudying and other strategies.",
        "The effect was larger or smaller depending, among other things, on whether students were told "
        "the right answers and on how many times they were tested.",
        ("yang2021",),
        "Mark a class as a quiz and your plan adds an hour to prepare for it.",
    ),
    Tip(
        "interleave",
        "studying",
        "moderate",
        "Mix different kinds of problems when you practise.",
        "Practising kinds of problems in a mixed order, instead of one kind at a time, helped in a "
        "meta-analysis of 59 studies, more when the kinds look alike. In a randomised trial with 787 "
        "seventh-graders, mixed maths practice scored 62 against 38 for the usual practice, on the "
        "authors' test 43 days later.",
        "For learning words, one kind at a time did better, and for reading texts the result was unclear.",
        ("brunmair2019", "rohrer2020"),
    ),
    Tip(
        "pretest",
        "studying",
        "moderate",
        "Try a few questions before the lecture, even if you can't answer them yet.",
        "Answering questions on material you haven't learned yet can help you learn it, as long as you "
        "study the right answers afterwards. A 2023 review found the effect with texts, videos and "
        "lectures, despite many wrong answers on the first try.",
        "How much it helps varies with how it is done and how learning is measured, and why it works "
        "is still being studied.",
        ("pan2023",),
        "Turn on a warm-up before each lecture in Settings.",
    ),
    Tip(
        "attend",
        "studying",
        "moderate",
        "Go to class when you can.",
        "In a meta-analysis of 69 studies with over 21,000 students, attendance was strongly linked to "
        "course grades, more than admission test scores, school grades or study habits.",
        "It is a correlation: it does not prove that attending is what raises the grades.",
        ("crede2010",),
        "Can't go? Tap the class in your calendar to plan a catch-up.",
    ),
    Tip(
        "sleep-after",
        "body",
        "strong",
        "Sleep after studying: that is when memories settle.",
        "Sleep actively consolidates memory: what was learned during the day is reactivated and moved "
        "to long-term storage. In one study, 40 people who slept between two practice sessions of "
        "foreign vocabulary needed half the practice the second time, and remembered more a week and "
        "six months later.",
        "The second study is small, and about learning foreign words.",
        ("rasch2013", "mazza2016"),
    ),
    Tip(
        "sleep-regular",
        "body",
        "early",
        "Keep your sleep regular all term, not only the night before.",
        "In a college chemistry class, 88 students wore sleep trackers. Sleep on the single night before "
        "a test showed no link to its result; sleep over the week and the month before did, and sleep "
        "accounted for nearly 25\u00a0% of the differences in grades.",
        "A correlation in one class: students who sleep well may differ in other ways too.",
        ("okano2019",),
    ),
    Tip(
        "exercise",
        "body",
        "moderate",
        "Move: a workout can help you remember.",
        "A meta-analysis found that a single bout of cardiovascular exercise improved long-term memory "
        "by a moderate to large amount. In one experiment, exercise four hours after learning improved "
        "recall two days later; exercise straight after did not.",
        "Months of regular training showed no clear effect on long-term memory in the same "
        "meta-analysis, and the timing comes from a single study.",
        ("roig2013", "vandongen2016"),
    ),
    Tip(
        "caffeine",
        "body",
        "moderate",
        "Coffee or tea can sharpen your attention; keep them far from bedtime.",
        "In 11 trials, caffeine with L-theanine (found in tea) improved alertness and attention "
        "for up to two hours, the effect seeming to follow the dose of caffeine more than that of "
        "L-theanine. But 400\u00a0mg of caffeine, even taken six hours before bed, cut measured sleep by "
        "more than an hour.",
        "General information, not medical advice. The EU's food safety agency considers up to 200\u00a0mg "
        "at once and 400\u00a0mg a day of no concern for healthy adults (200\u00a0mg a day in pregnancy), "
        "and says 100\u00a0mg close to bedtime may affect some adults' sleep.",
        ("camfield2014", "drake2013", "efsa2015"),
    ),
    Tip(
        "breaks",
        "breaks",
        "moderate",
        "Take short breaks, and decide when before you start.",
        "Across 22 groups of people, breaks of up to 10 minutes made them feel more energetic and less "
        "tired. In one study of 87 students, those who took breaks at set times (6 minutes every 24, or "
        "3 every 12) felt less tired and more focused than those who stopped when they felt like it, "
        "and seemed to get as much done in less time.",
        "Short breaks did not clearly improve how well people performed; after demanding work, more "
        "than 10 minutes may be needed. The study of set breaks lasted one day.",
        ("albulescu2022", "biwer2023"),
    ),
    # How the brain learns (D91): what the research shows about memory itself.
    Tip(
        "recall-strengthens",
        "brain",
        "strong",
        "Remembering something strengthens the memory; reading it again does little.",
        "Students learning foreign words went on either studying or testing themselves on words they "
        "already knew. Studying them again had no effect on recall later; testing them again had a large "
        "one. A meta-analysis of practice tests found testing ahead of restudying across many kinds of "
        "material.",
        "The experiment taught vocabulary. Its students' predictions of how well they would do were "
        "unrelated to how they did: what feels learned is a poor guide.",
        ("karpicke2008", "adesope2017"),
        "Your flashcards and self-tests make you recall, not reread.",
    ),
    Tip(
        "surprise",
        "brain",
        "early",
        "Guess before you check: a surprise is a signal to learn.",
        "Dopamine neurons in the primate brain change their firing when a reward or an important event "
        "is not what was predicted: their output seems to signal the error of the prediction, the "
        "quantity theories of learning use to update what is expected. In students, answering questions "
        "before learning the material helps them learn it, as long as they study the answers afterwards.",
        "The recordings are from animals. That guessing before checking uses the same signal in students "
        "is a reading of the evidence, not a finding.",
        ("schultz1997", "pan2023"),
        "Turn on a warm-up before each lecture in Settings.",
    ),
    Tip(
        "spacing-cells",
        "brain",
        "early",
        "Spacing is built into how cells remember.",
        "In human cells grown in a dish, not even nerve cells, four spaced pulses of a chemical signal "
        "switched on a memory-related gene more strongly and for longer than one massed pulse. Blocking "
        "two molecules of the memory pathway, ERK and CREB, removed the difference.",
        "Cells in a dish, with a light-making gene standing for memory: it shows how basic the spacing "
        "effect is, not how to plan a semester.",
        ("kukushkin2024",),
        "Your plan spaces each topic's reviews over days.",
    ),
    Tip(
        "curiosity",
        "brain",
        "early",
        "Start with a question you want answered.",
        "In a brain-imaging study, people remembered the answers to questions they were curious about "
        "better, straight away and a day later, and also unrelated things shown to them while they were "
        "curious. Curiosity raised activity in the midbrain and the nucleus accumbens, regions of the "
        "dopamine circuit that also respond to rewards; how much it helped memory went with activity in "
        "the midbrain and the hippocampus.",
        "One laboratory study with trivia questions: whether it carries over to a semester's courses was "
        "not tested.",
        ("gruber2014",),
    ),
    Tip(
        "reward-memory",
        "brain",
        "early",
        "Knowing that something is worth remembering helps you remember it.",
        "In a brain-imaging study, people were more likely to remember a scene 24 hours later when a cue "
        "had promised a high reward for remembering it than a low one. The reward circuit (the ventral "
        "tegmental area, the nucleus accumbens) and the hippocampus became active before the scene "
        "appeared, more so for the scenes later remembered.",
        "The reward was money, in a laboratory, in one study: a reason of your own is not the same thing.",
        ("adcock2006",),
    ),
    # Motivation and reward (D91): what keeps studying going, and what does not.
    Tip(
        "rewards",
        "motivation",
        "moderate",
        "Reward yourself with progress you can see, more than with treats.",
        "A meta-analysis of 128 experiments found that rewards promised for doing or finishing a task "
        "made people less likely to go back to it by choice afterwards, while positive feedback made "
        "them more likely to, and more interested in it.",
        "The effects depended on the kind of reward, and treats harmed children's interest more than "
        "college students'. Other meta-analyses of the same research reached different conclusions.",
        ("deci1999",),
        "Your week, your streak and Progress show what you did, not points to collect.",
    ),
    Tip(
        "choice",
        "motivation",
        "moderate",
        "Choose for yourself: the order, the place, which topic first.",
        "A meta-analysis of 41 studies found that being given a choice raised motivation, effort, "
        "performance and the feeling of being capable. The effect was strongest with 2 to 4 choices in a "
        "row, and for small choices that did not change what was learned.",
        "Choices between whole activities helped less than small ones, and the effect was larger for "
        "children than for adults.",
        ("patall2008",),
        "Drag a session to another time and your plan adapts around it.",
    ),
    Tip(
        "effort-feels",
        "motivation",
        "moderate",
        "Feeling that you learn less is not proof that you do.",
        "In college physics, students put at random in active classes learned more than those taught by "
        "highly rated lecturers, yet felt they had learned less: they took the extra effort as a sign of "
        "poor learning. In another study, people judged studying one painter at a time better than mixing "
        "painters, after their own test had shown the opposite.",
        "The physics study compared two ways of teaching, not of studying alone; the paintings study was "
        "about learning to recognise styles.",
        ("deslauriers2019", "kornell2008"),
        "Recalling and mixing topics feel harder than rereading one at a time, and work better.",
    ),
    Tip(
        "effort-reward",
        "motivation",
        "early",
        "Effort can become rewarding in itself.",
        "A review of research with animals and people proposed that when effort is rewarded, the feeling "
        "of effort itself takes on some of the reward's value and becomes less unpleasant, and the habit "
        "of working hard carries over to other tasks. A later review argued that effort is costly but "
        "also adds value: the same result can be more rewarding when it took more effort.",
        "Both are reviews that propose explanations; how to make effort rewarding for a given student is "
        "not settled.",
        ("eisenberger1992", "inzlicht2018"),
        "Each session you report counts in your week and your streak: the effort is what is counted.",
    ),
    Tip(
        "mindset",
        "motivation",
        "moderate",
        "Don't count on a mindset alone to raise your grades.",
        "Two meta-analyses, one of 273 studies with 365,915 students and one of 43 studies with 57,155, "
        "found weak links between believing that intelligence can grow and grades, and weak effects of "
        "programmes that teach the belief.",
        "Students from poorer backgrounds or at risk of failing might benefit from such programmes, the "
        "authors found.",
        ("sisk2018",),
    ),
    Tip(
        "track",
        "habits",
        "moderate",
        "Keep track of what you actually did.",
        "In 138 randomised studies with nearly 20,000 people, being helped to monitor progress made "
        "people more likely to reach their goals, more so when progress was written down or shared.",
        "The studies covered all kinds of goals, not studying in particular.",
        ("harkin2016",),
        "Reporting each session with one tap is exactly this.",
    ),
    Tip(
        "if-then",
        "habits",
        "moderate",
        "Decide when and where you will study, as an if-then plan.",
        "Across 94 tests, plans of the form “if it is Tuesday at nine, I study algebra in the "
        "library” had a medium-to-large effect on reaching goals.",
        "The tests covered all kinds of goals, not studying in particular.",
        ("gollwitzer2006",),
        "Your plan gives each session a day and a time; choose the place.",
    ),
    Tip(
        "habit",
        "habits",
        "early",
        "Missing one day does not undo a habit.",
        "In a study of 96 volunteers building a daily habit, missing one opportunity did not materially "
        "affect how the habit formed. Becoming automatic took from 18 to 254 days, so give it time.",
        "One study, with habits of eating, drinking or exercise, not studying.",
        ("lally2010",),
        "That is why your streak forgives a missed day.",
    ),
)


def sentences() -> set[str]:
    """Every sentence of this module a student reads, for the translation check."""
    out = set(TOPICS.values()) | set(TOPIC_NOTES.values())
    for label, meaning in STRENGTHS.values():
        out |= {label, meaning}
    for tip in TIPS:
        out |= {tip.advice, tip.why, tip.limits} | ({tip.in_app} if tip.in_app else set())
    return out


def of_the_day(day: date) -> Tip:
    """One tip a day, taking turns: the same for everyone on a given date, and every
    tip comes back after len(TIPS) days. Tips at the right moment are a later step."""
    return TIPS[day.toordinal() % len(TIPS)]
