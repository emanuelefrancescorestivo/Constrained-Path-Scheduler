# Decisions

Architecture and product decisions, newest last, each with what was chosen, what
was rejected and why. D1 to D7 (the hosted app's stack, storage, hosting, no
passwords, personal data, the name) are in `docs/ROADMAP.md`. A decision is
changed by a new entry that says which one it replaces, not by editing an old one.

The owner's answers of 2026-10-08, which D8 to D15 build on: **France first, in
French and English; a free pilot, then a free core with a paid tier; private,
opt-in study buddies; AI features on a small monthly budget paid with the owner's
API key.** `docs/MARKET.md` is the analysis behind the questions, `docs/STRATEGY.md`
the product they lead to, `docs/DESIGN.md` the screens.

## D8. Progress is derived from the plan, never counted separately

**Chosen.** Streaks, the week's ring, the calendar of days studied and the
milestones are computed when a page is drawn, from three things the subscription
already holds: the plan's past sessions (`PlanReport.history`, kept for the whole
semester), what the student reported (`Subscription.outcomes`, by session id), and
the plan's settings (start, time zone). `service.progress_view` is a pure function
of the subscription and `now`.

**Rejected.** A stored streak counter, incremented on each report. It drifts: a
report withdrawn, a session moved, a timetable change that renames a session, and
the counter disagrees with the history it claims to summarise. Recomputing is cheap
(a semester is a few hundred sessions) and always agrees with what the student
sees in the calendar.

**Consequence.** A report on a past session, made late, changes the past days it
belongs to. That is intended: it is how a forgotten tap is repaired (D9).

## D9. A streak counts days studied, forgives, and never scolds

**Chosen.** A day is *studied* when at least one session on it was reported done
or hard. A day with nothing planned (a day off, a weekend the plan left free) is
*rest*: it neither extends nor breaks a streak. A day with sessions planned and
none reported is *missed*; the first missed day of each week (Monday to Sunday) is
*forgiven* automatically. Today is never missed before it is over. The streak is the
number of studied days since the last unforgiven miss. The app shows the current
streak, the best one, and what the next session adds ("one session today makes it
5"); it never announces a lost streak.

**Why.** Missing a single day did not materially affect habit formation in Lally et
al. (2010); a broken streak lowers later engagement, more so when people blame
themselves and less when it can be repaired (Silverman and Barasch 2023). Rest days
are part of a good plan, so a planner that breaks streaks on them would punish
following it. Reporting late repairs a day honestly: the student did study.

**Rejected.** *Sessions not reported count as done* (the planner's rule, so a plan
is not derailed by a forgotten tap) is not used for the streak: a streak that grows
with no action measures nothing. *Streak freezes bought with points*: a currency is
a game around the product, and reviewers of Forest show what a currency costs in
goodwill (docs/MARKET.md §4). *Hours as the measure*: rewards long sessions, which
is YPT's failure; the ring fills at the plan's amount and no further.

## D10. Two languages from one catalogue, without a new dependency

**Chosen.** `cps.i18n`: the English sentence is the key; `cps/locales/fr.py` maps
it to French; a missing entry falls back to English. The language of a request is
held in a context variable that the web app sets per request, from the plan's
setting, else a `lang` query parameter (the language links on pages without a
plan), else the browser's `Accept-Language`. No cookie: the footer's promise of
none stays true. Service code calls `_("...")` where it writes a sentence a
student reads, so its functions keep their signatures and stay deterministic in
tests (English unless a test asks). Dates are written by `i18n.format_date` with
the language's own day and month names, never the operating system's locale
(Windows and Linux disagree). A test checks that every `_()` literal in the code
has a French entry. French uses *tu*, as student apps in France do; changing to
*vous* is a catalogue edit. JavaScript strings live in `static/i18n.js`.

**Rejected.** Babel and gettext `.po` files: a dependency and a compile step for two
languages and a few hundred strings. A language parameter threaded through every
service function: hundreds of signature changes for no gain in safety.

**Consequence.** Sentences built in service code before this change are translated
as they are touched; the ones still in English are listed in AUDIT.md item 40.

## D11. Study buddies: invited, few, and shown only what they need

**Chosen.** A student may connect with up to five buddies. Joining takes an invite
code that the inviter shares however they like (a link `/join/<code>` or six
letters read aloud); codes last seven days and serve several people, so one message
to a group works. What a buddy sees, and nothing more: the name the student chose
for buddies, their streak, this week's sessions done out of planned, whether they
studied today, and a "cheer" (once a day per buddy, shown on the receiver's Today
page). Never courses, times, tasks, exams or the calendar. Either side can leave at
any time; deleting a plan, by request or by expiry, deletes its connections and
cheers. Tokens never appear in a buddy's pages: connections have their own ids.

Stored in two new tables of the same SQLite file: `links` (pairs, with the date)
and `invites` (code, inviter, expiry); cheers are rows of `cheers`. `store` stays
ignorant of plans: these are relations between tokens.

**Why.** Accountability to a friend feels supportive and to a stranger evaluative
(docs/MARKET.md §3); no public profile means nothing to moderate and no one to
compare against. The six-letter code is how a student without a link finds a
friend's plan without either of them revealing their secret link.

**Rejected.** Leaderboards by hours (rewards overwork), public profiles and follower
counts (moderation, comparison), sharing courses or timetables (personal data a
friend does not need), a login to find friends (no passwords, D5).

**Consequence.** Joining takes a few taps more than it could: the join page shows
the code and says where to enter it in one's own plan (Progress, then Study
buddies); someone without a plan makes one first and the code comes with them.
Remembering the visitor's plan on the device (a cookie or browser storage) would
save those taps; it was rejected to keep the app free of stored identifiers.

## D12. AI through one provider interface, opt-in, with a hard monthly cap

