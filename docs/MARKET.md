# Market analysis

*Who this is for, who else serves them, what keeps people coming back to apps
like these, and where this project fits. Written 2026-10-08 from web research.
Every number has a source. Where the source is a search engine's excerpt of a
page rather than the page itself, it says so: the development sandbox could
search the web but could not open pages. Nothing here was measured on students
yet; the pilot (docs/PRODUCT.md) is how that happens.*

The owner's choices this analysis led to (2026-10-08): **France first, French and
English; a free pilot, then a free core with a paid tier; private, opt-in study
buddies; AI features on a small monthly budget.** `DECISIONS.md` records them.

## 1. Who it is for

**Primary: a student at a French university or grande école whose timetable is in
ADE or Hyperplanning, with partiels at the end of each semester and graded work
during it.** They already live in a calendar their university publishes; what they
lack is the time to turn it into a revision plan and the evidence, day by day, that
they are keeping up.

| | France, 2024-25 | source |
|---|---|---|
| students in higher education | about 3.0 million, the first time above 3 million (+1.4 %) | [MESR-SIES, effectifs 2024-2025](https://www.enseignementsup-recherche.gouv.fr/fr/les-effectifs-etudiants-dans-l-enseignement-superieur-en-2024-2025-100596) |
| of whom at universities (strict perimeter) | 1,631,500 | [MESR-SIES note, July 2025](https://www.enseignementsup-recherche.gouv.fr/sites/default/files/2025-07/nf-sies-2025-17-37563.pdf) |
| international students | 443,500 | [Campus France, September 2025](https://www.campusfrance.org/system/files/medias/documents/2025-09/20250905_CP%20Rentree_CampusFrance.pdf) |

The later markets, if France works: Italy, 2,050,112 university students in 2024/25
([ANVUR, via Italpress](https://www.italpress.com/?p=663388)), and the EU's 18.8
million tertiary students in 2022
([Eurostat](https://ec.europa.eu/eurostat/statistics-explained/SEPDF/cache/1152.pdf)).
These are populations, not a market: no one has yet paid for this.

**Two people to design for** (hypotheses, to be replaced by pilot interviews):

- *Léa, L2 at a Paris university.* Twenty hours of lectures a week, a part-time job
  on two evenings, partiels in January. She revises in the last ten days because the
  semester never shows her how far behind she is. She will not type her timetable
  into an app; she will paste a link.
- *Marco, M1 international student at PSL.* Courses in English and French, project
  deadlines in Moodle, a timetable in ADE. Keen to work steadily, short of a
  structure that holds when the week changes. Reads French slowly.

**Not for, now:** secondary-school pupils (no university timetable, younger users),
working professionals (Motion and Reclaim serve them), CPGE students (their weekly
oral tests are a different rhythm; worth a look after the pilot).

## 2. Competitors

Grouped by the job each does for a student. Prices are what the cited source showed;
they change, and store prices differ by country.

| | what it does | price | what keeps people | what students hold against it |
|---|---|---|---|---|
| **Motion** | auto-schedules tasks into free time | from $19 a month; $29 to $49 in other sources ([Saner](https://www.saner.ai/blogs/motion-reviews), [Morgen](https://morgen.so/blog-posts/motion-pricing)) | the schedule itself | built for work; a weak phone app, billing and support complaints ([Kimola, App Store US](https://kimola.com/reports/unlock-task-management-success-with-our-in-depth-analysis-app-store-us-151020)) |
| **Reclaim** | the same, around Google Calendar | free tier, student discount ([pricing](https://reclaim.ai/pricing)) | (not researched here) | knows nothing of a semester |
| **My Study Life** | classes, homework and exams, typed in | free with in-app purchases ([App Store](https://apple.co/35AgQz2)) | the timetable you typed | crashes and data loss after updates, silent notifications, no time of day on a due date, no LMS link ([Coursesync](https://www.coursesync.biz/post/mystudylife-reviews-is-it-good-free-and-worth-using-in-2026), [justuseapp](https://justuseapp.com/en/app/910639339/my-study-life-school-planner/reviews), [Coursicle](https://www.coursicle.com/blog/is-my-study-life-shutting-down/)) |
| **Structured, Tiimo** | visual day planners | Structured free or about $10 a year; Tiimo about €36 a year or €9 a month in Germany ([toolradar](https://toolradar.com/tools/structured), [appgefahren](https://www.appgefahren.de/app-store-awards-tiimo-ist-iphone-app-des-jahres-391462.html)) | a visual timeline of the day; Tiimo was named iPhone App of the Year | timer bugs, pushy rating prompts (Tiimo, per a competitor's page: [habi](https://habi.app/insights/tiimo-alternatives/)) |
| **Vaia (StudySmarter)** | flashcards, notes, AI study material | €4.99 or €9.99 a month, €34.99 to €89.99 a year on the German store ([App Store DE](https://apps.apple.com/de/app/studysmarter-die-lernapp/id1439949520)) | content made by students; claims 40 million learners | the subscription price ([App Store US](https://apps.apple.com/us/app/studysmarter-study-helper/id1439949520)) |
| **Quizlet** | flashcards and practice | Plus about $7.99 a month or $35.99 a year ([nibble](https://nibble-app.com/blog/quizlet-cost)) | (not researched) | (not researched) |
| **Partielo, Eliott** | French revision sheets and AI tutoring | free with purchases ([App Store FR](https://apps.apple.com/fr/app/partielo-fiche-de-r%C3%A9vision/id1639502571)) | material for the partiels | (not researched) |
| **Anki** | spaced-repetition flashcards | free (desktop, Android) | the algorithm; FSRS is opt-in since 23.10 ([Wikipedia](https://en.wikipedia.org/wiki/Anki_(software))) | (not researched; it plans cards, not time) |
| **Forest** | a focus timer that grows a tree; study rooms with friends | one-time purchase plus in-app purchases | the tree; friends' trees die if one leaves the app | features moved behind a paywall, coins, bugs after iOS updates ([Kimola, Google Play](https://kimola.com/reports/exclusive-forest-app-user-feedback-analysis-report-google-play-en-us-151945)) |
| **YPT (Yeolpumta)** | a study stopwatch with groups and rankings | free with ads and purchases ([App Store](https://apps.apple.com/us/app/-/id1441909643)) | live group status and rankings by hours studied; over 5 million installs ([AppBrain](https://www.appbrain.com/dev/Pallo+Inc/)) | rewards very long sessions; a student paper calls the design unhealthy ([Raffles Press](https://rafflespress.com/2025/02/13/yawns-pains-and-tears-on-ypt/)) |
| **Focusmate** | live, silent co-working with a stranger | free tier, paid membership ([about](https://www.focusmate.com/about/)) | a booked session with a person | its productivity figures are its own surveys |
| **ChatGPT study mode, Gemini Guided Learning** | AI tutors that ask instead of answer | free tiers (launched 29 July and 6 August 2025: [AlternativeTo](https://alternativeto.net/news/2025/7/chatgpt-launches-study-mode-to-foster-student-learning-and-critical-thinking), [Google](https://blog.google/outreach-initiatives/education/guided-learning/)) | already open in another tab | (out of scope: this project does not tutor) |

What none of them does, as far as a search can tell (docs/PRODUCT.md, 2026-09-29):
read the university's timetable and plan revision around it, with exams found in it.
That is a search, not a proof.

## 3. What makes people come back: the evidence

**Streaks work, and breaking them hurts.** Duolingo is the reference: excerpts of its
Q4 2024 shareholder letter say more than 10 million users keep streaks of a year or
longer, and a third of daily users have a "Friend Streak"
([SEC filing](https://www.sec.gov/Archives/edgar/data/1562088/000156208825000039/q4fy24duolingo12-31x24shar.htm),
seen through search excerpts, not opened). In seven experiments, an intact streak shown
in a log raised later engagement relative to a broken one; the harm was larger when
people blamed themselves for the break, and smaller when they could repair it
([Silverman and Barasch, *Journal of Consumer Research* 2023](https://udspace.udel.edu/items/42ce576b-8e1f-429a-8541-e29e48dbcfbb)).
One author's advice: do not tell people their streak broke, offer a fresh goal
([CU Boulder](https://www.colorado.edu/business/news/2023/04/20/research-streaks-marketing-tech-barasch)).

**A habit is built by repetition, and one missed day does not undo it.** In Lally et
al.'s study of 96 people repeating a daily behaviour, the time to reach automaticity
ranged from 18 to 254 days (a median of 66 among those the model fitted), and missing
one opportunity did not materially affect the process
([UCL](https://www.ucl.ac.uk/news/2009/aug/how-long-does-it-take-form-habit);
*European Journal of Social Psychology* 40, 2010).

**Deciding when and where works.** Across 94 tests, "if-then" plans of when and where
to act raised goal attainment by d = 0.65
([Gollwitzer and Sheeran 2006](https://kops.uni-konstanz.de/entities/publication/2e749bfb-8533-437c-8203-7e788c910c5f)).
A calendar event that says what to do at 14:00 is such a plan; this project already
makes one per session.

**Gamification helps sometimes.** A review of 24 empirical studies found mostly
positive but context-dependent effects, with novelty effects as a recurring caveat
([Hamari, Koivisto and Sarsa 2014](https://research.aalto.fi/fi/publications/does-gamification-work-a-literature-review-of-empirical-studies-o/)).
The authors' later review of 819 studies leans positive with "remarkable" amounts
of mixed results
([Koivisto and Hamari 2019](https://www.utupub.fi/items/42fd7508-27b2-4ead-aba2-b94e3a73eb04/full)).
Points and badges are not a strategy.

**Streaks can turn into anxiety.** Interviews and Reddit studies of Duolingo users
describe a shift from learning to protecting the streak
([Universidade Nova thesis](https://run.unl.pt/handle/10362/171644),
[Utrecht thesis](https://studenttheses.uu.nl/handle/20.500.12932/48993)). A review of
Duolingo's interface found manipulative patterns, such as excessive notifications,
next to honest ones
([Castro and Valença 2025](https://sol.sbc.org.br/index.php/ihc/article/view/37677)).
These are qualitative studies; no one has measured the harm.

**Other people help, if they are friends.** Research on studying in another person's
presence ("body doubling") is thin, as the ADHD charity CHADD notes
([CHADD](https://chadd.org/adhd-news/adhd-news-adults/could-a-body-double-help-you-increase-your-productivity/)).
A 2025 study of virtual co-presence found that accountability to a friend felt
supportive and to a stranger felt evaluative or competitive
([arXiv 2509.12153](https://arxiv.org/pdf/2509.12153)). Rankings by hours studied,
as YPT shows, reward the wrong thing for a planner whose point is a sustainable week.

**Students use AI already, and fear two things.** In a 2025 survey of 1,041 UK
undergraduates, 92 % used AI in some form and 88 % for assessed work; what put them
off was being accused of cheating (53 %) and false results (51 %)
([HEPI 2025](https://www.hepi.ac.uk/reports/student-generative-ai-survey-2025/)). That is
a UK survey; no French equivalent was found.

## 4. Review mining

**Method.** `benchmarks/reviews.py` reads the most recent App Store reviews of seven
competitors in France and the US, codes each into ten themes with English and French
keywords (price, bugs, sync and calendar, setup effort, reminders, motivation and
streaks, friends, AI, ads, design), and prints counts, mean ratings and the share of
each theme among one- and two-star reviews. It stores no reviewer names.

**Status: not run yet.** The sandbox this was written in cannot reach Apple's servers.
The counts belong here once the owner runs `python benchmarks/reviews.py` on his
machine; until then this section has themes and no numbers, per CLAUDE.md
invariant 4.

**Themes from review excerpts found by search (qualitative, not counted):**

1. **Typing the semester in is the tax.** Student planners make you enter classes and
   due dates by hand; their reviews ask for LMS links and times of day on deadlines
   (My Study Life, above). *This project's answer exists: a link, not typing.*
2. **Trust breaks on data loss and sync.** Crashes and lost data after updates are the
   angriest reviews (My Study Life, Forest). *A planner that loses a plan is worse
   than none: keep the store's guarantees (version compare-and-swap, backups).*
3. **Paywalls on what used to be free make enemies.** Forest's reviewers resent
   features moved behind a subscription. *Decide the free core once, before
   launch, and never shrink it.*
4. **Gamification that costs you something annoys.** Forest's coins and bushes. *No
   currency, no store.*
5. **Competition drives some and burns out others.** YPT's rankings keep people
   studying and push some to unhealthy hours. *Compare with your own plan, share
   with friends you chose, rank nobody.*
6. **Pushy prompts and billing tricks are remembered.** Motion's trial-to-paid
   complaints, Tiimo's rating prompts. *One honest price page; no rating prompts.*

## 5. Positioning

> For students at French universities who juggle lectures, deadlines and partiels,
> this is the study planner that reads your university timetable, plans your
> revision around it, and shows you every day that you are keeping up. Unlike Motion,
> it knows your semester; unlike flashcard and AI-tutor apps, it decides *when* you
> study, not what is on the card.

What it should be known for, in order: **set up in two minutes from a link; never
miss a deadline; see that you are on track; study with a friend, not against a
leaderboard.** What it should not claim: better grades (unmeasured), AI tutoring
(free elsewhere), or that the research planner is why it works (AUDIT.md item 36).

**Price.** A free pilot; then a free core (plan, calendar feed, progress, streaks,
study buddies) and a paid tier for what costs money to run or is premium by nature:
AI features beyond a monthly allowance, direct Google Calendar sync, multi-semester
history. Reference points from section 2: about €3 to €10 a month or €10 to €90 a
year. The pilot's willingness-to-pay question sets the number.

## 6. What this analysis does not know

- No student was interviewed. The personas are hypotheses.
- Review themes come from search-engine excerpts of review aggregators; the counts
  come when the script runs.
- Several sources are secondary (a newsletter summarising a filing, a blog
  summarising a paper); they are marked as such above.
- No French data on students' use of AI or of study apps was found.
- Competitors' retention figures are not public, except Duolingo's.
