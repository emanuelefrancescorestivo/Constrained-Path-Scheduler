# From a degree to what to study today: a plan

*The owner, 2026-10-20: "it would be easier if the user could just login and put that
major and then receive suggestions [of] the curriculum, of course they didn't have to
type in anything, they can just click on them, choose them, select them; and I guess
there should be also pre-made slots for these lectures to occur (in many universities
lessons from 9 to 12 or from 1 to 4); input your university, your major, and the app
retrieves the courses taught in that degree; then the user should be able to upload
the syllabus or the content of the lectures, and the study plan not only tells you
what to study but also on what to focus according to when the deadline is, and also
proposes different study methods" and "connecting the calendar to the ed platform
from a student's uni so when you click on the focus session it tells you what to study
from that course: too complicated?"*

This is a plan, not a decision: nothing below is built yet. The owner's choices are
listed at the end. It follows the project's rule: say what each part costs and what it
cannot do.

## What already exists to build on

| Piece | What it does today |
|---|---|
| The list of universities (D39) | a student picks theirs; classmates are matched by it |
| The timetable (`calendar_io`, ADE, any `.ics`, or drawn by hand, D32) | classes with their courses, every week |
| Lectures become topics (D50) | each week of a course's lectures is a topic the plan reviews |
| The learning platform's calendar (Moodle's export link) | deadlines arrive by themselves, with a guessed length |
| Exam weights and practice (D46, W5) | how much each exam counts, and practice sized to it |
| The forecast (D24) | each exam's predicted recall, from FSRS |
| The tips (D62) | study methods, each with its evidence and its limits |
| AI, opt-in and capped (D12) | one small interface to a model, a monthly cap, rules when it is off |

So the plan already knows *when* to study and *which course*; what it lacks is *which
part of the course* and *how*.

## The parts, from the smallest

### 1. Lecture blocks in one tap (small: days)

Drawing a week by hand (D32) asks for each class to be dragged. Most universities
teach in fixed blocks: two- or three-hour slots that repeat. Offer them as chips
("9–12", "14–17", and the ones seen most), tap a block, tap the days, name the
course. Later, the blocks of the student's own university, learned from the
timetables its students imported (part 2), come first.

### 2. A university and a degree, then the courses to pick (medium: one to two weeks)