**Chosen.** `cps.ai` has two features: reading a task typed in words ("stats report
for Friday, about 6 hours") into a name, a due date, hours and a course; and writing
the weekly review's paragraph from its numbers. Each has a rule-based version that
always works (patterns in English and French; a template paragraph) and a model
version used when four things hold: the server has an API key, the student turned
AI on in Settings, the month's spending is under the cap, and the student is under
twenty calls a day. Anything else, including any error, falls back to the rules
without the student noticing more than a plainer result.

The model version calls Anthropic's API through the official SDK (`anthropic`, in
an optional `ai` extra; the core does not import it). The model is a setting,
`CPS_AI_MODEL`, defaulting to Claude Opus 5.5 (`claude-opus-5-5`, $4 and $20 per
million input and output tokens, Anthropic's price list as cached on 2026-10-06);
Claude Haiku 5.5 (`claude-haiku-5-5`, $0.10 and $0.50) is the cheaper choice the
owner can make. Answers are constrained by a JSON schema (structured outputs), at
low effort. Every call's
tokens and cost are written to an `ai_usage` table; the cap, `CPS_AI_MONTHLY_CAP_USD`
(default 10), is checked against the month's total plus the call's worst case
before each call. The API bills in dollars, so the cap is in dollars.

What is sent: the typed sentence, today's date and weekday, the time zone and the
student's course names; for the review, its numbers and the course and exam names.
Never the token, the timetable, buddies or anything else in the plan.

**Why.** Students already use free AI tutors (ChatGPT's study mode, Gemini's Guided
Learning; docs/MARKET.md §2), so AI here does only what those cannot: put work into
this student's calendar and comment on this student's week. Opt-in, because what a
student types is personal data sent to a processor in the United States; the
privacy page names it.

**Rejected.** AI tutoring or quiz generation (free elsewhere, costly here); AI on by
default (consent first); a provider-neutral HTTP shim (the official SDK handles
retries, timeouts and errors; another provider can implement the same small
interface later); students' own keys (almost none have one). Also rejected, while
building it: Anthropic's server-side refusal fallback, which this decision first
named. The rules are already the fallback here, and a fallback model is billed at its
own rates, which the ledger would have to know to keep the cap honest.

**Built (step E5).** `cps.ai`: `read_task` (the rules: dates, weekdays, "in 3 days",
"12/10" day first unless the first number cannot be a month, times, hours or minutes
of work, the course sharing a word's first four letters; English and French);
`AnthropicProvider` (the SDK, a 20-second timeout, one retry, effort low, a JSON
schema); `ask` (the ledger, `ai_usage`: the cap checked against the month's spending
plus the call's worst case, counting every byte of the prompt as a token and every
output token as used; twenty calls per student per rolling day); what is accepted
back (a due date from today to a year ahead, hours from 0 to 200, a course from the
student's list, a paragraph of 20 to 700 characters), else the rules. A review's
paragraph is kept with the plan, keyed by week, language and the numbers it was
written from. In the new-task sheet, a one-line field and "Fill in"; without scripts,
a page with the form filled in. Settings: on or off, with what is sent. Deleting a
plan keeps its calls' costs in the ledger (the month's cap) and removes the plan's
token from them. Measured: nothing yet; no key has been used in this repository, and
the model's answers have not been tried against real sentences (the tests use a fake
provider).

## D13. The free core is decided now and never shrinks

**Chosen.** Free, always: the plan from a timetable link, the calendar feed, tasks
and deadlines, reports, progress, streaks, the weekly review (rule-written) and
study buddies. Candidates for the paid tier, after the pilot shows people would
pay: AI beyond a monthly allowance, writing directly into Google Calendar (W10),
more than one semester kept. Nothing is gated during the pilot, and no payment code
is written before W12 (docs/ROADMAP.md).

**Why.** Reviewers punish paywalls placed on what used to be free (Forest; docs/
MARKET.md §4). Progress and streaks are the habit; gating them would gate the
reason to return.

## D14. The calendar is the reminder; no push notifications yet

**Chosen.** A session's calendar event, which the student's own calendar app
announces, is the daily trigger, and it already says what to do and links to a
one-tap report. The app adds no notifications of its own in this stage.

**Rejected for now.** Web push (a service worker, keys, a consent prompt, and iOS
support only for installed web apps) and e-mail (an address to collect and a
sender to operate). Revisit after the pilot, with a limit of one a day.

## D15. Engagement is measured from the event log, on the server, without trackers

**Chosen.** The event log (`store.events`) already records setup, reports,
settings and task changes by kind; this stage adds one `visit` event per student per
day a plan's page is opened, which is what "active" means. `service.engagement` counts, for the pilot: students active in
the last 7 and 30 days, the share of planned sessions confirmed, streak lengths,
buddies connected, AI calls and their cost. `cps metrics` prints it. No analytics
script runs in a student's browser (D6).

**Built (step E6).** Buddies became the network (D16), so the network's counts
replace them: profiles, accepted follows, sessions shared and kept private,
explanations, kudos, comments, open reports, timed and checked focus sessions. Also
the north star (plans with a session confirmed or logged in 7 days), return on the
7th and 30th day after setup, and the guardrail (plans whose last 7 days held more
study than their own weekly limit). Return is measured from a `start` event, so
plans made before the event log existed are not counted in it. With a pilot of 10
to 20 students these are counts to read and ask about, not rates to test.

## The study network (2026-10-08, later the same day)

The owner widened the social side after D11: "it should work like a social
network, as on Strava": a student times a session, the device stays on it, and
afterwards publishes it (a photo of notes or exercises, the subject, perceived effort
and progress), others comment; an "explain it simply" section; a personal diary that
keeps momentum; international, across programmes and universities. His answers to
the four questions this raised: **web focus mode now, native blocking later; each post
chooses who sees it, followers by default; a public handle, university and programme,
signing in stays the secret link; report, hide and block, with the owner reviewing.**
D16 to D20 follow; D16 replaces D11.

## D16. A study network, Strava's shape, without a leaderboard (replaces D11)

**Chosen.** A profile is a handle (unique, 3 to 20 letters, digits, `_` or `.`), a
university and a programme, typed by the student, and a declaration of being 15 or
older (the age of consent to data processing in France). Following is asymmetric and
asked for: a follow request is accepted or not by the person followed, so that
"followers" means people one accepted. A post is a focus session (D17) or an
explanation (D19), and says who may see it: only me, followers, or everyone; followers
by default. Two feeds: Following (chronological, people one follows and oneself) and
Explore (everyone-posts, filterable by university, programme and course,
chronological). Kudos, one per person per post, and comments. No ranking of people
by hours or by anything else, and no counts of followers shown on profiles: the
network shows work, not popularity.

**Why.** Strava's loop is record, publish, receive kudos and comments; a diary of
one's own activities keeps the history visible. The rules of docs/STRATEGY.md still
hold: nothing rewards hours beyond a plan, nothing is bought, no public table of
people.

**Rejected.** Public by default (photos of notes in public from the first day;
moderation first, D20). An e-mail or university-e-mail sign-in (an e-mail service to
operate; the owner chose the handle and the secret link, with the weakness that
anyone can claim any university, stated on the profile page). Follower counts.

**Built (step E4b).** Sharing needs a handle: without a profile nobody could follow
the author or tell who wrote a post, so a post with no choice made stays "only me"
and a choice to share is refused with the way to fix it. The Following feed leaves
out one's own "only me" posts (they are in the diary). Explore shows only authors
with a profile. "Leave the network" deletes the profile, follows both ways, kudos
and comments given, and what others left on one's posts, and turns one's posts
"only me": the diary survives, the network forgets. On a phone the tab bar keeps
five tabs (Today, Calendar, Focus, Community, Progress); Tasks moves off it, and
stays on Today, in the wide top bar and behind the + button. Rejected: a sixth tab
(iOS stops at five), and Community inside Progress (the feed is the daily reason to
open the network; one tap, not two).

**Stored.** In the same SQLite file, tables of their own: `profiles`, `follows`,
`posts`, `kudos`, `comments`, `reports`, `blocks`. Post and comment ids are random,
not sequential. Deleting a plan, by request or by expiry, deletes its profile, posts,
comments, kudos, follows, reports it made and photos.

## D17. Focus sessions in the browser: a timer that tells the truth

**Chosen.** "Start" opens a full-screen timer for a course (or for a session of the
plan). The page keeps the screen awake where the browser allows (Wake Lock) and
records every time it is left (the page hidden), and for how long; the session's
focused time is the time on the page. Finishing opens the log: what was done, perceived
effort from 1 to 10, progress from 1 to 5, a note, photos, who sees it. A session
started from the plan reports that session done. A session can also be logged
without the timer, and says "not timed".

**Why not blocking.** A web page cannot block other apps on a phone or a laptop. Real
blocking needs native apps (iOS's Screen Time API, which Apple must grant; Android's
special permissions) and is a later step the owner chose to defer. What the web can do
honestly is make leaving visible: an interrupted session says so on its post, as
Forest's tree dies when the app is left.

**Consequence for D9.** A day with a logged focus session is a studied day, whether
or not the plan had something that day.

## D18. Photos: made small and stripped in the browser, checked again on the server

**Chosen.** Up to four photos per post. The page shrinks each photo in the browser to
at most 1600 pixels and re-encodes it as JPEG, which drops its metadata (a phone's
photo can carry its GPS position). The server accepts JPEG or PNG only, at most 3 MB
each, strips JPEG `APPn` and PNG ancillary metadata chunks itself (so a photo sent
without the script is cleaned too), and stores it next to the database under a random
name. A photo is served only to someone who may see its post, with the same security
headers as the pages. No new dependency: no image library is needed to remove
metadata segments.

**Known gap.** The daily backup copies the database, not the photos (docs/DEPLOY.md
says so). A photo is not re-encoded on the server, so a crafted file is served as it
came, under `Content-Type: image/jpeg` and `nosniff`.

## D19. "Explain it simply"

**Chosen.** A second kind of post: a concept, the course it comes from, and an
explanation written for someone who studies something else (at most 1,200
characters), with an optional photo. Readers answer "I got it" or ask a question in
the comments. Explore can show explanations alone. Shared with everyone by default,
since an explanation is written for strangers; a session's default stays followers.

**Why.** Explaining to a non-specialist is the Feynman technique; the research on
learning by teaching is in docs/MARKET.md §3 once checked. It also gives the network a
reason to read posts from other programmes.

## D20. Moderation for the pilot: report, hide, block, the owner reviews

**Chosen.** Every post and comment has "Report" (a reason from a short list). Content
reported by three different people is hidden at once, pending review. Anyone can block
someone: neither then sees the other's posts or comments, and a follow between them
ends. The owner reviews reports at `/admin/<CPS_ADMIN_TOKEN>`: keep (and clear the
reports) or remove. Community guidelines at `/guidelines`: your own work only, no
exam papers you were asked not to share, no other people's faces or names, be kind.

**Built (step E4c).** Reasons: spam or advertising, insulting or harassing, not
their own work, exam papers or answers, someone's private information, something
else. One report per person per item; the third different person hides it, from
everyone but its author, who sees "Hidden while it is reviewed". Reporting needs a
handle (so that three reports are three people with a profile, not three throwaway
plans; a plan costs nothing, so this is a speed bump, not a guarantee). The review
page lists the most reported first, with the text, the photos and the reasons; "Keep
it" shows the item again and closes its reports, "Remove it" deletes it with its
photos. Each decision is written to the event log on the author's plan. The key is
`CPS_ADMIN_TOKEN`, at least 24 characters, compared in constant time; without it
there is no page (404). A block is silent: the blocked person is not told. Rejected:
telling the reported author who reported (retaliation), and a page that lists the
hidden items for everyone (it would advertise them).

**Why.** Under the EU's Digital Services Act a hosting service needs a way to be told
of illegal content and to act on it; this is that, at a pilot's size. Automatic
screening by a model was the alternative; the owner chose to review reports in
person.

## Analytics and appearance (2026-10-08, later the same day)

The owner asked for a light mode, "more analytics: people love feeling in control,
especially of their progress, and love to see trajectory", and a discussion of a
notebook page and of a notes library with rewards. `docs/ANALYTICS.md` is the
analysis behind D21 to D24; the notes library is left to the owner (its §4).

## D21. Appearance: Automatic, Light or Dark, chosen per plan

**Chosen.** Settings has Appearance: Automatic (the device's setting, the default),
Light or Dark. The choice is kept with the plan, like the language (D10), so it
holds on every device the student opens the plan on. The page carries it as
`data-theme` on `<html>`; the stylesheet's dark tokens apply when the device is
dark and the plan does not say Light, or when the plan says Dark. Pages without a
plan (home, privacy) follow the device.

**Rejected.** A switch kept in the browser's storage: it would differ from one device
to the next, and applying it before the first paint needs an inline script, which the
Content-Security-Policy forbids. A cookie: the app has none (D6).

## D22. Trends: the student's own trajectory, never a comparison

**Chosen.** A Trends page, and a trajectory card at the top of Progress. One range
(4 weeks, 12 weeks, the semester) scopes everything on the page. In order: the exam
forecast (D24); hours studied per week (planned sessions done plus focus sessions
logged), the current week marked, with the 4-week average and the weekly limit
drawn on the same axis; the share of planned sessions kept per week; study load
(D23); hours per course; the share kept by morning, afternoon and evening; focus
sessions timed and checked. Stat tiles lead with this week against the student's
own 4-week average. Charts are SVG drawn on the server from `service.trends_view`,
coloured by the stylesheet's tokens (so they follow D21), each mark with its value
as a tooltip (`<title>`), each chart with a table of its numbers below it.

**Rejected.** Percentiles or comparisons with other students (D16); one composite
score (it cannot say what to change); minute-level screen-time charts; a charting
library (a script dependency for what static SVG does, and pages must work without
scripts).

## D23. Study load: minutes times perceived effort

**Chosen.** The load of a logged session is its minutes times its perceived effort
(1 to 10), the session-RPE method coaches use [ref:foster2001]; a week's load is the
sum, shown with its 4-week average. Only logged sessions have an effort, so planned
sessions reported done count in hours, not in load, and the page says so. It is
labelled as a description of how much and how hard the student worked, not of what
they learned: the method is validated for sport, not for study.

**Rejected.** Points (D13). Guessing an effort for sessions reported without one.

## D24. The exam forecast, and what one session adds

**Chosen.** For each exam still to come, the average predicted recall of its topics
on exam day: **if the plan is followed** (the plan's own `recall_at_exam`), and **if
nothing more were done** (the memory states the reported sessions leave, from
`_replay`, decayed to the exam). Their trajectory is drawn week by week by replaying
the sessions reported up to each week's end. On each upcoming self-test, the topic's
predicted recall on exam day with that session and without it, "if no other review
followed". Every figure is rounded to 5 % and labelled as an estimate of the FSRS
model with population-average weights and a guessed starting state (topics start
"seen once and shaky"), which counts a session reported done as recalled, "hard" as
forgotten, and an unreported one as done. This is the notebook's "revise today, +22 %
boost", computed rather than invented.

**Rejected.** Tips with invented percentages (exercise, diet; `docs/ANALYTICS.md` §3).
A forecast with decimals.

## The recall question and the notes library (2026-10-08, the owner's choice)

After docs/ANALYTICS.md, the owner chose two of its proposals: "build the notes
library and the recall question" (D25, D26).

## D25. A self-test asks how much was recalled

**Chosen.** A self-test's report is no longer Done or Hard but the answer to "How
much could you recall, without your notes?": Nothing, Some, Most or All (and
Skipped). The answers are FSRS's four grades (again, hard, good, easy) and are kept
on the reported session (`SessionView.grade`), so the memory replay, the plan's
next review of that topic, the exam forecast and each session's gain use what the
student recalled. Recalling nothing brings the topic back sooner than recalling
all of it. Any answer counts as done for the streak (the self-test happened). The
Trends page shows recall week by week, from nothing (0) to all (100), as the
measured part of the forecast. Tasks and exam practice keep Done, Skipped, Hard;
"done" and "hard" stay valid on a self-test, for links in calendar events made
before.

**Why.** Perceived progress is a feeling, and learners judge what they know
generously while the material is in front of them [ref:koriat2005]; a self-test is
the measure that holds up [ref:adesope2017]. Until now every self-test reported
done counted as recalled, so the forecast assumed success. Four answers in one tap
cost no more than the old two and say much more.

**Rejected.** A score typed as a number or a percentage (slower, and false
precision for "about half"); a second question after Done (an extra tap most would
skip, leaving the forecast assumed); asking after tasks and practice (they are not
retrieval).

## D26. The notes library: own notes, "helpful" marks, the month's top per course

**Chosen.** Option B of docs/ANALYTICS.md §4. Notes are a third kind of post: a
course, a title, a line on what they cover, and the pages as photos (up to eight;
a session or an explanation keeps four), with the student's word, a required box,
that they are their own notes in their own words. They are shared with everyone by
default, since the library is for others, and private without a handle (D16). The
library (Community, Notes) finds them by course, university and programme, the most
helpful this month first, or the newest. Others mark notes "Helpful" (the kudos
table, one per person, dated); an author cannot mark their own. Each calendar month
the three notes of each course (its name, ignoring case and spaces) with the most
helpful marks that month, from at least two people, carry "No. 1 this month" to
"No. 3", among notes shared with everyone and not hidden by reports. A profile says
"Shared 3 sets of notes · marked helpful 12 times". The guidelines gain a section
on notes, and, when the owner has set a contact address, a line telling a rights
holder to write to it; the report reason "Not their own work" already exists, and a
reported note leaves the top while it is hidden.

**Why.** It is what the owner asked for, rewards as recognition: a note recognised
for helping others is feedback, which does not lower interest the way an expected
tangible reward does [ref:deci1999]. Ranking notes and not people keeps D16's
rejection of leaderboards, and a monthly top per course gives a new student a
chance every month. Two people at least, so a note is not "top" because one friend
tapped it. Photos only, because the photo pipeline already strips metadata (D18); a
PDF would need its own checks.

**Rejected.** Money or points that buy things (D13; option D); a free month of the
paid tier for top notes (option C), until a paid tier exists and the pilot says
notes are worth it; ranking authors by marks (a leaderboard of people); counting
marks of all time for the top (old notes would hold it forever); PDFs (a parser and
its risks for one format).

**Open, and the owner's.** The copyright position of students' own notes in France
was read in guides, not in the law; a lawyer should confirm it before the library is
opened to the public (docs/ANALYTICS.md §4). The takedown route is an e-mail to the
owner, who removes a post through the review page; there is no form for rights
holders.

## The notebook's remaining proposals (2026-10-09)

The owner said "continue" after D26. The roadmap's remaining items were the
owner's own steps (hosting, accounts, payments) and three proposals from the
owner's notebook (docs/ANALYTICS.md §3), built here in order of cost: "in simple
words" in the session log (D27), a study group with a shared weekly goal (D28), and
flashcards (D29).

## D27. "In simple words" inside the session log

**Chosen.** The session log gets an optional field, "In simple words": what was
learned, in two sentences a student of another subject would follow (400
characters, line breaks kept). A session post shows it under its numbers, marked
"In simple words", in the diary and the feeds. "Explain it simply" (D19) stays the
post for one idea explained at length, which readers mark "I got it".

**Why.** The owner's notebook puts it inside logging: log, photo, effort and
progress, "explain what you did in simple words". As a second post (D19) it cost a
second step most would skip. Optional, because a forced sentence after every
session would be filled with anything.

**Rejected.** Making it required; scoring the explanation (by readers or by a
model): a score of a sentence would reward style, and the AI is opt-in (D12).

## D28. A study group with a shared weekly goal, nobody ranked

**Chosen.** A study group is up to eight students with handles (members and
invitations together); a student belongs to three at most. Its owner names it and
sets one weekly goal in hours for all of them together (1 to 200). Any member
invites by handle; the invited person accepts or declines, as with a follow (D16),
and nobody is told of a decline. The group's page, for its members only, shows this
week's total against the goal, "N h to go together", the weeks in a row the goal
was met (the week in progress does not break the run), the weeks since the group
began as columns with the goal as a line, and the members, each marked "studied" or
"not yet" this week. Never one member's hours. A member's study counts from the week
they joined, in their own time zone, counted as Trends counts it: planned sessions
reported done or hard, and sessions logged, each once (AUDIT item 47). Progress shows
the student's groups. The owner renames, changes the goal, removes members and
withdraws invitations; when the owner leaves, the longest-standing member owns the
group; the last one out ends it, with its invitations. Leaving the network or
deleting the plan leaves every group. A block between two members takes away
invitations between them; in shared groups each is left out of the other's list
("1 member is not shown") and still counts in the total. `cps metrics` counts
groups of two or more.

**Why.** The owner's notebook asks for leagues; D16 rejected leagues and rankings,
and docs/ANALYTICS.md §3 proposed a cooperative group instead. A shared goal turns
the network's cheering into a reason to study this week, and the run of weeks met is
a streak a group keeps together. The total, not each person's hours, is what the
group needs, and it keeps the group from becoming a ranking.

**Rejected.** Leagues, ranks or a member's hours (comparison, D16); a goal per
member (it becomes eight individual goals and a list of who missed theirs); joining
by a link (a link travels further than a handle typed by a friend); groups for people
without handles (members must know who they study with); counting a member's hours
from before they joined (the group's past weeks would change when someone joins).

**Disclosed.** In a group of two, the total and one's own hours give away the
other's. The page and the privacy notice say what members see.

## D29. Flashcards, scheduled by the planner's FSRS model

**Chosen.** A student writes their own cards, a question and an answer, by course,
one at a time or pasted one per line (a tab, as spreadsheets and Anki's text export
give them, or " | "; up to 200 at once, 2,000 in all). Cards are private. Each card
has its own FSRS-4.5 state from `cps.memory`: the first answer sets it
(`initial_state`), each later one updates it with the days since the last
(`review`), and the card comes back when its predicted recall falls to 90 %
(`interval_for_retention`). The review screen shows the question, "Show the answer"
(a `<details>`, so it works without scripts), and four answers, Again, Hard, Good,
Easy, each saying when the card would come back ("in 4 days"). Space and 1 to 4 work
as keys where a keyboard exists (`static/cards.js`). Due cards come first, the most
overdue first, then new cards, at most 20 shown for the first time a day. "Again"
brings a card back 10 minutes later in the same sitting. Every answer is kept
(`card_reviews`). Today and Focus say how many cards are waiting; `cps metrics`
counts answers.

**Why.** The owner's notebook lists flashcards; FSRS is a flashcard scheduler, and
the project already carries a pinned implementation of it (`tests/test_memory.py`),
so a card is the model's own unit and needs no second model. Retrieval practice is
the measure that holds up (D25, [ref:adesope2017]); cards make it a habit of minutes
rather than a planned session.

**Stated choices, not FSRS.** The 10-minute relearning step and the 20 new cards a
day are conventions of Anki and py-fsrs, not part of FSRS-4.5, which has no
same-day formula: a review minutes later barely moves stability. The 90 % target
is FSRS's usual default. The weights are population defaults, as everywhere in the
project.

**Rejected, for now.** Shared or public decks (the copyright questions of D26, and
moderation); counting card reviews for the streak or as study hours (a few taps
would keep a streak; the streak stays about sessions); feeding cards into the exam
forecast (the forecast models a course's topics, cards model single facts, and
adding both would count the same learning twice); images on cards; importing Anki
decks (`.apkg`).

## D30. Motion: movement that shows what changed, and joy at a milestone

*Asked by the owner on 2026-10-09: "add cool graphic features like animations and
motions and dynamics".*

**Chosen.** Motion with a job, in four kinds, all in plain CSS and one small module
(`web/static/motion.js`), no library:

1. **Arriving.** Moving between pages is a view transition: the top bar and the tab
   bar stay, the content rises in, and the current tab's pill (and the Community
   tabs' pill) slides to its new place. Flashcards slide out to the left and the
   next one in from the right. Browsers without cross-document view transitions
   (Firefox today) simply load the page.
2. **Data drawing itself.** On a page's first paint, rings fill, chart columns grow
   from the baseline one after the other, trend lines draw from the left, meters
   fill, the weeks of the day grid fill in from the oldest, and the numbers marked
   for it count up to the value the server wrote, the first time they come into
   view. Lists of cards rise in, the first few staggered.
   These play once per page: `motion.js` marks the page settled after it arrives.
3. **Changing in place.** When part of a page is replaced (a report on Today, a tick
   on Tasks, a kudos), the swap is one view transition: items that stay slide to
   their new places, the answered one fades, and the week's ring and numbers move
   from their old values instead of jumping. In the calendar, after a report, a
   move or a new task, every session the plan moved slides from its old slot to its
   new one, and new ones grow in: the plan adapting is something you see. (A
   session's id changes with its time, so blocks are paired by title, in order.) A
   kudos, an "I got it" or a helpful mark pops, with a ring bursting from it. Buttons give under a finger; cards lift
   under a mouse. The toast rises and sinks away.
4. **Achieving.** A burst of confetti, once per achievement on the device: a full
   week, a milestone, a group's weekly goal met, the day's flashcards done. Several
   at once make one burst. The focus clock breathes, and a thin ring goes round once
   a minute, in step with the seconds.

**Control.** Nothing moves when the device asks for reduced motion, and a new
setting (Settings, Appearance: Motion, Automatic or Reduced) keeps a plan's pages
still on any device. Every page is complete without the script: the numbers are
written, the rings drawn, the changes made; motion only shows them.

**Why.** Movement that connects a before and an after shows what changed (a
session leaving the list, the ring growing) where a jump makes the eye search, and a
celebration marks the moments the streak and the groups exist for. The owner asked
for it; Strava, which the network follows (D16), celebrates in the same way.

**Rejected.** An animation library (a dependency and a build step for what CSS and
the Web Animations API do); confetti on every report (it would be noise by the
third day; it marks achievements only); animating numbers on every swap from zero
(they move from their old value); sound and vibration (a phone in a lecture);
parallax and motion while scrolling (attention for nothing).

**Disclosed.** The browser remembers which celebrations it has shown, in local
storage, so each appears once; the privacy page says so. The pill's slide and the
page transitions need a browser with cross-document view transitions (Chrome and
Edge, Safari 18.2 and later); the rest works everywhere.


## D31. A public demo: a fresh made-up student for each visitor

*Asked by the owner on 2026-10-09: "make a demo on render like our feedforward so i
can send it to my friends to try it".*

**Chosen.** `cps demo --public`, deployed on Render's free plan from
`deploy/demo/render.yaml`, as FeedForward's demo is: one link to send, nothing to
sign up for, everything made up, wiped at each restart.

1. **A shared world, made at start.** Five classmates at five universities (marco,
   lena, sofia, noah, chloe) with five weeks of sessions, two explanations, four
   sets of notes marked helpful, kudos, comments, and chloe's group.
2. **A student per visitor.** The home page offers "Start the demo"; pressing it
   makes a student of one's own (a made-up handle such as `calm_otter27`) and opens
   their Today: the semester so far reported (a streak, trends, the week's ring), a
   diary, three classmates followed and following back, a follow request, kudos and
   a comment on what they shared, a study group with three teammates, chloe's
   invitation while her group has room, flashcards due. Visitors do not share a
   student, so one friend's taps do not change another's plan; they do share the
   classmates, so a post one makes for everyone, the others can see.
3. **Fast enough for a free instance.** Reporting a semester session by session
   plans it again after every report, dozens of times: seconds of computing, too
   long to wait for after a tap. That is done once, at start, into a model
   (`semester.json` in the store); each visitor gets a copy under a new token, and
   the cheap parts (diary, follows, group, cards) are made for them, in under a
   second on the computer this was written on (the test checks that the model is
   not made again for the next visitor, not a time). The model is made again after
   half a day, so that the sessions left to answer stay the last two.
4. **Today is always in week eight.** The sample timetable is the autumn semester
   2026-27. The demo moves every date in it by whole weeks (`showcase.shifted`:
   weekdays, times, titles and exams unchanged) so that today falls in its eighth
   week, on the real clock: seven weeks of history behind, exams ahead, and a
   visitor who tries their own timetable on the same server sees it planned for
   today. This replaces the first `cps demo`'s fixed clock (19 November 2026 outside
   the semester's weeks), which would have shown friends a date that is not today.
5. **Said everywhere.** The home page's card says the details are made up, that
   the demo starts again when it restarts and that the first visit can take a
   minute; every page's footer says "Demo"; an old link after a restart says what
   happened and offers a new student instead of a bare "not found". Thirty starts
   an hour per address rather than ten, since friends on one university network
   share an address.

**Rejected.** One shared demo student for everyone (each friend would see the
others' taps and a plan that makes no sense); asking visitors for their timetable
first (the point is to look around in a few seconds; the form is still there below);
a paid instance with a disk (nothing in a demo is worth keeping, and the owner asked
for a free demo); a Docker image as FeedForward's (Render's Python runtime installs
the package as the pilot does, one fewer file to keep).

**Disclosed.** The free instance sleeps after about a quarter of an hour without
visitors, and the first visit then waits about a minute while it starts and makes
the world again; links to students made before are gone. The review page's address
is printed in the service's logs. Nothing in the demo is a number in the documents.

## D32. Starting from zero: import a timetable, or draw the week by hand

*Asked by the owner on 2026-10-12, about the public demo: "it should let them start
from zero and input their calendar either with a file or manually with a very
welcoming calendar interface".*

**Chosen.** Two ways in, side by side on the home page, and under "Start the demo"
on the public demo:

1. **Import your timetable.** A file first, in a box to drop it on or tap to choose
   it (the browser's own file field, unseen, covers the box, so dropping needs no
   script); its link below. This is the existing start: the exams in it are found.
2. **No file? Draw your week.** One tap makes a plan with nothing in it and opens
   its calendar alone (no Today panel, no new-task button), with a welcome above it
   and, on the empty week, how to begin: drag where a class is (tap its hour, on a
   phone), name it after its course. A drawn class is a busy time of a new kind,
   Class: weekly by default, in its course's colour, its course offered again when
   the next class is named. The form has a day picker over the seven days from the
   first shown, so a class can be put on any day from a phone's 3-day view or with
   the "Add a class" button. "Next: exams and deadlines" leads to the existing
   settings page, where each course drawn is an exam row with its name filled in:
   its date is all the student types, and a row left without one is a class with no
   exam, left out. Then the plan, the week and the feed, as after an import.

On a phone, drawing comes first: a timetable file is rarely at hand there, and the
demo is shared on phones.

**Why.** The planner needs the busy times and the exams; a timetable file carries
both, but many students have no export at hand (or a university without one), and
friends trying the demo on a phone have none. A week drawn in a familiar calendar is
the same information, given the way people already enter it.

**Rejected.** A separate timetable editor (a second calendar to learn; the plan's
own calendar already draws, drags and resizes busy times); typed rows as the main
way (they stay, for a browser without scripts, now with each row's kind); asking
for the exams before the classes (the exam rows can then be offered by name);
guessing an exam per drawn course (a date nobody gave is not one to plan to).

**Disclosed.** A drawn class is not a lecture: the planner's weekly topics (METHOD.md
§7) come from a timetable file's lectures only, so a drawn course is studied as one
subject until its exam. A plan with no exam and no deadline makes no sessions, and
says so on the next page. Checked in Chromium at 1280 and 390 px, light and dark.

## D33. An app on the home screen: installable, with an icon, and working offline

*Step 2 of the owner's readiness review (2026-10-12): "a real application that any
student could download tomorrow".*

**Chosen.** The site becomes an installable web app, the shortest way to an icon on
a phone with no app store in between:

1. **A manifest per plan** (`/p/<token>/manifest.webmanifest`): installed from a
   plan's page, the app opens that plan; from the home page, the start page. Its
   name is in the plan's language. Icons at 192 and 512 px, a maskable one for
   Android's shapes, an Apple touch icon, an SVG favicon.
2. **The icon** says what the app is about (the owner asked for "how it optimises
   neuroscience of learning"): a brain, and inside it recall over time. Memory
   falls; each review, at the right moment, brings it back; after each it falls
   less and the next can wait longer, which is what the planner's FSRS model does.
   Two other drafts were shown (the curve alone; a filled brain).
3. **A service worker** (`/sw.js`, from the root so it may answer for the whole
   site). A plan's pages and the calendar's data are asked of the server first, and
   the last answer is kept on the device; without a network that copy is shown,
   under a banner saying it is the plan as last opened and that changes wait. A
   page never opened shows an offline page, in the language of the page that
   registered the worker. The app's own files are kept and refreshed in the
   background. Forms are never kept or replayed.
4. **Installing.** Where the browser offers it (Chrome, Edge, Android), a card on
   Today and a button in Settings call its own prompt; on an iPhone or iPad, the
   card says how (Share, Add to Home Screen), since Safari has no prompt. "Not now"
   hides the card on that device. Settings say in words how to do it elsewhere.

**Why.** Students live on their phones; an icon is the difference between an app
and a link they lost. Offline matters on the metro and in lecture halls with bad
reception: the next session and the week are what a student opens the app for.

**Rejected, for now.** App store apps (developer accounts for the owner, Apple's
review, a wrapper to maintain: later, once the web app has users); push
notifications (step 4 of the review: they need the app installed on an iPhone,
which this step makes possible); keeping forms sent offline to send later (a report
sent twice, or late without saying so, is worse than "try again").

**Disclosed.** The pages are sent with `Cache-Control: no-store`; the service
worker keeps its own copy anyway, which is a choice: only a plan's pages, on that
device, the 60 most recent; deleting the plan tells the worker to forget them; the
privacy page and Settings say so. Checked in Chromium, also with the server
stopped mid-session; not on a real iPhone, where Safari's support for installed web
apps differs (the owner's check, step 1 of the review).

## D34. Italian

*Step 2 of the readiness review: the owner's friends, the demo's first users, are
Italian.*

**Chosen.** A third language, complete: every sentence of the pages, the service's
messages, the calendar events and the scripts (962 and 69 entries, the same keys as
French), dates in Italian words ("lunedì 12 ottobre", "lun 12 ott"), decimal
commas, English's plural rule ("1 giorno, 0 giorni"). The informal *tu*, as French.
The language comes from the plan, else the address's `?lang=`, else the browser; a
demo student is planned in the visitor's language. The footer and Settings offer
the three. `tests/test_i18n.py` checks every catalogue the same way, now also that
each translation keeps its markup.

**Disclosed.** Tasks typed in one line are read by rules in English and French
only; in Italian, the AI reads them if it is on, else the form asks for the date.
Moodle deadlines are still read from English event names (as before). The
translation was written by the assistant, not by a native speaker: the owner, who
is one, is asked to read it.

## D35. Accounts that last: passkeys and a recovery code

*Step 3 of the readiness review. The owner approved passkeys for sign-in on
2026-10-12, and `cryptography` with them (pywebpush, approved the same day for step
4, is built on it).*

**The problem.** A plan is its private address, as a calendar's secret link is. A
student who loses it (a new phone, a cleared browser, a closed tab before saving the
link) has lost the plan, the streak and the network with it; nothing could find it
again. A product students keep for years needs a way back.

**Chosen.** Two ways back to the address, and still no name, e-mail or password:

1. **Passkeys** (WebAuthn). In Settings, "Add a passkey": the device makes a key
   pair for this site behind the student's face, fingerprint or screen lock, and the
   site keeps the public half, the kind of device ("iPhone", "Mac", from the
   browser's own words) and when it was added and last used. On the home page,
   "Sign in with a passkey": the device offers the passkeys it has for this site
   (none is named by the server, so nobody learns which exist), signs a fresh
   challenge, and the server opens the plan it belongs to. Passkeys that sync
   (iCloud Keychain, Google Password Manager) open the plan on the student's other
   devices too. Up to ten per plan; each can be removed.
2. **A recovery code**, for when every device is gone: twenty characters from
   Crockford's alphabet (no I, L, O or U; about 100 bits), shown once, kept only as
   its SHA-256. It works once: using it opens the plan and shows a new one at once.
   Making a new one replaces the old. Typed by hand, case, spaces and dashes do not
   matter, and O and I are read as 0 and 1.
3. **A suggestion on Today** ("Keep your plan"), while the plan has neither; "Not
   now" hides it for two weeks.

**How it is checked** (`cps/passkeys.py`, `cps/accounts.py`). The server follows
WebAuthn Level 2: the client data says `webauthn.create` or `webauthn.get`, carries
a challenge the server issued for that purpose (and that plan), not older than five
minutes, used once, and the site's origin; the authenticator data is for this site
(the SHA-256 of its host name) with the person present; the key is ES256, Ed25519
or RS256; the signature covers the authenticator data and the client data's hash; a
signature counter that goes backwards is refused (a copied key), zero is allowed
(synced passkeys keep it there). Attestation is not asked for: the site does not
need to know which device made the key. The few bytes of CBOR involved are read in
the module itself, so the only new dependency is `cryptography`, part of the `web`
extra and imported only when a signature is checked. The user handle a passkey
stores is derived one way from the plan's token, never the token. Challenges are
kept in the database, not in a cookie: the service still sets none.

**Tested** with a simulated device (real keys and CBOR, each field spoilable) for
the three algorithms and every refusal, and against one registration and one
sign-in recorded from Chromium's own virtual authenticator
(`tests/data/chromium-passkey.json`); the whole flow was walked in Chromium at
desktop and phone widths, with a passkey synced to a second device.

**Rejected.** E-mail links (an address to keep and protect, mail to send, a
provider to pay, which the owner declined until launch); passwords (to hash, leak
and reset); "Sign in with Google or Apple" (a third party told of every sign-in, an
account with each); a WebAuthn library (more code and dependencies to trust, and
to ask the owner for, than the few checks above need; attestation, which such
libraries mostly add, is not used here).

**Disclosed.** A plan with neither a passkey nor a code cannot be found again
without its address; the home page says so. The relying party is the host name the
service is reached at (`CPS_BASE_URL`, or Render's), so passkeys made on one address
do not work on another, as for any site; on a computer, use `localhost`, not
`127.0.0.1`, which browsers refuse for passkeys. In the public demo passkeys work,
and are deleted with the demo when it restarts. Not tried on a real iPhone or
Android phone (the owner's check); Chromium's authenticator was virtual.

## D36. Kept while used, and the next semester at the same address

*Step 3 of the readiness review. The owner approved on 2026-10-12: delete an account
12 months after the student's last visit, instead of 30 days after the last exam
(D6).*

**Chosen.**

1. **Retention.** A plan, with everything kept under its address (the profile and
   the network's rows, the diary, the flashcards, the passkeys), is deleted 12
   months after the student last opened it (`Subscription.seen`, counted once a
   day: any of the plan's pages, a passkey or recovery-code sign-in), or after it
   was made if never opened since. A calendar app reading the feed does not count:
   it reads for ever, even when the student has forgotten the plan. Settings say on
   which day the plan will go; the privacy page says the rule. A plan saved under
   the old rule gets the new one at the next sweep, before anything is deleted, and
   a plan with no exam is no longer kept for ever (AUDIT item 52).
2. **The next semester** (Settings, "Next semester"; and on Today, once every exam
   and deadline is behind). The student brings the new timetable (a link, a file or
   a week drawn); its exams are found as at the start. Kept: the address and the
   calendar feed (nothing to subscribe to again), the week's hours and days off,
   the study window, the language, the appearance, AI, the Moodle link, the
   deadlines still ahead, activities drawn other than classes (work, training), and
   everything kept under the address. Put away: the old timetable, exams, plan,
   reports and moved sessions, the classes drawn, finished or past deadlines, past
   one-off activities. What was studied is kept as the sessions progress counts
   (`options["past"]`: start, length, kind, course, report), so the streak, the
   weeks, the trends and a group's goal go on across semesters.

**Rejected.** Keeping plans for ever (a promise the privacy page could not make);
deleting at the end of each semester (the history is what a streak and a trend are
made of); a new address per semester (the feed, the home-screen icon and the
network would have to move).

**Disclosed.** Until the new semester has an exam or a deadline, it has no plan, and
the Progress page waits for it; the past sessions are kept meanwhile. The calendar
feed shows the current semester's sessions only. With no e-mail address, nobody is
warned before a plan is deleted; Settings show the date.

## D37. A silkier interface

*The owner, 2026-10-12: "smooth out the graphic interface, it should be more silky
and Apple-like".*

**Chosen.** A pass over every page at phone and desktop widths, light and dark,
keeping the layout and changing how it feels:

1. **Apple's own patterns where the app had a web or Android one.** On a phone, a
   new task is the "+" in the top bar (the floating round button went, and with it
   AUDIT item 54); the tab bar's five tabs have the same weight (Focus no longer
   sits in a raised blue disc); every on/off choice is a switch instead of a
   checkbox (still a checkbox to the browser and to screen readers; in a
   high-contrast mode the plain checkbox comes back); a session card's colour is a
   capsule inside the card, as Calendar draws an event, instead of a stripe cut by
   the corner.
2. **Shapes.** Rounder corners everywhere, and continuous corners (a squircle,
   which curves into the straight edge instead of meeting it at a circle's tangent)
   on cards, fields, sheets and popovers where the browser can draw them
   (`corner-shape`); pills and circles stay round.
3. **Motion.** A press is quick and the release soft (a short scale-down, a longer
   settle); sheets rise with a long, gentle deceleration and no bounce; popovers and
   pages arrive with less travel; the top bar is see-through over the page while
   nothing is beneath it, and its material and hairline fade in as the page scrolls
   under it (scroll-driven animation; elsewhere it keeps its material); links to a
   section glide there. All of it stops under reduced motion, as before (D30).
4. **Type and spacing.** Headings balance their lines and paragraphs avoid a lone
   last word; a page's large title keeps its distance from the first card; a field
   in focus has a thinner, crisper ring.

**Rejected.** A component or animation library (the app has no build step and no
framework, on purpose); a web font (the system font is San Francisco on Apple
devices already); imitating "Liquid Glass" refraction (costly to draw on phones, and
a page that must stay readable without it); squircles drawn with SVG masks (heavy,
and they break shadows and focus rings).

**Disclosed.** Continuous corners and the scroll-linked top bar depend on recent
browser features; where they are missing, corners are circular and the bar is
always solid. Checked in Chromium only, at 390 and 1280 px wide, light and dark,
with motion on and reduced; not on an iPhone.

## D38. Reminders on the phone

*Step 4 of the readiness review. The owner approved `pywebpush` on 2026-10-12, and
asked that nothing cost money until launch.*

**Chosen.** Web Push: a notification a few minutes before each study session,
saying the course and the time, the kind of session and its length, and the first
sentence of what to do; tapping it opens the plan.

1. **Turning them on** (Settings, Reminders): per device, "Turn on reminders on this
   device" asks the browser's permission and subscribes to its push service; the
   device appears in the list, can be removed, and "Send a test reminder" sends one
   at once. When: 5, 10 (the default), 15, 30 or 60 minutes before, or never. On an
   iPhone or iPad, web push works only in the app installed on the home screen
   (D33), and the page says so there.
2. **Sending.** The web app checks every minute which sessions start within the
   lead time and sends each reminder once (a session is claimed before it is sent,
   and the claims are kept three days), to every device of the plan, in the plan's
   language; it is dropped if not delivered before the session starts. A device its
   push service no longer knows is forgotten. A server that was asleep through a
   session's window does not remind late.
3. **Keys and privacy.** The server signs every push with its own key pair (VAPID),
   made on first use and kept in the store, or given as `CPS_VAPID_PRIVATE_KEY`:
   nothing for the owner to create. Each reminder is encrypted for the device; the
   push service (Google, Apple, Mozilla or Microsoft, whichever the browser uses)
   carries it without being able to read it. The server posts only to those
   services' addresses (AUDIT item 55), never to an address of a client's choosing.
   The privacy page says what is kept: each device's push address and keys, and
   which sessions were reminded, for three days.

**Checked.** Unit and page tests (`tests/test_reminders.py`): which services, the
key, the devices, the timing, once only, the language, the pages; and what leaves
the server: posted through a fake push service, decrypted with the device's own key
as a phone would, and its VAPID signature verified. In Chromium (its full build;
Chrome has no push in an incognito-like profile): turning reminders on registered
with Google's push service, the server kept the device, a test reminder sent through
Google's real push service was accepted, and the service worker showed a pushed
reminder as a notification. The headless browser could not receive from Google
itself, so the last step, a reminder travelling to a phone, is the owner's check.

**Rejected.** E-mail (no address is kept, D35), text messages (a paid gateway), a
native app (an app store account and review: later), a keep-alive job pinging the
free demo so that it never sleeps (its host's terms were not checked, and it would
only hide the limit below).

**Disclosed.** Reminders need the server awake at the time. Render's free plan, which
runs the demo, sleeps when nobody uses it, so there reminders arrive only while
someone keeps it awake; the demo's Settings say so. A deployment with an always-on
instance (DEPLOY.md) has no such gap. Not tried on a real iPhone or Android phone.
The new dependency brings others (`requests`, `aiohttp`, `http-ece`, `py-vapid`).
