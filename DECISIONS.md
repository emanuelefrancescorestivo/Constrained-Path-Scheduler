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
   Two other drafts were shown (the curve alone; a filled brain). *Replaced by the
   week's ring, D74.*
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

## D39. Your university, from a list, and the classmates it finds

*Step 5 of the readiness review ("insert your university?", the owner asked; "do 5
first", 2026-10-13).*

**Chosen.** The profile's university is suggested as the student types, from a list
of about ten thousand universities (Hipo's university-domains-list, MIT licence,
trimmed to country, name and web domains in `src/cps/data/`, its notice beside it),
the student's own country first (from the plan's time zone, through `tzdata`'s own
table). A university chosen from the list is kept as the list writes it, with its
country, shown as a flag; one the list lacks can still be typed. The profile also
asks the year (1st to 4th or later, Master's, PhD, other, or not said). People shows
"At <university>": the others there, the same programme and year first. A store
made before gains the two new columns when it is opened.

**Why.** Classmates find each other only if they write their university the same
way; a list makes that likely without asking anyone to prove where they study.

**Rejected.** Checking the university by an e-mail address (none is kept, D35);
Wikidata (names in every language, but a large query to keep current; later, if the
English names prove a problem); a ranking of universities (D41's rule: no rankings).

**Disclosed.** The list names most universities in English ("University of Bologna",
"Polytechnic Institute of Milan"), so its search also matches the web domain
("unibo", "polimi"); a student typing "Politecnico di Milano" and one choosing
"Polytechnic Institute of Milan" do not meet. The list is community-maintained and
may lag behind mergers and renamings.

## D40. Study groups joined by a link

*Step 5 of the readiness review.*

**Chosen.** A group's page offers "Make an invitation link"; any member may make it,
and the owner may replace it (the old one stops working). Whoever opens the link sees
the group's name, its size and its weekly goal (never who is in it) and joins with
their own plan: on a device where a plan was opened before, "Join with my plan" (the
device remembers the address of the plan last opened, in its own storage, never
sent); else by pasting the link on their plan's Groups page; a student without a
handle chooses one first and comes back to the invitation. The link is shared with
WhatsApp's own link, the phone's share sheet, or copied. The group's size (8) and the
limit of 3 groups each still hold, and someone blocked by a member, or who blocked
one, is told the link does not work.

**Why.** Invitations by handle need the handles first; a group of friends starts on
a chat, with a link.

**Rejected.** Links that expire on a timer (the owner replaces a link that went too
far instead); showing members' handles to whoever has the link (they would leak to
anyone it is forwarded to).

## D41. The badge shelf

*Step 5 of the readiness review ("prizes and badges?", the owner asked).*

**Chosen.** Progress shows twelve badges, each with a picture, earned with the day it
was earned, or not yet with how to earn it: the six milestones of D8 (first session,
3, 7, 14 and 30 days in a row, a full week) and six habits that make study stick: ten
self-tests, an exam rehearsal, five sessions with the focus timer, a hundred
flashcard answers, an explanation shared, a new semester. They are derived from what
the student did, every time, as progress is; the day is when the habit was first
reached (the streak's days as D9 judges them). A badge is celebrated once (D30), the
milestones under the keys they had.

**Rejected.** Badges for hours studied, and any ranking or leaderboard (they reward
showing off time, not learning, and shame the rest, D9); prizes with money's worth
(a contest has legal rules to check first: the owner's call, later).

## D42. A few questions about the week: the trip, sport, a job

*The owner, 2026-10-13, after trying the app: "make the very first login more user
friendly by asking questions on when commutes happen, if the user does any sport or
job in what times of the week so that the planner can then block accordingly".*

**Chosen.** Setting up gains a page between the exams and the week ("Your week",
step 3): four questions, each a card, each skippable, the whole page too.

1. **How long is your trip to university?** No trip, 10, 20, 30, 45, 60 or 90
   minutes, one way. The trip is kept free before the day's first class and after
   its last, on each day with classes, from the timetable file or the classes drawn
   by hand (`service.commute_events`). Time between two classes stays free: a
   student who stays on campus can study there.
2. **Do you play a sport or train?** 3. **Do you have a job?** 4. **Anything else
   every week?** Each: a name (an example in grey), the days as seven pills, from and
   to. Each answer becomes weekly busy time, one row per day chosen, of the kind the
   question is about (Training, Work, Other), so it shows on the calendar in that
   kind's way and can be dragged, resized or removed there like any busy time. A
   second answer per question is behind "+ Another one"; more go on the calendar.

The answers plan at once. The trip's length is in Settings ("Trip to university,
one way") afterwards, and Settings links to the questions again to add more.

**Why the trip is a length, not times.** A timetable changes from week to week; a
student asked "when do you commute" would give this week's times, wrong by the
next. The trip's length is stable, and the timetable already says when the student
is on campus. A commute typed as weekly times (the calendar's Commute kind) is still
possible for a student whose trip does not follow their classes.

**Rejected.** A chat that asks one question at a time (the same four answers, slower,
and a form works without scripts); asking for study preferences here too (the study
window, days off and weekly hours are already on the page before, and asking twice
is not friendlier); guessing the trip from the university's address (a home address
is personal data the product does not keep, D6); counting the trip as study time
(flashcards on the bus): a good idea for the planning review, not a guess to make
silently.

**Disclosed.** The trip is put around every timed event of the timetable on a day,
all-day events aside: a personal calendar (a Google Calendar secret address) with
dinners in it gets a trip around dinner too, so its owner answers "No trip" and
draws a commute instead. Online classes are not told apart from classes on campus.
Checked in Chromium at 390 and 1280 px, light and dark, in English and French.

## D43. Before friends try it: feedback, a copy of one's data, terms, an error log

*Step 6 of the readiness review, which the owner asked for on 2026-10-13 ("then move
onto step 6").*

**Chosen.** Four things a pilot needs before people other than the owner use it:

1. **Send feedback.** From the footer of every page, the end of Today's panel and
   Settings: what it is about (something wrong, confusing, an idea, something liked)
   and a few words. Kept with the page it came from, its plan's address cut out, and
   the language; deleted with the plan. The owner reads them, newest first, at
   `/admin/<key>/inbox`, next to the reports to review (D20). No reply is possible:
   the app knows no e-mail address (D35), and the form says so.
2. **Download a copy of my data.** One JSON file from Settings: the plan's document
   (the timetable as read, settings, exams, busy times, every report), its log, and
   the plan's rows of every table (`store._EXPORT`), with its posts' photos in
   base64. Another student appears by their handle only. A test fails when a new table
   is neither in the copy nor left out with a reason. Left out: secrets of the server
   or of a device (a passkey's public key, a push address and its keys, the recovery
   code's digest), which say nothing about the student.
3. **Terms of use, a draft.** `/terms`, in the three languages, in plain words: free;
   the address is the key; a plan is advice, the student stays in charge; what one
   posts stays theirs; 15 or older for the network; the pilot may be slow or down;
   leaving. It says at the top that it is a draft not yet read by a lawyer, and it
   names no law or court: those are the owner's to choose with one.
4. **An error log.** An error the server did not expect is counted, by route
   template (never the address), kind, message and the last lines of code, and the
   student sees a page that says the mistake is the app's. An error in a page's
   script is sent by `errors.js`, loaded first on every page, at most five a page
   and ten a minute per address. One row per kind of error, with how often and when
   it last happened; plans' addresses, tokens and e-mail addresses cut out of every
   text; kept 30 days after the error last happened.

**Rejected.** A hosted error tracker or a feedback widget from a third party (a
script and a company receiving students' pages, against D6); an e-mail address for
feedback (it would have to be published and read, and students would have to give
theirs); the server's log as the error log (gone at every restart on the free host,
and blind to the browser); a PDF for the copy (JSON is what the GDPR's portability
means: readable by a person and by another program).

**Disclosed.** The terms are a draft for the pilot, not legal advice. A browser that
blocks `sendBeacon` and `fetch` to the site sends no errors. The error log keeps an
error's message cut to 300 characters: a message that quotes what a student typed
keeps what fits. Checked in Chromium at 390 and 1280 px.

## D44. Silence is not success: a self-test not reported counts as unknown

*Phase A, item A1 of docs/ENGINE-REVIEW.md (weak spot W4), approved by the owner on
2026-10-13. Replaces, for self-tests, the planner's rule that a session not reported
counts as done as planned, and the line of D24 that the forecast counts "an
unreported one as done".*

**Chosen.** A self-test (a first review or a review) with no report a day after it
ended (`service.UNREPORTED_AFTER`) is replayed as "unknown": it stays on the
calendar and in the feed, but its topic gains no memory from it, so the plan brings
the topic back as if it had not happened. Until a day has passed it still counts as
done as planned, so the plan does not change the minute a session ends. A late
report, of any kind, counts again from then. The exam forecast on Trends says how
many self-tests it did not count, and sends the student to Today, which already asks
about the past week. Deadline work and exam practice not reported still count as
done: no memory hangs on them, and doubting them would plan work twice.

**Why.** Students already judge what they know too kindly: most reread rather than
test themselves and feel they know material they do not [ref:karpicke2009]. A plan that took silence for success would show a student who
stopped reporting as perfectly on track, and its forecast would be least true for
the students who most need it. Progress and the streak already refused it (D8, D9);
the planner now agrees with them.

**Rejected.** Treating silence as skipped (the session would vanish from the
calendar and the feed, as if it had not been planned); asking the moment a session
ends (a push for every session is noise; the reminder comes before, D38); a shorter
grace (students report in the evening for the day).

**Measured.** The benchmarks are unchanged: they replay every session as reported.
Tests pin the rule: overdue self-tests are unknown, tasks and practice are not, a
session within its day still counts, a late report counts again, an unknown session
stays in the feed, and a silent week leaves every doubted topic less stable than the
same week reported.

## D45. When the student studies best, and a pause after training or work

*Phase A, item A2 of docs/ENGINE-REVIEW.md (weak spot W1), approved by the owner on
2026-10-13.*

**Chosen.** The questions about the week (D42) ask "When do you study best?":
morning, afternoon, evening or no preference; Settings can change it. Each day's free
time is then cut into blocks nearest that part of the day (`plan.tile_free_time`
with `around`: 15:00 for the afternoon, as late as the study window allows for the
evening), and, when it is busy, as near to it as the day allows. Morning and no
preference keep the earliest free time, exactly as before (a test compares the new
code with the old on 400 random calendars), so a student who does not answer, and
every benchmark row above the new table, is unchanged. Half an hour after training
or work stays free (`service.PAUSE_AFTER`), to get home and eat; it is free time
kept, not an event on the calendar. Other weekly busy time gets no pause: it can be
anything, a choir or a nap.

**Why.** About 60% of 14,894 students were "jet-lagged" by their class times, and
the mismatch went with lower grades [ref:smarr2018]; that is correlational and from
one university, and the laboratory evidence on the time of day is mixed and small
[ref:bettencourt2024]. So the answer is the student's preference, not a rule about
chronotypes: a session at a time a student keeps is worth more than one at a time
the model prefers. Exercise a few hours after learning did not hurt recall
[ref:vandongen2016], so training after study is no problem, and the pause is about
logistics, not memory.

**Rejected.** Asking for exact hours (a second study window the student would have
to maintain; Settings already has one); a chronotype questionnaire (minutes of
questions for a preference the student can state in one tap); a pause after every
busy time (it would eat the gaps between classes).

**Measured.** `benchmarks/rule_vs_planner.py`, last table, the assistant with
Sundays off and 4.5 hours of practice. Mean predicted recall at the exams is the
same within 0.005 for every preference at 12, 15 and 18 hours a week (0.923, 0.919,
0.918 at 15 hours), and the evening's lowest topic is higher than the morning's
(0.772 against 0.755 at 15 hours). Which topics reach 90% moves, because many sit
just around the line: the afternoon gives 31, 51 and 59 of 69 topics against 30, 51
and 60 for the morning; the evening 32, 45 and 56. The evening at 15 and 18 hours is
the one real cost, four to six topics under the line. It is disclosed, not hidden:
a session kept in the evening is worth more than one skipped in the morning, which
the benchmark cannot see.

## D46. Exam practice sized by the course, taking turns, mixed

*Phase A, item A3 of docs/ENGINE-REVIEW.md (weak spot W6), approved by the owner on
2026-10-13.*

**Chosen.** Exam practice before an exam is sized by the weeks its course was taught
(weeks with a lecture in the timetable before the exam, those before the plan
included): 1.5 hours for every four weeks, at least 3 (`service.practice_hours_for`).
A twelve-week course keeps the 4.5 hours every exam had; a four-week module gets 3; a
sixteen-week course 6. A course with no lectures in the timetable keeps 4.5 hours.
Settings offers "Sized by each course" (new plans) or the same hours for every exam,
and each exam's row can set its own hours. Plans made before D46 keep the 4.5 hours
stored with them. When several exams are in their last two weeks, the one with the
most practice left per day before it gets the block: exams in the same week take
turns, and the nearest wins once it falls behind. Every practice session asks for
the kinds of question mixed as the exam will mix them, not one chapter at a time.

**Why.** Practice tests beat restudying [ref:adesope2017], and mixing problem types
beat blocking them on a delayed test, in a randomised classroom trial
[ref:rohrer2020]. Before D46 the assistant did all of one course's practice before
the next course's, so with six exams in five days the last course's practice came in
one run of days, and every course got the same hours whatever its length.

**Rejected.** 1.5 hours per three weeks, the review's first figure: it gives a
twelve-week course 6 hours, taken from the self-tests of the last fortnight, for an
amount the evidence does not fix either way; four weeks keeps today's hours for a
typical semester and scales from there. Nearest exam first (the blocked order above);
round-robin regardless of dates (it would starve an exam that is close).

**Measured.** The benchmark semester's six courses were each taught 11 weeks (a test
pins it), so sizing gives each 4.5 hours, and `benchmarks/rule_vs_planner.py` is
unchanged: 174 sessions, 51 of 69 topics at 90% or more. On it, practice now takes
turns: no course has two practice sessions in a row, and each is done before its
exam (a test). How much practice is right is not measured: the memory model does not
count practice at all, so no benchmark can show what it is worth.

## D47. A weekly budget offered from what is kept

*Phase A, item A4 of docs/ENGINE-REVIEW.md (weak spot W7), approved by the owner on
2026-10-13.*

**Chosen.** When each of the last three full weeks of a plan had fewer than 70% of
its past sessions done, and at least one session reported, Today offers a weekly
budget equal to the hours kept, in whole blocks and at least two
(`service.budget_offer`). The card says what was kept against what was planned, and
what the plan predicts at the exams with the smaller budget and with the present one
"if every session is kept". One tap ("Plan 4.5 hours a week") makes the plan again
with it; "Not now" leaves the budget as it is and asks again after three more full
weeks. Nothing changes without the tap.

**Why.** A plan nobody follows schedules nothing: when the student skips at random,
the rules no longer decide which topics get the time. A smaller plan the student
keeps lets them choose again, the most faded topic first. If-then plans raise goal
attainment (d = 0.65 over 94 tests, [ref:gollwitzer2006]): a plan matched to the
week the student actually has is the kind they carry out. This is a product
judgement, not a measured effect on learning (the review's verdict on W7).

**Rejected.** Shrinking the budget silently (the student would not know why the plan
changed, and a bad fortnight would cut a good plan); offering after one or two weeks
(an exam week or an illness is not a habit); counting a week with nothing reported
as kept nothing (silence is unknown, D44: Today asks for reports instead); hiding
the cost (the card says how many fewer topics the smaller plan predicts, even when
that argues against taking it).

**Measured.** Tests on the sample semester: with one session in three done for three
weeks (40, 30 and 30% kept, about 5 of 15 hours), the offer is 4.5 hours; with two in
three (70, 70 and 60%), there is none, because 70% is not under 70%; with nothing
reported, none; with two full weeks only, none; "Not now" holds for three weeks. No
benchmark: the benchmarks assume every session is kept.

## D48. Quick self-tests in the gaps between classes, and on the way

*Phase B, item B1 of docs/ENGINE-REVIEW.md (weak spot W2), approved by the owner on
2026-10-14.*

**Chosen.** Free time too short for a study block but at least an hour (with
90-minute blocks: exactly an hour, between two classes or between the study window's
edge and a class) holds a quick self-test of 45 minutes, 15 left to get to the next
class (`plan.short_gaps`, `service.QUICK_SLACK`). At most one a day, the day's first
such gap. Only the self-testing rule uses it: deadline work and exam practice need a
full block. The weekly budget is counted in minutes, so a quick self-test spends 45
of the week's minutes and a block 90; with blocks alone this is the old count
exactly. When the student says they can study on the way (train, bus), each trip of
half an hour or more holds a quick self-test too, for the length of the trip. New
plans have quick self-tests on (Settings: "Quick self-tests in the hour between two
classes"); plans made before D48 keep them off until the student turns them on. On
the way is off until the student says so, in the trip question or in Settings.

**Why.** The review found hour-long gaps wasted: the sample semester has 35 of them,
about two a week. Retrieval practice works in short sessions too: what matters is
recalling before checking [ref:adesope2017], and a gap an hour long is time enough
for five questions and their answers. Half-hour gaps are left free: that is the walk
between rooms.

**Rejected.** A quick self-test in any leftover free time (the tail of a long free
stretch would make a 2¼-hour session out of a block and a quick one); more than one a
day (the day's cap on blocks is there for fatigue); quick deadline work (a task's
hours are counted in blocks); studying on every trip without asking (many students
drive or walk).

**Measured.** `benchmarks/rule_vs_planner.py`, last rows: with quick self-tests, at
15 hours a week, 184 sessions instead of 174 (152 blocks and 32 quick ones), 53 of 69
topics at 90% instead of 51, mean recall at the exams 0.934 instead of 0.923, the
lowest topic 0.801 instead of 0.755, in 252 hours studied instead of 261; at 12 hours
34 instead of 30, at 18 hours 60 as before. **The limit:** FSRS counts a review, not
its length, so in the model a 45-minute self-test raises a topic's memory as much as
a 90-minute one, which it does not in a student; part of that gain is the model's,
not the student's. Without quick self-tests the plan is the same as before (a test).

## D49. How much an exam weighs, only between topics fading together

*Phase B, item B2 of docs/ENGINE-REVIEW.md (weak spot W5), approved by the owner on
2026-10-14.*

**Chosen.** Each exam can weigh less, as usual or more (Settings, beside the exam;
0.5, 1 and 2). The self-testing rule still takes the most faded topic, except among
the topics fading together, within 0.06 of predicted recall of the most faded
(`assistant.WEIGHT_TIE`): there the topic whose exam weighs most goes first. A
topic more faded than that is never passed over, so no course is left without
self-tests. With every exam as usual, the plan is the same as before.

**Why.** Students do not value every exam alike (credits, a course they must pass,
one they have already passed once). The review judged this a product need, not a
learning finding, so the weight only reorders near ties: it cannot starve a course,
and it does not change how many sessions there are.

**Rejected.** A weight multiplying how often a course is reviewed (it would starve
the light courses under a tight budget); a desired retention per course (FSRS's own
knob: it changes what "ready" means, and the forecast would then show each course
against a different line); a tie of 0.03, tried first: at 18 hours a week the heavier
course's recall at its exam went down instead of up.

**Measured.** `benchmarks/rule_vs_planner.py`, last table, Algebra 3 weighing more or
less: its mean predicted recall at the exam goes from 0.856 to 0.881 (more) or 0.814
(less) at 12 hours a week, from 0.897 to 0.923 or 0.856 at 15, from 0.931 to 0.935 or
0.919 at 18. The other courses pay for it: mean recall over all topics 0.923 against
0.915 (more) at 15 hours, and the lowest topic falls at 18 hours (0.872 against
0.820). A test pins that the heavier course is remembered better at all three budgets
and that every course still gets self-tests.

## D50. A heavy week is more than one topic; a poor self-test asks for a full block

*Phase B, item B3 of docs/ENGINE-REVIEW.md (weak spot W8), approved by the owner on
2026-10-14.*

**Chosen.** A week of one course with more than three hours of its lectures becomes
as many topics as it has three-hour shares, its lectures split in the order taught
("week of 05 Oct (1 of 2)", each available once its last lecture is over;
`service.LECTURE_HOURS_PER_TOPIC`, `service._week_parts`). Up to three hours, one
topic, as before. And after a self-test that went poorly (nothing or only some
recalled, or a lapse), the topic's next self-test waits for a full block: it is not
given a quick 45-minute one (D48).

**Why.** One 90-minute self-test cannot cover six hours of lectures as it covers
three: a week of a lab-heavy or intensive course was one topic, reviewed as if it
were a light one. Splitting by the lectures as taught keeps each part a real piece
of the course and each review honest about its size. A topic the student could not
recall needs relearning, not a quick check, which is what a short session is for.

**Rejected.** A topic's weight by hours inside the memory model (FSRS has no notion
of how much material a card holds; two topics model two reviews honestly); splitting
by fixed hours inside a lecture (a lecture is the unit a student remembers); a longer
than usual block after a poor self-test (it would break the student's chosen session
length; a full block instead of a quick one keeps it).

**Measured.** The sample semester has no week over three hours of one course (19
weeks of 1.5 hours, 65 of 3), so `benchmarks/rule_vs_planner.py` and
`benchmarks/semester.py` are unchanged. Tests pin the split (1.5 + 1.5 + 1.5 hours:
two topics of two and one lectures; six hours: two of two), a heavier Algebra 3
(twice the topics, the other courses unchanged), a US timetable with two lectures and
a lab a week (tests/test_generality.py), and the full block after a forgotten
self-test.

## D51. A warm-up before today's lectures, opt-in

*Phase B, item B4 of docs/ENGINE-REVIEW.md, approved by the owner on 2026-10-14.*

**Chosen.** In Settings, "A warm-up before each lecture" (off unless the student
turns it on). Today then lists the day's lectures still ahead, of the courses being
planned, with one instruction: two minutes before each, write two questions you
think it will answer and guess the answers (`service.warm_ups`). Nothing is
scheduled: no session, no block, nothing in the feed, no hours from the week's budget.

**Why.** A test before learning helps: classroom studies report about 12 to 13 points
higher on later exams [ref:pan2023], as summarised by [ref:sana2023]; wrong guesses
help too, because the student then notices the right answer. Two minutes cost almost
nothing, so the prompt need not take a slot of the plan.

**Rejected.** A scheduled warm-up session (a slot before each lecture is often the
trip or the walk between rooms; and it would spend the budget on something the model
cannot value); questions written for the student (the app does not know what a
lecture will cover; Claude could guess from a course name, but D12 keeps AI opt-in
and capped, and a guess at the syllabus is not worth it here); on by default (an
unasked prompt on every class day is noise).

**Measured.** Nothing: the memory model has no notion of a pretest, and the plan is
the same with it on (a test). Whether students use it is a question for the pilot
(D15), which counts Today's visits but not this card.

## D52. The product runs on FSRS-6; the research planner stays on FSRS-4.5

*Phase C, item C1 of docs/ENGINE-REVIEW.md, approved by the owner on 2026-10-14.*

**Chosen.** FSRS-6 with its default weights (`memory.FSRS_6`), implemented in
`memory.py` beside FSRS-4.5 and checked against py-fsrs 6.3.2 [ref:py-fsrs-6.3.2]
on two trajectories to 2e-12 (`benchmarks/fsrs6_reference.py`, pinned in
`tests/test_memory6.py`; invariant 9). A plan made by `make_schedule` (the
assistant, every student's plan) names its model in its preferences, `fsrs-6` from
now on; replays, the exam forecast, each self-test's gain, the recall curve and the
flashcards (`cards.MODEL`) all ask `service.model_of(plan.settings)`. A plan made
before D52 says nothing and keeps FSRS-4.5 until its next refresh, which makes it
again from its history with FSRS-6. A flashcard's stored stability and difficulty
carry over; its next answer updates them with FSRS-6.

**Why.** FSRS-6 predicts recall better on the open benchmark's review logs: log loss
0.3460 against FSRS-4.5's 0.3625 [ref:srs-benchmark]. It has a formula for a review
less than a day after the last, which a flashcard answered "Again" gets ten minutes
later (D29 had to work around its absence). And per-student parameters (C2) are
fitted in FSRS-6's form, so the product must run it first.

**Rejected.** Moving the research planner too: `clock.py`'s value functions and
their admissible bounds were built and checked on FSRS-4.5's curve (invariants 1
and 2), and re-deriving them buys the product nothing, since the product runs the
assistant (AUDIT item 36). `benchmarks/rule_vs_planner.py` compares the planner and
the rules on one model, FSRS-4.5, so every number it prints is unchanged. FSRS-7: on
default parameters no better than FSRS-4.5 on the benchmark (0.3620). A dependency
on py-fsrs at run time: the model is 21 numbers and a few formulas, and py-fsrs is
only a test dependency (the `dev` extra), to pin ours.

**Measured** (`benchmarks/fsrs6_product.py`, the synthetic semester of 69 topics,
Sundays off, 4.5 hours of practice per exam, every review assumed to succeed). The
plans differ by a session or two. Scored by FSRS-6, the plan made with FSRS-6
against the plan made with FSRS-4.5:

| hours a week | at 90% or more | mean recall at the exams | lowest |
|---|---|---|---|
| 12 | 31 against 30 of 69 | 0.875 against 0.874 | 0.761 against 0.752 |
| 15 | 44 against 50 | 0.917 against 0.923 | 0.829 against 0.792 |
| 18 | 58 against 62 | 0.943 against 0.940 | 0.849 against 0.876 |

A mixed result, said as such: the assistant is a rule (the most faded topic first),
not an optimiser, so a better model does not make its plan better on every measure.
At 15 hours the FSRS-6 plan reviews every topic at least twice (FSRS-4.5's leaves two
topics at one review), which raises the lowest recall, and leaves more topics just
under 90%. The reason to move is the forecast, not the plan: a student reads
predicted recall on Today, Progress and every self-test, and FSRS-6's is the better
prediction. The two models also disagree on the same sessions: FSRS-6 puts the
lowest recall of FSRS-4.5's own 15-hour plan at 0.792 where FSRS-4.5 says 0.755.
Neither is checked against students yet; that is Gate C's log loss and the pilot's
(D15).

## D53. A student's own memory parameters for their flashcards, from 400 answers

*Phase C, item C2 of docs/ENGINE-REVIEW.md. The owner chose, on 2026-10-14, our own
NumPy fitter over py-fsrs's optimizer, which brings PyTorch.*

**Chosen.** Once a student has given 400 flashcard answers a day or more after the
card's previous one (`fitting.MIN_REVIEWS`), their cards are scheduled with FSRS-6
parameters fitted to their own answers (`cps/fitting.py`); fitted again each time
those answers have grown by a quarter (`cards.REFIT_GROWTH`), from the 1,000 cards
answered most recently. The fit is the most probable set of the 21 parameters under
a prior centred on the population's: each parameter on a 0-to-1 scale between
py-fsrs's bounds (the four first stabilities on a log scale), the summed log loss of
the answers plus the squared distance from the defaults over twice 0.02 squared,
minimised by Adam on finite differences, its step annealed, every parameter set
replayed at once with NumPy. It takes seconds, so the web app runs it after the
answer that makes it due. It is kept in `card_fits`, goes in the plan's copy and is
deleted with the plan. The Flashcards page says which: "fitted to your own memory,
from N of your answers", or how many answers are still to come.

**Why.** Population parameters are wrong for a student in the same direction every
time (the engine review's W3). A fit to the student's answers corrects that once
there are enough of them; before, it chases noise, so the threshold is measured,
not guessed.

**Rejected.** py-fsrs's optimizer (PyTorch on a free server, for 21 numbers). A
looser prior: with 0.05, the fit gains a little more at 400 answers but costs a
student who is the population +0.0017 ± 0.0005, and 0.1 and 0.2 are worse
(`benchmarks/personal_fsrs.py --prior`, on 16 other students). The fit for the
plan's topics: a week of lectures is not a flashcard, and a semester at 15 hours a
week has 155 self-tests (`benchmarks/fsrs6_product.py`), under the threshold; the
plan keeps the population's parameters.

**Measured** (`benchmarks/personal_fsrs.py`: 20 made-up students, ten new cards a day
for 120 days, scheduled as the product schedules them, each fit tested on the three
weeks of answers after it; mean change in log loss against the population's
parameters, with its standard error):

| answers | students whose parameters differ | better off | gap closed | students who are the population |
|---|---|---|---|---|
| 200 | -0.0016 ± 0.0006 | 70% | 29% | +0.0001 ± 0.0001 |
| 400 | -0.0035 ± 0.0009 | 90% | 49% | +0.0002 ± 0.0002 |
| 800 | -0.0054 ± 0.0012 | 90% | 56% | +0.0001 ± 0.0002 |

The threshold is the fewest answers at which the fit is better by more than two
standard errors, for at least three students in four, and costs a student who is
the population less than 0.001: 400. The gain is small, as the gap it closes is
small: at 400 answers, 0.4237 with the population's parameters, 0.4124 with the
student's own. These are simulated students: how far real students are from the
population (each parameter times e^N(0, 0.3)), how they choose a grade and how many
new cards they already know are assumptions in `cps/fitting.py`. Whether it holds
for real students is the pilot's to measure (Gate C, D15).

## D54. CP-SAT tried against the rules: the rules stay

*Phase C, item C3 of docs/ENGINE-REVIEW.md. The owner approved OR-Tools on
2026-10-14 as an optional extra (`trial`) for a benchmark only; the product keeps
the rules unless the trial clearly wins.*

**Chosen.** The rules. `benchmarks/cpsat_trial.py` places, with CP-SAT [ref:or-tools],
in the same free blocks as the assistant: every review FSRS-6 would give a topic if
each came when its recall falls to 90% (from three days early to five late, or
dropped), three blocks of practice per exam in its fortnight, one thing a block, the
week's budget, a topic's reviews in order; a stated objective (a dropped review
100, a topic's next review 1,000, a missing practice block 100,000, a day early 1, a
day late 3). Once for the semester, and re-solved each week over the next four from
the topics' states, as the assistant replans. Both scored by the same FSRS-6
replay, every review assumed to succeed:

| hours a week | planner | at 90% or more | mean recall at the exams | lowest |
|---|---|---|---|---|
| 12 | rules | 31 of 69 | 0.875 | 0.761 |
| 12 | CP-SAT, re-solved weekly | 34 | 0.757 | 0.504 |
| 15 | rules | 44 | 0.917 | 0.829 |
| 15 | CP-SAT, re-solved weekly | 46 | 0.832 | 0.527 |
| 18 | rules | 58 | 0.943 | 0.849 |
| 18 | CP-SAT, re-solved weekly | 63 | 0.921 | 0.534 |

Solved once, CP-SAT is worse on every measure (20, 33 and 42 topics at 90%).
Re-solved weekly, it brings a few more topics to 90%, but leaves others far behind
(the lowest recall about 0.53 against 0.76 to 0.85) and the mean lower. The rules
choose by each topic's current predicted recall, the most faded first; the linear
model places reviews near ideal days that do not move when a review lands off its
day, and a weight cannot say how faded a topic is. Not a clear win, so nothing in
the product changes, and OR-Tools stays out of it.

**Rejected.** Recall inside the model (FSRS is not linear; piecewise
approximations make a larger model and a project of its own); weights tuned until
CP-SAT wins on this one semester (invariant 13). Found on the way: a first version
whose missing practice cost less than a dropped review skipped every practice block
to place more reviews; practice now outranks reviews, as in the rules. And wall-time
limits made a row depend on the machine; the solver now stops on a deterministic
work limit, with one worker and a fixed seed.

## D55. Predicted against reported recall, in the pilot's measures

*Gate C of docs/ENGINE-REVIEW.md ("log loss of predicted against reported recall on
our own data"), which also builds Phase D's first item.*

**Chosen.** `cps metrics` (`service.calibration`) prints the mean log loss of the
recall the product predicted, over what students then reported, lower is better:
flashcard answers a day or more after the card's previous one, predicted by FSRS-6's
defaults and by FSRS-4.5's (D52); the answers given after a student's own parameters
were fitted, by those and by FSRS-6's defaults, so the fit is tested on answers it
never saw (D53); and every self-test answered with how much was recalled (D25),
against the recall its plan predicted. Each with how many answers and students it
rests on.

**Why.** D52 and D53 rest on an open benchmark and on made-up students. Whether
FSRS-6 predicts these students better than FSRS-4.5, and whether their own
parameters predict them better still, is a number to read from their answers.

**Rejected.** A calibration chart (predicted against observed, by bins): with 10 to
20 students the bins are too thin to read, so the counts come first (D15).

**Measured.** Nothing yet: nobody uses the product. The tests check the measure on
made-up answers: it equals the fitter's own log loss, and counts only the answers
after a fit.

## D56. How an exam went, asked after it, shared only with consent

*Phase D, item D2 of docs/ENGINE-REVIEW.md; the owner said to start Phase D on
2026-10-14, which this item's approval (the question and its privacy line) was
waiting for.*

**Chosen.** For three weeks after each exam (`EXAM_ASK_DAYS`), Today asks the two
questions the plan named: "How did the course exam go?", one tap among five answers
(passed, and well; passed; just passed; not passed; did not sit it), with a mark if
the student likes, written as they write it (27/30, 14/20, B+); and "Did the plan
help?", if they like (yes, a lot; a little; not really; I did not follow it). Then a
box, unticked, that lets the owner read the answers.
"Rather not say" keeps only that it was asked. The answer is kept in the plan
(`exam_results`), so it goes in the plan's copy and is deleted with it, beside the
recall the model predicted for that exam from the sessions reported, as it stood
when the answer came (`recall_predicted`). `cps metrics` lists the shared answers,
each with its prediction, without course or plan, and only counts the others (D55).
The privacy page says all of this, in English, French and Italian.

**Why.** Every number the plan shows is a prediction, and until now nothing checked
one against an exam. A result per exam is the outcome students care about; set
beside the recall predicted for it, a pilot of 10 to 20 students can show whether
higher predictions go with better results, as counts to read rather than rates to
test (D15).

**Rejected.** A numeric grade on one scale: marks are out of 30 in Italy, 20 in
France, letters elsewhere, so the five answers are what is compared and the mark is
kept as written (invariant 13). More questions than the two (how prepared, hours spent):
each one costs answers, and the plan already knows the hours. Consent ticked by default, or
the answer sent anywhere without it. Asking by e-mail: the app does not know one.

**Measured.** Nothing yet: no exam has passed for anyone using it. The tests check
the question's three weeks, the consent and what the measures show.

## D57. Polish: type, buttons, smoothness

*The owner, 2026-10-14: "polish the design of font, buttons, smoothness, etc."*

**Chosen.** A pass over the stylesheet and two scripts, keeping every page's layout
and words (no new sentence, so nothing new to translate):

1. **Type.** Large type (page titles, the big numbers of Today, Progress and
   Focus) takes the display cut of the system face where there is one: SF Pro
   Display on Apple devices, Segoe UI Variable Display on Windows; body text keeps
   the text cut. Titles are a little tighter; numbers that change (counts, badges,
   stats) use tabular figures, so they do not shift sideways as they count up (D30).
2. **Buttons.** A button whose answer takes more than 150 ms shows a small spinner
   in place of its words until the answer comes, both for forms sent in the
   background (ui.js) and for ordinary ones (motion.js, cleared if the browser
   brings the page back from its cache); its neighbours stay disabled as before.
   The large buttons are capsules, as iOS draws its large buttons. Taps do not
   wait for a double-tap zoom.
3. **Smoothness.** Disclosures open and close with a short ease where the browser
   can animate to "auto"; fields ease into their focus ring; scrolling inside a
   panel, sheet or menu stops at its end instead of dragging the page behind it.
4. **Details.** The selected segment of a segmented control is white in light mode
   and gray in dark (it was the card colour, nearly invisible on dark); the selected
   tab's icon is filled; the flashcards card on Focus has the chevron the other
   cards that lead somewhere have; selected text and the caret take the app's tint;
   the footer sits at the bottom of a short page instead of halfway up.

Three defects found on the way, fixed here (AUDIT items 62 to 64): the field rule
inside cards used the `background` shorthand and so wiped the chevron of every select
in a card; a rule meant for a card's caption also shrank the number that counts up
beside it ("4/8" on Progress); and the segmented thumbs above in dark mode.

**Rejected.** A web font (San Francisco is already the face on Apple devices, and a
download costs every first visit); a spinner library or an animated SVG (one CSS
ring); a spinner at once on every press (most answers come in under 150 ms, and a
flash of spinner reads as a flicker); animating disclosures with a script (the CSS
way needs nothing and falls back to opening at once).

**Disclosed.** As D37: checked in Chromium only, at 390 and 1280 px wide, light and
dark, with motion on and reduced; not on an iPhone or in Firefox. Under reduced
motion the spinner is a still ring and disclosures open at once. The display cut
depends on the device's fonts; where it is missing, large type uses the text cut.

## D58. The home page: room to breathe, a field of neurons, a softer dark

*The owner, 2026-10-15, on the demo's home page: the slogan was stuck to the rest,
the page was all blocks, they wanted something "neuro-inspired, dynamic, flowing",
and the black theme was overwhelming.*

**Chosen.**

1. **Room.** The pitch (headline, one paragraph, the button) fills most of the first
   screen, centred, with space around it; the headline is larger. The demo's button
   is right under the pitch, with a link down to the ways in; what the demo is
   follows in two small lines instead of a card. The setup steps moved from above
   the headline to where setup starts.
2. **A field of neurons** behind the pitch: about fifty neurons joined by curved
   synapses, in the app's blue, violet and teal, over a slow aurora glow. Signals run
   along fourteen synapses at their own pace, the neurons glow and fade, the field
   drifts. It is one SVG in the page, drawn once by a seeded script
   (`python -m cps.web.neural`, checked by a test), coloured and moved by CSS, faded
   behind the words. The headline's last words take the same colours.
3. **Flowing, not blocks.** The two ways in are glass over a quieter glow, edged with
   the field's colours, and lift a little under the pointer; "Already have a plan?"
   is a passage under a hairline, not another box; the four promises are nodes on
   one line (a column on a phone) instead of four boxes, the fourth no longer alone
   on its row. The setup steps everywhere are nodes on a line, the current one
   alight. As the page scrolls, each part rises into place.
4. **A softer dark,** for every page: the background is a blue-black ink (#0e1015)
   instead of pure black, the cards a little lighter (#1a1c23). The course colours
   still pass every check of the reference palette's validator on the new surface
   (lightness band, chroma, colour-blind separation, contrast).

After a failed start the hero is short and the message sits with the forms, so it
is not a screen below the pitch.

**Rejected.** A canvas animation with a script (the page must read the same without
scripts, and a canvas costs a frame loop even when nothing changes); a WebGL
background (heavy on phones, nothing to show without it); a video (weight, and no
light and dark); a photo or an illustration of a brain (literal, and it would date
the page); pure black kept for OLED screens (the owner found it overwhelming, and
the ink costs almost nothing on them); a web font.

**Disclosed.** Checked in Chromium only, at 1440 and 390 px, light and dark, with
motion on and reduced: under reduced motion nothing moves and nothing is hidden.
The field is decoration, hidden from screen readers. Masks, gradient text and the
scroll-linked rise need recent browsers; elsewhere the field has hard edges or the
parts are simply there. The field keeps running while the home page is open, which
costs some battery on a phone; not measured.

## D59. The calendar shows the whole day

*The owner, 2026-10-15: "the calendar should show the whole 24h, not just 8 to 23".
Step 1 of the plan agreed that day (the next steps: changing a lecture, lectures that
become tests, the tips).*

**Chosen.** The calendar's grid runs from midnight to midnight in every view (Day,
3 Days, Week), with midnight named at the top, as Apple Calendar draws it. It still
opens where it did: at the start of the study window, or an hour and a half before
now on today; the hours outside the study window stay shaded. Nothing else changes:
the planner still studies only inside the window, and the student's own busy times
and classes can now be drawn at any hour.

**Rejected.** Growing the grid only when an event falls outside 07:00 to 23:00, as
before: a class at 07:30 is on the grid, but the student cannot draw a 06:00 run or a
night shift on an empty morning. A setting for the visible hours: one more question
for something scrolling already answers.

**Disclosed.** The grid is 1,296 pixels tall at every width; the page scrolls inside
it. An event that crosses midnight is still cut at midnight, as before. Checked in
Chromium at 1280 and 390 px.

## D60. A class of the timetable is the student's to skip

*The owner, 2026-10-15: lecture blocks "cannot be locked in, because if one day I
cannot go I delete it and maybe put a block of self-study". Step 2 of the plan agreed
that day; turning a class into a quiz, a mock exam or a partial exam is step 3.*

**Chosen.** Tapping a class on the calendar (or, without the calendar's script, in
the week's list) offers, under "Can't go?":

- **Skip it and study it on my own**: the class is skipped and a task as long as the
  class (to the half hour above) is added, "Catch up: course, class of day", due
  when the course's next class begins (within two weeks; otherwise a week after the
  class missed). The plan fits it in like any deadline, often into the time the
  class freed, which is the self-study block the owner described. The student can
  change it or move its session like any other.
- **Skip this one**: its time is free for the plan.
- **Skip it every week**: the same class (same title, weekday and start time) from
  this one on.

A skipped class keeps its place on the calendar, faded and struck through, with
"Restore" (for a weekly skip, "Restore every week"); restoring removes the catch-up
task unless it was finished. Changes are kept with the plan
(`options["lecture_changes"]`), so they go in its copy and are deleted with it, and
they are applied each time the timetable is read (`analyse_calendar`): the class is
an event marked "skipped", shown but not busy, and it no longer brings a trip to
campus, a warm-up (D51) or a refusal when a session is moved onto its time.

A class is known by its start and its title, as the timetable gives them, so a
change outlives a new reading of the timetable's link; pages name it by its start
and a digest of its title (`lecture_ref`), so the timetable's raw text ("Grp: CM .,
Salle: ...") never reaches a page, which shows only the cleaned title. Exams are not
skipped here (their date is changed in Settings), nor classes already begun.

**Rejected.** Deleting the class from the timetable (the next reading would bring it
back, and the student could not undo it); fixing the catch-up at the class's own
time (a student who cannot go is often busy then; the plan may still choose it, and
the student can move it); a catch-up for a class skipped
every week (one task a week would pile up unseen: the student adds the work as a
task); asking why the class is skipped.

**Disclosed.** The lectures still count as taught: a week's topic (D50) starts "seen
once and shaky" whether or not the student went or caught up. A class the
university moves is a new class: the change stays with the old one and the new one
is not skipped. Attendance goes with grades in college (Credé, Roch and Kieszczynka
2010, a correlation, not a cause), so the first choice offered is to catch up.
Checked in Chromium at 1280 and 390 px.

## D61. A class can be a test: a pop-up quiz, a mock exam, a partial exam

*The owner, 2026-10-15: a lecture can turn into a quiz, a mock exam or a partial exam,
"because professors decide to give tests to assess the class's level". Step 3 of the
plan agreed that day.*

**Chosen.** Under "Is it a test?" (the calendar's popover and the week's list), a
class to come becomes:

- **A pop-up quiz.** The class still takes its time; an hour to prepare (a task,
  "Prepare for the quiz: course, day", due when it starts) is planned before it.
  Quizzes in class raise learning (a meta-analysis of 222 classroom studies found a
  medium effect, g of about 0.5 [ref:yang2021]), and a short self-test before one is
  the practice it rewards.
- **A mock exam.** It counts as exam practice for its course (D46): the practice the
  plan schedules before the final is shorter by its length, rounded to blocks.
- **A partial exam.** An exam of its own: practice is planned in the days before it,
  sized like an exam's on the weeks taught before it (D46), under its own name
  ("Algebra 3, partial exam of Thu 5 Nov", in the course's colour). The student says
  whether passing it takes its material off the final. If it does, the weeks of
  lectures taught before it are examined there: their exam is the partial's, so
  they are reviewed for it and not after, and each week is forecast at its own exam.
  If it does not, the weeks stay with the final and the partial only
  brings its practice.

A test teaches nothing new, so it is not a lecture: its week's topic ends with the
classes before it. "It's an ordinary class" undoes the mark, and a quiz's
preparation goes with it unless done. Like a skip (D60), the mark is kept with the
plan and applied each time the timetable is read; a test is marked one class at a
time, never every week. A course's own exam stays its final in the plan's summary.

**Rejected.** Counting the quiz itself as a review of the course's topics in the
memory model (which topics, and how well, the app cannot know without asking; a
question after the quiz is a later step); a partial exam that is also on the final
for the same topics with two targets (the rules review a topic while its recall
falls below the target, whatever exam it is for, so keeping it for the final keeps
it reviewed); the after-exam question (D56) for partial exams, until the pilot shows
it is wanted.

**Disclosed.** A course whose lectures are not in the timetable is one topic, and
cannot be split at a partial: its material stays with the final. The preparation
for a quiz is an hour whatever the quiz. Checked in Chromium at 1280 and 390 px,
with and without scripts.

## D62. Tips with their sources

*The owner, 2026-10-14: a tips section "that says idk that consuming theanine and
caffeine improves focus, that you need breaks every XY minutes, … all info backed by
research to make a student feel like they have all the tools for coping with
studying well". Step 4 of the plan agreed that day, the owner reviewing the wording.*

**Chosen.** A Tips page, reached from Focus, and one tip a day at the foot of Today.
Fourteen tips in four topics (how to study; sleep, movement and coffee; breaks;
habits), each with:

- the advice, in one sentence;
- why, with the figures its sources give;
- its limits: what the evidence does not show;
- how strong the evidence is, in words and a colour that only repeats them: strong
  (many studies agree), moderate (good studies agree, with conditions or open
  questions), early (one or a few studies, or correlations only); the level is a
  judgement written in the code, not a computed grade;
- where the app already does it, when it does;
- its sources, each linked, with the day it was read.

The tips rest on: testing yourself [ref:dunlosky2013] [ref:adesope2017]; spacing
[ref:cepeda2006] [ref:cepeda2008]; quizzes in class [ref:yang2021]; mixing kinds of
problems [ref:brunmair2019] [ref:rohrer2020]; questions before a lecture
[ref:pan2023]; going to class [ref:crede2010]; sleep after learning [ref:rasch2013]
[ref:mazza2016]; regular sleep [ref:okano2019]; exercise [ref:roig2013]
[ref:vandongen2016]; coffee or tea [ref:camfield2014] [ref:drake2013]
[ref:efsa2015]; short breaks at set times [ref:albulescu2022] [ref:biwer2023];
keeping track [ref:harkin2016]; if-then plans [ref:gollwitzer2006]; a missed day
in a habit [ref:lally2010].

`tests/test_tips.py` holds the rules: a tip cites only entries of
docs/REFERENCES.md marked checked, read on the day the page shows, with the same
first author, year and DOI; every figure a tip writes in digits is in what was read
of its sources, or within 5 % of one ("over 21,000" for 21,195); every sentence has
a French and an Italian entry. Twelve entries cited before as unchecked were read
at a research database for this and are now checked.

Coffee and L-theanine, the owner's own example, are general information: what the
trials found, how caffeine late in the day cut sleep, and the EU's safety limits as
limits. No dose is advised and no supplement named. The EU's 2011 opinion on
L-theanine's health claims could not be opened from here, so nothing is said about
L-theanine on its own.

Today's tip takes turns, the same for every student on a given day. Tips at the
right moment (before an evening session, after a long Focus session) are the next
step and will replace the rotation. Opening the page is logged once a day per plan,
and `cps metrics` reports how many plans read the tips in 7 and 30 days: the AI
coach waits until the beta shows that they are read.

**Rejected.** A recommended dose of caffeine, L-theanine or anything else (not the
app's to prescribe); "every 25 minutes" as a rule for breaks (the one study of set
breaks lasted a day); a figure found only in summaries, such as a median of 66 days
to form a habit; tips written by a language model (each sentence here was written
against its source's record and is checked by a test; a model's would not be);
effect sizes as numbers on the page (the sources' own words, medium or moderate to
large, are kept instead).

**Disclosed.** The sources were read as abstracts and records (Europe PMC, Crossref,
Semantic Scholar, publishers' and agencies' pages), not as full texts. The wording
is the owner's to review; the Italian was not written by a native speaker (D34). No
study shows that tips in an app improve students' results: these are information,
and whether students read them is measured. Checked in Chromium at 1280 and 390 px,
light and dark, and without scripts at 390 px.

## D63. Tips at the right moment, brought by a neuron

*Step 5 of the plan agreed on 2026-10-14; the owner, 2026-10-15: "start steps 5-6-7,
I'll only check if there's any flaws in the product once we have everything
covered". The avatar without AI first, as recommended then.*

**Chosen.** Today's tip is the one the plan's day calls for, with a line saying why
now. First what cannot wait, in this order:

- an exam (or a partial exam) within three days: keep sleep regular;
- a quiz or a mock exam in the coming week: quizzes are study;
- a session skipped yesterday: missing one day does not undo a habit;
- a class skipped this week without a catch-up: go to class when you can;
- a study session this evening, from noon on: coffee or tea, far from bedtime.

Then the ordinary moments, which take turns day by day when several apply:

- a lecture later today, when the warm-up (D51) is off: try a few questions first;
- training today: a workout can help you remember;
- a self-test today: test yourself instead of rereading;
- after 20:00 on a day with study done: sleep after studying.

When nothing applies, the tips take turns as before (D62). The line names only facts
the plan holds ("A test is coming: Quiz: Algebra 3, Thu 1 Oct 16:00."); what the
research found stays in the tip. A tip the day calls for sits near the top of
Today; one taking turns, at the foot.

On Focus, before starting: decide when the breaks will be. In a running session, a
card asks "A short break now?" once 24 minutes have passed, the block length of the
study behind it [ref:biwer2023]. The server writes it into the page hidden, with the
time; the clock's script only shows it then, and without scripts it is shown when
the page is loaded after that time.

The tips are brought by a neuron (since replaced by the spark, D66): the home
page's neurons (D58) as a small cell with a face, dendrites above and an axon to its
terminals. It breathes and a signal runs down a dendrite to it; under reduced motion
it keeps still. It is decoration:
on-screen characters help learning only a little, more for school pupils than for
students, and more when they speak in text than aloud [ref:schroeder2013], so this
one only carries the text. Settings, Appearance: Tips On or Off, for Today and
Focus; the tips page stays.

**Rejected.** A character that talks or animates on its own initiative; a named
mascot (a name is the owner's to choose); notifications for tips (the reminders,
D38, are for sessions); moments the plan cannot see (how the student slept, what
they drink).

**Disclosed.** The moments and their order are judgements, not measured. A plan
with study every day sees the self-test tip often; the turns between ordinary
moments soften that. Checked in Chromium at 1280 and 390 px, light and dark.

## D64. The weekly note on the science of learning

*Step 6 of the plan agreed on 2026-10-14; inside the app only, as recommended then
(an e-mail list needs an outside service and consent records).*

**Chosen.** A note a week: one study told in plain words, two short paragraphs on
what it did and found, "What it means for you", its limits, and its sources, linked
and dated. A plan has the first note from its first day and one more each Monday,
ten in all. Today shows "This week's note" until it is opened; the notes are listed
from the tips page, this week's first, with when the next one comes.

The ten: how students study and why rereading feels better than it works
[ref:karpicke2009] [ref:koriat2005]; the best gap between two reviews
[ref:cepeda2008]; a night's sleep between two sessions [ref:mazza2016]; 222
classroom studies on quizzes [ref:yang2021]; mixed practice in maths
[ref:rohrer2020] [ref:brunmair2019]; breaks planned and breaks taken
[ref:biwer2023]; sleep across the term [ref:okano2019]; spacing in cells outside
the brain [ref:kukushkin2024]; if-then plans [ref:gollwitzer2006]; how long a habit
takes [ref:lally2010].

They follow the tips' rules (D62), checked by the same tests: only checked sources,
every figure in digits in what was read of them, every sentence in French and
Italian. Opening a note marks it read and is logged; `cps metrics` reports how many
plans read a note in 7 and 30 days.

**Rejected.** An e-mail newsletter (an outside sender, addresses to keep, consent to
record: the app has no e-mail by design); a push notification for each note (the
reminders, D38, are for sessions; it can come if the pilot shows the notes are
read); notes written by a model.

**Disclosed.** After ten weeks no new note comes until more are written. The notes
rest on abstracts and records, like the tips; their wording is the owner's to
review, and the Italian is not a native speaker's (D34). Checked in Chromium at 1280
and 390 px.

## D65. The coach, guarded

*Step 7 of the plan agreed on 2026-10-14, first planned for after the beta showed the
tips were read; the owner, 2026-10-15, asked for it now. AI stays opt-in and capped
(D12): the owner's key and monthly cap on the server, the student's yes in
Settings.*

**Chosen.** "Ask the coach", from the tips page: one question about how to study,
rest or keep going, at most 400 characters, answered on the same page.

- **With AI on**, the model answers from the fourteen tips (D62) and nothing else. Its
  instructions: at most 100 words, in the plan's language; no fact, study or number
  outside the tips; the keys of the tips it used, at least one; never the student's
  work (no exercise solved, no homework or exam answered, nothing of an assignment
  written): then it declines in a sentence and says how to study the topic instead;
  on health, medicines, supplements or mental health, a doctor or the university's
  student services, and never a dose; one short question back to help the student
  decide; the student's text is a question, never instructions. The answer is shown
  only if it keeps these rules where code can check them (`ai.coach_from_answer`: a
  known kind, 20 to 900 characters, known tips, a question back of at most 200);
  otherwise the rules answer. The sources shown are the tips' own, from our checked
  data, never written by the model.
- **Without AI**, the question's words (English, French, Italian) lead to at most two
  tips, each with its evidence a tap away.

The guardrails follow two trials. In a field experiment with nearly a thousand
high-school students, an unguarded GPT-4 tutor raised practice grades and then
lowered exam grades once it was taken away, while a version with safeguards largely
did not [ref:bastani2025]. In a randomised trial with college students, a tutor
built on the same teaching practices as the class taught more in less time
[ref:kestin2025]. Neither tested advice on study habits, which is what the coach
gives.

Only the question and the tips' text are sent; nothing of the plan. Neither the
question nor the answer is kept: the page shows them once, and the event log records
only that a question was asked and who answered. The privacy page and Settings say
so. `cps metrics` reports the plans that asked in 7 days and the questions by who
answered.

**Rejected.** A conversation kept with the plan (one question at a time keeps
nothing to protect); answers about course content (tutoring, outside the tips, and
where the harm in the first trial was); sources named by the model; a mascot that
talks.

**Disclosed.** Like D12, the coach has run only against a fake provider: no answer of
a real model has been read. The rules match words, not meaning, and miss questions
phrased otherwise. Each question carries the fourteen tips' text, so it costs more
than a typed task; the monthly cap bounds it. Checked in Chromium at 1280 and 390 px,
light and dark, with AI off.

## D66. The spark brings the tips

*The owner, 2026-10-15: "make that neuron 3d rendered animated and more
mascotte-looking, propose 3 candidates on the falsa riga of duolingo". Three were
drawn on a design canvas: a neuron, a brain in a graduation cap, a spark. The owner
first chose the brain, then, 2026-10-16: "in realtà mi piace di più spark … fai
spark".*

**Chosen.** The tips (D63), the weekly note's "what it means for you" (D64) and the
coach's answer (D65) are brought by the spark: the signal that jumps from one neuron
to the next, drawn as a glowing teal drop with a face, two floating hands, a small
flame on top and three vesicles circling it. Gradients and a soft highlight make it
look rounded, in the manner of Duolingo's characters, but it is SVG written into the
page (`_mascot.html`): no image file, no library, nothing fetched. It has five moods,
each a still pose that `service` picks (`TIP_MOODS`, `focus_tip`, the weekly note,
`coach`):

- think (eyes up, a small "o", three dots): beside a way to study, the weekly note,
  and the coach's answer;
- rest (eyes closed, z): beside sleep and breaks, and at the break on Focus;
- cheer (hands up, stars): beside exercise and a habit;
- wave: beside going to class, and on Focus before starting;
- idle: beside coffee, and when the coach has no tip to give.

With motion allowed it floats, blinks and looks around, its flame flickers and its
vesicles circle; each mood moves its own way (it jumps, tilts, breathes, waves).
Under reduced motion, or the plan's Motion: Reduced, it keeps its pose and stays
still. Each drawing's gradients carry ids of its own, so two on one page, or one in a
hidden card (the break on Focus), are drawn right.

**Rejected.** D63's neuron (the owner asked for a mascot); the brain in a cap (the
owner's first pick, before the spark); a rendered 3D image (heavier, one picture per
mood, not drawn in the page); an animation library (CSS is enough, and the
Content-Security-Policy allows no inline script). A name was left to the owner,
who kept the drawing's working name on 2026-10-16 ("spark va bene"): Spark
introduces itself while setting up (D70) and floats as the assistant (D71).

**Disclosed.** As in D63, it is decoration and only carries the text
[ref:schroeder2013]; which mood goes with which tip is a judgement, not measured.
Checked in Chromium at 1280 and 390 px, light and dark, still and in motion; not on a
real phone.

## D67. A focus session's length, and its rewards

*The owner, 2026-10-16: "when starting the focus to choose for how long and give
'prizes' accordingly, like you get a badge for 30 mins, if u do 3hrs though you get
rewards at idk min 30, 1h, 1h45, 2h30".*

**Chosen.** Before starting the timer, the student picks how long: 25 or 45
minutes, 1 h, 1 h 30, 2 h, 3 h, or no limit. The plan's session offers its own
length first (the nearest of these); anything else offers 45 minutes. Rewards come
at 30 minutes, at an hour, then every 45 minutes (1 h 45, 2 h 30, 3 h 15…), and at
the length chosen: a 3-hour session has them at 30 min, 1 h, 1 h 45, 2 h 30 and 3
h, the owner's example. The running page lists them as small chips, those reached
and the next; when one comes, the spark cheers on a card ("1 h of focus! Next: 1 h
45.") and confetti goes up once (D30). Going on past the length keeps the ladder
going.

Rewards count focused time: the clock less the time away from the page, as the
heartbeats measure it (D17), so leaving the app does not earn them. The diary card
says the length and whether it was reached, and how many rewards; the badge shelf
(D41) gains five badges: a length reached ("A goal kept"), and the first timed
session of 30 minutes, an hour, 1 h 45 and 2 h 30. They are earned only by
sessions timed and checked by the page: minutes typed into the log earn none
(such a session is no longer timed, D17).

**Rejected.** D41 kept hours off the badge shelf, because hours reward showing
off time; the owner asked for these, and they stay within the student's own
sessions, with no ranking and nothing shown to others beyond the diary card the
student already shares. A reward at every chosen length only, or at every 30
minutes (too few for a long session, or too many); hiding the length's progress
behind a ring (the chips can be read without colour).

**Disclosed.** The ladder's steps are the owner's suggestion, not measured. Long
sessions are not advice: the break card still comes after 24 minutes (D63), and a
break on the page counts as focus, a break away from it does not. Checked in
Chromium at 1280 and 390 px, light and dark.

## D68. The calendar scrolls sideways, a day at a time

*The owner, 2026-10-16: "make the calendar scrollable left and right like notion
calendar or google calendar".*

**Chosen.** The calendar is a strip of days: the days shown (one, three or seven,
D59) and a week drawn ahead on each side. A swipe, a trackpad or Shift and the
wheel moves it sideways, and it comes to rest on a whole day (CSS scroll snapping),
as Notion Calendar does; the arrows, the arrow keys and Today slide it by the days
shown, or to today. The day names ride along with the columns, held at the top,
and the hours stay at the left. When the strip comes to rest within two days of
either end, it is drawn again around the first day shown, so it never runs out;
the bar names the days shown, not all those drawn. A study session or a busy time
dragged to the strip's edge carries it along. A swipe at either end stays in the
calendar instead of going back a page (`overscroll-behavior-x`).

The server draws up to 21 days at once (a week shown, a week each side), up from
14; 21 days take about 12 ms to make (measured on the sample semester, this
machine). Drawing a week by hand (D32) stays a single week: it is weekdays, not
dates.

**Rejected.** Pages that turn by the days shown, as Google Calendar's web page does
(the owner named both, and the continuous strip is the one that feels like a
phone); drawing days one by one as they come into view (more requests, and a block
dragged across days needs both drawn); a library for it (the CSS does the
snapping).

**Disclosed.** Checked in Chromium at 1280 and 390 px, with a mouse and with touch
emulated; not on a real phone. Where a browser does not know the `scrollend` event
(which ones was not checked), the strip's resting place is taken after a pause of
160 ms.

## D69. The student's day: getting up, going to bed, lunch

*The owner, 2026-10-16: "the user can choose when they go to bed and when they wake
up, when they have lunch if they want to work or not".*

**Chosen.** "Your day" is the first card of the questions about the week (D42) and
replaces "Study from … until …" in Settings: when the student gets up and goes to
bed (times to the quarter hour), and lunch, from and to, with "Keep it free" (the
default) or "I can study then". Sessions are planned from half an hour after getting
up to half an hour before bed: the study window is made from them, rounded
inward to the quarter hour. A bedtime after midnight ends the
window at midnight, the planner's day. Lunch kept free is busy every day, weekends
too, and shows on the calendar hatched like the trip to university (D42), with a
line saying where to change it; studied through, it is kept but blocks nothing.

A plan made before these questions keeps its study window and shows it widened by
the half hours (8 to 22 reads "07:30" and "22:30"); nothing is kept free at lunch
until the student says so. The questions refuse a day shorter than three hours, a
time that is not one, and a lunch that ends before it starts.

**Rejected.** Sleep as busy events on the calendar (the night is already shaded
outside the window, D59); a lunch per weekday (one answer covers most students; a
day that differs can be drawn on the calendar as busy time); asking for dinner too
(not asked for; an evening class or a dinner is busy time like any other).

**Disclosed.** The half hours at each end are a stated choice, not measured. A
student who works nights, getting up in the evening, cannot say so here: the
window is one stretch within a day. Checked in Chromium at 1280 and 390 px, light
and dark.

## D70. Spark asks about the week, and a time can be drawn

*The owner, 2026-10-16: "it should be easier, maybe exactly the assistant can help
the user at the start to set the times they do sport, work, sleep etc.. it is not
really easy to do that with buttons with times and stuff like that, it'd be cool to
choose to draw it too".*

**Chosen.** While setting up, the questions about the week (D42, with the day of
D69) come one card at a time, with Spark (D66) above them saying what the card asks
and how to answer it ("Question 2 of 6"), Back and Next under each card, and the
save button on the last; Spark waves at the first card, thinks through the others,
cheers at the last. Next checks the card's own fields first, as the form would.

Every time on the page can be drawn on a bar of the day, midnight to midnight, as
well as typed: the sun and the moon are dragged to when the student gets up and
goes to bed (the night is shaded, across midnight too), lunch is a block to move,
and on the sport, job and other cards a drag across the bar draws the block (a tap,
an hour from there), which moves, and whose ends move when it is wide enough to
hold them (on a phone an hour is about a dozen pixels: there the block only moves,
and the fields below set its length). Times snap to the quarter hour. The bar only
fills in the fields and follows them; it is hidden from screen readers, which keep
the fields.

Without the script, or after an error, the page is the form with every card, as
before. Outside setting up (from Settings), the cards are all shown, each with its
bar.

**Rejected.** A chat that types the answers in words (the AI is opt-in, D12, and
times are clearer drawn than written); a week grid to draw on here (the calendar is
one, after this page; a bar per answer is enough for one routine); answers kept
until the end in the browser (the cards are one form, sent once).

**Disclosed.** Drawing was checked in Chromium at 1280 and 390 px, with a mouse and
with touch emulated, not on a real phone. What Spark says was written for this page;
its French and Italian were not written by a native speaker (D34).

## D71. Spark floats on the plan's pages

*The owner, 2026-10-16: "make the assistant a floating cloud on the screen that you
can click to help you".*

**Chosen.** On every page of a plan, low on the right (above the tab bar on a
phone), Spark (D66) floats in a speech bubble. Tapped, it opens a sheet: "Hi! How
can I help?", a question for the coach (D65), and the ways Spark helps: setting up
the day and the week (D69, D70), a focus session, the study tips, the weekly note.
Where the browser knows popovers it is the same kind of sheet as the new task's;
without a script, or without popovers, the bubble is a link to the coach's page,
which links to the rest.

It stays out of the way: not while setting up, not on the coach's own page, not over
a running focus session's clock, and not over a form whose save button sticks to
the bottom of a phone's screen (Settings, the session log). Pages end with room for
it, so their last lines scroll above it.

**Rejected.** A chat window that opens on its own, or a bubble that speaks first
(D63: the character only carries the text, and a student came to do something
else); the tip of the day in the sheet (it is on Today and Focus already, and every
page would have to work it out).

**Disclosed.** Checked in Chromium at 1280 and 390 px, light and dark, not on a real
phone. Over the calendar it covers the corner of the last column's late evening; the
calendar scrolls.

## D72. The privacy page names who runs the service and who hosts it

*The owner, 2026-10-19: "let's do the pilot, guide me through hosting it".*

**Chosen.** Before the first student, the privacy page names the person who runs the
service and answers for its data (the controller, under the GDPR, D6) and the
company that hosts the server, with where. Both are settings of a deployment, not of
the code: `CPS_CONTROLLER`, typed in Render's dashboard as the contact address is,
and `CPS_HOST`, set in `render.yaml` beside the region it names ("Render (Frankfurt,
EU)"). Left empty, as on a computer or in the demo, the page says neither.

The blueprint asks for Render's plan by its new name, `0.5c-512mb` ("starter"
before Render renamed its plans on 2026-08-26; the old name still works, and the
price did not change).

**Rejected.** The owner's name in `render.yaml` (a setting typed once in the
dashboard keeps it out of the repository); one sentence with the host and the
region in English, which the French and Italian pages would have shown untranslated
around it.

**Disclosed.** This is what a privacy notice needs to say who and where; it is not
legal advice, and the page as a whole is still the owner's to read and approve (C2).

## D73. Calm glass: a sky of the hour, clear glass, a garden, two type faces

*The owner, 2026-10-19: "let's make it even better with some liquid glass design, ask
me for font choices, doodles, and other decorations that streamline the UX and make
it more soothing to the eye, more addictive to watch, less information overloading".
The options were drawn on a design canvas, each on the same Today screen; the owner
chose clear glass, Bricolage Grotesque with Figtree, the garden, and a sky that
follows the day and drifts slowly.*

**Chosen.**

- *Less on Today.* On a phone, the next session sat below the weekly review, a tip,
  the cards and the account card, under the fold.
  Now: a greeting of the hour, the week and the streak as two chips, the next
  session large with Start focus (its reasons folded under "Why now"), two sessions
  to report with the rest folded, Spark's tip of the moment, three sessions coming
  up. The weekly review, the cards, the account and budget offers, the warm-up,
  Tasks, Exams and the weekly note are under one "More" row that names what it
  holds. The exam question (D56), the semester's end and warnings stay in sight.
  Everything is still in the page, without a script; "More" stays open when the
  panel is redrawn.
- *The sky.* Behind a plan's pages, four soft colours of the part of the day in the
  plan's time zone (morning from 5, afternoon from 12, evening from 18, night from
  22; `service.sky_at`), in light and in dark, drifting over 90 seconds. A page left
  open moves on with the clock.
- *Clear glass.* Cards, the bars, the sheets and Spark's button are glass: a light
  fill, a blur of what is behind, a bright edge on top. On a phone the tab bar floats
  as a pill, with Spark in a round drop beside it. The calendar is glass too, but
  nearly solid, as it is read closely.
- *The garden.* Line drawings in the margins grow with the streak
  (`service.garden_stage`): a seedling, then two leaves and a bud from 3 days, a
  vine from 7, a flower from 14. They sway a little. Decoration only: the streak's
  number is on Today and Progress.
- *Type.* Bricolage Grotesque for what is large, Figtree for text. Both are files of
  the app with their licences (SIL Open Font License), so a page asks nothing of
  any other server; the policy now allows fonts from the app itself.

**Rejected.** Fonts loaded from Google (every page would give Google the student's
address, against the privacy page); a refraction filter on the glass (Chromium only,
heavy on a phone); hiding the session's instructions behind "More" (what to do is
the point of the card); the garden growing as a reward that can be lost (the streak
is forgiving, D9: a missed day does not wither anything, the stage follows the
streak as it is counted).

On a wide screen the garden sits in the corner of Today's panel, above the greeting,
where its text does not scroll over it; the vine stays off the plan screen, where the
calendar would hide it. Settings' sections, the statistics' tiles and the badges are
glass too.

**Disclosed.** Checked in Chromium at 390, 1280, 1440 and 1920 px (every page of a
plan at 1280 and 1920), light and dark, still and in motion, not on a real phone. Browsers without backdrop blur show the glass as a
plain translucent fill; a device that asks for less transparency gets solid
surfaces. The drift, the sway and the panel's motion stop under reduced motion.
The service worker keeps the fonts and the drawings for offline use.

## D74. The app's icon: the week as a ring of seven days

*The owner, 2026-10-19, after four rounds of drafts on a design canvas (a spark and a
sprout; a brain and a spark; one shape with light and depth; the ring branched six
ways and drawn in six colours): "can we pick T and make it update each day of the
week and its colours throughout the day? but choose the white/black one according
to the user dark/light mode as a static icon". The brain with its recall curve (D33)
read as overlapping shapes, not as one mark.*

**Chosen.**

- *The drawing.* Seven arcs in a ring, one per day, Monday just right of the top and
  the days going clockwise. The days before today are filled, today's ends in a gold
  light, the days to come are faint. At first sight a ring filling up; once the app
  is used, the student's week with today in gold. Each day keeps its colour whatever
  day it is (Monday is always Monday's colour). Drawn on the server
  (`cps.web.icon`), as the charts are, with SVG attributes only.
- *Live where it can be.* On a plan's pages the top bar and the browser's tab show
  today's icon: the weekday in the plan's time zone (`service.look_of`), in the
  colours of the sky of the hour (D73: blue and mint in the morning, indigo and
  white in the afternoon, violet to orange in the evening, deep blue at night). A
  page left open swaps it as the hour and the day turn (`ui.js`, by the device's
  clock, as the sky). It is served at `/icon/week-<day>-<look>.svg`, the same for
  everyone and kept a day; the service worker keeps each one shown, for offline.
- *Still where it must be.* What a phone puts on its home screen cannot change once
  installed, so it is the drawing made once, on a Friday: white for light mode,
  graphite for dark. A plan set to Light or Dark (D21) installs its own; one that
  follows the device asks for the graphite one while the device is dark (`pwa.js`
  sets the manifest's `?look=dark` and the dark Apple touch icon). The SVG icon a tab
  shows outside a plan (the home page, the setup) carries both drawings and picks by
  the device's mode. `icon.main()` writes the SVGs; the PNGs are those drawings
  rendered by Chromium at 32, 180, 192 and 512 px, and a test fails if the SVGs and
  the module disagree.

**Rejected.** A home-screen icon that changes by itself (a web app cannot; native
apps cannot either, Apple's own Calendar aside); counting only the days actually
studied (a missed day would show as a gap on the icon, against the forgiving streak,
D9); the brain, the head, the neuron and the other drafts (each read as a picture of
a thing, not as the week the app plans); the spiral and the comet (prettier, but
they say "time" without saying "your week").

**Disclosed.** Checked in Chromium only: the top bar at 1280 px in light mode and at
390 px in dark mode, the tab's icon address, the swap of both after the clock moves
on, the graphite icon asked for on a dark device, every day in every look at 64 and
28 px, and icon.svg switching by mode under the app's Content-Security-Policy.
Not checked on a real phone. Safari shows the white PNG in its tab; whether iOS
keeps the dark Apple touch icon when the device is dark at the moment of "Add to Home
Screen" was not tried. An installed icon stays as it was installed, even when the
mode changes later. The server counts the day in the plan's time zone and the page in
the device's, as the sky does, so a student abroad may see the icon change a few
minutes after the page loads.

## D75. The pilot on a server of its own, at Hetzner

*The owner, 2026-10-19, after comparing hosts (Hetzner, Fly.io, Scaleway, Render,
Railway): "ok let's find a better best hosting website even if it costs more, i am
willing to pay more for a few months to get knowledge on how to develop an app",
then "i dont have a domain but lets use the german one then".*

**Chosen.** The pilot runs on a Hetzner Cloud server in Germany that the owner runs
(steps in `docs/HETZNER.md`):

- *One image* (`Dockerfile`): the app as its own user, the database in `/data`, a
  health check. Built on the server, so an Intel or an Arm machine both work.
- *Caddy in front* (`deploy/hetzner/compose.yaml`, `Caddyfile`): the only container
  the internet reaches; it gets and renews the Let's Encrypt certificate, sends
  HTTP on to HTTPS, compresses, sets HSTS, and keeps no access log (a plan's address
  is its key). The app is reachable from Caddy only, which is why trusting the
  forwarded headers (`--behind-proxy`) is safe there.
- *The server set up at its first start* (`deploy/hetzner/cloud-init.yaml`): a user
  `deploy` with the owner's key, no passwords, no root, a firewall open to SSH, HTTP
  and HTTPS only, fail2ban, security updates every night with a restart at 04:30
  when one needs it. A key line left unfilled keeps root's sign-in rather than
  locking the owner out.
- *Deploys from GitHub* (`.github/workflows/deploy.yml`, `deploy/hetzner/deploy.sh`):
  after CI passes on a push to `main`, or by hand. GitHub's key may run the deploy
  script and nothing else; the script builds, starts, and waits for /health, and
  fails the run if the app does not answer. The workflow skips itself until the
  repository has its settings.
- *Backups*: the app's own daily copies in the data volume (seven days, as before)
  and Hetzner's daily backups of the whole server (seven, stored apart).
- *Checks in CI*: the image builds and answers /health; the compose file and the
  Caddyfile are valid; `tests/test_deploy.py` keeps the settings, the port, the
  volume and the safeguards in step with the app.

**Why.** The owner wants to learn how an app runs, and a server of one's own is what
every platform hides; the data stays with a European company; the cost is about the
same as Render's. The files are ordinary Docker, so the pilot can move to Fly.io,
Scaleway or another server without rewriting anything.

**Rejected.** Render for the pilot (kept for the demo, and `render.yaml` kept as a
second way); Fly.io and Scaleway (close seconds: Fly hides the server, Scaleway
costs more for the same); images built by GitHub and pulled from a registry (one
more account and a published image, for a build that takes minutes on the server);
an updater that pulls new images by itself (a deploy should follow CI, not a timer);
a managed database (SQLite on the volume is what the app is built for, D2).

**Disclosed.** Checked here with Docker: the image (built in a copy of the Dockerfile
that trusts this sandbox's network certificate; the committed one is unchanged),
Caddy on `localhost` with its own certificate, HTTP sent on to HTTPS, the security
headers, a plan made over HTTPS and still there after the containers were removed
and started again, the app's copies made at start, no plan address in either log,
`--wait` waiting for health. The cloud config was validated by Ubuntu 24.04's own
cloud-init, and its user, key and clone steps were run in an Ubuntu container. The
workflows pass actionlint. Not checked: a real Hetzner server, a Let's Encrypt
certificate (it needs a real domain), the firewall, SSH rules and automatic updates
(they need a running system), and the Deploy workflow (it needs the owner's
settings). Prices and the console's wording come from third-party pages and are
marked to be checked.

## D76. The sky follows the sun where the plan lives; the icon takes its colours

*The owner, 2026-10-19, with five screenshots of the demo: "ok the liquid glass is
not everywhere, the header is black and the rest is liquid glass, the icon does not
follow the theme, make sure the shades respect the day time (rn it's 16h30 so what
colour should it be?)". The sky of D73 changed at fixed hours (evening from 18 all
year), so at 16:30 it was still "afternoon", and in summer it turned to evening two
hours before the sun set. The icon's colours (D74) were the sky's named moments,
and one look served light and dark alike.*

**Chosen.**

- *The sun where the plan lives* (`cps.daylight`). The place is the plan's time
  zone: the tz database gives each zone the coordinates of its main city
  (`zone1970.tab` in the `tzdata` package, already a dependency); a zone without
  one falls back to its UTC offset at latitude 45. The sun's height comes from
  NOAA's general solar position equations. Folded into one number from 0 to 4: deep
  night 0, sunrise 1, the day's highest sun 2, sunset 3, then dusk back to night;
  daytime is measured against that day's highest sun, so a low winter noon is noon
  too. Checked against a published table: for Paris on 19 October 2026,
  generalblue.com gives sunrise 08:16 and sunset 18:54; the model gives 08:15 and
  18:55 (`tests/test_daylight.py`).
- *The sky's colours* (`cps.web.sky`). Twelve moments along those positions (deep
  night, blue hour, dawn glow, sunrise, morning, day, the same day with the sun
  lower, late afternoon, golden hour, sunset, dusk, late dusk), each a base and four
  soft lights, in light and in dark, blended in 400 steps; no step moves a colour
  channel by more than 12 of 255. A page links `/sky/<step>.css`, the same for
  everyone and kept a day, and the calm-glass rules paint its variables. An open page
  asks `/look/<token>` every five minutes and swaps the sheet and the icon; without a
  script, the page keeps the sky of when it loaded; without the sheet, D73's four
  skies of the hour are still in `style.css`.
- *What 16:30 looks like*: in Paris on 19 October the sun is 20 degrees up
  (step 236): the clear blue of the day with mint below. The light warms from 16:52,
  the golden hour starts at 17:49, the sunset's rose and coral are at 18:54, violet
  dusk at 19:30, indigo from 20:08, and night is complete at 20:41
  (`tests/test_daylight.py` checks each time). The greeting still follows the clock
  ("Good afternoon" until 18), as people say it.
- *The icon in the sky's colours, light or dark.* The live icon's tile is the sky
  at that step, pale in light mode and deep in dark mode; the days take the hues of
  its four lights, made strong enough to read at the size of a tab; today keeps its
  gold. It is served at `/icon/week-<day>-<step>-<mode>.svg`, where the mode is the
  plan's appearance (D21): Light or Dark draws that one, Automatic carries both and
  shows the one the device is in, as `icon.svg` does. The icons a phone installs
  stay white and graphite (D74).
- *Glass everywhere* (AUDIT 68): the top bar, the fields and the save bar are glass
  on every page of a plan, in both modes.

**Rejected.** The device's clock for the sky (D73's way: it knows the hour, not the
sun, and a device abroad shows its own hour); the browser's location (a permission
prompt, and the student's whereabouts sent to the server, for a colour); fixed hours
for sunrise and sunset (wrong by hours in June and in December); the sun worked out
by the page's script (logic in the front end, and a page without its script would
have no sky); an hourly palette (steps a student would see jump).

**Disclosed.** The sun is the zone's main city's: under Europe/Paris, a student in
Brest sees Paris's sunset, about half an hour before their own (a test measures it);
a zone without coordinates is placed at latitude 45. Checked in Chromium only: the
plan page at 1280 px at nine moments of 19 October and at 390 px at four, in light
and dark, the top bar's glass and the icon at each; not on a real phone. An open
page follows the sun within five minutes. Since this decision, D73's "four soft
colours of the part of the day" and D74's "colours of the sky of the hour" are the
sun's, as above.