**There is no single database of every degree's courses.** The European ECTS
Users' Guide asks institutions to publish a course catalogue online, and the Erasmus
Charter requires it of its signatories ([ECTS Users' Guide 2015][ects]; an example,
[the University of Granada's catalogue][ugrcat]), but each university publishes it
in its own format, often a PDF or its own website. Three sources, in order:

- **The students themselves (recommended).** Each imported timetable already names
  its courses. Course names seen in the timetables of at least *k* students of the
  same university and degree (k = 3 or more) are offered to the next student as chips
  to tap. Opt-in, names only, no one's timetable shown. Grows by itself, works in
  every country, costs nothing. Weak for the very first students of a degree.
- **Typed once, kept for the next ones.** A student who types a course adds it to
  the list for their degree (the same threshold before others see it).
- **Official catalogues, university by university.** Reliable, but a reader to write
  and keep up for each university's website. Worth it only for the universities of
  the pilot.

### 3. A syllabus, read into topics (medium to large: two to three weeks)

The student uploads a course's syllabus (PDF or text) or pastes its list of
contents. It is read into **topics, each with its week, its weight and its kind**
(theory, exercises, a lab, reading). The topics replace "week 5 of Algebra" (D50)
with "week 5: eigenvalues and diagonalisation", so the session card, the calendar and
the focus page say *what* to study.

- **Reading it.** With AI on (D12), the model extracts the topics and the student
  checks the list before it is used; with AI off, the student edits the list by
  hand (one line a topic). A PDF needs a text extractor, a new dependency
  (`pypdf` or similar), which is the owner's to approve.
- **What to focus on.** The plan already weighs a topic by the exam's weight, the
  time left and the predicted recall (D24, D46). A topic's own weight in the
  syllabus (hours, or "important for the exam") joins those.
- **What is kept.** The topics, not the file: the PDF is read and dropped. A syllabus
  may be under the university's copyright, so it never enters the notes library
  (D26) unless the student says they may share it.

### 4. How to study each topic (small to medium: about a week, after part 3)

Each session proposes a method suited to the topic's kind, drawn only from the tips
with checked evidence (D62): retrieval practice and spaced practice for everything
(Dunlosky and colleagues rate both of high utility, [ref:dunlosky2013] in
docs/REFERENCES.md), interleaving across topics when practice comes near an exam
(two checked references in docs/REFERENCES.md, with their limits: a moderate effect
overall, weaker for words). Worked examples before problems, for exercises, has no
checked reference yet: one must be added before the app proposes it. The student can
switch methods, and which ones they keep becomes a measure (see ANALYTICS.md §6).

### 5. The learning platform: what to study, from the course's own page (large)

**Not too complicated for one university that agrees; no single route for every
university.**

- **Moodle** (common in Europe). Moodle has a web-service function that returns a
  course's sections and the activities and files in each, as its own page shows them
  (`core_course_get_contents`; [Moodle tracker MDL-68935][mdl68935]). The app would
  read the sections of each course the student follows ("Week 5: Eigenvalues; slides;
  exercise sheet 4") and put them on the matching sessions. To call it, the app
  needs a token for the student, which Moodle gives to its mobile app through
  `login/token.php` ([MDL-45639][mdl45639]). That works only where the university has
  turned on Moodle's mobile services, and **a token opens the student's whole Moodle
  account**: it must be stored encrypted, used read-only, revocable, and covered in
  the privacy page and a data protection impact assessment. Where the university signs
  in through its own single sign-on, the password route does not work and the
  mobile app's browser sign-in would be needed (to be checked on a real site). The
  better route is the university's Moodle team agreeing to a pilot, with an account
  limited to reading course pages.
- **Canvas.** Each institution must issue the app a developer key (OAuth 2); asking
  students to make their own access token goes against Instructure's API policy, and
  several universities have turned student tokens off (UW-Madison in November 2025,
  Ohio State in May 2026), citing tokens that bypass multi-factor sign-in
  ([Canvas community][canvas], [Ohio State][osu], [University of Missouri][umsystem]).
  So Canvas is one agreement per university.
- **Blackboard, Brightspace, Google Classroom**: also registered per institution;
  not checked here.

What is already there stays the first step: the platform's calendar export brings
the deadlines without any token.

## How it fits together

```
 timetables (import, ADE)      syllabus (PDF, text)       Moodle course pages
 courses typed by students          │                      (with the university)
          │                         ▼                               │
          ▼                  topics: week, weight, kind  ◄───────────┘
 degree → courses (k ≥ 3) ──────────┤
                                    ▼
                    the assistant (D44 to D51): which topic, when, how long
                                    │
                                    ▼
      the session: "Week 5: eigenvalues · practise exercises 3–6 · retrieval first"
```

- **Store.** New tables beside the plan's document: `degree` (university, name),
  `course` (degree, name, semester, seen by how many), `topic` (plan, course, week,
  title, weight, kind, source), `connection` (plan, platform, site, an encrypted
  token, when it was last read). The plan's own document keeps which courses and
  topics are its own.
- **Service.** `courses_for(university, degree)`, `read_syllabus(text)` (AI or rules),
  `set_topics(subscription, course, topics)`, `method_for(topic)`; the assistant takes
  topics instead of weeks. The web pages only call these (CLAUDE.md, invariant 11).
- **Generality.** Each new reader comes with a case for another country in
  `tests/test_generality.py` (invariant 13): a degree's courses, a syllabus format, a
  slot grid.

## Phases, and what each needs from the owner

| Phase | Builds | Size | The owner's decision |
|---|---|---|---|
| 1 | lecture blocks in one tap; courses from classmates' timetables (k ≥ 3, opt-in); topics typed per course, shown on the session | about two weeks | the threshold *k*; the opt-in wording |
| 2 | syllabus upload and reading; topic weights in the plan; a method per session | two to three weeks | a PDF reader dependency; AI on for this, and its monthly cap |
| 3 | one university's Moodle course pages, read with its agreement | depends on the university | which university; who writes to its Moodle team; a data protection review |

## Not promised

- That every degree's courses can be found: catalogues differ, and the students' own
  timetables are the only source that grows everywhere.
- That a syllabus is always read right: the student checks the topics before the plan
  uses them.
- That the platform can be read without the university: on Canvas it cannot, and on
  Moodle a student's token is a key to their whole account.

[ects]: https://education.ec.europa.eu/sites/default/files/document-library-docs/ects-users-guide_en.pdf
[ugrcat]: https://ugrcat.ugr.es/en/about-ugrcat
[mdl68935]: https://tracker-old.moodle.org/browse/MDL-68935
[mdl45639]: https://tracker-old.moodle.org/browse/MDL-45639
[canvas]: https://community.canvaslms.com/t5/Developers-Group/Canvas-API-Access-Question-Higher-Education/td-p/572723
[osu]: https://it.osu.edu/news/2026/08/11/carmencanvas-personal-access-tokens
[umsystem]: https://tdx.umsystem.edu/TDClient/66/MOOnline/KB/ArticleDet?ID=2302
