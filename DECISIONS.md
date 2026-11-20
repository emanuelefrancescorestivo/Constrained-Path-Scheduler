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
  nearly solid, as it is read closely. (Since D78: moderate glass, half as opaque.)
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

## D77. Spark's tour: each section shown once, the first time it is opened

*The owner, 2026-10-19, after setting up a plan on the demo: "i would like the user to
receive a first tutorial by Spark when they first enter, or else it feels too
overwhelming, a tutorial that guides u in each section and highlight where to click
and to do what... like standard app tutorials".*

**Chosen.**

- *One short tour a section, on its first visit.* The calendar while setting up
  ("week": the week itself, moving a session, blocking time, what comes next, the
  last step), Today once set up (the next step, the week and the streak, reports,
  the calendar, a new task, the other sections, Spark), then Tasks, Focus, Community
  and Progress, two or three steps each. Seven steps at most: a long tour is skipped,
  and each section explains itself when the student gets there.
- *What a tour looks like.* The page dims, the part a step is about stays lit with a
  bright edge, and Spark's bubble sits beside it: a title, one or two sentences on
  what it is for and what to do, "2 of 5", Skip the tour, Back and Next (Got it at the
  end). The light glides from one step to the next and follows the page as it
  scrolls; the calendar scrolls to the session a step is about.
- *Who decides what.* The server says which tour a page shows (`service.tour_for`:
  its section's, not yet seen) and remembers the ones finished or skipped (the
  plan's `tours_seen`, `POST /p/<token>/tour`). The page carries the steps, each
  naming what it lights (`data-tour` marks in the templates), in the plan's
  language. `tour.js` only lays them out: it passes over a step whose part is not on
  screen (a phone has no top bar's links, a new plan nothing to report yet), and a
  tour with nothing on screen stays unseen for a later visit (Focus while a timer
  runs).
- *Settings, Appearance: "Spark's tour, Show it again"*: every tour comes back,
  Today's at once.
- *Keyboard and assistive technology.* The bubble is a modal dialog; the rest of the
  page is inert while it shows; Next has the focus, Tab stays on the bubble's
  buttons, the arrows go back and forth, Escape skips. The step's words are
  announced as they change. Under reduced motion nothing glides.

**Rejected.** One long tour of every section at the first visit (the owner asked for
less at once, not more); a video or a carousel of screenshots before the app opens
(it shows a made-up plan, not the student's own, and is forgotten by the time it is
needed); a tour library (a dependency for 200 lines of layout); tours that cannot be
skipped; tips that come back by themselves after being skipped (Settings brings them
back on request).

**Disclosed.** Checked in Chromium only: every tour walked to its end at 1280 px
(Today in light and dark, Tasks, Progress, the setup calendar) and at 390 px
(Community in dark), each remembered after it ended and absent on the next visit,
no script errors. Not checked with a screen reader or on a real phone. Without a
script there is no tour; the pages are complete without it. The French and Italian
were written here, not by native speakers.

## D78. Moderate glass on the calendar, and an accent that follows the sky

*The owner, 2026-10-19, with screenshots of the demo in Italian at 18:16: "if i put
full screen the adapting background does not work; in the main page id like to try
a moderate version of liquid glass for the calendar, and need to make those electric
blue buttons that are like a shard in the eye, adapt together with the theme, in
this image maybe they could be purple, or a stronger colour than their immediate
background".*

**Chosen.**

- *The calendar, moderate glass.* D73 made it "glass, but nearly solid" (80% white,
  84% graphite). Now half (50% white, 52% graphite) with a stronger blur, so the
  sky's colour shows through the grid; the events keep their solid tints and read as
  before. The strip of day names and the hours stay frosted at 86%, as sessions
  scroll under them.
- *An accent of the moment.* Buttons, links, what is chosen and the tab bar's
  current tab took Apple's blue all day. Now each moment of the sky (D76) has a hue:
  indigo at night, violet at dawn, a deeper blue by day, indigo in the late
  afternoon, violet in the golden hour, purple at sunset, violet at dusk, never red
  or orange (red is for exams and today). Its lightness is solved, not picked, and a
  test checks all 400 steps in both modes. In light mode white text on a button
  keeps 4.8:1 (WCAG asks 4.5, which Apple's #007aff does not reach, at 4.0), and so
  does accent text on the sky. In dark mode a button keeps 3.3:1 with white text
  (Apple's own dark blue has 3.65), and accent text is a lighter "ink" of the same
  hue at 7.5:1 against the sky, so a blue link does not sink into a blue sky. It is
  served in `/sky/<step>.css` with the sky.
- *Full-screen focus keeps the sky* (AUDIT 71).

**Rejected.** A fixed purple (the owner's example is one moment; at noon purple on a
blue sky is as loud as blue was); the accent picked from the sky's own lights (at
dusk they are pale, and the button would sink into its background, the opposite of
the ask); dark text on light buttons in dark mode (no other button in the app works
that way); the calendar as clear glass like the cards (its small text over a busy
sky did not read).

**Disclosed.** Checked in Chromium only, at 1600 px, light and dark, at 18:16 on 19
October (Paris), and full screen in both modes. The hues are a choice of taste, the
owner's to review; only the contrast is measured. The pages without a sky (the home
page, the setup's first step) keep Apple's blue.

## D79. A month in the calendar; the focus ring and the cursor

*The owner, 2026-10-20: "we could allow a monthly calendar view"; "i also dont like
the cursor when hovering on the calendar"; and, with a screenshot of the focus timer
at 0:29:45 with its ring nearly closed, "maybe you can make it end the full circle
when the minute ends?"*

**Chosen.**

- *Month*, beside Day, 3 Days and Week (not while drawing a week by hand, which is
  about hours). Six weeks from the Monday on or before the month's first day, as
  Apple's and Google's calendars show it. Each day lists what tells most first, exams
  and tests, then study sessions, then classes, as many as fit (three on a computer)
  and how many more; the student's own busy times, the same every week, stay in the
  views with hours. On a phone the rows become coloured dots, as Apple's Calendar
  does. Tapping a day opens it in the Day view, where sessions are moved; the arrows
  go a month at a time. The calendar's data route now gives up to 42 days.
- *The ordinary arrow* over empty time on the calendar, in place of the spreadsheet's
  thick cross; sessions keep the grab hand.
- *The focus ring closes as the minute turns* (AUDIT 72): drawn from the same clock as
  the digits, every frame; the digits turn on the second.

**Rejected.** A month with every class in it (a weekly timetable fills every cell the
same way and hides the exams); dragging sessions in the month (no hours to drop on);
a pointer hand over empty time (it says "link", not "place").

**Disclosed.** Checked in Chromium only, at 1440 px in light and dark and at 390 px:
the month drawn, the next month, Today, a day opened in the Day view, no script
errors; the ring sampled once a second for 40 seconds, within the second of the
digits.

## D80. Insights: what a student's own data says, unlocked with use, and a pace that never asks for more

*The owner, 2026-10-21: "let's build the analytics first, and again the key here is
to get the user hooked, nearly addicted, because we do it for the right cause:
encouraging students and motivating them to study efficiently and avoid burnout".*

**Chosen.** The first four measures of ANALYTICS.md §6, which need no new data,
and a pace check, as cards on Progress ("Your insights") and one line on Today
(`cps/insights.py`, pure functions; `service.insights_view` gathers and words them).

- *Starting on time*: of the planned sessions started with the focus timer, the share
  begun at most 10 minutes after their planned start (early counts), and by how much
  the late ones were late (the median). The timer's start is now kept with the
  session's log (`started`), so it counts from logs written from 2026-10-21 on.
- *Focus*: of the time the timer ran, the share on the page (D17), week by week; the
  longest session without leaving the page.
- *When you remember best*: self-tests' answers by part of the day; a best part is
  named only when two parts have at least 3 answers each and differ by 15 points of
  100 or more.
- *Your memory against the forecast*: how often the self-tests found something
  remembered (any answer but "nothing", as FSRS and the pilot's calibration count it,
  D55) against how often the plan predicted, rounded to 5 points (D24). Comparing
  the answers' own scale (nothing to all) with the forecast was tried first and
  rejected: it is not a probability, and it put the demo's student far further
  below the forecast than their answers were.
- *Your pace*: hours this week against the student's own limit and the mean of their
  last four weeks, and days of study in a row. Ten days in a row suggest a day off,
  with a link to the week's settings; over the limit, or 1.3 times the usual weeks and
  at least 2 hours more, says so and suggests a free evening. It never asks for more:
  below the usual weeks, it only states the hours.

What keeps a student coming back, in that spirit: each card *unlocks with use* and
says how many more sessions or self-tests it needs ("Answer 6 more self-tests to see
this"), with a bar filling; a card just unlocked shows "New" for two days, and becomes
Today's line; a longest session is celebrated once (the confetti of D30) when it is
set this week, beats an earlier best and lasts at least 20 minutes; a small line shows
each measure over eight weeks, with its direction. On Today, the pace comes first
when it warns, then a record, then a card just unlocked; otherwise nothing, so the
line stays worth reading. Every threshold here (3, 6, 5 answers; 10 minutes; 15
points; 1.3 times; 10 days; 20 minutes) is a stated choice, not a result.

**Rejected.** Rankings or comparisons with other students (D16); one combined score
(ANALYTICS.md §2); a pace that asks for more hours when a week is light (the plan
already holds the work; a light week may be a rest the student needs); a best time of
day from fewer than 3 answers a part; insights about sleep, screen time or place
(not recorded, and asking is surveillance).

**Disclosed.** Small numbers: a share of 3 sessions moves in thirds, and the cards say
how many they rest on. Time away is time off the page, not proof of distraction. The
forecast uses population weights, so a gap is a hint about the model as much as about
the student. Starting on time sees only sessions started with the timer. Checked in
Chromium only, at 1280 px in light and dark and at 390 px, on the demo's student
(`cps demo`): the pace, two locked cards, no clear best time (52 % against 46 %), and
the forecast 10 points below.

## D81. A course's topics: pasted once, read by rules, shared with classmates

*The owner, 2026-10-21, after reading docs/CURRICULUM.md §3a: "build the classmate
sharing plus paste reader".*

**Chosen.** Ways A and C of CURRICULUM.md §3a, without a model and without a new
dependency.

- *The paste reader* (`cps/syllabus.py`, pure). The student copies a course's program
  and pastes it; rules cut it into topics. "Week 3: ...", "Semaine 3", "Settimana 3",
  "Semana 3", "Woche 3" give a week; "Lecture 5", "Cours 5", "Leçon", "Séance",
  "Lezione 5", "Clase", "Aula", "Vorlesung", "Hoorcollege" give a lecture; a line
  under one belongs to it until the next. Bullets and numbering are dropped ("1.",
  "2)", "1.3", "a)", "iv."), but a bare number stays ("3 laws of motion"); a
  one-letter abbreviation counts only before a colon or a dash ("L1 norm" is a
  topic, "L3: Banach spaces" a lecture). Headings, a pasted table's header row and
  links are skipped; cells of a pasted table are joined; bullets run together on one
  line are split. At most 60 topics of at most 120 characters.
- *Placed on the weeks of lectures.* A topic's week is the course's n-th week with
  lectures in the timetable (weeks without them, such as a break, are not counted);
  its lecture, the n-th lecture's week. A list that names no week is spread over the
  weeks in order, each taking a share of the topics as large as its share of the
  lecture hours (D50). The student sees each topic with its week before saving
  ("Read it"), and can change the text and read it again.
- *Shown where it is studied.* A session reviewing "the week of 19 Oct" shows "In the
  syllabus: Quadratic forms" on Today and on its own page; "what was taught before"
  shows the weeks begun before the plan. The session's title, and so its id, does not
  change (`_week_label` names a week in one place). A session of a course without
  topics links to the course's page.
- *Shared with classmates, opt-in.* Saving can offer the list to students of the same
  university (as the profile writes it, D39) who take a course of the same name,
  without a name. Their course page shows up to three lists, the most taken first,
  with the first three topics; one tap takes a list, placed in their own timetable,
  and counts one use (once per plan). Today names "Topics from a classmate" in its
  fold while a course has none and a list waits. Not sharing again, or deleting the
  plan, withdraws the list; copies already taken stay with those who took them. The
  shared list is in the plan's copy of its data (D43). Settings links to the topics.

**Rejected.** A model for this first step (CURRICULUM.md §3a: E comes after, behind the
AI switch); reading a PDF on the server (a new dependency; pasting avoids it); dates
in a program (a date stays in the title: day-month orders differ by country); sharing
by default (the owner's opt-in; a list says nothing about who wrote it, but a student
may not want to give their work away unasked); showing who shared a list; a rename
of the plan's weekly topics after the syllabus (it would change sessions' ids, and
their reports with them).

**Disclosed.** No real syllabus was tried: the rules come from the shapes listed above,
with a case per language in `tests/test_generality.py`. Week numbering follows the
timetable's weeks with lectures; a program that counts a break week as a week is one
week off after it, which the preview shows. A shared list has no report button yet:
it is only titles, without links, read by the same rules. Classmates are matched by
the university and course names as written; two spellings of one course do not
match. Checked in Chromium only, at 1280 px in light and dark and at 390 px: the
course list, a pasted program read, saved and shared, the session's syllabus line, a
second student seeing the list on Today and taking it.

## D82. Your memory: progress that only spaced recall can grow

*The owner, 2026-10-21, with a screenshot of the insights: "i need more graphs,
numbers, colours, stuff that makes u addicted to progress but also that addiction
MUST DIRECTLY TRANSLATE TO QUALITY OF STUDYING, find the match from empirical
research and everything must converge to the ultimate goal of making study
efficient, fun, addictive (how do we retain memories? by intensity or repetition,
hence study something and make it intense=interesting, or repeat solving exercises
even with solutions aside several times and spacing such practice!)".*

**The match, from the checked references (docs/REFERENCES.md).** Practice testing and
spaced practice are the two techniques rated of high utility in a review of ten
([ref:dunlosky2013]); practice tests beat restudying ([ref:adesope2017]) and quizzing
raised classroom achievement ([ref:yang2021]); the best gap between reviews grows
with how long the memory must last ([ref:cepeda2006], [ref:cepeda2008]); left alone,
most students reread rather than practise retrieval ([ref:karpicke2009]); monitoring
progress, physically recorded, helps reach goals ([ref:harkin2016]). So the numbers to
watch grow must be ones that only spaced retrieval moves. Hours, pages and minutes on
the app are not among them. On "intensity": no checked reference here says that
interest or emotion strengthens memory, so the app does not claim it. What it can
show is effort at the right moment: in the memory model, a recall made when
recall had fallen adds more than one made at once (FSRS's spacing term, `memory.py`;
`tests/test_learning.py` holds it for FSRS-6).

**Chosen.** A "Your memory" section at the top of Progress (`cps/learning.py`, pure;
`service.learning_view`, from the plan's FSRS replay):

- *Memory strength*: the median stability of the topics taught so far, in days
  (the days until a topic's predicted recall falls to 90 % without a review), as a
  big number and a line over eight weeks, "up from" the first. Milestones (a week,
  two, a month, two, three, six) get the confetti once (D30) and a badge.
- *Today's map*: every taught topic as a tile, grouped by course, coloured in four
  levels of today's predicted recall (90 % or more, 80, 70, below), with the count of
  each and, in its tooltip, the course's syllabus topics (D81). A self-test moves a
  tile up; time without one lets it fade.
- *Memory days added*: the stability the week's reviews added, summed over topics, in
  columns over eight weeks. A review after a gap adds the most; one crammed right
  after the last adds little; a forgotten topic's loss counts against it.
- *Recall reps*: self-tests answered, exam practice done, flashcards answered, per
  week, with this week's split: repeated retrieval, the user's "repeat solving
  exercises several times".

The colours: one teal ramp of four steps, an ordered scale checked against the light
and the dark surfaces (the light end clears 2:1, the steps are monotone and apart),
with its own steps in dark mode (the strongest brightest), and the text on each
tile in ink or white at 4.5:1 or more; levels are also written as numbers, and a
topic to review has a dashed outline, so colour never says it alone. Teal is not
used elsewhere in the app; red stays for exams and today.

**Rejected.** Points for minutes or sessions (they reward presence, not memory); a
ranking against classmates (D16); counting unreported sessions as reps (a rep is an
answer or a tap; the plan's own convention, D44, still decides the model's state);
claiming that intensity or interest builds memory (no checked reference); a single
"learning score" (ANALYTICS.md §2).

**Disclosed.** All of it is the memory model's estimate with average weights, said on
the section and in "How it is measured", not a measurement of the student; a topic
starts "seen once and shaky" (D50), so the first weeks' strength is low by
construction and its growth looks steep. Recall reps count what was reported, not
what was learned. Checked in Chromium only, at 1280 px in light and dark and at 390
px, on the demo's student: strength 11 days, up from 0.4, 7 topics strong, 8 good,
3 fading.

## D83. A whole course page: its topics, its assessment and its credits, by rules

*The owner, 2026-10-21: "create the study plan based on the official syllabus of unis
(is there a common structure they share so that we can parse it without API calls?
or use a very cheap model?)".*

**The common structure.** In the European Higher Education Area, the ECTS Users' Guide
recommends the same items for every course unit in a catalogue: its code and title,
type, cycle, year and semester, number of ECTS credits, lecturers, learning outcomes,
mode of delivery, prerequisites, course content, recommended reading, planned
learning activities and teaching methods, assessment methods and criteria, and
language of instruction ([ref:ects2015], page 57); one credit is 25 to 30 hours of
work (page 10). It fixes neither their order nor their layout, so every university
writes them its own way, in its own language. That is enough for rules: the headings
recur ("Course content", "Contenu du cours", "Programma", "Contenidos", "Inhalt",
"Inhoud"; "Assessment", "Modalités d'évaluation", "Modalità d'esame", "Sistema de
evaluación", "Prüfungsform", "Toetsing"), and the weights of an assessment are
written as percentages.

**Chosen.** The paste reader (D81) takes a whole course page too (`cps/syllabus.py`):

- *Sections*: a line that is one of about a hundred headings, in seven languages,
  opens a section (content, learning outcomes, assessment, prerequisites, reading,
  teaching methods), with what follows it on the line ("Assessment: written exam
  70 %"); numbered headings count ("3. Course content"), bulleted lines do not (a
  list item "- Evaluation: kernel and image" stays a topic). The longest section of
  each kind is kept, so "Exam: 15 January" above the assessment's list does not hide
  the list.
- *Topics* come from the content section when it has one with a topic in it, else
  from all the text, as before: learning outcomes and reading lists are not topics.
- *Assessment*: its parts and their percentages ("Written exam 70 % and coursework
  30 %", "(60 %) examen final"), kept only when they add up to 100, within 1.
- *Credits*: "6 ECTS", "ECTS: 7,5", "9 CFU", "5 LP", "6 EC", from 1 to 30, and the
  hours of work they stand for, 25 to 30 an ECTS credit, lectures included.

The course's page shows the assessment and the workload above its topics ("6 ECTS
credits: about 150 to 180 hours of work in all, lectures included"), in the preview
and once saved; saving the topics' own lines again keeps what the page said.

**Rejected.** A model for this step: the rules read the common structure at no cost;
the model stays the route for what they cannot read (PDFs, prose, tables), behind
the AI switch, CURRICULUM.md §3a, at most $0.40 a ten-page PDF with the default model
and $0.01 with the smallest (`benchmarks/syllabus_cost.py`). Using the assessment's
weights or the credits in the plan: exam weights exist (D49, set by the student), and
the credits' hours are a typical workload, not the student's; both are shown, and the
student decides. Sharing the assessment and credits with classmates: the shared list
is topics only (D81); the next step if students ask for it.

**Disclosed.** No real course page was tried: the headings are the ECTS guide's items
in the words of seven languages, with a case per language in
`tests/test_generality.py`. A page without headings is read whole, as before. A
percentage that is not a weight (an attendance rule) makes the sum miss 100 and the
assessment is not shown. Outside the EHEA (the United States, for one) credits are
not ECTS and are not read.

## D84. Flashcards from notes: read by rules, picked by the student, the model on request

*The owner, 2026-10-21: "to create flashcards from content (people will pay for
this)".*

**Why.** Practice testing is one of the two techniques rated of high utility in a
review of ten ([ref:dunlosky2013]); left alone, most students reread instead
([ref:karpicke2009]). Writing cards is the step that stops them. Reading the cards out
of notes the student already has removes most of it, and the flashcards (D29, FSRS-6)
then space the retrieval.

**Chosen.**

- *The reader* (`cps/cardmaker.py`, pure, no model): five shapes, in seven languages'
  words. A question and its answer ("Q:/A:", "Question/Réponse", "D:/R:",
  "Pregunta/Respuesta", "Frage/Antwort", "Vraag/Antwoord", "Pergunta/Resposta"; a line
  ending in "?" and the next); a term and its definition ("Term: definition", "–",
  "=", "::", "→"); a list under a heading ending in ":" (two to eight items, the card
  says how many); a word in bold, highlighted or an Anki cloze, as a gap; a definition
  sentence ("An eigenvalue is a…", "Une valeur propre est un…", "Un autovalore è un…",
  "Ein Eigenwert ist eine…"). Not cards: sentences about "it" or "this", "Note:",
  "Date:", a week of the syllabus, a link. A definition sentence is tried before a
  term's "=", so "Ein Eigenwert ist eine Zahl λ mit Av = λv" is a definition. Each
  card once, at most 200 (the flashcards' own limit for one paste).
- *The page* (Flashcards → "Make cards from your notes", or "Make flashcards from these
  notes" on a text note): paste, read, see every card found with its kind, untick
  what you would not ask yourself, choose the course, add. Nothing is added before
  the student chooses. "Also the other way round" adds the reverse of each term and
  definition.
- *The model, on request*: with AI on (D12), "Read them with AI too" sends the notes
  and the course's name to the model, which writes at most 40 cards from the text
  alone (its instructions forbid adding facts); its cards come first, marked "Written
  by AI", with a line asking the student to check them; the rules' cards stay below.
  The answer is held to a schema and to the flashcards' limits; a wrong or empty
  answer leaves the rules' cards. The privacy page names what is sent.

**What it would cost, and the paid tier.** One call at most, as the app counts it
against the monthly cap before making it (a full box of 20,000 characters, the
answer's whole cap): $0.20 with the default model, $0.10 with the middle one, $0.005
with the smallest (`benchmarks/syllabus_cost.py`). The rules' cards are free and stay
free. The model's cards are the natural paid feature the owner named; payment,
prices and accounts are the owner's to decide (CLAUDE.md), so nothing about paying is
built: the switch is the existing AI setting and its caps.

**Rejected.** Making cards without the student's choice (a card nobody would ask
themselves is noise in their reviews); a model by default (cost, privacy, and the
rules already read the usual shapes); cards from photos of notes (that needs the
model's vision or an OCR dependency; the next step if students ask, behind the same
switch).

**Disclosed.** The shapes come from how notes are commonly written, not from students'
real notes; no model answer has been tried on real notes (the tests use a fake
provider). A card made by rules is only as good as the line it came from; the
preview is there for that.

## D85. Notes for a study group: text or photos, seen by its members only

*The owner, 2026-10-21: "the note sharing system in groups".*

**Chosen.** A set of notes (D26) can go to one of the student's study groups (D28),
beside "Only me", "Followers" and "Everyone". Notes can now be written as text (up to
20,000 characters) beside or instead of the photos; the text is what flashcards are
made from (D84).

- *Who sees a group's notes*: its members, and only while the author is one; a note
  leaves the group with its author (it stays theirs, in their diary). Never across a
  block. A group note is in no feed and not in the library, which are for followers
  and everyone; its photos follow the same rule (the server checks who asks).
- *Where*: the group's page lists its notes, newest first, with "Share notes with the
  group" (the form opens with the group chosen) and, for text notes, a link to make
  flashcards from them. The author can move a note to the group, or out of it, from
  its menu.
- *Stored* in the posts table, as before: `visibility = "group"` and the group's id in
  the post's data; no new table, so the data copy (D43) and deleting a plan already
  cover them.

**Rejected.** A shared, editable document per group (a different product, with
conflicts to resolve); notes visible to former members (a group's notes are for
those who study together now); notes in several groups at once (one choice, like the
other visibilities; post again for a second group).

**Disclosed.** Group notes have the library's moderation (report, block, hidden after
three reports) and the same "own work" checkbox. Checked in Chromium only.

## D86. The workload check: what a course's credits stand for, against the plan

*The owner, 2026-10-21: "build the workload check".*

**Chosen.** On each course's page (D88), the hours its ECTS credits stand for, 25 to 30
a credit, lectures included ([ref:ects2015], page 10), against the hours the student
has for it: the course's lectures in the timetable from the plan's first day, and
the plan's study sessions for it, done (not skipped) and to come. One sentence says
where the total falls ("97 h in all, 53 h under what the credits suggest"; "within";
"more than"), a bar shows lectures and study against the credits' band, and "How it
is counted" says what is in and out. The credits come from the course page the
student pasted (D83), or are typed on the course's page (1 to 30, halves allowed;
typed credits win, empty takes them off). The Courses list notes a course with fewer
hours than its credits suggest.

**Rejected.** Raising the plan's hours for a course below its credits: the credits
describe a typical student's workload, not this student's, and the app never asks for
more (D80's pace). The plan's hours follow the weekly hours the student sets and the
exams' dates; an exam's weight (D49) only reorders self-tests between topics fading
together, so it is not offered as a way to more hours. Counting hours logged outside
the plan: they are not tied to a course reliably.
A red warning: being under the credits is common, as is being over.

**Disclosed.** Lectures before the plan began are not counted, so a plan made in the
semester's fifth week shows fewer lecture hours than the course has. The plan's
future sessions change as it replans. Outside the EHEA credits are not ECTS.

## D87. Cards filed under a week, practised with that week

*The owner, 2026-10-21: "build ... cards tied to the week".*

**Chosen.** A flashcard can belong to one week of lectures of its course (a column
`week` on `cards`, the Monday, empty for the whole course). The card maker (D84), the
new-card form and a card's page offer the course's weeks ("Week 3 · 12 Oct"), the
week in course by default; a link from a week opens the maker for that week. A week's
cards are practised from three places: its row on the course's page ("Practise its 4
cards"), the self-test of that week on Today and on the session's page ("Your cards
for this week"), and the address `cards/review?course=…&week=…`. Practice shows each of
the week's cards once a day, the due and most overdue first, then the others, then
the new; an answer is an ordinary review, so FSRS counts the days since the last one
and a card practised before it is due gains less. Cards without a week stay in the
course's deck as before, and every card keeps its own schedule.

**Rejected.** Counting a week's card answers as a self-test of the plan's topic: the
student reports the session already, and both would count the same recall twice.
Showing cards outside their schedule more than once a day: FSRS gains little from it.

**Disclosed.** Weeks are the timetable's weeks of lectures from the plan's first day;
a card filed under a week keeps it if the timetable changes.

## D88. Courses: a section of its own

*The owner, 2026-10-21, with a screenshot of the topics page in French: "there's
problems with formatting too, and i think it should be a section per se the one for
syllabus and content so a student can check what's been done and what's to do, see
the progression and access material simply, maybe later on we can make a connection
to a student's education platform".*

**Chosen.** A Courses section in both bars, between Tasks and Focus on a computer and
after Calendar on a phone.

- *The list*: each course with its colour, its next exam, a bar of the weeks taught,
  how many were reviewed, its topics and cards, and a note when its hours fall under
  its credits.
- *A course's page*: progress (weeks taught out of all, weeks reviewed at least once,
  this week); then week by week, each marked done (✓), in course (●) or to come (○),
  with its syllabus topics (D81), "reviewed n times" or "taught, not reviewed yet",
  today's predicted recall in the colours of Your memory (D82), the reviews planned,
  its cards (D87) and its links; the topics taught before the plan and those without a
  week; the assessment and the workload check (D86); and its material: the deck, the
  student's notes and their groups' notes for the course (D85), the classmates' notes
  in the library, and links.
- *Links to material*: the course on the learning platform, slides, a shared folder:
  an address (http or https only, at most 500 characters) and a name, for the whole
  course or one week, at most 30 a course, kept in the plan and seen by the student
  only. They open in a new tab. This is the simple way to "access material" now; the
  connection to the platform itself is CURRICULUM.md §5's, which needs a university's
  agreement and a token policy, and is not built.
- The topics' list (D81) moved here: the old address leads to Courses, and each
  course's page leads to its topics.
- Spark shows it around the first time, as every section (D77): the list, the weeks,
  the workload, the material; Today's tour names Courses among the sections.

The phone's bar has six tabs since: it takes the whole width and Spark waits just
above its right end, as it did below 340 px (AUDIT item 69). Measured in Chromium at
320, 344, 360, 375, 390 and 430 px in English, French and Italian: no label touches
another or leaves the bar; the tightest gap is 2.1 px, French at 320 px.

**Rejected.** Folding Courses into Focus or Settings: a student looks for a course
first, and the owner asked for a section. Apple's five-tab advice for phones: six fit,
measured, and Spark moved rather than a section hiding. Reading the platform's
course pages now (CURRICULUM.md §5: per university).

**Disclosed.** A week is "reviewed" once one of its study sessions was done, which
says nothing of how well; recall today is the model's estimate (D82). Checked in
Chromium only, at 1280 px light and 390 px dark, on the demo's student.

## D89. The science behind it: one page, for students and their teachers

*The owner, 2026-10-21: "for the demo that i want to send to my teacher to review, and
for a user potentially interested, put somewhere maybe if you touch on the logo, how
the engine works explained with the same webpage design with analytics colours even
the maths behind it and science behind it but in a user friendly way".*

**Chosen.** A page, "The science behind it", at `/science` for anyone (a teacher needs
no plan) and at `/p/{token}/science` inside a plan. Inside a plan, the logo opens a
small menu: Today, or this page; the footer of every page and the home page link to it
too. It says, in order: what the app does in three steps (predict, plan, learn); how
memory fades (the forgetting curves of three topics, in the memory colours of D82, and
FSRS-6's curve as a formula); why the wait matters (the stability a successful recall
adds after 0, 5, 15 and 30 days, as a chart and a table, and the update as a formula
whose last term is the spacing effect); testing beats rereading (five checked studies,
each in one sentence); how the week is planned (the assistant's five rules, and the
research planner, AO* with an SSP-MMC-style cost, as the reference that checks them);
what is fitted to the student and when; what is not known yet; and the sources.

**Every number is computed or cited.** The curves, the table and the settings quoted
(the 90 % target, 20 new cards a day, 10 minutes for a forgotten card, 400 answers
before a student's own weights, 25 to 30 hours an ECTS credit) are computed by
`service.science_view` from FSRS-6 and the app's constants as the page is drawn, so the
page cannot disagree with the app. Each number from research is one its checked entry
in docs/REFERENCES.md states (`tests/test_science.py` checks both, and that every source
is a checked entry with its own DOI). The planner's "about one session in twenty",
"half a minute" and "a hundredth of a second" are `benchmarks/rule_vs_planner.py`'s,
as quoted in the assistant's own documentation.

**Rejected.** A page of maths for specialists (the owner asked for it user friendly:
each formula comes with one sentence saying what each letter means); MathJax or another
script for the formulas (an external script, and the CSP; HTML's `var` and `sup` are
enough); a claim that the app raises grades (not measured; the page says so).

**Disclosed.** The page describes FSRS-6 with its default weights; the research planner
runs FSRS-4.5 (D52), which the page does not detail. Checked in Chromium only, at
1280 px light and 390 px dark, in English and in Italian.

## D90. A semester's program in one document, pasted or as a PDF

*The owner, 2026-10-22, sending their school's semester syllabus (a 17-page PDF of
seven courses): "parsing syllabus and ECTS, topics, evaluations?"; asked whether a PDF
library could be added: "Yes, add pypdf".*

**Chosen.** Courses → "Import your semester's program": the student chooses the PDF, or
pastes its text. The server reads the PDF's text with pypdf, keeps nothing of the file,
and `syllabus.courses` cuts the document into its courses: at the names its contents
page lists, at the plan's own course names, else at the title above each repetition of
the headings a course has (outcomes, content, assessment). Page headers, footers and
page numbers (any short line repeated three times that is not a heading, a week, a row
or a field) are dropped first. Each course found is offered to the plan's course with
the most words in common ("Algebra 3" and "ALGEBRA 3 - CM"; "Algebra 2" is not one),
and the student can change it or leave it out; nothing is saved before "Save these
courses", and a text changed since it was read is read again rather than saved.

Within a course, the reader now knows what such a booklet holds (AUDIT item 74):

- **A dated schedule.** A row led by its date ("Tue, 15-09-26 1 Groups…", "lun. 5 oct.",
  "9/15/2026", "Sept 14") is one topic; the weekday, when given, settles which number is
  the day; a number alone ("12.3 Groups") is a section, not a date. A cell that wraps
  goes on the row above; what the row says to read ("Gallian, Part 1, Chapter 3") is
  kept apart as its reading; "Continuation" and a day without class are not topics.
  Numbered rows are weeks only under a header that names a week column ("Week  Content");
  a numbered list stays a list.
- **Assessments in their week.** A row whose first sentence names a mock exam, a midterm,
  a quiz, a partial or a presentation ("Midterm. Regular expressions") gives that
  assessment in its week and the rest as the topic; an assessment part held "in weeks 3
  and 10" keeps its weeks. Both appear on the course's week rows (D88), in red, the
  colour of exams. A session reviews a week's topics, never its mock exam.
- **Assessment weights as written.** "Partial exam 1 (20%)", "midterm 40% (written)",
  "2 quizzes … in weeks 3 and 10: 20% (10% + 10%)" (the split in brackets is not a part),
  lines that wrap; still kept only when the parts add up to 100.
- **Hours of classes.** "84h / semester", "42h Lectures + 42h Tutorials", "21 hours CM /
  21 hours TP", "Ore di lezione: 48": the course's contact hours. The workload check
  (D86) then says what the credits leave for the student's own study, and that over the
  course's weeks of lectures (for 6 credits and 72 h of classes: "the rest, 78 to 108 h,
  is your own study", `tests/test_program.py`). Hours a week, and office hours, are not
  read as the semester's.
- **More headings.** "Learning objectives", "Course description" (the content, when it
  holds the list), "Required/Recommended readings", and headings that only end a section
  (language, format, level, semester, credits, the staff, the hours), so "Language of
  instruction: English" is no longer a topic. Of several content sections, the one with
  the most topics is read; prose (two sentences, or sixteen words) and the label over a
  bulleted group are not topics; a line a PDF wrapped joins the one above.
- **Days place topics.** A topic's day puts it in the teaching week that contains it
  (`syllabus.place`); without a year ("lun. 5 oct.") it takes the year of that week; a
  day outside the timetable's teaching weeks falls back on the week number. Topics saved
  again from their lines keep the day and the reading they had.

**Why rules, again.** The same reasons as D81 and D83: free, on the server, the same
answer every time, and the student checks every course before it is saved. The real
booklet the owner sent was read by these rules: all seven courses with their credits,
hours and assessment, the topics of six (one has prose only), with weeks or dates for
three. It is not in the repository, which tests the same shapes with made-up booklets in
English, French,
Italian and German, and a US one with the month first (`tests/test_program.py`,
`tests/test_generality.py`).

**Rejected.** A model for every PDF (D83's reasons; still the paid candidate for prose,
CURRICULUM.md §3a); OCR for scans (a heavy dependency; a scan is told to paste its text
instead); keeping the PDF to read it again later (nothing needs it); PyMuPDF (its AGPL
licence); pypdf was chosen as pure Python, with no compiled part to ship.

**Disclosed.** One real program document was tried, the owner's; the others are written
for the tests. A PDF is read within 10 MB, 80 pages and 200,000 characters; a PDF crafted
to make pypdf work long within those is not stopped. An upload above 1 MB passes through
a temporary file the web framework deletes at the end of the request. Course names are
matched by their words, not their meaning ("Analyse 3" and "Analysis 3" are not matched).
Checked in Chromium only.

## D91. Tips by topic, and what research says about the brain and motivation

*The owner, 2026-10-22: "making the tips page not a whole scrollable but u click on a
category and get results, neuroscience of learning, of effort and reward, of tips and
tricks to make studying motivating and inherently rewarding".*

**Chosen.** The tips page lists its topics, each with a line saying what it is about and
how many tips it holds; a topic opens on its own page, with the other topics as a row of
pills above it. A tip linked from Today, Focus or the coach opens on its topic's page at
the tip. Two topics are new, ten tips in all, each from abstracts read at Europe PMC on
2026-10-22:

- *How the brain learns*: recalling strengthens a memory more than rereading
  [ref:karpicke2008] [ref:adesope2017]; dopamine neurons signal prediction errors, and
  guessing first helps students [ref:schultz1997] [ref:pan2023]; spacing works in single
  cells [ref:kukushkin2024]; curiosity engages the dopamine circuit and the hippocampus
  [ref:gruber2014]; a promised reward does too, before learning [ref:adcock2006].
- *Motivation and reward*: expected rewards lower the wish to go back to a task, positive
  feedback raises it [ref:deci1999] (until now cited from a search engine's summary,
  now read at the source); choice raises motivation [ref:patall2008]; effortful learning
  feels like learning less [ref:deslauriers2019] [ref:kornell2008]; effort can become
  rewarding [ref:eisenberger1992] [ref:inzlicht2018]; and, honestly, growth-mindset
  programmes have weak effects on grades [ref:sisk2018].

Each says how strong its evidence is, what it does not show (animals, one laboratory
study, money as the reward, reviews that propose rather than test), and, where true,
what in the app already does it. The tips' rules (D62) hold: only checked sources, every
number one they state, French and Italian for every sentence.

**Rejected.** Claims the abstracts do not make ("dopamine makes you learn", sample sizes
not given); popular sources (books, talks) without a checked study; a tip per study in
the weekly note's place (D64 keeps its own pace).

**Disclosed.** Abstracts, not full texts. The brain tips describe research; how far it
carries to a student's semester is, for most of them, not known, and each says so.

## D92. Moodle: links and the program now, an integration when a university agrees

*The owner, 2026-10-22, asked about "moodle login and course retrieval"; chose "Not yet:
links + plan".*

**Chosen.** Nothing that signs in to Moodle is built. A student keeps links to a course's
material (D88), imports the program (D90), and gets deadlines from Moodle's calendar
export as before. CURRICULUM.md §5a writes down, step by step, what to do the day a
university agrees: the agreement and its data protection review first; a dedicated,
read-only web service on the university's site with six functions, each checked in
Moodle's own source on 2026-10-22; the student's token encrypted, never logged, never in
the plan's copy, deleted with the plan; the student shown what was read before it
changes the plan; tests against a fake Moodle, and a generality case for a second site.

**Why.** A Moodle token opens a student's whole account (CURRICULUM.md §5); asking for
passwords or tokens without the university's agreement would put that key in a pilot's
database for a benefit the program import already gives most of.

## D93. Light on any device: nothing loops while a page is read, and solid glass where blur is slow

*The owner, 2026-10-22: "i have a very powerful laptop we need to make sure it is not too
heavy to run on simpler hardwares".*

**Measured first** (`benchmarks/page_cost.mjs`, headless Chromium, which draws without a
graphics chip, so blur and compositing run on the CPU: a stand-in for a weak one). On
main (e61158a), every plan page kept the browser at 73 % to 110 % of a CPU core while it
sat open and nobody touched it (Tips 73 %, Today 110 %, the home page 95 %). Paused
animations brought it to 0 % to 4 %; removing the blur alone, to 19 % to 39 %. The cause
was the combination: the sky drifting behind the glass (90 s, forever), Spark's loops, the
garden's sway and the home page's lights kept the page drawing 60 frames a second, and
each frame made every pane blur again. Any one of them moving cost about as much as all
of them (Today: 108 % with both Spark and the garden, 106 % or 93 % with one of them,
7 % with neither). Spark's tour also laid the page out on every frame while open.

**Chosen.**

- **Nothing loops while a page is read.** Ambient motion (Spark, the garden, the home
  page's field and lights, the focus dial's breath, a running session's pulse) plays for
  six seconds after a page arrives or after a tap or a key, then rests (`data-rest` on
  `<html>`, set by `motion.js`; the CSS pauses every endless animation); the next tap or
  key wakes it, and Spark wakes under the pointer. Scrolling and moving the pointer do
  not wake it, or a page being read would never rest. The sky no longer drifts at all: it
  still follows the sun through the day (D76). A test checks that every endless
  animation in the stylesheet is one that rests, or a spinner.
- **Solid glass where blur is slow.** A new setting, Glass: Automatic (the default),
  See-through, Solid. Solid draws plain surfaces with no blur anywhere. Automatic is
  see-through unless the device asks for less transparency (`prefers-reduced-transparency`)
  or drew fewer than 30 frames a second while the page arrived (measured by `motion.js`
  over 45 frames, half a second after load); such a device then starts solid on its next
  pages too (one word kept in its browser, said on the privacy page).
- **Scripts that drew every frame**: the tour places its light only when its target
  moved; the focus ring moves on every frame while the page is in use and with the digits,
  once a second, while it rests.

**Result** (same script, same machine; whole browser, % of one core; "lively" is 1 to 9 s
after the page arrives, "reading" 15 to 25 s):

| Page | Reading, before → after | Lively, before → after | Lively, solid |
|---|---|---|---|
| Home | 95 % → 3 % | 103 % → 61 % | 46 % |
| Today | 110 % → 4 % | 109 % → 65 % | 20 % |
| Courses | 100 % → 3 % | 94 % → 54 % | 18 % |
| A course | 107 % → 4 % | 108 % → 68 % | 18 % |
| Tips | 73 % → 0 % | 73 % → 28 % | 8 % |
| Focus | 100 % → 4 % | 102 % → 60 % | 20 % |

Scrolling with the CPU slowed four times, frames slower than 60 Hz allows: before, up to
16 % (Today); after, 0 % to 14 % see-through (the longest, a course's page) and 0 % to
2 % solid. Page weight was not the problem: pages are 154 to 179 kB compressed, and both
hosts compress (Render serves brotli, Caddy zstd and gzip).

**Rejected.** Turning the glass and the motion off for everyone (the owner chose them,
D73, D66; on a capable device they cost nothing once a page rests); detecting a weak
device by its number of cores or its memory (no browser reports its graphics chip, and
Safari hides both; measured frames say what matters); a separate "lite" version of the
app (two sets of pages to keep).

**Disclosed.** Measured in headless Chromium on one machine, its software rendering
standing in for a weak graphics chip; not measured on a real low-end phone or laptop, nor
in Safari or Firefox. In this measurement Automatic stayed see-through at phone size
(frames held 30 a second) and turned solid at a 1280-pixel window. For the first six
seconds after a page opens, or after a tap, see-through glass still costs a weak device
about half a core to two thirds; Solid, about a fifth. A device judged slow once (a
busy moment can do it) stays solid on Automatic until See-through is chosen or the site's
data is cleared. Without scripts, nothing rests: the page works as before and costs as
before.

## D94. An account with an e-mail and a password, before the plan

*The owner, 2026-10-23, before giving the app to the cohort: "we need to make the user
make an account the first time they enter … they need to make an account with their
email". Asked how, the owner chose an e-mail and a password, the address confirmed
later rather than before the first plan.* D35 had declined e-mail links and passwords
"until launch"; this is launch.

**Chosen.**

- **The account first.** The home page offers "Make your account" (an e-mail address
  and a password) before the two ways to start; the plan made next belongs to it
  (`accounts.token`), one plan an account, kept across semesters as before (D36). The
  tests' own apps keep accounts off (`Config.accounts`), so the rest of the suite still
  checks plans as D35 made them; `cps web` and `cps demo` turn them on, and
  `CPS_ACCOUNTS=off` turns them off again.
- **A plan of an account opens only for it.** Every `/p/<token>/…` and `/s/<token>/…`
  page of such a plan asks anyone not signed in to that account to sign in, whatever
  address they hold; after signing in they land on the page they asked for (only a page
  of their own plan, never another site). Two things stay open by their own link: the
  calendar feed, which a calendar app reads without anyone signed in, and the app's
  manifest, which a browser fetches without cookies. A plan without an account (one
  made before, a demo student, D31) still opens by its address.
- **The password.** OWASP's Application Security Verification Standard 4.0.3, V2.1: at
  least 12 characters once runs of spaces count as one, up to 128, no rule about kinds
  of characters; and a short hand-written list of obvious choices (digit runs, keyboard
  rows, "password" and "motdepasse" with digits), with any password of two different
  characters at most, and any containing the address. It is kept only as scrypt with a
  random salt, N = 2^14, r = 8, p = 5, one of the five settings of equal strength in
  OWASP's Password Storage Cheat Sheet (16 MiB a hash, against 128 for N = 2^17). Its
  settings are written beside it, so they can be raised later without locking anyone
  out.
- **Wrong passwords.** Ten in a row lock the account for 15 minutes, counted per
  account whatever addresses try (ASVS V2.2.1 allows up to 100 failures an hour; this
  allows 40); per address, at most 120 sign-ins an hour (password, passkey or code),
  and 40 new accounts and 40 new plans (they were 30 sign-ins and 10 plans): a class on
  one university network reaches the internet through one address, and a cohort signs
  up together. Signing in ends the session the browser had, if any. A wrong address
  and a wrong password get the same answer, and take as long (an unknown address is
  checked against a hash made for nobody).
- **Signed in: one cookie.** `cps_session`, a random value of 32 bytes; the server keeps
  only its SHA-256, with the kind of device (iPhone, Windows… from the browser's
  description) and when it signed in and was last used. HttpOnly, SameSite=Lax, Path=/,
  Secure whenever the site is reached over HTTPS; 90 days after its last use, the last
  use written down at most once a day, when the cookie is sent again. Signing out
  deletes it and the device's offline copies (D33). Forms keep their same-origin check
  (AUDIT 37), and a plan's pages still need the plan's token in their address, which
  another site does not know.
- **E-mail, for two things only.** A link that confirms the address (7 days), and a
  link that chooses a new password (2 hours, once; it signs every device out). Each is
  kept as a digest and works once; a newer one replaces an older one. Opening a link
  changes nothing: a button on its page does, since mail scanners open links first.
  "Forgot your password?" says the same whether an account has the address or not. Sent
  through any SMTP server (`cps/mail.py`, the standard library, `CPS_SMTP_*`), in plain
  text, with no image and no link but its own. Without one the app says it cannot send
  e-mail, the student confirms later, and the owner makes a reset link on the inbox
  page and sends it themself; the demo (`cps demo`) shows on screen the e-mail it would
  send, since its accounts are made up.
- **Confirming is not needed to use the app**, only to reset a password by e-mail: the
  owner's choice, so the first plan is not one trip to the inbox away.
- **Settings, "Your account"**: the address and whether it is confirmed (send the link
  again), change the password (the current one needed; the other devices are signed
  out), sign out, sign out the other devices. Passkeys and the recovery code stay, as
  other ways to sign in, and a sign-in with either opens the account too.
- **What goes, and when.** Deleting the plan deletes the account, its sessions and its
  links. An account that never made a plan is deleted 30 days after it was made; a
  session unused for 90 days, a link past its time, by the sweep. The plan's copy (D43)
  has the address, when it was made and confirmed, and the devices signed in; never the
  password's digest nor a cookie's.

**Rejected.** A code by e-mail at each sign-in (no password to forget or leak, but no
sign-in at all until an e-mail service is set up and paid for; the owner chose the
password); confirming before the first plan (the owner's choice); "Sign in with Google"
(D35); an authentication library or service (Supabase was weighed on 2026-10-23: it
replaces what the store already does, and its sign-in centres on what D35 avoided);
argon2 (the strongest choice, a new dependency to ask for; scrypt is in Python's own
`hashlib`); a check against leaked passwords (an outside service told of each password,
or of part of its hash).

**Disclosed.** Checked with the test client (`tests/test_email_accounts.py`) and in
Chromium; no real e-mail was sent, because no SMTP account exists yet (the owner's step,
`docs/HETZNER.md` step 6). The address is not proved to be the student's until it is
confirmed, and an account is made for any address typed. A plan without an account
(the demo's students) still opens by its address, which its calendar feed carries
(AUDIT 76). The list of obvious passwords is short and written by hand. The cookie is
the only one; that it needs no consent, being needed for the service to work, is this
project's reading of the ePrivacy rules, not legal advice.

## D95. Notes as PDFs

*Same request: "be able to upload their notes". Asked what that should cover, the
owner chose PDFs (not a bigger limit for photos, not a storage limit per student).*

**Chosen.** A set of notes (D26) takes up to three PDFs, 10 MB and 300 pages each,
beside or instead of its photos and its text, in the same field: a PDF is told by its
first bytes, not by its name. Each is written again page by page with pypdf before it
is kept (`pdftext.clean`): without its document information and XMP metadata (often its
author's real name, the app that made it), and without what runs when it opens (an open
action, additional actions, form scripts, its names tree). Its text, as far as it has
one, is kept with the post (50,000 characters for a set) and makes flashcards (D84),
after the set's own text; a scan has no text and is kept all the same. A PDF is seen by
exactly those who see its post, like a photo (D18), and is saved, then opened by the
device's own viewer (`Content-Disposition: attachment`), never shown inside a page of
the site. It goes with its post, with its plan, and is in the plan's copy (D43). The
post shows each PDF's name as uploaded, its pages and "Download".

**Rejected.** Showing a PDF inside the page (a viewer that runs what the file holds,
under this site's address); keeping the file as uploaded (its metadata names its
author); a storage limit per student (the owner did not choose it; the size and the
count of files bound an upload, the change limit of D35 bounds a plan's uploads an
hour).

**Disclosed.** Tried on PDFs made by the tests and by pypdf, with an author, a creator
and an open action; not on files from GoodNotes, Notability or OneNote. How long the
server takes to rewrite a large PDF was not measured. No limit on a student's total storage: a
student could fill the disk with many sets of notes over time.

## D96. What a student uses, counted only with their yes

*The owner, 2026-10-23, before the pilot: "can we implement a consentual tracking for
user to see what they use and not use in the first months of trial to then improve and
streamline the app?"* The event log of D15 says who came back and how often; it does
not say which of the app's thirty-odd pages anyone opens, which is what simplifying it
needs (the review of 2026-10-22 counted the pages; it could not say which are used).

**Chosen.**

- **Asked, never assumed.** Today asks once, in one card, "May the app count which of
  its parts you use?", with "No thanks" beside "Yes, count"; Settings has the same
  switch ("Help make the app simpler"), off until the student turns it on. No answer
  counts nothing. The answer is kept per plan (`usage_consent`), with when it was given.
- **What is counted: a part's name and a number a day.** Thirty-nine parts
  (`usage.PARTS`: Today, Calendar, a course's page, Focus, timing a session, saying how
  a session went, Flashcards, answering one, the coach, Settings…), each worked out
  from the page's address and the request's method alone (`usage.part_of`), so never
  from what the page held or what was typed; per plan, per day (UTC), how many times
  (`usage`: token, day, part, n). Not the hour, not the order, not how long. Addresses
  that are not a part a student chose to open are not counted: the panel fetched again
  after a change, the sky, a photo, the manifest, the counts' own page.
- **On the server, at the page.** The middleware counts after the page is answered,
  only a page that opened (200) or a form that went through (2xx or a redirect), in one
  statement that writes nothing unless the plan said yes. No script in the browser
  (D6), nothing stored on the device, no third party.
- **A no deletes.** Saying no, or turning it off later, deletes what was counted at
  once. Each day's counts are kept 180 days ("the first months of the trial"), then the
  sweep deletes them; they are in the plan's copy (D43) and go with the plan.
- **The student sees theirs.** Settings links to "What was counted": the parts used, on
  how many days, how many times, the last day, and the parts not opened since the yes.
- **The owner sees totals.** `/admin/<key>/usage` and `cps metrics`: how many said yes
  and no; for each part, how many of those who said yes used it, how many of them in
  the last 7 days, on how many student-days and how many times; the parts nobody used.
  Never a plan's address nor one student's row.

**Rejected.** An analytics service or script (D6; and a cookie banner for it); counting
by default with a way out (counting is not needed to make the plan, so it waits for a
yes); keeping the hour or the sequence of pages (more than "used or not" needs, and
closer to watching someone); asking during setup (one more question before the first
plan, which the review set out to cut); keeping counts after a no (allowed for what was
given before, but a student who says stop expects it gone).

**Disclosed.** With 10 to 20 students these are counts to read and ask about, not rates
to test, and those who say yes may use the app differently from those who say no. A
part used through a script alone (opening a fold, Spark's sheet, the calendar's views)
is not seen: only pages and forms reach the server. A day is a UTC day. That consent is
the right basis, and that server-side counts of this kind need no cookie consent, is
this project's reading of the GDPR and the ePrivacy rules, not legal advice.

## D97. Five sections, one message, one tour

*The owner, 2026-10-23, after the review of the app's weight (packages A and B before
the pilot, C in its first week): Community keeps its tab; Spark goes into the top bar;
what the teacher found heaviest was "too many sections".*

**Chosen.**

- **Five sections**: Today, Calendar, Courses, Community, You (the tab bar; on a wide
  screen Plan, Courses, Community, You, the plan and the calendar being one page there).
  **You** is one short page in three groups: Progress, Flashcards, Tasks, Focus and diary;
  Study tips, The science behind it; Settings, Connect calendar, Send feedback. Every other
  page belongs to one section, and the tab bar shows which: Progress, Flashcards, Tips,
  Tasks, Settings under You; Focus under Today, where its door is (each session's Start,
  and "Study something else" under the next step). The gear, the "Connect calendar" link
  and the logo's menu (D89) leave the top bar: their places are under You.
- **Spark in the top bar.** A small button at the end of the bar opens the same sheet
  (D71); nothing floats over a page's buttons any more.
- **One tour.** Today's, once after setting up, three steps: the next step; the week and
  how a session went; where the rest is. The other sections' tours (D77, 2 to 7 steps
  each) become one line under the page's title the first time it is opened
  (`service.SECTION_HINTS`), remembered like a tour; Settings' "Show it again" brings
  both back.
- **One session to report.** Today asks about the latest session that ended; those
  before it count as done as planned (D44) and say so, and can still be changed on the
  calendar.
- **One message.** Besides the plan, Today shows at most one card at a time, the first of:
  an exam just past (D56, one exam at a time), the end of the semester, the question about
  counting what is used (D96), a question that makes the plan fit (D98), an insight (D80),
  a tip the day calls for (D63) (`service.today_message`). What is under "More" stays.
- **Plain words, the first four.** "First self-test" is "First review", as its own
  detail already said; "Practice: 4.5 h" is "Exam practice: 4.5 h"; "Busy times not in
  your timetable" and "Your classes and busy times" say "when you are not free".

**Not changed yet: the other words, for the owner to approve.** "week of 21 Sep" on a
session (the lectures of that week: "lectures of 21 to 25 Sep" needs the week's last
day of lectures); "Self-test" (kept: it names what the student does, D24); "Usual weight"
(an exam's weight, D49); "recall reps" and "memory days" (Your memory, D82); "Keep your
plan" (a plan without an account, D35); "Busy times" on the calendar's button.

**Rejected.** Four tabs with Community under You (the owner kept its tab); hiding Focus
(it is where a session is timed; it moved, it did not go); one tour per section made
shorter (a line says what a section is for, and does not cover the page); Spark only on
Today (one place on every page is easier to find again).

**Measured** with `benchmarks/screen_load.mjs` (fixed by AUDIT 77), on this change and on
the commit before it (3cf1831), in Chromium at 390 × 844: tour steps on a new student's
first visits, 19 before (Your week 3, Today 6, Courses 1, Community 3, Focus 3, Progress
3), 3 after; pages with Spark's button floating over the content, 22 of the 35 pages before,
none after; Today at week 8, 30 things to tap before, 24 after. Not measured: whether
students find their way better; that is what the pilot and D96's counts are for.

## D98. Setting up in three screens; the rest asked on Today, one a day

*Same request: package A of the review.*

**Chosen.**

- **Three screens**: the timetable (or a week drawn by hand), **Your exams**, **Your
  week** with the plan in it ("Looks good"), then Today. On Your exams each course shows
  its exam's date, from the timetable or to type; how well it is known, its weight, its
  exam practice and "Skip" fold under its own "Adjust"; deadlines, how much to study and
  when the student is not free fold under one closed section, their defaults kept. The
  questions about the week (D42, D45, D69) and putting the plan in a calendar (D33) are no
  longer steps.
- **Nothing else on screen while setting up**: no tab bar, no top bar's sections, no "+",
  no Spark, and the logo is not a link. One way forward.
- **What setting up no longer asks comes back on Today**, from the day after the plan
  was made, one question a day, each with "Not now" (`service.FIT_QUESTIONS`): the week
  (sport, a job, the trip, when you study best), the learning platform's deadlines (not
  asked once its link is given), the plan in the phone's calendar. Taken or put off, a
  question is settled and the next waits for tomorrow; Spark's sheet and Settings keep
  every one of them at hand.

**Rejected.** Asking all of it later on one page (the same weight, moved); asking only
in Settings (nobody would find the trip to campus there); a question a day for good (three
in the first days, then none).

**Measured** as in D97: from the home page to Today, accepting every default, 14 taps
before (3 of them for the account, D94) and 7 after; the screens of setting up, 9 before
(the exams, six cards about the week, the week, the calendar) and 2 after (the exams, the
week); on the busiest of them, 80 things to tap or fill and 51 fields before, 25 and 6
after; its length, 5.8 phone screens before and 1.9 after. The plan made with the defaults
is the same plan: nothing in it changed, only what is asked when.

**Disclosed.** The defaults now stand unless the student opens a fold: a student who
trains every evening has those evenings planned until the week's question is answered,
from the second day. Checked in Chromium only.

## D99. A picture for each place under You

*The owner, 2026-10-25, looking at You (D97): "cool but average short attention-span
hyper dopamine seeking user needs icons or emojis or even better personalised
app-themed drawings for each of the sections, or else they'll struggle to get it the
first time (a deck of cards for flashcards, a to do list,...)".*

**Chosen.** Each of the nine rows begins with a tile of its own colour with the thing
drawn on it, as the icons of a phone's settings: a ring closing on a tick (Progress), a
fanned deck of cards with a question on the front one (Flashcards), a clipboard with ticks
(Tasks), a stopwatch partway round (Focus and diary), a lit bulb (Study tips), memory
fading and climbing back at each review, the curve the plan is made of (The science
behind it), a gear (Settings), a calendar page with one session marked (Connect
calendar), a speech bubble with a heart (Send feedback). Drawn as inline SVG in one
template (`_art.html`), white on the tile with the accents in a deep shade of it; no
image file, no font, nothing to load. Decoration: hidden from screen readers, since the
row's words say the same; a tile leans a little under the pointer and nothing moves on
its own (D93); it keeps its colours in high-contrast modes.

**Rejected.** Emoji (drawn differently on every phone and computer, and some not at all
on older ones); an icon font or a library of icons (a new dependency, and generic shapes
rather than the app's own); pictures on every page of the app at once (You is where the
places are listed; the tab bar has its icons already). Focus's links to Flashcards and Study
tips carry the same two pictures, so a place looks the same wherever it is linked.

**Disclosed.** Checked in Chromium at 390 px (light and dark) and 1100 px (dark, French);
the colours are a choice of taste, told apart by their drawings as much as by their hue.

*Added 2026-10-26, the owner on Study tips: "could add icons here too".* Each of its six
topics has its own tile: a mortarboard (How to study), a brain (How the brain learns), a
trophy (Motivation and reward), a loop closing on a tick (Habits), an hourglass (Breaks), a
moon asleep (Sleep, movement and coffee); "Ask the coach" a speech bubble with a question,
"The weekly note" a letter. Checked in Chromium at 390 px (dark, light) and 1100 px.

## D100. A look back worth looking at: the climb, pictures on Progress, the diary by week

*The owner, 2026-10-25, three screenshots: Progress's insights ("same icon principles
here, and the histogram is not really motivating"), and the diary ("boring to watch if
u're a user looking back at what u did").*

**The climb, in place of the weekly columns on Progress.** The card adds the hours up
week by week since the plan began. A total only rises: a week cut short reads as a
shorter step up, never as a drop, which is how the columns showed every week in progress
(this week's column, half a week old, always the lowest). A gold flag stands where each
milestone was passed (1, 5, 10, 25, 50, 75, 100, 150 h and on, `trends.MILESTONES`; the
first come close together, so a student meets one in their first week), the next one is
a dashed line with its number, and the summit is now. Under it: a flag passed this week
(celebrated once, like the badges), or how many were passed, and the biggest week. The
columns stay on Trends, where the weeks are compared. The line is drawn in a box stretched
to the card's width only, its height in pixels, so flags and words placed at a share of
the width keep their shape on a phone and on a computer (`charts.climb`).

*Rejected:* keeping the columns with this week drawn "so far" (still a comparison the
student loses every Monday); a streak of weeks (Progress has the day streak already); a
goal line to reach (D80: the pace never asks for more; the next flag is shown, not asked
for).

**Pictures on Progress (D99's principle).** Each insight has its own tile: a speedometer
for the pace, an alarm clock for starting on time, a target for focus, the sun and the
moon for when you remember best, a crystal ball for memory against the forecast; a study
group has three people.

**The diary week by week.** Each session begins with a picture of what it was, read from
the words of its title (a past paper or an exam, exercises or a problem sheet, a summary or
notes, reading or a chapter, a lecture or a video, a project or a lab, flashcards; any
other title, a pile of books), in English, French, Italian, Spanish and German words
(`service.WORK_WORDS`); the course's colour dot before its name; effort as ten bars rising
to it; progress as a plant, from a seed (stuck) to a flower (a breakthrough); the rewards
of a timed session as medals. The weeks are folds, newest first, each with its sessions,
its hours and the best plant of the week, the biggest week marked; this week and the last
are open, the older ones folded, which also shortens Progress, a page a semester's diary
made very long.

*Rejected:* emoji for the kinds (D99); a model to read the titles (a picture is not worth
a model's cost); hiding the older weeks behind a separate page (a fold opens in place and
works without a script).

**Disclosed.** The milestones are a choice, not a result. The kind of work is read by
rules from a title the student typed; a title the words miss gets the pile of books, and
a compound word is missed (German "Übungsblatt"). Checked in Chromium at 390 px (light,
dark, Italian) and 1100 px (dark, French); not on a real phone. Whether the climb
motivates more than the columns did is for the pilot to say.

## D101. Flashcards from a course's materials, by the smallest model

*The owner, 2026-10-25, with a screenshot of Flashcards: "we really should invest in
making it easy to make flashcards from courses materials with some cheap but efficient
language model".*

**Chosen.** Flashcards, a course page and each of its weeks offer the course's materials
in one tap: its topics, as its syllabus gave them (D81, D83, D90), and the notes of the
course the student can open, theirs and their groups', with their PDFs' text (D85, D95).
The page lists what will be read; the rules read the notes at once, for free, and with AI
on a button has a model write cards from all of it. The model names, for each card, the
topic it belongs to, and the card goes to that topic's week (D87), so a whole course's
cards land in their weeks without the student filing them; a week's button asks for that
week's topics alone. Nothing is kept before the student ticks the cards.

The cards are written by Claude Haiku 5.5 (`ai.CARDS_MODEL`; `CPS_AI_CARDS_MODEL` names
another), at low effort, with a 90-second timeout: writing cards from material that is
there is reading and rephrasing, and the student checks every card before keeping it.
One call reads at most 150,000 characters and gives at most 60 cards. The cut keeps the
prompt under Haiku 5.5's price step at 100K tokens (prices five times higher past it,
`ai.LONG_PROMPTS`, now counted) even at two characters a token. A whole course costs
$0.0098 to $0.0137 a call at two to four characters a token, against $0.39 to $0.55 with
Opus 5.5 (`python benchmarks/syllabus_cost.py`); the cap counts the worst case, $0.1066,
before calling. The monthly cap and the daily limit of D12 are unchanged.

*Rejected:* one call a note or a week, merged (more calls for the same reading, and the
model cannot weigh the topics against each other); the Batch API at half price (an answer
within the hour is too late for a student waiting on the page); sending the PDFs
themselves (each page billed as an image too, D84's benchmark; their text is already
read); the coach's model, Opus 5.5 (forty times the price for a task a student checks
card by card).

**Disclosed.** No model has been called: every answer in the tests is a fake provider's,
so how good Haiku 5.5's cards are is not known until a key is set (the owner's step, no
key in this repository). Two to four characters a token is an assumption. A classmate's
notes reach the model under the reader's yes alone, as one note already could since D84;
the privacy page and the notes form now say so (AUDIT 81), and whether that should need
the author's yes too is the owner's decision.

## D102. The app opens on the feed

*The owner, 2026-10-28: "as soon as you open [the app it] should have the feed then the
rest"; asked which screen, they chose "the app opens on the feed".*

**Chosen.** A student who has joined the study network (has a handle) and has a plan
opens the app on Community's feed; every other student opens on Today, as before. One
front door, `/p/<plan>/open`, decides (`service.front_door`), and every way in goes
through it: the home page when signed in, signing in by e-mail or passkey, the installed
app (its `start_url`; its `id` stays the plan's address, so an app installed before stays
the same app), the demo's Start, and the logo. Community moves to the first tab, and to
the first place in the top bar, since an app opens on its first tab; Today is the second,
one tap away. Today's address is unchanged, so a link, a reminder or a calendar's link to
a session still opens Today. Without a network the installed app opens on the feed kept
on the device, else on Today's.
The feed's first-visit line now says what the feed is to a member, instead of asking
them to choose a handle (AUDIT 82).

*Rejected:* the feed for everyone (a student without a handle would open on an empty feed
and an invitation to choose one, and a student just through setting up needs Today and
its tour first); moving the feed to Today's address (every link to Today, the tour and the
reminders point there); keeping Today as the first tab while the app opens on the feed (an
app that opens on its second tab reads as a mistake).

**Disclosed.** Checked in Chromium only. A home-screen app added before this change may
keep opening on the address it was added with until it is added again: browsers update an
installed app's manifest on their own schedule, and Safari keeps the address it was added
with. Whether students open the app more often for the feed is for the pilot to say.

## D103. Lighter pages: Progress folded, Settings in pages

*The owner, 2026-10-28, agreeing to package C of the review of the app's weight
(2026-10-23; D97 put it in the pilot's first week): "yes agreed work on that". The
review's C1 and C2; its C3, a pilot switch for extras, is not part of this change (below).*

**Chosen.**

- **Progress** shows the week's ring, the streak, each exam's readiness and the climb (D100)
  on top, and everything else under one **More detail** fold, named by what it holds (Your
  memory · Your insights · Your diary · Badges), in the style of Today's More (D97). The
  climb stays above it: it came after the review, on the owner's word that Progress should
  reward (D100). The fold opens by itself when a link leads into it (`?more=1`, used by
  Today's insight and the weekly review) and after a change to the diary, whose notice and
  entry are inside it. **One "How we measure these"** under the insights explains every
  number (memory strength and each insight), instead of one fold on each card.
- **Settings** is six rows, each its own page with its picture, as a phone's settings: Your
  week (hours, days off, the day, the sessions, exam practice, self-tests, the trip, when
  you are not free), Exams and deadlines (each exam as a row, the ones found in the
  timetable with Plan for it, Add an exam, the deadlines, the timetable and the next
  semester), the account (Your account, or Keep your plan without one), Reminders and the
  app, Language and appearance, AI and your data. **Each exam is changed on its course's
  page** (its date, how well it is known, its weight, its practice, or no longer planned
  for); setting up keeps its one exams screen (D98).
- **A page saves only what it shows** (`forms.week_revision`, `deadlines_revision`,
  `exam_revision`): what it leaves out is not passed to `service.revise_subscription`, so it
  stays as it is, and the exams' list is read again inside the store's compare-and-swap, so
  a change made elsewhere meanwhile is kept. Saving a page unchanged changes nothing but
  the defaults it shows (no trip, any time of day), which the plan already used.
- Every link and redirect to a part of the old page points to its page now; a bookmark to
  one of the old anchors (`…/settings#ai`) lands on the list, and ui.js takes it on to the
  page that holds it (each row names its old anchors). The weekly review's link pointed to
  an anchor Progress did not have (AUDIT 83).
- Five new pictures for the rows (a week, a key, a bell, a palette, a shield); Exams and
  deadlines takes the exam paper (D100).

*Rejected:* posting every setting from every page as hidden fields (a change made in
another tab meanwhile would be overwritten); keeping each exam's whole row on Exams and
deadlines as well as on its course's page (two places to change one thing; the list links
to the course instead); folding the climb with the rest (the review predates it, and it is
the part of Progress meant to reward); a page of its own for each part of Progress (more
pages to learn; one fold keeps them one tap away, and works without scripts).

**Measured** with `benchmarks/screen_load.mjs` in Chromium at 390 × 844, on this change and
on the commit before it (986cd07), for @alex eight weeks in: Progress 9.1 phone screens
and 1,241 words before, 1.7 and 139 after, the fold closed (the review's target: 3 or fewer);
Settings 8.9 screens with 104 things to tap or fill and 47 fields before, 1 screen with 18
things to tap and none to fill after (target: 2 or fewer); of its pages, the longest is Your
week, 2.6 screens and 39 things to tap; a course's page grows from 3.4 to 4 screens, with
its exam. For a new student, Progress from 8.2 screens to 2, Settings from 9.2 to 1. The
taps from the home page to Today are the same, 7.

**Not done: the pilot switch (C3).** The review proposed a server setting that turns some
extras off without deleting them, so that the pilot tests the core, and asked which: the
AI coach, cards from notes, the program import, the weekly note, badges, Trends. That
choice is the owner's and has not been made; nothing is switched off.

**Disclosed.** Checked in Chromium only. Without scripts, an old bookmark to a part of
Settings opens the list, not the part. Renaming a course is no longer offered on the exam's
form (Settings had the name as a field): a course drawn by hand is renamed on the calendar,
one from a timetable keeps the timetable's name. Whether students find a setting faster
across six pages than on one long one is for the pilot (D96's counts) to say.

## D104. A class's link that brings the program

*The owner, 2026-11-02: "build the class invite link that brings the program", after
the review of what the app still needs proposed recruiting whole classes rather than
students one by one, with one link per class that brings the class's program.*

**Chosen.**

- **One link per plan, for the whole class.** On Courses, a student whose courses have
  topics makes "Your class's link" (`/c/<code>`) and shares it in the class's chat.
  Whoever opens it sees the courses and, for each, its number of topics and the first
  three, and whether its assessment and credits come with it.
- **Taken with one's own plan, in one tap.** The link's page offers "Take it with my
  plan" to a device that has opened a plan before (as a group's link does, D40); on
  another device the link is pasted on Courses. The plan's page then shows each course
  of the link beside the plan's own, matched by the words their names share, as a
  semester's program is (D90: "Algebra 3" and "ALGEBRA 3 - CM"); a course not in the
  plan is shown as such and cannot be taken. A course with no topics of its own is
  ticked; one that has some is not, and says it would replace them. The topics are
  placed on the taker's own weeks of lectures, with what the course page said
  (assessment, credits, hours) when the link has it; the taker's own shared list for
  that course is withdrawn, since the topics are the class's now.
- **Read live, counted once.** The link holds no copy: it reads its plan when opened,
  so topics added later reach whoever opens it next. A plan's first take counts once
  ("Taken by 3 plans so far"). A new link replaces it and stopping it ends it; what was
  taken stays taken. The link goes with its plan (`_FORGET`) and is in the plan's copy
  of its data, without its code, which is the link itself.
- **Meeting the class.** A taker without a handle is offered the profile's form filled
  in with the link's university and programme, so the class find each other as
  classmates (D39).
- **What it shows.** The courses, their topics, and the university and programme of
  the maker's profile; never the maker's handle, plan or sessions. Making the link is
  the yes to this, and the card says so before it is made.

*Rejected:* a study group for the class (a group is eight at most, D28, for a shared
goal among friends; a class is 30 to 200); a copy of the topics frozen when the link is
made (a correction or a week added later would never reach the class); taking every
matched course without asking (a student's own topics are theirs to keep); adding the
link's courses that are not in the plan (a course is planned for from its exam, which
only the student knows); showing who made the link (the class needs the program, not a
name; the network is where people choose to be seen).

**Disclosed.** Courses are matched by the words of their names, as a program's are: two
courses whose names share less than half their words are not matched, and a course
named alike at another university would be. Whoever has the link sees the courses and
their topics, as with any link shared in a chat. Checked in Chromium only, at phone and
desktop widths. Whether whole classes join this way is for the pilot to say.

## D105. After an exam, the forecast beside how it went

*The owner, 2026-11-02: "build the exam card", after the review of what the app still
needs proposed closing the loop after exams: the app asked how each exam went (D56) and
kept the recall it had predicted beside the answer, but only the owner's measures read
it back.*

**Chosen.**

- **The answer, beside its forecast, where the student looks.** Once a student says how
  an exam went, Today shows it for a day (`OUTCOME_HOURS`), next after the question
  itself in Today's one message (D97): "Algebra 3: Not passed (12/30). The plan forecast
  N % of its topics recalled on exam day." It leads to Progress, where "How your exams
  went" lists every exam answered about, under the exams to come; the course's page
  says it under the exam's date. "Rather not say" shows nothing.
- **The forecast as it stood.** Each row shows the recall the model predicted for the
  exam when the answer came (`recall_predicted`, kept since D56), rounded to 5 % as
  every forecast is (D24), with a bar of that length; and the result as the student
  chose it among the five answers, with their mark as they wrote it.
- **Read down the list.** Exams are listed highest forecast first, so a forecast that
  holds shows as results that go down the list too. With two exams or more, a line
  counts the pairs: "Compared two by two: in 5 of 8 pairs, the higher forecast went
  with the better result." Two exams are compared only when their results differ and
  the forecasts shown differ; "Did not sit it" and an exam with no forecast are never
  compared.
- **What the forecast is, every time.** Under the list: the forecast is how much of a
  course's topics an average memory would recall on exam day, from the sessions
  reported; a result also depends on the exam, and a few exams make a hint, not a
  verdict.
- **A course taken again is asked about again** (AUDIT item 84). An answer is kept under
  the course and the exam's moment; an answer kept before, under the course's name
  alone, answers the exams of that course before it was given, never a later one. The
  first answer stays: a resit lists both.
- **Marks in the language's scale.** The examples under the question's mark field
  follow the language's usual scale: out of 20 in French ("14/20, 16,5/20"), out of 30
  in Italian ("27/30, 30 e lode"); English keeps all three kinds. The mark is still kept
  as written, whatever its scale.

*Rejected:* turning the forecast into an expected grade (no scale maps recall to marks,
and marks have no common scale, D56); a score of how right the forecast was, such as a
correlation (with three to six exams a semester it would swing with one exam; counting
the pairs that agree says the same in words a student can check down the list);
comparing a student with others (the measures stay the owner's, with consent, D56); the
card on Today until it is dismissed (a day is enough to see it; Progress keeps it).

**Disclosed.** The forecast is the memory model's with average weights and a guessed
starting state (D24), not a measurement; its result is set beside a pass, not a mark. The
mark's examples follow the language, not the country: a student in Switzerland, marked 1
to 6, reads the French examples out of 20. The pages were checked in Chromium only, at
phone and desktop widths, light and dark, with a plan whose clock stood after its exams:
no demo student has an exam behind them, so the demo shows nothing of it until one does.

## D106. The full check's fixes

*The owner, 2026-11-03: "build all 14, merge when green", after a check of the whole app
(every page in Chromium at phone and desktop size, light, dark and without scripts; a
new student's first minutes with `benchmarks/screen_load.mjs`; each page's server time;
the security headers, cookies and limits) ranked fourteen fixes.*

**Chosen.**

1. **A whole lecture signs up together** (AUDIT 85). New accounts and new plans per
   network address per hour go from 40 to 200: a lecture hall on one Wi-Fi shares one
   address, and Y1 is fifty. Sign-ins stay at 120, each account locking after ten wrong
   passwords (D94).
2. **The home page fits a phone** (AUDIT 86). The glow behind the sign-up spilled 30 %
   past each side; a phone widened the page to fit it and zoomed everything out. It now
   reaches the screen's edge and no further, and the home page clips sideways overflow
   on its root too.
3. **The first Today is the plan.** The question about counting what is used (D96) is
   asked from the day after the plan was made, not on the first screen a new student
   sees.
4. **44px to aim at.** On a touch screen every button, pill, tick and fold answers over at
   least 44px of height (Apple's figure) through an invisible band, and links that stand
   alone through padding a negative margin takes back, so nothing moves; rows of
   controls closer than 44px get more room between them. Desktops with a mouse are
   unchanged.
5. **Text at 4.5:1.** The base blue is `#006ae0` (white on it 5.1:1, as text on the page
   4.6:1, where `#007aff` gave 4.0), grey text is 75 % of its ink (4.8:1 on the page,
   where Apple's 60 % gave 3.3), and the text that used the faintest grey (the setup
   steps, the footer, a hint) uses grey; white text on red (a destructive button, the
   calendar's "now", a count) sits on `#d92d20`, 4.8:1, where the system red gave 3.6.
   In dark mode a button now keeps 4.8:1 with
   white text, its pressed colour 4.5, where D78 kept 3.3 after Apple's dark blue: the
   owner was asked to choose and asked for all fourteen, so the readable one.
6. **The timetable is expanded once.** `calendar_io.expand_events` keeps what it
   expanded (128 timetables and windows), keyed by the timetable's text: a course's page
   expanded it twice, a Settings page three times, Courses once per course. The events
   are frozen and each caller gets its own list.
7. **`/health` reads the data** (AUDIT 88): one read of the database, 503 when it
   fails, so Docker's check and an uptime monitor notice a missing or damaged store.
8. **A first session on the first day.** On the day the plan is made, when no session
   of it is left that day, Today's one message (D97) offers 25 minutes of focus on the
   course of the first session ahead, with "Not now"; it comes after an exam's question
   and answer and before everything else.
9. **Files named under a version, kept a year.** Every page names the app's files as
   `/static/v/<version>/…`, the version a digest of the files themselves, and those are
   served `immutable` for a year; the scripts' imports and the stylesheet's fonts are
   relative, so they follow. A page kept offline that names an older version gets
   today's file, asked about again; the plain names keep AUDIT 67's `no-cache`. The
   service worker keeps the versioned names and carries the version, so a release
   replaces the copies of the last.
10. **Unique IDs, one main heading, no level skipped** (AUDIT 87): the weekly review's
    card is `review-card` (the section around it on Progress is `review`, AUDIT 83), the
    quick-add sheet's course list is its own; Agenda and Card review have a main
    heading for screen readers, and Science, Community and Semester no longer skip from
    h1 to h3.
11. **12px as the smallest text**, from 9.6px for the tab bar's labels and 10.9 to 11.8px
    for badges, the calendar's times and labels. The tab bar is the one exception:
    11px on phones narrower than 376px (401px in French, for "Communauté"), 10px under
    341px, where every label still fits (AUDIT 69). Text inside a drawing scales with it.
12. **Progress's fold sent when opened.** Closed, "More detail" is a link to the page
    with it open (`?more=1`, as links into it always were), and what it holds is neither
    made nor sent.
13. **Community shows ten posts, then "Show more"**, ten more a tap, up to sixty, a link
    that works without scripts and lands on the first post it adds.
14. **The weight measured** (`benchmarks/page_weight.py`): a first visit to Today, raw and
    with gzip, and how many of the next page's files are asked about again. It decides
    that the stylesheet needs no splitting: compressed, it is less than the fonts.

*Rejected:* a larger button on touch screens instead of an invisible band (rows of small
buttons would double in height); 12px for the tab bar on every phone (French cuts
"Communauté"; a label on two lines breaks the bar); a version as a query string (`?v=`):
the scripts' relative imports and the stylesheet's fonts would lose it; the first day's
session scheduled into the plan (it would be a session the student did not choose to
plan; a timer started from Today is theirs); hiding the tip on the first day for good
("Not now" brings it back the same day).

**Disclosed.** Checked in Chromium only, at 320 to 1280px, with touch emulated; not on a
real phone, nor in Safari or Firefox, nor with a screen reader. Tap areas are measured by
asking the page what lies 21px above and below each control's centre: on 17 pages, 47
controls still answer over less, where controls sit closer than 44px (a card's edge, a
menu over a post). The dark mode's buttons are darker than Apple's. Server times were
measured on one machine and vary from run to run; only that the timetable is expanded
once is tested.

**Measured** (`python benchmarks/page_weight.py`): a first visit to Today is 12 answers,
425 KB, 181 KB with gzip, of which the two fonts are 95 KB that do not compress; the
stylesheet is 42 KB with gzip. Progress next, with the fold closed: 23 KB of page, its 8
files already kept and none asked about again.


## D107. A first run after Strava's

*The owner, 2026-11-03: "we should have an intro for the first time an user uses the app,
like we already do, but since we are inspired by strava let's translate strava's first
login intro into our own app". A deck of the proposal, ten mock-up screens, came first;
then "yes go with your suggestions, build it and merge when green".*

What Strava's first run does, from published teardowns (screensdesign.com, UXCam's,
updated April 2026, Mobbin's captures; not from the app itself): a few screens of the
product before the account; the account and a check of the address; "Tell us who you
are" (name, birthday, gender); permissions and a subscription offer; then straight to a
first recorded activity, the feed, clubs and challenges left for later. A step to follow
athletes, or one for privacy, could not be confirmed there, so none is claimed.

**Chosen.** A new plan, made on the home page, has a first run (`service.first_run`);
each step after the account can be skipped:

1. **Three welcome cards on a phone**, before the account (Strava's product screens): the
   plan around one's life, every week back before it fades, the class. The browser snaps
   them as they are swiped; no script. A computer keeps the pitch, and so does the demo,
   whose pitch is its button.
2. **Who's studying?** (Strava's "Tell us who you are"): the network's profile, handle,
   university, year, at least 15, asked here instead of on the first visit to Community.
3. **What do you study?** (Strava's sports): setting up's exams page, under this title; the
   timetable already knows the courses, the student checks the dates and can skip one.
4. **Study with your class**, the step Strava does not have: its feed is full from day
   one, ours (D102) is empty for a student alone. A class link opened before the plan
   existed (D104) is carried through the account and offered here, its topics taken in
   one tap; the classmates the profile finds (D39), five, can be followed.
5. **Who sees your sessions?**: the three choices posts already have (only me,
   followers, everyone), asked once, followers suggested; the diary's page starts from
   it, and each session can still say otherwise.
6. **Today with one session**: the first day's 25 minutes (D106) come first when nothing
   else is planned that day, and Spark's one line ("Your plan is ready. One session today
   is enough") stands in for the tour, which Settings can still show.
7. **First session done!** (Strava's saved activity): the diary's page after the first
   session opens with its summary, the minutes, day 1 of the streak, the course.
8. **Same time tomorrow?**: reminders (D38) are asked after the first session, not in
   setting up (a critique of Strava's flow is that it asks for notifications before the
   home screen), with the plan's next session and the calendar's link; until it is
   answered, Today asks once a session is done. Turning them on, or "Not now", ends the
   first run.

A new semester (D35), and a plan made before D107, go as they went: setting up's three
steps, the week with its plan, the tour. Each step is logged ("first run") for the pilot's
measures (D15).

**Of the five choices in the deck**, the owner took the two marked as suggested (followers
by default, reminders after the first session); for the other three the build takes the
deck's: the welcome cards, the profile in the first run, the class step for everyone
(the class's own card only with its link).

**Changed from the deck.** A handle instead of a first name: the network already shows
handles, and a name would be one more piece of personal data the app has no use for. The
courses are setting up's form under the deck's title, not chips: it already reads the
exams and leaves a course out, and is tested. The summary keeps the diary's two questions
(how hard, how far) instead of the deck's recall chips. The week with its plan is no
longer a step of a first run; it is a tab. The install card waits until the first run is
over, since the question about reminders says how to put the app on an iPhone's home
screen.

**Found on the way** (AUDIT 90): the first day's short session was offered again after it
was timed and logged; it now stops once a session is logged that day.

**Not chosen.** A birthday and a gender (the app has no use for them); a subscription
offer (nothing is sold); uploading contacts (classmates come from the class link and the
university); leaderboards (badges reward habits, never hours); asking for notifications
while setting up.

**Measured** (`node benchmarks/screen_load.mjs`, Chromium at 390 × 844, every default
accepted): from the home page to Today, 10 taps, 3 of them for the account (7 before:
"Get started", the class and who sees the sessions add one each, who is studying is one
"Skip", and the week's screen is gone); to a first session done and saved, 16.

**Disclosed.** Checked in Chromium only, at phone size with touch emulated and at 1280px;
not on a real phone, nor with a screen reader. Strava's flow is described from teardowns,
not tried. Whether the first run brings more students back on day 7 is for the pilot to
say, with the measures the app already counts (D15).

## D108. Studying now, and a session's reactions

*The owner, 2026-11-03, after asking about chats, seeing who is studying, reactions and
comments to make the app worth opening for students it cannot oblige: "yes build studying
now and reactions, merge when green". Chats were advised against for the pilot (the owner
would moderate private messages between students, and classes already have their group
chats); comments already exist.*

**Chosen.**

1. **Studying now.** While a student's focus timer runs, the people who follow them (an
   accepted follow) see it at the top of Community's feed: the handle, the course, for
   how long, and **Join**, which starts the viewer's own 25 minutes on their course of
   the same name (else their first). The focus page, while its timer runs, says who else
   is studying. It is **off by default**: the yes is on the profile page and, for a new
   student, beside "Who sees your sessions?" in the first run (D107). It is never shown
   to anyone else, nor to anyone blocked either way. It follows the timer: shown for the
   session's goal and ten minutes more (two hours without a goal), longer while the
   page's heartbeat says it goes on, never past four hours, and gone when the timer
   stops. A no, or leaving the network, takes it away at once. One row per student
   (`presence`: the course, since when, until when), in the plan's copy of its data and
   in the privacy notice. A still green dot says "now": nothing pulses (D93).
2. **A session's reactions.** Kudos become one of four: 👏 Kudos, 🔥 On fire, 💪 Keep
   going, 🧠 Big brain. One per person per post; the same again takes it back, another
   replaces it. The author sees what came and does not react to their own. Kudos given
   before D108 are 👏. Notes keep their "Helpful" mark and explanations their "I got
   it": the notes library ranks by them (D26).
3. **The demo** shows both: two of each student's classmates are studying as the student
   arrives (said yes, timer running), and their reactions vary.

**Found on the way** (AUDIT 91): a form sent in the background lost the value of the button
that sent it; and the demo's classmates could give kudos to their own posts.

**Not chosen.** Chats (above). Notifications when someone reacts or starts studying: the
reminders (D38) are about the student's own sessions, and one more reason for the phone
to buzz is not what makes study stick. Showing who is studying to everyone at a
university: a follow is the consent. Counting who joined whom: Join starts an ordinary
session.

**Disclosed.** Checked in Chromium only, at phone size with touch emulated and at 1280px,
not on a real phone nor with a screen reader; the reactions' pictures are the device's
emoji. That studying alongside others helps is not shown here: whether Join is used, and
whether it brings more sessions, is for the pilot (D15, D96).

## D109. How the code works, and the science page drawn as it scrolls

*The owner, 2026-11-04, sharing the project with fellow students of AI: "can we make just
like we made 'how the science behind it works' a page that as you scroll is animated that
shows how the code works? how the heuristic, the score, the A* happen? and can you also
animate the how the science behind it works? that way we can also put the animations in
the pitch".*

**Chosen.**

1. **A page, `/how` (and `/p/<plan>/how`), that runs the research planner on demo.py's
   worked example as it is drawn**: two topics (Analysis, S = 2 and D = 7; Algebra, S = 4
   and D = 5), an exam in 21 days, a student's five weekdays with one 1.5-hour block an
   evening. Nine scenes, each a few sentences beside a figure, each naming its module:
   the week as one integer, a bit per half hour (`timegrid`); a topic as two numbers and
   what tonight's review does to it, recalled or forgotten (`memory`, FSRS-4.5, the
   planner's model); the score, blocks used plus reviews still owed plus a penalty per
   topic not ready; value iteration's table of the cost to reach the target and why it
   is a lower bound (`ssp`); tonight's AND/OR choices with each future's expected cost,
   the lowest circled; the nodes AO* opens with each of the four guesses; the exhaustive
   check (3,906 states, the same optimum, the guess never above the true cost); the
   receding horizon; and the rules the app actually runs, with the sentence the science
   page already says about why. The solve takes about a second and a half, once per
   process (`service._code_solved`); the words are formatted per request, in the reader's
   language (`service.code_view`).
2. **Its sentences claim only what the numbers say, and a test holds them to it**
   (`tests/test_how.py`). The example's optimum is to leave the whole week free: five
   evenings cannot bring either topic to a stability of 21 days even if every one went
   to it (11.7 and 17.3), so the penalty is paid either way, and a review at 96 % recall
   saves less than the block it costs. The page says so, rather than showing a busier
   example: a longer window does spend blocks, but its exhaustive check grows several
   times over with each evening added, too slow to run as a page is drawn. If the example changes so that a sentence no longer
   holds, the test fails.
3. **Figures that draw themselves as the page scrolls**, on this page and the science
   page: each figure is a view timeline (`.scroll-fig`), its parts come in one after the
   other (`.st1` to `.st5`), a curve or an edge is drawn (`.st-draw`, its length set to
   1), a bar grows, a mark appears. CSS only, and only where the browser ties animations
   to scrolling and motion is wanted: elsewhere, with reduced motion, or with the plan's
   Motion: Reduced, every figure is simply complete. Scrolling back runs it backwards;
   nothing loops (D93). The science page gains a chart: a topic tested each time it falls
   to 90 %, three times in 60 days at longer and longer gaps, against the same topic left
   alone (59 % after 60 days), from FSRS-6 as the app runs it.
4. **For the pitch**, the scenes are recorded as short clips by scrolling the page in a
   headless browser; the clips are not in the repository.

**Found on the way** (AUDIT 92): the science page's chart labels were 8.4px on a phone.

**Not chosen.** An animation library or a script: the page must work without its scripts,
and CSS scroll timelines need none. Animating the search live, node by node: the numbers
would be the same, and a page that computes while it is read costs a slow device (D93).
A bigger example: above.

**Disclosed.** Scroll-driven animations need Chrome or Edge 115+ or Safari 26; elsewhere
the figures are still. Checked in Chromium only, at phone size and 1280px. The example is
small on purpose; the planner's real cost and gain are those of
`benchmarks/rule_vs_planner.py` (AUDIT 36), and the page says the app runs the rules.

## D110. The code page in the app's own look

*The owner, 2026-11-04, of the code page (D109) and its clips: "try to make it more on the
theme and style of the app so it's engaging to watch but not too childish".*

**Chosen.**

1. **The app's sky and glass.** The two pages anyone can read without a plan, the code
   and the science, wear a plan's sky at dusk (the hour the worked example plans for),
   with the first sprout of the garden; inside a plan both keep the plan's own sky.
   Their cards are the app's glass, with nothing new.
2. **What is large is large, as on Progress.** Each scene of the code page has one big
   number in Bricolage, set beside what it counts: 240 bits, 96 % recall tonight, a
   score of 13.22, 51 sweeps, 5 futures, 114 nodes, 0 states where the guess
   overshoots, 5 days in view. All come from `service.code_view`.
3. **The app's own colours, each for what it means there.** The two topics are the
   first two course colours (as a student's first two courses get them), on the curves,
   in the tree, in the value table; the exam is red, the only red on the page (red is
   for exams and today); the search's choices are the accent of the moment; a review
   recalled is the app's green check, one forgotten a grey cross.
4. **The figures in the app's manner.** The week is drawn as the Calendar draws it, a
   column a day, sleep and classes as blocks and the study blocks in the accent, with
   each day's bits beside it. The score is a receipt: the plan the search chose, every
   evening left free, term by term (0 + 3.22 + 10 = 13.22, its total the exhaustive
   optimum). The guesses are the app's meters, what was checked its tiles with a ring.
   The page opens on tonight: the two courses, the exam, the question.
5. **Where you are on the page.** A row of the nine steps under the title, each a
   link, and a hairline of the accent under the top bar that fills as the page
   scrolls, only where the browser ties animations to scrolling.

**Not chosen.** Spark explaining the page, confetti, bouncing: the audience is students of
AI, and the owner asked for engaging, not childish. Numbers that count up (motion.js):
they run on time, not on scrolling, so a recorded clip would speed them up.

**Disclosed.** The sky of the public pages is fixed at dusk; it does not follow the
reader's sun, as a plan's does (D76). Checked in Chromium only, light and dark, at phone
size and 1280px.

**Later (D120).** The owner asked for the glass to go from the pages that explain: they are now
on the app's plain grey, without the sky, the public ones and a plan's alike.

## D111. The code page in three dimensions

*The owner, 2026-11-04, of the restyled code page (D110): "nice make it 3d now".*

**Chosen.** CSS 3D only, where the third dimension says something:

1. **The week as layers.** The calendar of scene 1 is three layers, as the code builds
   it: the sleep mask, the classes mask, then the study blocks cut from what is left.
   As the figure scrolls in they float apart, tilted, each on a faint sheet with its
   name, and fall into one calendar by the middle of the screen. Flat, they are simply
   the calendar.
2. **The value table as a landscape.** Above the table, a tower for each of its 25
   cells, as tall as the value (in twentieths of the largest), the costliest at the
   back, the two topics' towers ringed in their course colours. The towers rise, back
   rows first, while the plane turns. The table under it still says every number.
3. **Depth for the rest.** Every figure of the code page and the science page rises
   from the page tilted back and comes upright by the middle of the screen; the four
   tiles of what was checked turn over one by one.

**Not chosen.** WebGL or a 3D library: a new dependency and a script, where the page works
without its scripts and CSS draws boxes in 3D well enough. A tower that fades in: opacity
below 1 flattens what is drawn in 3D (`tests/test_how.py` keeps the keyframes solid).

**Disclosed.** Where the browser does not tie animations to scrolling, or motion is
reduced, the layers are one calendar, the landscape stands still and the figures are flat.
Checked in Chromium only, at phone size and 1280px; what a tilting glass card costs a slow
device was not measured (D93).

## D112. The engine as it runs, on solid panels

*The owner, 2026-11-04, of the 3D code page (D111): "make it like the theme but not exactly
equal, the liquid glass makes it hard to follow on such technical pages, and please make the
page animated as you scroll, the 3d too, like as you scroll u see the path of A*, you see how
things are computed, you see the engine".*

**Chosen.**

1. **Solid panels on the technical pages.** The code page and the science page keep the
   app's sky, type, course colours and red for exams, but their cards are solid surfaces
   with a hairline and a soft shadow, not glass (AUDIT 94). The rest of the app keeps D73.
2. **Four scenes that run as they are scrolled through.** A scene's words come first; then
   a tall track whose stage stays in view while the track is scrolled, the figure beside
   (on a phone, above) the step it is at. Each step is one sentence, one at a time:
   - *The week* (`timegrid.py`): 240 bits, every one 0; sleep sets its bits (one OR with
     its mask), then classes theirs (another OR), each layer apart in depth; what is still
     0 is cut into study blocks, and the three layers fall into one calendar.
   - *Value iteration* (`ssp.py`): the landscape's 25 towers grow through the table's
     sweeps, at sweeps 0, 1, 2, 3, 4, 6, 10 and the last (51): every cell 0, then 1
     everywhere (one review), then the costs spreading back from the target, the shakiest,
     hardest topics highest; a readout says the sweep and the largest change in the whole
     table then (1, then 0.88 at the fourth, 9e-10 at the last).
   - *The search* (`plan.py`, a new scene): AO* drawn from its own run. `solve_ao_star`
     takes an optional trace (each expansion's state, the root's value after it, and the
     marked solution graph then) and `ssp.solve` the table after each sweep; the search and
     the solve are the same with or without (`tests/test_how.py`). On a disc, tonight in
     the centre and a ring for each evening, the 114 nodes the planner's run opened (and
     the states its best plans reached) sit under the node that first produced them, in
     the colour of the choice that led there. As the scene scrolls, nodes appear when they
     are produced and fill when they are opened, the best plan of the moment is drawn
     thick, and below it the root's estimate climbs to the optimum, 13.22, with the
     planner's guess (from 4, done at 114) and with none (from 0, done at 184). The steps
     of the scroll are cut over the longer run, so the guided search ends about 62 % of
     the way through, and the last step says what no guess costs. The disc lies tilted at
     first and comes upright as the search ends.
   - *Replanning* (`rolling.py`): the 21 days to the exam and the window of 5 planned
     exactly, moving on a day at a time, 17 plans, the days done behind it.
3. **Every number from the code.** The bits, the sweeps' heights and changes, the trace's
   nodes, steps, best plans and root values are computed by `service.code_view`, and the
   tests hold the sentences to them: the estimate at the root never falls and never passes
   the optimum; the last best plan leaves every evening free; the first sweep puts 1 in
   every cell short of the target.

**Not chosen.** A script that drives the scenes: the page works without its scripts and
CSS ties animations to scrolling where the browser can. Every state AO* produced on the
disc: about 180 drawn already make a dense ring on a phone; the states never opened and
never on a best plan are left out. A counter that ticks up by itself: a number in the CSS
would be one the code does not compute; the readouts are the code's numbers, one per step.

**Disclosed.** Where the browser does not tie animations to scrolling, or motion is off,
each scene is complete (the calendar flat, the settled table, the whole search with its
last best plan, the first window) and its steps are a numbered list. The replanning strip's
CSS holds 21 days and a window of 5 (the tests check the page's numbers match). Checked in
Chromium only, at phone size (iPhone 13) and 1280px, light and dark; not on a real phone,
nor what the tall tracks cost a slow device.

## D113. The science page in motion

*The owner, 2026-11-04, of the science page: "this page needs to be updated with the new 3d
animated science behind it, and i need to have access in the same way via the settings to the
page 'how the code works'".*

**Chosen.**

1. **Three scenes that run as they are scrolled through**, on the code page's stage (D112):
   the figure stays in view, the step beside it (below it, in the science page's narrower
   column), every number computed from FSRS-6, the model the app runs.
   - *Memory fades, at its own pace.* The days pass under the chart: a cursor moves from
     today to day 60, the three topics' curves drawn up to it, and three columns beside it,
     one per topic, drain at their topics' pace (eight stops, in twentieths of full recall).
     A readout gives each topic's recall on the day it names, whole days: on day 2 the
     shaky topic is at 90 %, on day 7 the week-long one, while the solid one is at 97 %; on
     day 60, 59, 71 and 85 %.
   - *Why the wait matters, as a landscape.* 25 towers in 3D: topics that held 3, 7, 15, 30
     and 60 days (back to front), each tested when its recall had fallen to 100, 95, 90, 80
     or 70 % (left to right), as tall as the times longer a successful recall makes it hold.
     They rise a column at a time. A readout follows the topic that held a week: at once, 7
     days still; at 95 %, 15 (about twice); at 90 %, where the app asks for a self-test, 23;
     at 70 %, 60, with 30 % of such recalls failing. Along each row the gain grows; down
     each column it shrinks: a solid topic gains less from the same wait.
   - *Kept up, or left alone.* The days pass under the chart again: the tested topic and the
     same topic alone drawn up to the day, each self-test appearing on its day (2, 10, 35),
     a readout of the tested topic's recall and how long it holds against the topic alone;
     it ends holding 74 days, alone at 59 %.
2. **How the code works under You**, the row after the science page's, with a picture of
   its own (a search tree, the best plan drawn thick).
3. **The last readout switches on** when its step comes, here and on the code page, rather
   than fading in over the step.

**Not chosen.** Topics of 1 and 2 days in the landscape: FSRS-6 treats a review within the same
day apart (its short-term stability), which such a topic reaches at 95 % and 90 %, and the
landscape would show a step that is the model's rule, not the spacing effect. The spacing line
chart of D89 for one topic: the landscape shows the same curve for five topics, and the rows
under it keep that topic's numbers.

**Disclosed.** Where the browser does not tie animations to scrolling, or motion is off, each
scene is complete (the whole chart, the columns and readout at day 60, the settled landscape)
and its steps are a list. Checked in Chromium only, at phone size and 1280px. A page error that
clicking any row under You raises in headless Chromium ("Transition was aborted", a page
transition skipped) was there before this change and is not fixed here.

## D114. The technical pages in plain words, asked by the student

*The owner, 2026-11-04, of the code and science pages: "it needs to be explained also with a
more accessible language alongside the technical one, with some student role play similar and
metaphors to understand, maybe also the animations could help with that". Of the three
proposals put to him, the second (a plain layer over the technical one, on both pages). The
student was first a drawing of the owner, at his wish; seen, he chose instead "the avatar that
the student creates" (D115).*

**Chosen.**

1. **A conversation opens each part.** The student asks what a student would ("How does the app
   even see my week?"), Spark answers with a picture from everyday life, and one line, *Where
   the picture stops*, says where the picture is no longer true. On the code page the
   technical text follows under *Under the hood*, unchanged. The code page's pictures are all
   of a trip, as its opening one is (a navigation app for your memory): light switches for the
   week, a phone's battery for a topic, a golf score for the plan's score, ripples from a stone
   at the goal for value iteration, a loaded coin for each review, a walk through a new city
   for AO*, the distance as the crow flies for the guess, doing a sum the long way for the
   exhaustive check, headlights for replanning, a cook's quick recipe against a slow one for
   the rules. The science page's: three leaking glasses for forgetting, the gym for the
   spacing effect, watering a plant for self-tests, riding a bike for testing against
   rereading, a to-do list for the rules. Every number an answer says is the page's own
   (184 corners with no guess, 114 with the best one; after 51 sweeps the water is still),
   checked by a test.
2. **Each step of a scene run while scrolled through says it plainly first**, then exactly,
   smaller, under it ("Sleep flips its switches on." over "Sleep sets its bits, from 23:00 to
   07:00: one OR with its mask.").
3. **The pictures are drawn into the figures**: Day 1 as 48 switches that flip on with sleep,
   then with classes; a battery on each node of tonight's review, its size the topic's
   stability (full when recalled, small when forgotten); the golf card's words beside the
   score's terms; three ripples spreading from the goal across the value landscape's floor
   while it fills; each outcome a coin that flips as it comes in; the crow's line against a
   winding road over the guesses' bars; a car on the window of replanning, its beam the days
   planned exactly; too light, just right, too heavy on the spacing landscape's floor, each
   with its column; a drop of water at each self-test. Under reduced motion each is simply
   there, complete.
4. **The student is the reader**: their own avatar (D115), named "You" in the bubble; a
   silhouette on the public pages and before they make one. Hidden from screen readers, the
   bubble naming who speaks.

**Not chosen.** A separate page in plain words: it would have doubled the pages and parted the
picture from the figure it explains. A switch between plain and technical: it hides one
reader's text from the other, and a page that works without its scripts cannot remember the
choice. Spark moving while it answers: its motion is for the places it brings a tip (D63),
not a page being read (D93). A fixed character (first a drawing of the owner): the owner preferred
the reader's own avatar (D115); the drawing and the photographs it was made from are not in the
repository.

**Disclosed.** The pictures are the owner's to review, as the tips' wording is (D62); the
Italian was not written by a native speaker (D34). Each picture is a likeness, and the line
under it says where it fails: a switch cannot tell a tired hour from a fresh one, memory does
not show its charge, the penalty's size is a choice in the code, a session left unreported
counts as done. Checked in Chromium only, at phone size and 1280px, with motion and without.

**Later (D116).** The owner found the pictures still hard to follow; the conversations, the
sixteen pictures and the plain captions left the code and science pages for a page of their own,
How it works, told in one picture.

## D115. The student's own avatar

*The owner, 2026-11-04, of the student drawn after him in the chats of D114: "i don't like the
[character] let it be the avatar that the student creates".*

**Chosen.**

1. **An avatar maker under You** (`/p/<plan>/avatar`, a card at the top of You): six skin
   tones, seven hair styles (short, swept, long, curly, a bun, shaved, a headscarf), eight
   colours for the hair or the headscarf, a moustache or a beard, round or square glasses, and
   a top in one of the seven course colours. Drawn in Spark's manner, soft shapes and no
   outlines (`_avatar.html`).
2. **The preview follows each choice with no script.** It draws every part at once, and CSS
   shows the ones whose radio is checked, in the colours checked (`.av-maker:has(...)`); Save
   keeps it. Where `:has` is missing, the preview shows the avatar as saved, and changes after
   Save. Every colour is a class (`skin-*`, `col-*`, `top-*`), so there is no style attribute;
   a test keeps the preview's colours the same as the avatar's.
3. **Kept as the names chosen** in the plan's options (`service.AVATAR_PARTS`): nothing is
   uploaded. A name this version does not draw falls back to where the maker starts.
4. **It asks the questions on the science and code pages** (D114), named "You"; a plain
   silhouette before it is made, and on the public pages.

**Not chosen.** A photo: a drawing keeps the pages light and says nothing a student did not
choose. Generating an avatar from a photo: a model and an upload for a picture 48 pixels wide.
More parts (eyes, noses, ages, body shapes): seven groups already cover hair, skin, face and
dress, and each part costs a drawing and two translations. The avatar in Community instead of
the initial on a colour: other students would see it, which this change did not ask for; it is
shown on the student's own pages only.

**Disclosed.** Six skin tones and seven styles are a choice, not every person; the headscarf
takes the hair's colour. Checked in Chromium only, at phone size and 1280px, light and dark,
with the preview and, without scripts, by saving.

**Later (D116).** The avatar is now the student in How it works; the code and science pages no
longer carry a conversation.


## D116. How it works: a garden, in one story

*The owner, 2026-11-05, of D114's plain words: "even i struggle to understand the explanation,
it needs to be understood by 1st years and by non-experts in the domain, and that is what the
animations should help with too, propose different animation and explanation plans so we can
choose together". Four ways were put to him on a page with working mock-ups (one story, a game,
three ideas, a comic), with one picture for everything (a garden, a phone battery, a real
example) and how the pages fit (two pages, a switch, layers as before). His choice: "A, with B's
game in chapter 4, the garden, two pages. but also make 3d animations so it's cooler and more
engaging".*

**Why the plain words of D114 were hard.** Sixteen different pictures, one per section, each a
world to learn before its idea; the code's order, not the student's questions; the animations
still showing the engine while the words described a battery; and long answers.

**Chosen.**

1. **A page for everyone, How it works** (`/garden`, `/p/<plan>/garden`): one story, one picture.
   A topic is a plant in a glass pot; the water is how much you remember; the roots, how long
   that lasts. Five chapters, in the order a student asks: every topic is a plant (the water goes
   down: forgetting); each plant dries at its own pace (three topics over two months); water it
   as it starts to droop (the same plant watered at once keeps roots of 7 days and is at 85 % two
   weeks later; watered as it droops at 90 %, its roots grow to 23 days and it is at 96 % a week
   after); you only have a few free evenings (the code page's week, its layers falling into one);
   every morning it looks again (remembered, the roots grow to 23 days; forgotten, they shrink to
   1 and the topic comes back soon). Then exam day, the five ideas again, and the two technical
   pages for the curious. Every number from FSRS-6 (`service.garden_view`).
2. **3D, in CSS**: glass pots whose water is a box that drains as the page is scrolled, plants
   that always face the reader and droop and pale as the water falls, roots that grow or shrink,
   a drop that falls as a pot is watered, a shelf that turns, the week's layers (D111) and the
   tap's card in depth. Without motion every scene is complete, its steps a list.
3. **The game, in chapter 4**: five free evenings, three topics, the exam on Saturday. The
   student picks a topic or Rest for each evening; the server answers with both gardens on exam
   day, theirs and the app's (its rule: the driest topic once it is down to 90 %, else the evening
   stays free). Scored on two things, the lowest plant and the evenings used, because the rule
   is not the way to the highest number: in this example it keeps every plant at 93 % or more
   with 3 evenings, while revising every evening reaches 98 % with all 5; the page says which
   happened. A form, so it works without a script.
4. **Two pages.** How it works is first under You, in every page's footer, and at the top of the
   science and code pages ("New here? Start with How it works"). Those two are technical again:
   D114's conversations, pictures and plain captions are gone from them. The student's avatar
   (D115) is the one who asks, at the top of How it works.
5. **Written short**: one picture for the page, sentences of 15 words or fewer, 45 words a step at
   most; `tests/test_garden.py` counts them. The page is counted as its own part of the app (D96),
   the fortieth.

**Not chosen.** The game alone (B): it needs a script, and teaches ideas 2 to 4 only. Three cards
(C): too little depth. A comic (D): a drawing per panel in three languages. A battery: it explains
revising at the right time badly. Layers on the technical pages: they kept two readers on one
page.

**Disclosed.** The garden is a likeness: a plant shows thirst, a memory does not, and the water is
the model's estimate with average weights; each chapter's last line says where its picture stops.
The game's three topics are made up and every revision in it succeeds. The French and Italian
were not written by a native speaker. Checked in Chromium only, at phone size and 1280px, light
and dark, with motion and with it reduced, and the game without a script; what the 3D costs a
slow device was not measured.

**Later (D117).** The owner found the boxes crude and the pinned chapters badly laid out; the
pots are hyacinths in round glasses drawn in WebGL, over a flat drawing, and each chapter's words
stand beside its picture.

## D117. How it works in WebGL: hyacinths in glasses

*The owner, 2026-11-05, of D116's chapter 2 on the live demo: "formatting is weird, i dont want
cubes i want some polygons spheres curves i want the graphics and animations to be excellent".*

**What was wrong.** The pots of D116 were boxes: CSS 3D draws only flat rectangles, so a glass,
its water and its shelf could only be cubes. And each pinned chapter (D112's stage, as tall as
the screen, its figure centred) kept its words above the track, so the heading sat at the top of
the screen, the picture far below it and the step at the right (AUDIT 95).

**Chosen.**

1. **WebGL, drawn by the page's own script** (`static/garden.js`, no library). A topic is a
   hyacinth in a hyacinth glass, the glass that holds a bulb above water: the water is how much
   is remembered, as before, and the roots, which hang in the water, how long it lasts. The
   glasses, bulbs and the round wooden table are surfaces of revolution (a profile turned about
   its axis); stems and roots are tubes swept along curves; leaves are curved ribbons with a
   shallow channel; buds and flowers are spheres, each flower six petals round a heart. One small
   shader lights them: a sky and a ground, a key light, glass by its angle to the eye (Fresnel)
   with a window's reflection, the water darker with depth and its surface rippling where a drop
   falls, the table's rings. The glasses are drawn back to front, each far side, its water, its
   roots again through the water, then its near side.
2. **The same story, moving the same way.** As the page is scrolled the table turns, the water
   falls through the scene's stops, leaves droop and yellow as it falls, a drop falls and ripples
   the water when a glass is watered, roots grow or shrink, the forgotten plant wilts, and on exam
   day the flower stalks lengthen and open. Each scene's numbers come from the server in its
   `data-scene` (the water at each stop, the roots, the parts of the scene in which a drop falls,
   roots grow or a plant wilts), so the script only draws them; `tests/test_garden.py` checks
   them against `service.garden_view` and against the stylesheet's ranges.
3. **Roots on one scale for the page**: a topic's roots are as long as it holds, against a month
   (the bottom of the glass): shaky, fine and solid 1, 5 and 20 twentieths; watered just in time,
   from 5 to 16. The opening shelf is the game's three topics as they stand on Monday, and exam
   day is the same three after the app's week (water 19, 19, 20; roots 5, 10, 18): the roots that
   grew are the week's work.
4. **Drawn only when something changed.** A frame is drawn when the page scrolls, a scene comes
   into view, the page is resized or its theme changes; a scene eases to where the page is and
   stops (D93). A scene's context is made when it first nears the screen. With reduced motion, or
   where scrolling does not drive animations, each scene is drawn once, complete.
5. **A flat drawing under it**, in SVG from the same profiles: curved glasses, water that falls
   with `--zq` as before, leaves that droop, roots, buds and flowers. It is what a reader sees
   without WebGL or without a script, or if the browser takes the context back.
6. **The words beside the picture.** Each pinned chapter's heading and lines are in its stage,
   the picture beside them and the step under them; under 860px, the words, the picture, then the
   step, in one screen.

**Not chosen.** Curves in CSS 3D (a glass of dozens of planes, every one a layer to composite,
and no glass, highlight or water surface). A 3D library such as three.js: a dependency, for the
maintainer to decide, and far more than six shapes need. Flat SVG alone: no depth, which the
owner asked for in D116.

**Disclosed.** Checked in Chromium only, with software rendering (SwiftShader), at phone size and
1280px, light and dark, with motion and with it reduced, and without a script; not on a real
graphics chip, a phone, Safari or Firefox. What it costs a slow device was not measured. The
garden is a likeness, as D116 says; the roots' scale and the flowers' colours are choices.

**Later (D119).** The owner chose new plants from six designs: a glowing succulent rosette in a
light theme, a plant of light in a dark one.

## D118. The explaining pages scroll as stories

*The owner, 2026-11-05, of How it works, the science page and How the code works on a laptop:
"when u scroll on the laptop everything is far from eachother wheres i want some smooth flow of
everything".*

**What was wrong.** A pinned scene (D112) was a track three screens tall whose stage, as tall
as the window, held a figure about half the page's width, centred, with one step under it; the
chapter's words had scrolled away above it. On a laptop most of every screen was sky: measured
by `benchmarks/scroll_flow.mjs` at 1440 × 900, 46 % of How it works' screen was empty on
average, 40 % of the science page's and 38 % of the code page's, and at 57 %, 37 % and 31 % of
the positions more than half of it (AUDIT 96).

**Chosen: on a laptop each pinned scene is a story in two columns.** Its words and then its
steps go down the left column; its picture, in the right one and wider than the page's text,
stays in view while the steps go by, and the step the picture is at is lit, the others faded.
The story is the scroll timeline (`--pin`); the track and the stage hand their pieces to its
grid (`display: contents`), so the templates only gained a wrapper (`.code-story`), with each
scene's opening words moved into it on the code and science pages. Without scroll timelines or
with motion reduced, the same two columns stand still, every step listed. The garden's WebGL
follows the same timeline. Measured again at 1440 × 900:

| page | screens | empty, mean | positions more than half empty | tallest empty band |
|---|---|---|---|---|
| How it works | 15.5 → 8.9 | 46 % → 11 % | 57 % → 0 % | 505 → 196px |
| The science behind it | 13.7 → 8.8 | 40 % → 10 % | 37 % → 0 % | 278 → 276px |
| How the code works | 19.4 → 12.2 | 38 % → 13 % | 31 % → 0 % | 439 → 368px |

At 1280 × 800, empty on average: 39 % → 11 %, 35 % → 9 % and 35 % → 16 %. **Phones keep the pinned stage**: in one narrow column a
picture beside its steps does not fit, and the stage already holds the picture and its step in
one screen. Their figures move from run to run (the garden's positions more than half empty were
5 % to 28 % in four runs of the same page), as each position is measured while pictures settle.

**Not chosen.** Shorter tracks alone: less scrolling, the same empty screens. A taller figure
filling the stage: most charts are wide and short, and would have floated in white. Moving every
chapter's words into the stage, as D117 did for the garden: the code page's run to a formula and
two paragraphs, too tall for a phone's stage.

**Disclosed.** Checked in Chromium only, at 1440 × 900, 1280 × 800 and 390 × 844, with motion and
with it reduced. The lit step is the one whose part of the scene is running, not exactly the one
in the middle of the window: they drift apart by up to a step's height where a scene's words are
long.

**Later (D120).** The owner found the text sparse and the formatting uneven: on a laptop each
pinned scene is now one panel held in view, its words and all its steps on the left (the current
one lit, none faded), its figure filling the right; the three pages share one layout without the
sky or the glass.

## D119. How it works: a glowing rosette by day, a plant of light by night

*The owner, 2026-11-05, of D117's hyacinths: "the plants are ugly, make 6 propositions of design
and ill pick the best". Six live previews were put to him on one page (a money plant in a glass
globe, low-poly facets, a clay sprout, a succulent rosette, a bonsai on a rock, a plant that glows
in the dark), each with sliders for the days since a revision and for the roots, and a week played
out. His choice: "rosette for light and night glow for dark, but maybe the rosette should have the
glowing feature too so that it is more visible how it withers".*

**Chosen.**

1. **The plant follows the theme.** In a light theme a topic is a succulent rosette in a shallow
   glass bowl on pebbles, its forty leaves in the golden-angle spiral; in a dark theme, a plant of
   light in a jar of glowing water, tendrils with glowing tips. Water is still how much is
   remembered, roots how long it lasts; the server's numbers are unchanged (D116, D117).
2. **The rosette glows while it is remembered**, as the owner asked, so that withering shows: its
   leaves lit from within, a mint rim, a pool of light on the water under it. As the water falls,
   the glow fades, the leaves thin, droop and turn from mint to brown, the outer ones first; a
   wilted plant (chapter 5's forgotten one) loses its light at once. On a light page added light
   vanishes into the white, so the rosette's glow is laid over the page rather than added to it.
   The plant of light dims and curls as it is forgotten.
3. **In flower on exam day**: arching stalks of coral bells, as an echeveria flowers; the plant of
   light's tips swell and brighten.
4. **Three bowls in a triangle**, the middle one in front, so that low, wide bowls fill the
   picture; two side by side. The jars stay in a row.
5. **The flat drawing** (no WebGL, no script) is the rosette in its bowl, in both themes: its water
   falls with `--zq`, its leaves droop to either side and brown, its pool of light fades.

**Not chosen.** The other four designs. One plant for both themes: the owner wanted each theme's
best. A glow added to the light page (as the dark design's is): it does not show on white.

**Disclosed.** The rosette's glow and colours, and the flowers, are choices of taste. Checked in
Chromium only, with software rendering, at 1280px and 390px, light and dark, with motion and
without, and without a script.

## D120. The explaining pages, read: no glass, one layout

*The owner, 2026-11-05, after D118 and D119 went live: "the formatting is not homogeneous, the
text is sparse and there is too much void from the laptop page, something is off so use again the
7 laws of UX UI to make it easilydigestible, starting from removing liquid glass from those pages
that explain".*

**What was wrong.**

1. **Glass.** How it works, the science page and How the code works wore the app's sky (dusk on the
   public pages, D110; the plan's own on a plan's) under translucent bars. Their panels had been
   solid since D112 (AUDIT 94), but the page behind them and the bars were still sky and glass.
2. **Not homogeneous.** Three widths (the science page's text at 46rem, the code page at 64rem,
   D118's stories breaking out to 74rem), three different openings, the science page's sections in
   one column where the other pages had two, chapter numbers on two pages of three.
3. **Sparse.** D118 spread a scene's steps down a tall column, a quarter to a third of a window
   apart, with only the current one lit and the others faded to 42 %. At 1440 × 900 a screen
   showed on average 51 readable words on How it works, 184 on the science page and 123 on the code
   page. D118's measure counted a row as filled by a faded line, so it had scored this well
   (AUDIT 97).

**Chosen, by seven laws of UX.** The laws are Jon Yablonski's *Laws of UX* and the Gestalt
principles it collects. No source agrees on which seven; these are the seven that bear on a page
that is read.

1. **Prägnanz (simplicity): no sky and no glass.** The three pages are on the app's plain grey
   (`--bg`) with solid panels and solid bars, on a plan's pages too (`sky` set to nothing in
   `web/app.py`, `body.reading`). Their accent is the app's own blue, not the moment's.
2. **Similarity: one layout for all three.** One width (74rem), one grid (words 5, figure 7, 56px
   apart), one type scale (body text 18px on a laptop, headings up to 2.25rem). Every chapter is
   numbered, and each opening is the same: a kicker, the title, one line, the chapters, then the
   figure beside them. The science page gained its numbers and its list of chapters, using its own
   headings, so no new sentences.
3. **Jakob's law: laid out as articles and documentation are.** Words on the left, the figure on
   the right, both from the top. The words stay beside a taller figure as it goes by.
4. **Proximity.** A scene's words, steps and figure are in one place. A paragraph that follows a
   scene sits beside its evidence: the science page's note on FSRS-6 next to the maths, the
   research next to the formula, the code page's sentence next to its table.
5. **Common region.** Each pinned scene is one panel, as tall as the window under the bar, and its
   figure's card fills the whole right column.
6. **Miller's law.** A scene's three to five steps are read together as one list, not one at a
   time three screens apart.
7. **Von Restorff effect.** The step the figure is at is the only one lit, in the text's colour on
   the accent's tint. The others stay readable in the muted grey (4.8:1), not faded.

**How.**

- The templates gained one wrapper: `.code-panel`, inside each `.code-story`.
- On a laptop the panel is the grid: the words, then the steps, on the left, the figure on the
  right. The track and the stage that a phone pins give their pieces to it (`display: contents`).
- Where scrolling drives animations, the story is the track (180vh, 260vh for AO*'s 114 nodes)
  and its panel is held in view. Elsewhere, and with motion reduced, the panel simply stands.
- Every other part of the pages is a row (`.code-scene`, `.code-row`) of the same two columns.
- A laptop's window is shorter than its screen. Under 860px tall a panel's words are set a size
  smaller, so that the longest (the code page's value iteration, the science page's spacing) fit:
  checked by script at windows of 1440 × 789, 1280 × 689, 1366 × 657 and 1280 × 640. Under 640px
  the panels do not pin.
- Phones keep D112's pinned stage, on the plain grey.

**Measured** by `benchmarks/scroll_flow.mjs`, which since D120 also reports the share of the
window's area that content covers and the words readable on a screen, at 1440 × 900:

| page | screens | empty, mean | tallest empty band | area covered | words read |
|---|---|---|---|---|---|
| How it works | 8.9 → 10.5 | 10 % → 14 % | 196 → 198px | 46 % → 44 % | 51 → 77 |
| The science behind it | 8.8 → 9.8 | 10 % → 14 % | 276 → 151px | 39 % → 49 % | 184 → 224 |
| How the code works | 12.2 → 13.8 | 13 % → 15 % | 368 → 167px | 42 % → 49 % | 123 → 178 |

At 1280 × 800, words read: 48 → 79, 175 → 214, 117 → 170; area covered: 52 % → 51 %, 46 % → 54 %,
48 % → 54 %. No position is more than half empty on a laptop, before or after. At 390 × 844 the
pages are as before, within the run-to-run noise D118 noted.

**Not chosen.**

- D118's column of steps, unfaded: the steps then still stand screens apart.
- The steps under the figure, in the right column (tried first): on How it works the left column
  held only a chapter's two lines, and 25 % to 32 % of a screen was empty.
- Each panel centred in the window (tried): blank bands above and below; 21 % to 24 % empty.
- Every chapter's words in the stage, as D117 did for the garden: too tall for a phone's stage
  (D118's reason).

**Disclosed.**

- The pages are longer on a laptop (How it works 8.9 → 10.5 screens, the science page 8.8 → 9.8,
  the code page 12.2 → 13.8): each pinned scene holds the window for about four fifths of a screen
  of scrolling.
- The mean empty share rose from 10 % to 13 % to 14 % to 15 %. The faded steps D118 spread down a
  column covered rows without being read; the white margin under each panel now does not cover
  them.
- Checked in Chromium only: at 1440 × 900, 1280 × 800 and 390 × 844, with the four window sizes
  above for fit; light and dark; with motion, with it reduced, and without a script.
- The seven laws are a lens chosen here, not a measure. Whether students read further is for the
  pilot.

## D121. How it works for the whole screen, cramming told honestly, a month to plan

*The owner, after D120 went live: "the whole 3d thing is ugly, it clearly should employ the whole
screen but you keep it like a mobile version even from a laptop. make two different versions. for
the laptop make use of the whole available space, make it interactive, propose ideas, anyone needs
to understand what that is about, the text also is very AI, curate the text a bit better and make
it more natural". Three proposals for the laptop followed as a page to try (A, a story that fills
the screen; B, a month to plan; C, a week against the app). Of B's and C's scoring the owner said
"one can easily say that cramming works best" (AUDIT 98), then chose "A+B".*

**What was wrong.**

1. **A phone's page on a laptop.** D120's panel gave the plants seven twelfths of a 74rem column, in
   a card 540px tall at most, and said nothing on them: the reader had to find the number in the
   step on the left.
2. **Words written to a rule.** D116's sentences were of 15 words or fewer, one idea each. They read
   as a list ("The water in its pot is how much you remember. Day by day, the water goes down. That
   is forgetting."), and the owner read them as written by a machine.
3. **A game cramming won.** D116's week counted every revision as a success and scored a plan only
   by its weakest topic on exam day (AUDIT 98).

**Chosen.**

1. **The laptop's story (A).** How it works is wider than the other two explaining pages (100rem;
   the words a third, the plants two thirds). Every chapter is pinned, so each holds the window as
   it is scrolled through, its plants filling the right two thirds. Over each plant, a label: how
   much of the topic is remembered (red under 90 %) and a line under it (how long it lasts, or when
   it is due); in the corner, the day it is (Day 0 since the lecture, Day 19 without revising, Exam
   day). Along the bottom of the window, the seven chapters, the one in view filled as far as it
   has gone by. The labels and days are the server's words, set as text in an SVG over the canvas
   (garden.js sets no style), from each scene's numbers at 25 stops (`GARDEN_FINE`) beside the
   eight the flat drawing has. On a large, wide figure three bowls stand in a row, each label clear
   of the others' plants; the camera frames the bowls themselves wherever the scene turns them.
   Phones keep D112's pinned stage and its steps one at a time, with the labels' numbers.
2. **Words as a person says them.** Every sentence was written again, up to 25 words, a chapter's
   paragraph up to 60 (`tests/test_garden.py` holds both). The chapters are named by what a
   student would ask: Forgetting, Its own pace, Timing, Cramming, Your evenings, Telling it, Plan a
   month. The title: "Your courses are plants. Your memory is the water."
3. **Cramming, told honestly.** A new chapter, "Cramming works, for a day": the same new topic
   crammed the night before an exam on day 28, or revised whenever it dips to 90 %, each through
   the exam and a month on. A revision works only as often as the topic is still remembered then
   (its recall is that chance): it is then a success (grade Good), otherwise a lapse (Again). Each
   revision splits the topic's future in two; the expected recall is followed through the month,
   the futures whose memory ends alike merged (`service._expected_memory`). Crammed, the topic is
   at 96 % on exam day against 91 % for the spaced one; it spent 25 days below 90 % before it,
   against 4, after two revisions; the cram, after 27 days untouched, works 68 % of the time; a
   month after the exam, 80 % against 85 %. The chapter says the first number as plainly as the
   others, and that the app plans exam practice in the last days too.
4. **A month to plan (B).** D116's week game gives way to a month: three made-up courses (new and
   found hard, new, known fairly well), a revision ticked on any evening from day 1 to 27, the exam
   on day 28. The server answers with each course's memory through the month as a line, the chance
   each ticked revision works, the plants on the day under a slider (or Play), and the plan scored
   three ways against the app's rule (each evening the course that has faded most, once it is down
   to 90 %): the weakest course on exam day, the days below 90 % before it (all three added up), and
   the weakest a month later, with the revisions each took. What the comparison says comes first:
   the best cram (one course an evening in the last three, the hardest last) wins exam day, 94 %
   against 91 %, and the page says so, then that its courses spent 63 days below 90 % (the app's
   plan: 11) and stand at 74 % a month later (83 %). Ticking every course every evening wins all
   three, with 81 revisions against the app's 6, and the page counts them. The app's plan revises
   on days 2, 6 and 15, 3 and 12, and 13. A GET form, so it works without a script; with one, a
   tick asks for the month again (`?part=month`) and garden.js puts its lanes, scores and plants in
   place, keeping the focus and writing the plan in the address.

**Measured** by `benchmarks/scroll_flow.mjs` at 1440 × 900, How it works against D120's:

| | screens | empty, mean | tallest empty band | area covered | words read |
|---|---|---|---|---|---|
| How it works | 10.5 → 15.4 | 14 % → 15 % | 198 → 207px | 44 % → 58 % | 77 → 135 |

At 1280 × 800: 16.0 screens, 58 % of the area covered (51 % before), 144 words read (79). The page
is half as long again: two more chapters pinned (Forgetting, which was a row, and Cramming) and the
month to plan. On a phone (390 × 844) it is 23.6 screens long, 35 % empty on average and 8 % of the
positions more than half empty, its tallest empty band 482px: each pinned chapter is a track three
windows tall there (D112), and there are six. The science and code pages are unchanged (225 and
178 words read at 1440 × 900).

**Not chosen.**

- C, a week against the app (the third proposal): five evenings cannot show what spacing buys,
  and in the real app the last days before an exam go to exam practice, which the memory model
  does not count. The month shows the same choice over the time it takes.
- Scoring exam day alone, or every revision as a success: that made cramming look best (AUDIT 98).
- The full screen without the words beside it (A as first proposed, the words over the plants):
  on a laptop's wide window the plants need two thirds of it, and the words stay readable beside
  them in the layout D120 gave the three pages.
- Telling one student's luck (a random success or failure for each revision): a reader would see
  a different month each time for the same plan; the expected value is the same every time.

**Disclosed.**

- The courses are made up, and the app's plan in the month is its self-test rule alone, without
  the exam practice the real app also plans in the last days. Exam practice is not in the memory
  model (D46).
- The expected value merges futures whose stability and difficulty round alike (to a fortieth of
  a log-day, a quarter of a difficulty point); it is the model's estimate with average weights.
- The month's verdict ranks the plans by rules written here (the weakest course; the days below
  90 % added up), which are choices.
- Checked in Chromium only, with software rendering: at 1440 × 900, 1366 × 657 and 390 × 844,
  light and dark, with motion, with it reduced (the panels stand, the scenes at rest, the slider
  still works) and without a script (the month is a form). Not on a real phone, Safari or Firefox.
  The month's labels can still touch at some widths in a dark theme, where the jars are narrow.
- The wording is the owner's to review; the Italian was not written by a native speaker.

## D122. The science page told as a new city (reverted)

*D122 told the science page as a student's first month in a new city, each figure a street map
turning into its chart as it was scrolled (merged in pull request 64). The owner, seeing it live:
"the plant was more 3d and was better, this route thing is not good... we need to find something
else". It is reverted whole, so the science page is again D113's and D120's, and the street map
planned for the code page (D123) was not merged.*

- What was learnt: a flat map drawn in lines read as a diagram, not a world; the owner prefers
  the depth and material of How it works' WebGL plants (D117, D119).
- Next, the owner's choice of three 3D worlds (one garden, the brain, glass and light): the
  brain, a memory trace as a glowing pathway that dims as it fades and thickens with each recall,
  first as a live prototype to approve before the pages change.
- The code is in the history (the merge of pull request 64 and its revert), should a part of it
  (the picture that turns into the chart, static/morph.js) be wanted again.

## D123. The code page as a street map (not merged)

*The code page's companion to D122, AO*'s search drawn as a street map with a "Recalculating"
banner, was built on a branch and not merged once D122 was reverted. Its number is kept so that
D122's note, which names it, stays true. The code is on the local branch `d123-street-map`.*

## D124. The app opens on Today again (replaces D102)

*The owner, 2026-11-12: "revert the original landing page to the calendar, then the others".*

**Chosen.** Every student opens on Today, the plan: on a laptop the day's panel beside the
calendar, on a phone the panel with the Calendar tab next to it. Today is the first tab again
and the top bar reads Plan, Courses, Community, You, as D97 had them; the feed is the fourth
tab, one tap away. The one door, `/p/<plan>/open` (D102), stays: `service.front_door` now
answers "today" for everyone, so an installed app, the demo's Start, signing in and the logo keep
working, and the choice has one place to change. Offline, the installed app opens on the kept
Today.

**Rejected.** Opening on the week's calendar itself (on a laptop the plan is the calendar
already; on a phone the day's panel says what to do now, and the calendar is the next tab).
Removing the door (every installed app points at it).

**Disclosed.** Checked in Chromium only. A home-screen app added under D102 may keep opening on
the feed until it is added again (browsers update an installed app's manifest on their own
schedule).

## D125. The memory loop, first slice: beat the forecast, memory banked, done for today

*The owner, 2026-11-12: "i want to work on a more sophisticated addictive hooking reward system:
study how to do so and return the references from neuroscience and already existing practices
from big companies / this must serve the purpose of keeping students motivated and
self-entertained by studying so that they do not even think about doing anything else". The
research is the owner's doc "A reward system that keeps students studying" (2026-11-12); its
first slice was approved: "I approve, let's start building".*

**The line.** Hooked on the plan, not on the app. The app's own memory model says why the goal
stops at the session: under FSRS-6 with the default weights, a second correct pass on the same
day multiplies how long a topic lasts by 1.10 at 12 hours, 1.05 at a day, and 1.00 from 2.12
days on (`memory._short_term6`), while a review 3 days later of a topic lasting 3 days takes it
to 11.1 days; students already sleep too little; and in a 16-week course a leaderboard and
badges lowered motivation and exam scores. The references behind each mechanic are in the doc
and, for the sentences the app says, go through docs/REFERENCES.md before they are quoted: this
slice quotes none.

**Chosen.** Three mechanics, each on what the plan already computes (D24's forecast, D82's
memory days):

- *Beat the forecast.* `service.session_gains` carries, for each self-test, the model's chance
  of recalling the topic as the session starts (rounded to 5, D24), whether it is a second pass
  on the same day, the memory days it adds (stability after less before, as D82 counts them),
  the day the topic then holds 90 % until, and when it comes back. The chance is said only
  after the answer (`_memory_words`): "Recalled, as the model forecast (90 %)"; a recall the
  model gave under 50 % is "Against the odds", the one moment celebrated (confetti once, D30);
  "Forgotten at a 90 % chance: good to know now. It comes back Mon 5 Oct 12:30" names no loss;
  a second pass the same day is "Already safe today: a second pass on the same day adds
  nothing". Before the answer the card says "Answer first: the model's forecast shows after".
- *Memory banked.* The same line says "+5 memory days: it now holds until Fri 9 Oct", the day
  the plan brings the topic back (the planner's own rule), and "A model's estimate".
- *Done for today.* Once every session planned today is reported, none is running and at least
  one was kept, Today's next step is "Done for today": the sessions kept, the day's memory days
  (the net, when positive) and "Next: Fri 9 Oct 08:00, Self-test: Algebra 3". It says "The next
  session adds more than a second pass tonight would" and asks for nothing more; "Study
  something else" stays a link. Today also shows the latest answer for three hours ("Just
  answered"), where "How did it go?" stood, with the session's course colour.

**Why these words.** Positive feedback about competence raised intrinsic motivation where
rewards promised for doing the task lowered it (Deci, Koestner and Ryan 1999, `deci1999`); the
surprise is the student's own memory against a forecast, never chance; forgetting is
information, since a broken streak hurts most when the student blames themselves (Silverman
and Barasch 2023, `silverman2023`); the chance is shown after the answer so that D55's
calibration (predicted against reported recall) is not anchored by the prediction.

**Rejected.** The chance before the answer (anchors the self-report and D55). A number for a
loss on the card (D82's weekly chart keeps the net). Confetti on every close of the day (a
reward that always comes stops registering; D30 keeps it for achievements). Points, a currency,
a ranking, bought streak repairs, random prizes (the doc's "What not to build"; D9, D16, D41).
Changing the focus ladder (D67): the owner's choice stands; the memory line sits beside it.

**Disclosed.** Every figure is the model's estimate with average weights and a guessed starting
state (D24), said on each card. "Against the odds" at 50 % is a threshold chosen, not measured.
With a plan that reviews at 90 %, most answers read "as forecast"; the surprises come after a
lapse or a missed week. Whether the loop keeps students on their plan is for the pilot
(docs/PRODUCT.md). Checked in Chromium only, at phone size and 1280px. The rest of the doc's
proposal (repair and pause, a shared goal, the stakes and a curious start, reminders that vary,
unannounced moments, the semester recap) waits for the owner's next word.

## D126. Repair and pause: a missed day made up by studying, a pause that keeps the streak

*The owner, 2026-11-13: "now build the rest of the loop, repair and pause first". The
research doc's F: a repairable streak hurts less when it breaks (Silverman and Barasch
2023, `silverman2023`), Duolingo's Streak Freeze covers a skipped day and Apple's
watchOS 11 pauses the rings for a day, a week, a month or more without losing the
streak; repair by studying, never bought, never by an ad.*

**Chosen.**

- *Repair.* A day judged missed (D9: sessions planned, none reported, the week's
  forgiveness spent) can be repaired the next day or the one after
  (`progress.REPAIR_WITHIN`): Today's message slot offers it first ("Yesterday was
  missed. 25 minutes of focus today repair it.", with the course of the missed day's
  first session; "Not now" puts it away for the day). The card starts the focus timer
  with the missed day marked (`start_focus(repair=…)`, checked to be one of the last two
  days); when the session is saved to the diary, timed and at least `REPAIR_MINUTES`
  (25, the timer's shortest goal) long, the day is recorded repaired
  (`options["repairs"]`). A repaired day passes the streak on, like a forgiven one, and is
  judged before the week's forgiveness, so that stays for another day: the student
  studied for it. Minutes typed into the log repair nothing (D67). A student who did
  study that day is told to report its session instead: a late report counts on its
  day (D9).
- *Pause.* On Progress, under the streak: "Pause the streak", for today, until Monday
  (through Sunday), a week, or until a day, at most 90 days (`PAUSE_MOST_DAYS`). Its days
  are "paused": they neither extend nor break the streak, and the plan is made again at
  once leaving them free (`make_schedule(pauses=…)`, `_assist` drops their blocks; a
  session the student placed there stays). A studied day stays studied. One pause at a
  time: a new one replaces one under way or ahead; past ones are kept, for the days they
  explain. "Resume" ends a pause yesterday. Today's strip says "Paused until Mon 5 Oct:
  your streak waits." The day grid gains two states, repaired (the forgiven dot in the
  accent) and paused (hatched), and its hint says what they are.

**Rejected.** Repair by reporting any later session as done (the streak would never
break: the forgiveness already covers one day a week). Repair bought, with points or an
ad (D9). An untimed make-up (minutes typed earn nothing, D67). Making the paused days
busy in the calendar (a pause is about the streak and the plan, not the timetable). A
pause with no end (90 days is a semester's share; set another after).

**Disclosed.** The 48 hours and the 25 minutes are choices, not measured. A pause moves
the sessions it displaces to the days after it; near an exam that can mean more in fewer
days, as a skipped week would. Checked in Chromium only, at phone size and 1280px.
Whether repairs and pauses keep more students on their plan is for the pilot.

## D127. Studying together: a pair's days in a row, and a group's memory days

*The owner, 2026-11-13: "now build the rest of the loop". The research doc's G: a
shared goal, opt-in, nobody ranked (Sailer and Homner 2020 found competition combined
with collaboration worked best for behaviour; a leaderboard and badges lowered
motivation in a real course, Hanus and Fox 2015), and Duolingo's Friend Streak, two
friends keeping a shared count (the company reports learners with one are 22 % more
likely to finish the day's lesson, blog.duolingo.com/product-lessons-friend-streak; a
company figure, not a study). The shared goal was already there: a study group's weekly
goal (D28), which the doc missed.*

**Chosen.**

- *Studying together.* A student with a handle asks another, on their profile ("Study
  together"); the other accepts or declines on Progress, and a decline tells nobody, as
  with a follow (D16). Both then see, on Progress beside the streak and in one line under
  Today's strip, the days in a row they both studied since they agreed
  (`progress.together`): a day either missed ends the run, a day both studied adds one,
  any other day holds it (rest, forgiven, repaired, paused, today, a day one has no plan
  for), each day judged by each student's own streak in their own time zone (D9, D126).
  And whether each studied today. Never anyone's hours, plan, courses or sessions. Three
  pairs at most, asks included (`social.BUDDIES_EACH`). Either can stop it; a block,
  leaving the network or deleting the plan ends it; the plan's copy lists it (D43); the
  privacy notice says what the other sees.
- *A group's memory days.* A study group's card and page add the memory days its
  members' reviews added this week (D82's measure, summed), beside D28's hours: the
  learning, not only the time.

**Rejected.** Changing a group's goal from hours to sessions kept (D28 chose hours; the
owner's to change). A pair's count of hours or sessions (a comparison between two, D16).
More than three pairs (each is a daily look at someone else). Showing a pair to anyone
else. A message when the other studied ("Ada studied, your turn"), which pressures; the
line says only what is true.

**Disclosed.** In a pair, the count and the "studied today" give away each one's days
studied, as a group of two gives away its hours (D28); the profile's hint and the privacy
notice say so. Two time zones far apart judge "today" each in their own. The 22 % is
Duolingo's figure, not measured here; whether pairs keep students on their plan is for
the pilot. Checked in Chromium only.

## D128. The stakes and a curious start: what a session keeps, and one question to open it

*The owner, 2026-11-13: "now build the rest of the loop". The research doc's A (the stakes
before a session: reward anticipation before learning went with better memory a day later,
Adcock et al. 2006, `adcock2006`) and B (a curious start: information met in a state of
curiosity was better remembered a day later, Gruber, Gelman and Ranganath 2014,
`gruber2014`). Both are single laboratory studies, the first with money as the reward, the
second with trivia questions; how far either carries to a self-test of lectures is not
known.*

**Chosen.**

- *The stakes.* A self-test still to do says, in one line, its exam in so many days and the
  day the topic stays above 90 % until if it is recalled in this session: "Exam in 9 days.
  Recalled in this session, it stays above 90 % until Tue 6 Oct (a model's estimate)" (the
  case `tests/test_stakes.py` pins), or
  "until then" when that day is the exam's or later (`service._stakes`, from
  `session_gains`, which now carries the exam's day). It shows on the session's card (Today,
  its own page) above Start focus, on the focus page's offer from the plan, and under the
  course while the timer runs. Only what the plan's forecast says: no loss framed as a
  threat, no number the model does not give. It goes once the session is answered or
  skipped, and for a second pass on the same day, which adds nothing (D125).
- *A curious start.* A planned self-test of a week of lectures opens, when that week has
  cards (D87), with one of them: "Before your notes", its question, and "Think of the
  answer: it shows when you finish". The answer is not on the page while the timer runs; it
  comes on the log page with a link to the week's cards (`service.opening_card`,
  `opener_view`). The card asked is the one the model gives the least chance of recall now
  among those answered before (most to gain from being asked), else the first written; a
  question asked again would be the same card until it is answered in the deck. One tap
  sets it aside ("Skip the question"), and then nothing comes at the end.

**Rejected.** The chance of recall at the session's start (D125 shows it only after the
answer, so the student's own report is not anchored). A question for a session without
cards made up from the syllabus's topic names (there would be no answer to give at the end).
Grading the opening question as a card review (the student has just studied the topic, so
the grade would not be a recall); the deck stays the place for that. Counting a skipped
question against anything.

**Disclosed.** The 90 % day is FSRS-6 with average weights after a recall graded good, as
the plan assumes; a hard or failed recall keeps it less long, which the memory loop says
after the answer (D125). Far from the exam the line gives a number of days months away, true but
not pressing. The heartbeat of the focus page leaves the stakes out, so it does not replay the
plan every beat. Checked in Chromium only, at phone size light and dark and at 1280px;
whether students answer the question or skip it is for the pilot.

## D129. Reminders that vary: one true line in turn, and none between bed and getting up

*The owner, 2026-11-13: "now build the rest of the loop". The research doc's J. Duolingo
builds its daily reminders from hand-written templates and found that a template the
learner had not seen recently moved behaviour more than one seen often; choosing and
varying them raised daily active users by 0.5 % and new-user retention by 2 % over its
baseline (Yancey and Settles 2020 [ref:yancey2020], a company's study of its own app).*

**Chosen.** A reminder's title stays the course and the time (D38). Its body starts with
the kind of session and its length, then says one line among those that are true of it:
the first sentence of what to do (as before); for a self-test, the day a recall now keeps
the topic above 90 % until, or "until the exam" (D128, "an estimate"); for a self-test of a
week with cards, "It opens with one question from your cards" (D128); on a day with more
than one session, "Session 2 of 3 planned today". The line is taken in turn along the
plan's sessions (`service._reminder_payload`, `_reminder_lines`), so two sessions in a row
with the same lines to choose from never say the same one. And no reminder is sent between
going to bed and getting up (D69, `service.awake`): one still due then waits until the
student is up, within its window (D38); one for a session after bedtime is not sent. The
reminders' settings say both.

**Rejected.** A bandit that learns which line works (no answers to learn from yet; the
pilot's measures, D15, would say first whether reminders bring sessions at all). Lines about
the streak, a classmate who studied ("your turn"), or what is lost by not studying: they
pressure, and D127 rejected the classmate one already. The card's own question on the lock
screen (a notification can be read by anyone near the phone). More than one reminder a
session.

**Disclosed.** Rotation is not what Duolingo measured, which chose templates by their
results as well as their freshness; whether varied lines bring more sessions here is for
the pilot. A reminder's line follows the plan as it stands when it is sent. Not tried on a
real phone (D38); checked through the tests and the settings page in Chromium only.

## D130. Unannounced moments: a topic's first month, and a first week without forgetting

*The owner, 2026-11-13: "now build the rest of the loop". The research doc's H: rare
moments, never scheduled in advance. In Deci, Koestner and Ryan's meta-analysis of 128
studies, rewards expected in advance lowered free-choice intrinsic motivation, and positive
feedback raised it (d = 0.33) [ref:deci1999]. So each moment is feedback on what the
student did, said once, with nothing counted down to.*

**Chosen.**

- *A topic's first month.* When an answered self-test takes its topic past 30 days of
  memory for the first time (the stability before it under 30, after it 30 or more, the
  answer a recall), its card adds "A first for this topic: it now holds more than a month",
  beside the garden's flower, with confetti once (D30). Never on a forgotten answer, a
  second pass on the same day, or a topic already past a month (`_memory_words`'s `month`,
  from `session_gains`, which now carries the stability after the review).
- *A first week without forgetting.* The first week (Monday to Sunday) in which the student
  recalled every self-test they answered, at least three, is said on Today the week after:
  "A first. Last week, you recalled every self-test you answered", with how many and which
  week (`service.recall_week`, Today's message "recalled", after the first run's messages
  and before the semester's end). Only the first such week, and only the week after it.

**Rejected.** The doc's "a first week with every topic above 90 %": on the sample semester
followed exactly as planned (two courses, seven weeks), some topic taught before each week
was below 90 % at the end of that week (`tests/test_moments.py` pins it), as
`benchmarks/rule_vs_planner.py` finds that not every topic reaches 90 % at the weekly
budget; the moment would never come. A bloom that
grows in the margins' garden with the count of month-long topics (the garden already follows
the streak, D73; two meanings in one picture). A progress bar or a count toward either
moment, which would make it announced. A moment for hours studied (D41 has no badge for
hours).

**Disclosed.** A month is FSRS-6 with average weights after the answer as graded; the
threshold is a choice. "Recalled" counts Some, Most and All, as the plan does (D125); a
student who answers Most every time gets the week easily, as the plan assumes they would.
Checked in Chromium only, at phone size light and dark and at 1280px; whether these moments
keep anyone studying is for the pilot.

## D131. The semester recap: the semester in numbers, shared by choice, kept at the next start

*The owner, 2026-11-13: "now build the rest of the loop". The research doc's I: after the
last exam, what the semester built, as Strava's yearly recap does for a year of training (a
company's practice, no figures behind it here); a card the student may share; the next
semester starting clean (D36 already keeps the past for the streak and the trends).*

**Chosen.** Once every exam is behind and no deadline is ahead (`semester_over`), Today's
end-of-semester card leads to `/p/<plan>/recap` ("See your semester in numbers"), and the
page shows, as tiles: the study sessions kept and their hours, the days studied, the best
streak, the memory days the semester's reviews added (D82's measure, summed), the topics
that hold a month or more (D130), the recalls against the odds (D125) and the best run
studied together (D127) when there are any, and the exams as the student said they went
(D105) (`service.recap_view`). Every figure is one Progress already shows; the best streak is
Progress's own (a test checks). Sharing is the student's: the device's share sheet, or the
text to copy without a script, with the numbers only, never the plan's address or the
courses' names (`recap_share_text`); nothing is posted for them. Starting the next semester
keeps the recap (`options["recaps"]`, the last eight), and the page then shows it, with the
earlier ones listed; the new semester's numbers start from zero.

**Rejected.** A post in Community made for the student (sharing is theirs to choose, D16). An
image to share (a drawing the server would have to make, for a text that says the same).
Comparing the semester with classmates' (no rankings, D16). Showing a tile at zero for the
month-long topics (it would read as a failure; the tile appears from one).

**Disclosed.** Sessions count what was reported or logged, as Progress does: a semester
followed without reports shows few sessions, and self-tests left unanswered a day after
they ended add no memory (D44). Memory days and month-long topics are the memory model's
estimate with average weights, said on the page. Checked in Chromium only, at phone size
light and dark and at 1280px; the share sheet itself was not tried (headless Chromium has
none), the copy button was.

## D132. Calm glass: tinted by default, nothing on the app's pages moving with the scroll

*The owner, 2026-11-16: "the liquid glass works well but sometimes it makes me nauseous",
then "do the glass". Apple answered the same complaint about its Liquid Glass with a
setting in iOS 26.1, Clear or Tinted, the second more opaque. WCAG 2.3.3 (Animation from
Interactions) names movement that follows scrolling, parallax first, as what brings on
dizziness and nausea in people with vestibular disorders.*

**Chosen.**

- *Tinted glass by default.* Glass's Automatic setting (D93) now tints the panes: their
  fill is 74 to 88 % opaque instead of 24 to 56 %, in light and dark, so what passes under
  the top bar, the tab bar and the cards as a page scrolls barely shows; their edge, light
  and blur stay. Automatic still turns the glass solid when the device asks for less
  transparency or blurs too slowly. See-through keeps D73's clear glass; Solid is unchanged.
  Settings say so.
- *Nothing on the app's own pages moves with the scroll.* The home and sign-in pages' cards
  no longer rise into place as they scroll in (D37's `surface`), and pages fade into each
  other without the small rise they had (D30). Only the explaining pages keep their scenes
  that run with the scroll (D109, D112, D113), and only with motion on. The top bar's
  colour still comes in as a page scrolls under it: a colour, nothing that moves.
- `tests/test_tinted_glass.py` checks the three tinted blocks, that every rule tied to the scroll is
  an explaining page's (under motion on) or the top bar's colour, and that page transitions
  only fade.

**Rejected.** Solid by default (the owner likes the glass; tinted keeps it). Removing the
explaining pages' scenes (they are what those pages are for, and Motion: Reduced already
stops them). A new setting (three were enough: Automatic changed its meaning).

**Disclosed.** Which effect made the owner feel sick was not isolated: the tint and the
removed movements are the likely causes, not measured ones. The tint's values are choices
of taste, checked in Chromium only, at phone size light and dark and at 1280px. Someone who
chose See-through before keeps it.

## D133. The study map: who is studying where, the people followed by name, everyone else counted

*The owner, 2026-11-16: "a map of where other people are studying if one wants to join
them", then "do … the map with dependencies and show only followers names but count other
people studying there already". The owner approved the two outside pieces this needs: a map
library and a tile server.*

**Chosen.**

- *Places, not people.* A place to study is public: a name and the point a student tapped
  on the map, added by anyone with a handle (D16), ten a day at most; the same name within
  about 50 m is the same place (`social.add_place`). Who added it is kept only for their copy
  and their deletion; deleting the plan leaves the place, without them (`_FORGET`).
- *Who is there.* A student is at a place only while their focus timer runs there, only if
  they chose that place for the session, and only if they said yes to Studying now (D108).
  On the map, the people a student follows show by handle and course; everyone else is a
  count; nobody blocked either way is shown or counted (`social.who_is_where`,
  `service.map_view`). A place's line reads "@bob and 1 other", "2 people studying",
  "Just you" or "Nobody yet".
- *Study here, Join.* From the map or its list, a running session moves to the place; without
  one, the focus page opens with the place chosen, and the session started there carries it
  (`start_focus(place=…)`, `choose_place`, `leave_place`). The focus page says "At {place}".
- *The map.* `/p/<plan>/map`, linked from Community and the focus page: Leaflet 1.9.4
  (BSD 2-Clause, the minified script, its stylesheet, images and licence shipped in
  `static/vendor/leaflet/`, its integrity checked against npm's record) over OpenStreetMap's
  standard tiles, under their usage policy: the attribution on the map and below it, a
  "Report a map problem" link, the tile address given by the page rather than written in the
  script, only the tiles in view (no prefetch, nothing kept for offline: the service worker
  ignores other sites), and a referrer on each tile, the site's origin only
  (`referrerPolicy: "origin"`), so a plan's address never reaches the tile server. Only this
  page's Content-Security-Policy allows the tile server's images, and only its
  Permissions-Policy allows the device's position: "Near me" uses it to move the map, and it
  is never sent. The list of places, with Study here and Join, works without the script;
  adding a place needs the map. Under reduced motion the map neither glides nor fades
  (D132), and in dark mode its tiles are drawn dark.

**Rejected.** Students' own positions as dots (where someone is, live, is what a stalker
wants; a chosen public place says enough to join). Counts only from three people upward (the
owner chose to count everyone else; a count names nobody). Names for everyone (a classmate
not followed has not agreed to be named). Reading places from OpenStreetMap's own data (an
Overpass query from the server, another outside service; students add the places they use).
A map library that draws vector tiles in WebGL (heavier, and a GPU at work under a page that
should rest, D93). Caching tiles on the server (the policy discourages it, and it would make
the server a tile proxy).

**Disclosed.** OpenStreetMap's tile servers see each viewer's internet address and the area
viewed (the privacy notice says so); they come with no guarantee and may refuse heavy use, in
which case another tile source is a one-line change in `service.MAP_TILES`. A place's point is
where a student tapped, not checked against the world; a place named to mislead can be added,
and there is no reporting of places yet. A count of one at a small place can tell a classmate
who is there. Places are listed within about 28 km of the map's centre, which is the student's
place, else their time zone's main city (D76), not their university. Checked in Chromium only,
at phone size light and dark and at 1280px, with tiles loaded through this environment's
network; not on a real phone, Safari or Firefox.

## D134. Flashcards from a file of notes, read for free on the server

*The owner, 2026-11-16, after trying the demo: "the option to upload notes and get flashcards
from them must be put even if via a dependency but it's better if it's free".* Notes became
cards only when pasted as text (D84), or through the model when AI is on (D101), which the
demo is not; there was no way to send a file.

**Chosen.**

- *A file, one step from Flashcards.* The Flashcards page opens with "Make cards from your
  notes": a file field and "Read my notes", sent as an ordinary form (no script needed). The
  page that makes cards takes a file too, above the box for pasted text, and shows the text it
  read in that box, to change before reading again. The cards are offered as for pasted notes,
  each picked by the student (D84).
- *What is read* (`notefiles.py`): PDF (pypdf, already in the `web` extra, D90), Word
  (.docx), PowerPoint (.pptx), LibreOffice's text and slides (.odt, .odp), Markdown and plain
  text, told apart by their first bytes. The Office and OpenDocument files are zips of XML,
  read with Python's own `zipfile` and `ElementTree`: no new dependency, no model, no cost,
  nothing sent anywhere, the file not kept.
- *The shapes the rules read are kept.* A heading becomes a Markdown heading, a list item a
  "- " line, a word in bold `**…**` (so a bold term becomes a blank to fill), a whole short
  line in bold a heading, as students write them. A slide's title goes over its points, so a
  slide becomes a list to recall; a deck is read in its own order (the presentation's list,
  not the files' numbers), without slide numbers, footers or speaker notes; a deck of text
  boxes placed by hand takes its topmost short box as the title. Word's heading styles are
  read by their names in English, Italian, French, German, Spanish, Dutch and Polish;
  LibreOffice's through their parent styles. A PDF's symbol-font bullets become items.
- *Limits.* 10 MB a file (as a PDF), 25 MB unzipped in all, 300 slides, and the card rules
  now read 60,000 characters at once instead of 20,000 (about 20 pages; a longer file is cut
  and the page says so). An XML part with a DOCTYPE is refused (no format here needs one;
  declared entities are how XML is made to swell, and Python's expat bounds them too). Old
  binary .doc and .ppt files, photos and other kinds are refused with what to do instead.

**Rejected.** OCR for photos and scans: Tesseract or an ONNX model is a dependency for printed
text only, and handwriting, which most photographed notes are, needs a model; the AI path
(Claude Haiku, D101) reads text only too. A free model running on the server: gigabytes on
disk and seconds a page on the pilot's small server. python-docx and python-pptx: two
dependencies for what the standard library reads in a few lines. Keeping the file (it would
be a copy of a student's notes the server does not need).

**Disclosed.** Cards come from rules, so notes written as prose give few: definitions,
questions, lists and words in bold give most. Tried on files written by the tests and by
LibreOffice (as .docx, .pptx, .odt, .odp and PDF), not on files saved by Microsoft Office,
Google Docs, Pages, Notion or a note-taking app. Equations, tables' layout and pictures are
not read. Checked in Chromium only, at phone size and 1280px.

## D135. The tips where they apply: beside a session, a card, a course, the calendar

*The owner, 2026-11-16, after trying the demo: "the study tips and neuroscience of learning
should not be hidden like this but rather appear in context, when focusing, when planning,
etc..."* A tip reached Today only when nothing else needed saying (D97's one message, the
tip last), Focus showed one about breaks, and the rest waited on the tips page under You.

**Chosen.**

- *One tip a page, beside what the page is about* (`service.tip_here`, `_why.html`): Focus
  before a session, a self-test's cards, Flashcards, cards from notes, the calendar, the
  week's settings, a course, Tasks and Progress. Each comes from the tips of D62 and D91 (the
  science of learning and the brain's tips among them), with the evidence each already
  carries: the advice, then folded, without a script, what the research found, how strong it
  is, what it does not show, its checked sources, and a link to its topic on the tips page.
- *Why here, from the plan.* A line above the advice says only what the plan holds: "This
  session is a self-test: recall what you can before you look at your notes" when the session
  about to start is one; "{course}'s exam is on {day}: sleep over the weeks before it counts"
  from three weeks before an exam; "Your plan comes back to {course} on {days}", the next
  three days of one course's sessions; a test in a course from its timetable. These come first; otherwise a page's tips take turns day by day, each page at its
  own offset, so two pages seldom show the same tip on the same day.
- *Calm.* A card with the spark (D66) on most pages; on the calendar one line above the grid,
  its research scrolling inside the line when opened, so the grid keeps its room. Tips off
  (Settings, Look) removes them all; the line and each card link to that switch.

**Rejected.** A tip on every card of a page (more words than the page's own). Showing tips as
pop-ups or in the tour (they would interrupt what the student came to do). Tying the calendar's
mix of courses in a day to interleaving (the research mixed kinds of problems within one
subject, not courses: the course page says that instead). Tips chosen by a model (D65's rules
stay: a tip may cite only a checked reference).

**Disclosed.** Which tip a page shows is chosen by rules from the plan, not from what a student
needs; the lines tie research done elsewhere (laboratories, other subjects, other goals) to
this plan, and each tip's "what it does not show" says how far. Whether students read them
more here than on the tips page is for the pilot (`log_tips` counts the tips page only).
Checked in Chromium only, at phone size and 1280px.

## D136. A sidebar on a laptop, as Notion Calendar's

*The owner, 2026-11-16, after trying the demo: "a sidebar like in notion calendar instead of
navigating by exiting and entering different pages would considerably streamline user
experience".* On a laptop the top bar held four sections (Plan, Courses, Community, You), and
Focus, Flashcards, Tasks, Progress, Tips and Settings were a page further, under You.

**Chosen.**

- *One sidebar on every page of a plan* from 1100px (`_sidebar.html`,
  `service.sidebar_view`), fixed under the top bar, whose four sections give way to it:
  - this month, from a Monday, each day a link that opens the calendar on that day
    (`/week?day=`; the calendar's Today button still goes to today), today in red as the
    calendar draws it, a dot under a day the plan studies, a red dot and red figure on a day
    with an exam or a test, each day's label saying which;
  - Today, Calendar, Focus, Flashcards (with the cards waiting), Tasks, Progress;
  - the courses in their colours, each with the days to its exam, red within two weeks, as
    Notion Calendar lists calendars;
  - Community, Who's studying where, Study tips, You, Settings.
  The section open is marked; the sidebar keeps its place while pages change (its own
  view-transition name), so moving between sections changes only the page beside it.
- *Light.* It reads the plan as kept (sessions, exams, tests, course colours) and the count
  of cards waiting; no replay. If working it out fails, the page shows without it.
- *A phone keeps the tab bar* (D97), and a window under 1100px the top bar's sections: below
  that width a sidebar would take a third of the page.

**Rejected.** Opening pages in a panel beside the current one (Notion Calendar's event panel):
every page would need a second, partial form, and pages must work without their scripts; the
fixed sidebar removes most of the going back and forth. A month that pages back and forth in
the sidebar (a script, or a reload for each month; the calendar's Month view does it). The
sidebar on a phone (a drawer behind a button is a step more than the tab bar).

**Disclosed.** Checked in Chromium only, at 1440 × 900 light and 1280 × 800 dark, with the
1024px and phone widths falling back; not in Safari or Firefox, nor with a screen reader. The
course list shows the plan's courses with exams, as Courses does, at most eight.

## D137. More ways to make the avatar

*The owner, 2026-11-16, after trying the demo: "more personalisation options to create the
avatar".* D115 had six skin tones, seven hair styles in eight colours, a beard or a
moustache, two pairs of glasses and seven colours of top.

**Chosen** (`service.AVATAR_PARTS`, `_avatar.html`, `avatar.html`):

- skin: eight tones, one lighter and one deeper than D115's ends;
- hair: twelve styles (an afro, a bob, a ponytail, braids and bald added) in twelve colours
  (red, platinum, green and purple added);
- eyes (soft, wide, happy, a wink), eyebrows (soft, straight, thick, raised) and a mouth
  (a smile, a grin, calm), where D115 drew one of each;
- facial hair: a goatee and stubble added; glasses: sunglasses added;
- on the head: a cap, a beanie, headphones or a flower; details: freckles, earrings or a
  beauty mark;
- clothes: a T-shirt, a hoodie, a shirt or a sweater, in ten colours (the seven course
  colours and charcoal, white and sand); a background in seven colours.

Each part is a few shapes in the manner of D115, no outlines; the maker's preview still
follows every choice through CSS `:has` with no script, each shape tagged with the options
it belongs to. An avatar saved before keeps its parts and takes each new part's first
option, which draws it as it was.

**Rejected.** Free colour pickers (a colour field per part: many more combinations nobody
reads, and colours that fight the app's own). Uploaded pictures (D115: nothing uploaded).
Hats in their own colours (the clothes' colour keeps the number of choices down).

**Disclosed.** Still a choice of shapes, not every person; the drawings were checked in
Chromium only, at phone size, in eight combinations and as saved.

## D138. Cards from notes written by Claude, checked card by card

*The owner, 2026-11-16, after uploading a lecture's PDF on the demo (AUDIT 99): "the flashcard
making must be perfected, we can use reasoning ai agents and llms for the purpose, even free cloud
ones or install a small one with the app, these flashcards are not okay clearly". Asked which, the
owner chose Claude Haiku over a free cloud model and a small model on the server.*

**Chosen.**

- *Two calls, as an agent would: write, then check* (`ai.cards_prompt`, `ai.check_prompt`,
  `ai.checked_cards`). The first call writes cards the way an exam asks: each definition,
  theorem, formula and method, a front that stands alone and names its subject (never
  "Definition 1.1"), the shortest complete answer, mathematics in Unicode, words a PDF ran
  together repaired; never a title, a lecture number, an aside or a fact the notes do not state.
  The second call reads the same notes and the numbered cards and gives each a verdict: keep,
  fix (with the corrected card) or drop (not stated in the notes, about a title or the layout, a
  repeat). A card it does not mention is dropped; a check that cannot be read leaves the cards
  as written, and the page says they were not checked.
- *The model's cards alone.* Once the model wrote cards, only they are offered (D101 listed the
  rules' cards under them); the rules are the fallback when the model is off, over the cap or
  gives nothing, and the page says so. The student still picks each card (D84).
- *With the student's yes, reading notes is the model's.* "Read my notes" sends them to Claude
  when AI is on (D12); "Rules only" keeps them on the server. Without the yes, Flashcards and the
  page that makes cards offer AI in one tap, saying what is sent where, and come back.
- *The cost.* Claude Haiku 5.5 (D101's cards model): at most 0.023 USD a file of 60,000
  characters, written and checked, 434 files under the default 10 USD monthly cap
  (`benchmarks/syllabus_cost.py`); 20 calls a day per student make 10 files. The demo installs
  the `ai` extra and declares `ANTHROPIC_API_KEY`, typed in Render's dashboard by the owner, with
  a cap of 5 USD a month for every visitor together.
- *The rules, the fallback, read lecture notes better* (AUDIT 99): a PDF's lines broken
  mid-sentence are joined; a phrase with a verb or a pronoun, a lowercase start of three words
  or more, and a title ("Lecture 08", "Settimana 3") are not terms; a numbered definition,
  theorem, lemma, proposition or corollary with a name, in the seven languages, becomes
  "Definition: name" with its statement, which stops at the proof.

**Rejected.** A free cloud model (its account and its data terms for students' notes) and a small
model on the server (memory and a minute a file on the pilot's server; weaker on mathematics):
the owner's choice. One call with thinking instead of two (the check, a separate reading of each
card against the notes, is what the owner asked for and what a student can be told). Fixing
pypdf's run-together words by guessing from glyph positions (widths are not in the text pypdf
gives; a wrong guess splits words).

**Disclosed.** The model's cards and the check ran against a fake provider in the tests only:
no real Claude answer has been tried on real notes yet, which needs the key on a server. The
check reduces, but does not remove, cards that are wrong or poorly put; the student still reads
each. The notes go to Anthropic in the United States only with the student's yes. The rules'
cards from a LaTeX PDF keep words run together. Checked in Chromium only.


## D139. The look the owner chose: one face, precise icons on soft tiles

*The owner, 2026-11-16: "rework on UX and UI graphics and make it look more like the notion-like
sidebar we added earlier also in other sections, replacing colourful and nauseous icons with more
minimalist ones that still catch the eye, and in general let's pick a better font, give me
choices". Shown seven faces, four kinds of icons and three backgrounds on the app's own screens
(an artifact, not in the repository), the owner chose "Font: Plus Jakarta Sans · Icons: Line on a
soft tile · Background: Now", adding: "look for example at the task icon being not precise, we
need graphics to be spot on, same for the settings one". The icons were drawn again from one set
and shown before the owner said "Go".*

**Chosen.**

- *One face, Plus Jakarta Sans*, for reading and for what is large (D73 had Figtree and
  Bricolage Grotesque). A variable font from 200 to 800, its Latin and Latin Extended files
  shipped in `static/fonts` with the SIL Open Font Licence, as D73's were: fetched from Google
  they would give Google every student's address. The first page asks for one font file instead
  of two (`benchmarks/page_weight.py` measures what a first visit downloads).
- *Lucide's icons* (`web/icons.py`, `static/vendor/lucide`): an open set drawn on one 24-unit
  grid with one stroke width and round ends, so every icon has the same weight; the hand-drawn
  ones had ticks against their box (Tasks) and a gear that read as a sun (Settings). Only the 58
  icons the app draws are shipped, unchanged from lucide-static 0.460.0, with its licence (ISC;
  a part from Feather under MIT). The server writes each icon into the page: no request, no
  script, under the Content-Security-Policy, offline and in print.
- *Each place's picture a line icon on a soft tile of its colour* (`_art.html`): flat, without
  D99's gradients and shadows, ten hues chosen for each mode, darker on light and lighter on
  dark. The sidebar, the tab bar, the "+" and the calendar's arrows, the posts' actions, the
  badges and the questions about the week use the same set.
- *The sky stays* (the owner's "Background: Now"), and so does the glass.

**Rejected.** An icon font (a request, and letters before it loads), a sprite read by
`<use href>` (one more request, an icon missing until it arrives), the whole set of 1,500 files.
Keeping two faces (the owner chose one). Course colours for the tiles (a place would read as a
course).

**Disclosed.** Lucide is third-party artwork under its licence, shipped with it; the owner
approved adding it. The hues and the face are choices of taste; checked in Chromium only, at
phone and laptop sizes, light and dark.

## D140. Today as the app's home; Calendar the calendar alone

*The owner, 2026-11-16: "be mindful that as of now from the sidebar the sections 'today' and
'calendar' are the same. what can we put in today? like the headquarters of the app? students
like having everything under control, like a tip here, a study session there, a feed window,
let's say a general menu to then dive into other things, what do you think?" A mockup on a
laptop and a phone was shown before the owner said "Go: the look, then Today as home".*

**Chosen.**

- *Today is a page of widgets, the most urgent first* (`_panel.html`): the next step with
  Start focus and its stakes; what needs an answer (how a session went, in one tap, or the
  self-test just answered); the day's one message (D97, unchanged); the day's list; the week
  and the streak; the cards waiting; the next tasks; the next exams with their topics on
  track; a tip; the classmates. Each has a head with its picture (D139), its name and the
  section it opens. A widget with nothing to say is not drawn; what remains (last week,
  keeping the plan, a weekly budget, a warm-up, the weekly note, a classmate's topics) folds
  under "More" as before (D73). One column on a phone in that order; two columns from 720px;
  three from 1100px, the next step on the left and the day's list on the right.
- *The day's list* (`service.day_agenda`): today's classes, exams, tests, busy times and
  sessions in time order, each over, going on or to come, with a line at now and a session's
  report; the trips to campus and lunch (D42, D69) are left to the calendar.
- *A window on classmates* (`service.classmates_glance`): who of the people followed is
  studying now, with Join (D108), and the two newest posts of the people followed, as
  Community shows them; never the student's own, nothing for a student without a handle,
  no window when there is nothing in it.
- *Calendar is the calendar alone, full width* (`plan.html`). D73 to D139 drew the day's
  panel beside the calendar on both, so on a laptop the two were one page. On a phone they
  were already two tabs. A report from Today, where there is no calendar, is sent by the
  page itself and the widgets are fetched again (`app.js`).

- *Then sharper, and about time and attention* (the owner, 2026-11-18: "i want the dashboard to
  look sharp and notion style with our original hint, a student should feel in control of their
  attention and time and feel empowered with some daily tips to study and live well"). Today's
  cards are near-opaque panes with a hairline, small corners and no blur, and a widget's head is
  its icon and its name in sentence case, as Notion's pages; the sky stays behind, the hint of
  the hour. Two widgets join: *your time and attention* (`service.time_and_attention`): the day
  from getting up to going to bed (D69) as one bar, the hours of classes, of study planned, of
  busy times and what is left, the student's own; and the minutes focused with the timer since
  Monday, the sessions timed, and of those checked how many were spent without leaving the app
  (D67), only what the timer logged. And *tips for today* (`service.tips_for_today`): a tip to
  study (how to study, how the brain learns, motivation) and one to live well (sleep, movement
  and coffee, breaks, habits), a different pair each day, never the one the day's message
  gives, each with its evidence a tap away; the tip that took turns (D62) gives way to them.
- *The workspace* (the owner, the same day: "there could be external links to moodle where the
  material is, so students can manage the workspace from there"; `service.workspace_view`):
  each course with the links the student keeps for it (D88: its page on the learning platform,
  slides, a folder), this week's first, each opening where it lives with its site's name under
  it, and "Add a link" to the course's page; and the learning platform's own page, "Open
  Moodle", when the calendar link given for deadlines (D92) says which: every Moodle site
  exports its calendar at `<site>/calendar/export_execute.php` and keeps a student's dashboard
  at `<site>/my/` (`service.learning_platform`). Another platform, or no link, shows no such
  button: the app does not guess an address. Moodle is still not signed in to (D92).

**Rejected.** Widgets the student arranges (a choice to make before the layout is known to
help). A feed on Today (Community is a tab; two posts are a window, not a feed). Keeping the
day's panel beside the calendar on a wide screen (the two pages would be one again).

**Disclosed.** The tasks, which D73 folded under "More", are a widget again: Today is longer
than D97 left it, on the owner's word; whether students find their way better is for the
pilot. "Yours" is the hours awake less the timetable and the plan: meals, travel and rest are
in it. Attention counts only sessions timed with Focus. The links are the ones the student
added; the app reads nothing from Moodle's pages. Checked in Chromium only, at phone, tablet and
laptop sizes, light and dark.

## D141. Spark's tour in every section; sharing in fewer taps; classmates check each other

**Asked.** The owner, 2026-11-18, with a phone's screenshot of the Explain form in Italian (the
Post button over the fields, the Course box empty): "sparks tour is important in every section,
and we need to streamline the interactions like don't ask many taps for sharing notes and
explaining stuff, it ought be encouraging and empower students ideas exchange and peer to peer
feedback review validation".

**Chosen.**
- *Spark's tour in every section, again* (`service.SECTION_TOURS`, `_tour.html`). D77 gave each
  section a tour; D97 kept Today's and gave the others one line under their title, to lighten
  the app. Now each section (Calendar, Courses, Community, You, Focus, Progress, Tasks,
  Flashcards, Tips) shows its tour on the visit that shows its line: two to four steps, each
  lighting a part of the page. Community's steps say how to share in a tap and how to check a
  classmate's work. The line and the tour are remembered together as the page goes out, so a
  section shows its tour once even if it is left half way; Settings show them all again, as
  before. Today's stays as D97 left it, until finished or skipped. Settings has none (six rows,
  each its own page), nor the pages reached from a section.
- *Sharing in fewer taps* (`explain.html`, `notes_new.html`, `_share_course.html`). The course is
  a row of chips, and the one being studied is already picked (`service.share_course`: the session
  under way, else the last one ended today, else the next). A box under the chips takes another
  course, and wins when typed. Optional fields fold away: a sketch for an explanation, what notes
  cover. The button sits where the form ends: the sticky bar floated over the fields on a phone
  (AUDIT item 101). Links that open a form carry what they know. A course's page opens either
  form on its course. Each taught week has "Explain it simply" with its first topic filled in.
  Today's classmates widget has "Explain an idea" and "Share notes".
- *Classmates check each other* (`social.toggle_check`, `checks_of`, `fixes_of`, `accept_fix`;
  the `checks` table, the comments' `kind` and `accepted`). A reader of an explanation or of notes
  says "It's right" with one tap, one per person, taken back by the same tap. The author sees
  "Checked by 2 classmates", or "Not checked yet". A reader who spots a mistake writes the right
  version in the comment box and sends it with "Suggest a fix". The author may accept it: the post
  is not edited, but readers see the accepted fix first, marked as accepted. Each post's card shows
  how many fixes were suggested and how many accepted. After sharing, the notice says classmates
  can now say it is right or suggest a fix. A session is not checked: it says what someone did, not
  what is true. Checks and fixes go with the post, with whoever leaves the network, and with a
  deleted plan; a plan's copy of its data lists its checks (D43).

**Rejected.**
- A score of trust, or ranking by checks. The notes library keeps ranking by "helpful" (D26); a
  count of checks says only that some readers found it right.
- Letting a fix edit the post (the author's words stay theirs).
- Checking limited to the post's course (courses match only by their words, D104).
- One tour for the whole app at the start (the owner asked for each section's).

**Disclosed.**
- "It's right" is a reader's word, not a verification: no one checks the checker, and a wrong
  explanation can be checked by readers who share the mistake.
- A block either way hides the blocked person's checks, as it hides their comments.
- The tours' words are the owner's to review; the Italian was not written by a native speaker.
- Checked in Chromium only, at 393 × 720 with touch emulated and at 1280px, not on a real phone.

## D142. The explaining pages on a phone; cramming and the month at equal effort

**Asked.** The owner, 2026-11-18, with seven screenshots of How the code works and How it works
from an iPhone (Safari 26, in Italian): "it doesn't format well some bits as you can judge [...]
and the results of the comparison between cramming the night before and studying consistently
are not in favour of our app- maybe we got the whole approach wrong ?"

**What the model says.** Computed with the cramming chapter's own functions (FSRS-6, average
weights, each revision working only as often as the topic is remembered then;
`tests/test_garden.py::test_what_d121_showed_and_what_it_left_out`). One new topic, the exam on
day 28:

| | revisions | exam day | a month on | days below 90 % before |
|---|---|---|---|---|
| crammed the night before (D121's picture) | 1 | 96 % | 80 % | 25 |
| revised whenever it dipped (D121's "app") | 2 | 91 % | 85 % | 4 |
| the same, and once more the night before | 3 | 99 % | 91 % | 4 |
| crammed the last three evenings | 3 | 98 % | 85 % | 23 |

The approach stands; D121's picture compared the wrong things. It set one crammed revision against
the self-test rule alone. That left out the exam practice the app plans in the final days (D46),
and gave cramming fewer revisions. What remains is what the spacing research says: the best gap
between reviews grows with how long the topic must be remembered [ref:cepeda2008]. For a test
tomorrow, massing works.

**Chosen.**
- *The cramming chapter at equal effort* (`service._garden_cram`). The spaced topic is revised
  whenever it dips to the target, and once more the night before, standing for the exam practice.
  The cram takes as many of the last evenings. Exam day 98 % against 99 %, a month on 85 % against
  91 %, 23 days below 90 % against 4. The words still say cramming works for the next morning. Then
  they say a spaced plan with the same evenings is just as ready, isn't shaky all month, and keeps
  more. The limit line says the last revision is the app's exam practice, and that a whole semester
  wouldn't fit in the last evenings.
- *The month at equal effort* (`service._garden_month_app`, `garden_month_view`). The app's plan is
  its rule, then a last pass: one course an evening in the last three, the hardest last. That makes
  9 revisions: the weakest course at 98 % on exam day and 88 % a month on, 11 days below 90 % across
  the three courses. "Cram at the end" now takes the same 9 revisions in the last nine evenings:
  97 %, 82 %, 48 days. A plan with fewer revisions is told the evenings it saves ("It does save
  evenings: 3 revisions, against the app's 9."). Where the app's plan already wins exam day, the
  days below the target begin with "And", not "But".
- *The explaining pages on a phone* (AUDIT item 102). Below 861px a pinned scene no longer pins.
  Its stage was shorter than the window, so a quarter of the screen stood blank above and below,
  and each scene took three screens of scrolling. Now it stands in the page with every step listed
  and the current one lit, and its figure runs while it crosses the screen. The figure is its own
  timeline, shared with its steps through `timeline-scope`.
  - The two curve labels on the code page are kept a line apart (`service._apart`).
  - The two long formulas break between their terms.
  - The answer chapter's labels say "lasts 7 days" (a phone shows no line under them), and its
    question card stands flat.
- *Measured* (`benchmarks/scroll_flow.mjs`, Chromium, 390 × 844):
  - How it works: 10.7 screens (23.6 before), 26 % of a screen empty on average (35 %), 5 % of
    positions more than half empty (8 %).
  - How the code works: 13.0 screens. The science page: 10.8.
  - The laptop sizes are unchanged.
  - The tallest empty band, 683px on How it works in that run, is a chapter's words still fading in
    as they enter, and changes from run to run (237 to 350px in two more).

**Rejected.**
- Keeping one cram against the rule, with a caption: the picture is what a reader remembers.
- Giving the spaced plan more revisions than the cram, D121's unfairness turned around.
- A semester of sixty topics drawn out: one line says it, and the month shows three courses.
- On a phone, pinning with the stage at the top: the blank moved to the bottom, and the three
  screens of scrolling stayed.

**Disclosed.**
- The last pass stands for exam practice, which the product plans as practice papers over the
  final two weeks (D46), not one revision of each topic the night before.
- The app's forecasts do not count that practice as a revision of each topic, so they show a plan
  without its last pass. Making the pass explicit in the planner, and counting it, changes the
  product's plans: it waits for the owner's word.
- The comparison is the model's, with average weights, as expected values.
- Checked in Chromium only, at a phone's size and a laptop's. Safari 26's layout was read from the
  owner's screenshots; whether it supports `timeline-scope` was not checked, and without it the
  steps on a phone stay listed, unlit.

## D143. The avatar in ink, with a flag of where you're from

**Asked.** The owner, after an iPhone's screenshots: "in the avatar section would like to add
the chance to put the flag were you're from, and really i want to polish and finalise the
characterisation well with proper better graphics and a characteristic design so show me
some options". Four styles and three places for the flag were drawn on a page to choose from;
the owner chose "Avatar: Ink · Flag: a round badge".

**Chosen.**
- *Ink* (`style.css`, D143's block): every shape of the avatar drawn with a dark line, flat
  fills, no shading or shine, as a drawn portrait.
  - The parts and every option of D115 and D137 stay, restyled rather than redrawn.
  - A skin tone is kept as itself, lightened a fifth. At 45 % the tones ran together, so the
    drawing stopped being the person.
  - The clothes keep their colour, softened; the background is a paler paper of its own.
- *A flag of where you're from* (`service.flag_countries`, `set_avatar`, `share_flag`; the
  profiles' `flag` column):
  - It is a round badge at the avatar's bottom right, in a ring of the page's colour, chosen on
    the avatar page.
  - None is shown until one is chosen. The page offers the university's country (D39) and the time
    zone's first, then every country in a list; a country picked from the list wins.
  - The flag reaches the student's profile, so others see it as a badge on their initial in
    Community, and the page says so. "No flag" shows none anywhere.
  - A flag chosen before the handle reaches Community when the handle is chosen.
  - The flag beside a university, the university's country (D39), stays as it was: where
    someone studies is not where they are from.
- *The flags* are flag-icons 7.6.0's square ones (MIT, approved by the owner's choice on the
  options page, which named it), 249 countries. Each is its own file under
  `static/vendor/flags/1x1/` with the licence, fetched only by a page that shows that flag.
  The countries' names come from the same package (`data/countries.json`, its ISO countries).
- *An abandoned page transition* (AUDIT item 103): `errors.js` no longer reports the rejection
  a browser raises when a link is followed while a page-to-page transition runs (D30).

**Rejected.**
- The university's country as the flag: it is where someone studies.
- A flag on by default, from the time zone: where a student is from is theirs to say.
- A preview of each flag on the avatar before saving: it would need a rule per country, and an
  inline style the pages' Content-Security-Policy forbids. The choices show each flag, and the
  avatar shows the saved one.
- The other three styles (Clay, Shapes, today's): the owner chose Ink.

**Disclosed.**
- The countries' names are in English in every language, as the universities' are (D39).
- A few flags are large files, Serbia's 181 KB, fetched by the pages that show it.
- The flag is shown to everyone in Community who can see the student's profile.
- Checked in Chromium only, at phone size, light and dark.

## D144. Focus and attention from Today

**Asked.** The owner chose from the options page for Today as headquarters: "Build: focus,
attention, steps, clear-mind". This record is the first two. Steps and the clear-your-mind list
come next, once their two studies are checked.

**Chosen.**
- *Focus from Today* (`service.focus_quick`, the Focus card under the next step). It starts
  anything besides the plan's next session in one tap; that session already starts from its own
  card.
  - The course last timed is chosen first, then the others in their colours' order, four at
    most.
  - Three of the focus page's lengths are offered: 25 minutes, 45 and 1 h 30. The shortest is
    chosen, since a short start is the easiest to begin; D106's first session was 25 minutes
    too.
  - It posts to the focus page's own form, so nothing about the timer changed.
  - Before: "Study something else", then a course typed, then Start, from two pages. Now:
    Start, once a course has been timed or is the first in the list.
  - The tip on breaks sits under the button, with its evidence a tap away (D62: Albulescu et
    al., 2022 and Biwer et al., 2023, both checked).
  - The card is not shown while a session runs: Today's banner leads back to that session.
- *Attention day by day* (`service.time_and_attention`'s `days`, `_focus_days`).
  - Each day of the week, Monday first, has a column for the minutes the timer logged. The
    longest day is full height and labelled with its time. Today's column is in the accent, and
    the days ahead keep their letter, faint.
  - Minutes typed by hand are not counted, as before (D140).
- *The part of the day you focus longest* (`_focus_best`).
  - This is the part of the day (morning, afternoon, evening, as `progress.part_of_day`) whose
    timed sessions lasted longest on average over the last four weeks.
  - It is named only when four sessions say when they started (saved since 2026-10-21, D80) and
    two parts have two sessions each. One session is an anecdote, and a part with nothing to
    compare it with is not "longest".
  - The line says how many sessions it rests on.

**Rejected.**
- Every length of the focus page on Today: six chips and "No limit" would make the card a page.
  The link to the focus page says "Other courses and lengths".
- The plan's next session as the card's first course: that session starts from its own card,
  with its report tied to it. A second way to start it would start it without that tie.
- The hour of the day as a heat map: four weeks of a few sessions spread over 24 hours says
  nothing. Three parts of the day are what the app already uses (D45, D80).
- "When you focus best": the timer measures how long, not how well.

**Disclosed.**
- Four sessions and two per part are choices, not results. Average minutes describe the
  sessions a student chose to time, not their capacity: a long evening session may be the
  evening the student had free.
- The week's columns count sessions saved with the timer only, so a student who logs by hand
  sees none.
- Checked in Chromium only, at phone size and 1280px, light and dark.

## D145. On your mind, closing the day, and assignments in steps

**Asked.** The owner chose from the options page for Today as headquarters: "Build: focus,
attention, steps, clear-mind". D144 was the first two; this is the rest.

**Chosen.**
- *On your mind* (`service.add_to_mind`, `plan_mind_item`, `cross_off`, `mind_view`; a card on
  Today after the day's agenda): what is on the student's mind, written down in a line.
  - An item given a day (today, tomorrow, or a date from the card's menu) is a plan for it. It
    is listed first, in its days' order, and its day is shown in the accent once it has come.
  - An item without a day stays a note, newest first, until crossed off.
  - Masicampo and Baumeister (2011) found that unfinished goals intruded on other tasks until a
    specific plan was made for them [ref:masicampo2011]. A day is the plan the card can hold.
  - "Plan it as a task" sends the words to the new-task form (D12): a deadline with hours to
    plan becomes a task, and the plan makes room for it.
  - A list holds 40 items; when full, it drops its oldest note, never an item with a day.
- *Close the day* (`close_day`).
  - From three hours before bed until getting up (D69), once an evening, the card asks for
    tomorrow's first three things, "as specifically as you can".
  - Each is given the day after that evening, and after midnight that is the day that has begun.
  - "Not tonight" closes the day with nothing written.
  - Scullin et al. (2018) found that 57 young adults who wrote a specific to-do list for 5 minutes
    before sleeping fell asleep faster than those who listed what they had done [ref:scullin2018].
- *Assignments in steps* (`add_step`, `tick_step`, `drop_step`, the task card).
  - A task folds out a list of the student's own steps, ticked off one by one: "1 of 2 steps".
  - They are the student's words, not the planner's blocks. They change nothing in the plan, and
    the plan's sessions are counted above them as before.
  - Twelve at most. Steps go when the task is deleted, and with the tasks at a new semester.
- *Two tips* (D62), each with only its checked source.
  - "Write down what is left undone, with when you will do it." (Habits.)
  - "Before bed, write tomorrow's to-do list, as specifically as you can." (Sleep.)
  - The card's "Why" opens the evening's tip in the evening and the plan's in the day.
- *The two sources* were read at Europe PMC and Crossref on 2026-11-18 (docs/REFERENCES.md).

**Rejected.**
- Every item becoming a task: a task needs hours and a deadline, which most of what is on a
  mind does not have. The list keeps them light, and "Plan it as a task" is a tap away.
- The steps as the plan's blocks: the planner sizes blocks by hours and memory, and steps are
  units of meaning. Tying the two would make the plan follow a student's way of writing.
- "Close the day" as a notification: reminders already vary and stay quiet between bed and
  getting up (D129). The question waits on Today.
- A list of what was done in the evening: Scullin et al. found the to-do list helped sleep, and
  the completed list did not.

**Disclosed.**
- Both studies are laboratory studies. Masicampo and Baumeister's abstract gives no sample sizes,
  and Scullin et al. tested one night in a sleep laboratory with healthy young adults. Whether a
  list written in an app does the same is not known.
- Three hours before bed, three things and forty items are choices, not results.
- The list lives in the plan's document: it is exported and deleted with it, and never shared.
- Checked in Chromium only, at phone size and 1280px, light and dark.

## D146. The evenings redrawn: day dials, where each evening goes, a card of lights

**Asked.** The owner, on iPhone screenshots of the explaining pages: the bits and watering
graphics "with the days of the week drawing" were "quite poor in terms of expressiveness". A page
of four drawings followed, each played with made-up numbers. The owner's answer: "i like all
choices but the plant one".

**Chosen.**
- *Day dials* (How it works, chapter 5, `service._garden_dials`). Each of the worked example's five
  days is a 24-hour dial, midnight at the top.
  - The nights are drawn in grey, the lectures in the timetable's grey, and the free evening in the
    accent with a glow.
  - In the middle is the topic the evening goes to, or "Free, night off".
  - The scene runs as the old one did: the week, then sleep, then lectures, then the evenings, and
    last what each evening goes to.
- *The week, then the water* (How it works, a new chapter 6, "Each evening",
  `service._garden_evenings`, `_garden_water`). The same week as columns, and under them three
  topics' tubes, one per evening.
  - Each tube is filled to how likely the topic is to be remembered as that evening begins: FSRS-6
    with average weights, the topics of D116's week.
  - A wire runs from each evening to the topic it goes to, whose tube fills back to 100.
  - The steps are the evenings. Day 1 goes to Analysis at 90 %, Day 2 to Algebra at 90 %, Days 3 and
    4 are free (the lowest topic is at 92 and 91 %), and Day 5 goes to Physics at 90 %. That is the
    app's rule as `_garden_app_plan` always ran it, now drawn with what it saw.
  - The tubes run from 80 to 100 %, and the figure says so: on a scale from 0, 90 against 98 would
    look the same. Every level of the week is above 80, which a test keeps true.
- *A card of lights* (How the code works, scene 1). The week as the code builds it: a light per half
  hour, off when free (0).
  - The sleep mask lights its bits, then the classes mask, each an OR.
  - Then three dark lights in a row fuse into a study block, glowing. Day 1's bits are written under.
  - It replaces D111's three layers falling into one; the steps' words are unchanged.
- *The chapter line* holds eight chapters, and the two tall figures are held to a laptop panel's
  height.
- *AUDIT 104*: How the code works was 7px wider than a phone, from the search's tilted disc. Its
  frame now clips what passes its sides.

**Rejected.**
- *Thirstiest first*, a plant in a bowl with a gauge: the owner left it out.
- One chapter with both drawings: the dials say where the evenings come from, the water where they
  go. Together in one figure, neither could be read on a phone.
- The made-up week of the options page (named courses, a gym, a job): the pages draw from their own
  worked example, as every figure there does. So its five days are alike, and the chapter's words
  name the gym and work only as what fills a real week.
- Each tube's level moving through the week in one tube: it needs a width per frame per topic, and
  the pages' Content-Security-Policy forbids inline styles. A tube per evening shows the same, and
  needs no script.

**Disclosed.**
- The week is the worked example's (the same lectures every day, one block an evening), and the
  three topics are made up.
- The tubes are FSRS-6's estimate with average weights, and every revision here is a success.
- How it works is 17.3 screens long at 1440 × 900 and 12.0 on a phone (15.3 and 10.7 before),
  measured by `benchmarks/scroll_flow.mjs`.
- Checked in Chromium only, at 390 × 844 and 1440 × 900, light and dark, with motion and without.

## D147. Hours that fit the exams (and a last pass, measured and rejected)

**Asked.** After D142's cramming chapter, a last revision before each exam, made explicit in the
plan and counted in the exam forecast. The owner said "yes". The last pass was then measured,
changed nothing, and was put back; the owner chose instead "Warn early on hours".

**Measured first: a last pass** (`benchmarks/last_pass.py`). The rule would have given a topic a
self-test as soon as its predicted recall *at the exam* fell under 90 %, in the last 3 to 28 days
before it, not only once it is under 90 % now. On the benchmark semester (six courses, 69 topics,
six exams within five days, FSRS-6, Sundays off, 4.5 hours of practice per exam):
- *3 to 21 days:* it never fires, at 12, 15 or 18 hours a week or with no weekly limit. The
  results are unchanged: 31, 44 and 58 of 69 topics at 90 % or more.
- *28 days:* it fires a few times and loses a topic: 43 of 69 at 15 hours, 57 at 18.
- *Why:* the self-test rule already revises each topic as it dips to 90 %, right up to its exam.
  Where topics miss, the weeks before the exams have no free block left. At 18 hours a week, and
  with no limit, the plan makes the same 197 sessions and keeps the same 58 of 69: the calendar,
  not the rule, is the limit then. The exam forecast already counts every planned self-test.

**Chosen: hours that fit the exams** (`service.hours_offer`, a card in Today's one message, D97).
- When the week's hours leave topics under the target on their exam day, Today says how many,
  and what more hours a week would keep.
- More hours are tried a block at a time, up to four blocks (6 hours with 90-minute blocks).
  Each try is the whole plan made again with those hours.
- Every step that keeps more is a button, and the fewest hours that keep the most is the primary
  one. On a plan of three courses at 6 hours a week, a week in, 21 of 34 topics end under 90 %.
  The steps read 12 at 7.5 hours, 4 at 9, and none at 10.5 (`tests/test_hours_offer.py`).
- A tap plans those hours through the budget's own route (D47).
- "Not now" waits two weeks.
- It is not shown in the plan's first week, whose questions about the week change the plan
  (D98). Nor with no weekly limit, while every topic is kept, once every exam is less than a week
  away, or beside D47's smaller budget offered from what is kept. A student who keeps few sessions
  is not asked for more hours.
- In Today's one message it comes after the question about counting use and before the
  questions that make the plan fit.

**Rejected.**
- The last pass, measured above: a rule that never fires is not worth its explanation.
- Counting exam practice in the forecast as a revision of each topic: the forecast would rise by
  assumption. A past paper samples a course, and FSRS has no way to say how much it revises each
  topic.
- One offer of the hours that keep every topic: on the plan above that is 4.5 more hours, while
  1.5 more already keep 9 topics. The steps let the student choose.
- Fewer days off as a second offer: the weekly hours are what most students can name, and the
  plan's warning already says fewer days off would help.

**Disclosed.**
- The numbers are the memory model's estimate with average weights, if every session is kept,
  and the card says so.
- Four blocks, a first week, two weeks, a week before the last exam and the message's place
  among Today's are choices, not results.
- Checked in Chromium only, at phone size and 1280px, light and dark.

## D148. Deadlines in the platform's language, and tasks typed in Italian

**Asked.** "Continue with the next improvements." Chosen from the product plan's open items
(docs/PRODUCT.md, "Moodle in languages other than English") and the limits CLAUDE.md listed:
deadlines were read from English event names only, and a task typed in one line in English or
French only, while the app speaks Italian (D34).

**Found first: whose language a deadline is in.** Moodle writes an activity's calendar event
when the activity is saved, with its name built from a language string in the language of
whoever saved it (`mod/assign/locallib.php`, `get_string('calendardue', 'assign', …)`), and its
calendar export writes that stored name as is (`calendar/export_execute.php`). So an Italian
university's export says "Relazione è in scadenza", whatever language the student reads the app
in, and one export can mix languages. Read in Moodle's source (the 4.5 branch) on 2026-11-19.

**Chosen.**
- *Moodle's own strings, eight languages* (`calendar_io._MOODLE_DEADLINES`). For each, the 4.5
  language pack's strings for an assignment's due date (`calendardue`), a personal extension
  (`calendarextension`) and a quiz's close (`quizeventcloses`), downloaded from
  download.moodle.org and read on 2026-11-19: English, French, Italian, Spanish, German,
  Portuguese, Brazilian Portuguese and Dutch. Those are the languages the syllabus reader
  already knows (D81), with Brazil's Portuguese, which Moodle keeps apart.
- *Every language on every event*, whatever the app's language, since the teacher's language
  decides. The teacher's grading date (`calendargradingdue`) and a quiz's opening
  (`quizeventopens`) are worded differently in each pack, so they match none of the patterns,
  as "is due to be graded" and "opens" did not in English.
- *A final full stop is optional*: German's due string ends with one, and an older pack or a
  name edited by hand may not.
- *The first string that matches decides.* With ordinary names no two match the same event
  (`tests/test_deadlines.py` fills every string with names that have parentheses, accents and
  another language's short words). A name made of another string's own words ("Se cierra is
  due") matches two, and then the table's order decides, English first.
- *Typed tasks in Italian* (`ai.read_task`): weekdays, months ("gen", "mag", "giu", "lug",
  "ott", "dic" short), "alle 18" and "alle 18.30", "a mezzogiorno", "a mezzanotte", "6 ore",
  "un'ora", "90 minuti", "di lavoro", "domani", "dopodomani", "oggi", "stasera", "tra 2
  settimane", "fra 3 giorni", "la settimana prossima", "prossimo martedì", "questo sabato",
  "1° dicembre"; "entro", "per", "il", "prima del", "da consegnare" join a date to the task's
  name and are taken away with it; Italian words for kinds of work ("relazione", "esercizi",
  "compito"…) do not pick a course.

**Fixed on the way** (AUDIT.md item 105). Moodle's export escapes a bare "&" in a name as
"&amp;" (`core\formatting::format_string`, read in the same source), so "Q&A sheet" arrived as a
task named "Q&amp;A sheet", and a course's short name the same. Both are now unescaped once
before they are read.

**Rejected.**
- *Strings typed from memory* or a translation of the English ones: Moodle's packs say "Se
  cierra X" in Spanish and "Termina o prazo de 'X'" in Portuguese, word orders a guess would miss.
- *Choosing the language from the student's own setting*: the event's language is the
  teacher's.
- *Requiring an event with no duration* (Moodle's deadlines start and end at the same moment):
  it would set apart events a person named "… closes", but no such event has been reported,
  and it changes how English exports are read.
- *Italian three-letter months "set" and "ago"*: both are English words ("set theory",
  "2 days ago"), as "mar" is left out for English and French already.

**Disclosed.**
- The exports in the tests are written in Moodle's shape for each language
  (`tests/test_generality.py`), not taken from a real university; a site that changed its own
  strings (Moodle allows it) is not read.
- An event a person named in one of these shapes ("Library closes", "Bibliotheek sluit") is
  taken as a deadline, as it already was in English.
- The Italian sentences were written by the developer, not a native speaker (as D34's were).

## D149. A layout pass from the owner's screenshots

**Asked.** On 2026-11-19 the owner tried the live demo in French on a laptop and a phone, and
sent screenshots: "where the paging is awkward, too many blank void empty spaces, some pads don't
separate well"; of the calendar, "this whole upper bar is annoying, it puts everything down by
its width". The defects among them are AUDIT items 106 to 110. The avatar's hair and haircuts,
asked at the same time, are a slice of their own.

**Chosen.**
- *The top bar is the sidebar's head on a laptop.* From 1100px, where the sidebar holds the
  sections, the bar's band across the page carried only the name, New task and Spark. They now
  sit at the top of the sidebar: the name and Spark on one line, New task under them, the width of
  the sidebar. The page starts at the window's top, and the calendar gains the bar's 52px. A
  phone, and a laptop page without the sidebar (setting up), keep the bar.
- *No tip line on the calendar.* D135's one-line planning tip sat above the grid and pushed it
  down. It is gone, with its styles; planning's tips stay on the tips page and Today's.
- *Today's widgets packed.* On a laptop each row of widgets was as tall as its tallest, leaving a
  gap under the others. `app.js` now lets each widget span rows of 2px for its height and the
  gap, so each column runs on; the most urgent widgets keep their places at the top. Without the
  script the grid is as before.
- *Workspace says once what a link can be*, and only while no course has one; a course without
  links is one row, its name and "+ Add a link".
- *The weekly note's "What it means for you"* is a title on its own line, not run into its
  sentence.

**Rejected.**
- *CSS masonry* (`grid-template-rows: masonry`): only behind a flag in one browser.
- *Columns* (`columns: 3`): they fill one column before the next, so the most urgent widgets
  would no longer lead across the top.
- *Moving the calendar's tip under the grid*: the grid fills the window, so it would be out of
  sight, as good as gone, and still built on every visit.
- *Hiding the top bar on every page*: a phone has no sidebar, and its bar holds the "+" and Spark.

**Not fixed.** A flashcard the owner made on the demo from a LaTeX lecture PDF ran words together
("LetGandHbe groups"). pypdf 6.21.4 read every LaTeX-like layout tried here correctly (kerned
words, a word per position, a font change at each symbol, fonts without a space), so the cause in
that file is not known; it stays under AUDIT item 99 until the file can be tried.

**Disclosed.** Checked in Chromium only, at 2000, 1440, 1280 and 1200px and a phone's size, light
and dark; the list of countries (AUDIT 109) was not tried on Windows, where it was seen.

## D150. The avatar: no line through the hair, a lower quiff, more cuts and features

**Asked.** The owner, on 2026-11-19, with screenshots of the avatar maker: "you can still see the
border of the head", "hair is too tall", then "add the fade haircut, the mullet, more trendy
haircuts and features for personalisation".

**Found first.**
- *The line was the sheen*, a white arc D115 drew over every haircut. The ink drawing (D143)
  hides it on a saved avatar, but on the maker the sheen carried every haircut's class, so the
  rule that shows the chosen cut in the preview showed it too, across the hair.
- *The swept cut stood 17 units above the head* (its crown at 6, the head's top at 23); the
  others stand 1 to 2 above.

**Chosen.**
- The sheen is gone from the drawing and the style sheet, and the swept cut's crown is at 16.
- Nine more cuts, each at the end of the list, so no saved avatar changes: a fade (a short top,
  the sides faded to the skin), a mullet (short on top and at the sides, the ears showing, long
  at the nape), curtains (a middle parting to the jaw), a French crop (a straight fringe), a
  wolf cut (shaggy layers), locs, space buns, a pixie and cornrows.
- Lashes and sleepy eyes, a slit in the brow, a smirk and lipstick; a nose ring, ear studs and
  dimples; a bandana, a bucket hat and a headband; a jacket and a turtleneck. Each drawn in the
  ink of D143, in the clothes' or the hair's colour where it has one.

**Rejected.**
- *More than one detail at a time* (earrings and a nose ring): the details are one part with one
  choice; making them a set changes how an avatar is saved, for every student. Later, if asked.
- *A second hair colour* (dyed tips, highlights): a colour per part is how the drawing is
  tinted; a second one means a second set of swatches.

**Disclosed.** The shapes are a choice of taste, drawn at the avatar's 120 units; checked in
Chromium only, light and dark, on four skin tones and four hair colours.

## D151. Mathematics in flashcards made from a PDF

**Asked.** The owner, on 2026-11-19, with two cards from their LaTeX lecture notes: "the maths
need to be properly rendered in the flashcards or else it is impossible to learn anything from
them" (AUDIT item 111).

**Chosen.**
- *A page of notes is built from pypdf's pieces* (`pdftext.maths_text`), in the order the PDF
  draws them, as notes made into cards only (`notefiles`). A piece joins the one before with a
  space where pypdf's layout reading separates those two words and never writes them together
  (the layout reading spaces words from the fonts' widths, but reads two columns across, so it
  is the judge, not the text). A piece set at most 82 % of the line's size, raised or lowered by
  12 to 70 % of it, is a superscript `^{…}` or a subscript `_{…}`; two pieces of one script are
  one. A mark drawn alone over the next sign is put on it (TeX's slash and "=" make "≠"). TeX's
  big sum, product and integral, written with letters' codes in a font without Unicode names,
  are named again. A page's number alone on its last line goes. Anything that fails gives the
  page's plain text.
- *A card draws the marks* (the `maths` filter): `R^{n}` as R with n raised, `x_{i}` lowered,
  and a superscript and a subscript on the same sign stacked (x with i and 2, a fraction's 1
  over 2, a sum's limits), never apart from their sign at a line's end. Everything else is
  escaped, and a mark left open stays as typed. A student may type the marks too.
- *The card rules*: an "=" with a variable on its left and a number, a sign or a variable on
  its right is an equation, not a term ("In dimension n = 1, …"; "Vitamin C = ascorbic acid"
  stays a term); a long statement is cut at a sentence's end past half its length, else at a
  word, never inside a mark.
- *The model writes the same marks* when it writes or checks cards (D138).

**Rejected.**
- *pypdf's layout reading alone*: it spaces words right but flattens the scripts and reads two
  columns as one line.
- *A drawing of the PDF's page on the card*: it needs a PDF renderer (a dependency to approve,
  MuPDF's licence is the AGPL) and a crop no rule finds reliably.
- *KaTeX*: it draws LaTeX, and the text from a PDF is not LaTeX; superscripts and subscripts
  cover what the reading recovers. A dependency to approve, if typed LaTeX is wanted later.
- *A program's PDF read the same way* (D90): "1^{er} octobre" would stop its dates being read.

**Disclosed.** Tried on one page made by pdfTeX, not on the owner's file (not kept) nor on
XeLaTeX, LuaLaTeX, Word or a scan; the thresholds are what that page needs, not measured on
many. pypdf can give two scripts in one piece (a sum's "n" above and "i" below as "ni").
Cards made before D151 keep their old text: they are made again from the file.

## D152. No line above a page on its first visit

**Asked.** The owner, on 2026-11-19, of the calendar's first-visit line ("Fais glisser une
séance…"): "this thing is still bothering"; and of pages in general, that a band above each
pushed it down.

**Chosen.** No section draws its first-visit line (D97) any more: Spark's tour of the section
(D141) says what it is for, once. The first visit is still recorded, so a tour left half way
does not come back. With D149's planning tip, the calendar now opens on its grid.

**Disclosed.** Without a script there is no tour, so a page opened without one no longer says
what its section is for.


## D153. Your avatar on your posts; Community among the main sections

**Asked.** The owner, on 2026-11-19, of a post in Community that showed its author's initial:
"the avatar does not pop out here"; and "community is at the very bottom it could be easier to
reach maybe from the headquarters/dashboard".

**Chosen.** When a student saves their avatar (D115), it is kept with their profile, as its flag
already was (D143): only its parts' names, each one the maker offers, and read back as letters,
digits and dashes alone, since they become class names. Wherever Community draws a person (a
post, a comment, a profile, a group, Studying now, Today's window on classmates), the avatar
they made takes the place of their initial, with the flag's badge on it; without one, the
initial on its colour, as before. A feed draws the same person once per post, so each drawing
gets its own clip path's id from a counter, not one per person.

On a laptop, Community moves from the sidebar's bottom group to the main sections, after the
calendar; the study map stays below. A phone's tab bar already had it, and Today's window on
classmates links to it.

**Rejected.** Drawing the avatar from the plan's options at each post: a post is read by other
students, whose pages have no access to the author's plan, only to their profile.

**Disclosed.** An avatar made before this change reaches Community the next time it is saved, or
when a profile is made; until then, the initial shows. Each avatar is under 4 KB of drawing
before gzip each time it is drawn (`tests/test_avatar.py`), so a feed of people with avatars is
heavier than one of initials; gzip takes much of it back, since the drawings repeat.

## D154. Setting up as one sentence; Moodle in three steps

**Asked.** The owner, on 2026-11-19, of setting up's deadlines, study time and busy times: "i
think these deserve more attention but definitely need to be made very easy to fill in so
students do not have much friction to start with, make proposals". Of the three proposed, they
chose B, the defaults as one sentence, with Moodle in three steps, and the busy-time line fixed
(it said "The next step asks about them", and no step did: AUDIT 112).

**Chosen.** Under the exams, setting up shows what the plan assumes as a few sentences, each value
underlined: "You get up at 07:30, go to bed at 22:30 and have lunch at 12:30. You study at most 2
sessions of 90 min a day. Outside your classes, you are free all the time. Your deadlines: none
yet." A value tapped opens its choices under the line (six times to get up, six to go to bed,
three lunches or none, one to four sessions, 45 to 120 minutes); one chosen closes them, and the
sentence shows it. When everything is right, going on is the one tap it was. Under the busy line,
"+ Training", "+ Work", "+ Busy" and "+ Trip to university" each open a small form: the days, and
hours already filled in (a busy time with no day chosen is none; one with days and no hours says
so); the trip is its minutes each way. Under the deadlines, "From Moodle" and "+ Type one".

From Moodle is three steps in Moodle's own words, read from Moodle 4.5's strings in the student's
language (English, French, Italian): open the export page (Calendar, then "Import or export
calendars", then "Export calendar"; or type the Moodle's address, and the page's script makes the
link to `<site>/calendar/export.php`, the same on every Moodle site); choose "All events" and
"Recent and next 60 days", then the button "Get calendar URL"; paste the link. Pasted, the link is
read at once and the page lists the deadlines it brings (`deadlines_preview`), before anything is
saved. Once a link is kept, its export page is a tap away.

Each value is a `<details>`, so it opens without a script, one at a time where the browser knows
`name`; the value shown follows the choice through CSS (`:has`). What the sentence does not show
(days off, a limit on weekly hours, exam practice, the self-tests and warm-ups, the busy times
already there) is sent back as it is. A week drawn by hand keeps its classes' typed rows under
the sentence, for a browser without scripts (D32). Setting up now offers the trip, which D98 had
left to Today's questions; those questions still come for what was not answered.

**Rejected.** A, one question at a time on cards: about eight taps to answer all three, and a
student who is fine with the defaults still pages through them. C, the week said in one line and
read by rules: fastest when the rules read it, but what they miss must be said and corrected,
and it adds a reader of free text in three languages to setting up. A link that opens Moodle's
export already filled in: Moodle's form needs a signed-in session's key, so the last click stays
the student's.

**Disclosed.** The choices are around where most students' days fall; a value set elsewhere is
added to them. Moodle's words are those of Moodle 4.5's language packs; a site that changed them,
or a Moodle in another language than the app's, shows other words. The preview reads the link
from the server, as saving it does. Without `::details-content` (browsers before Chromium 131 and
Safari 18.4), each value sits on its own line. Checked in Chromium only, at phone size with touch
and at 1280px.

## D155. Edit Today

**Asked.** The owner, on 2026-11-19, of Today on a laptop: "this page is very overwhelming i do not
feel in control". Of the three proposed, they chose B: "Edit Today", as a phone's widgets, each block
on or off, five on to start, the order kept.

**Chosen.** Today starts with five widgets: Up next, Today (the day's list), Focus, Tasks and Exams.
"Edit Today", beside the greeting, opens a page of twelve switches, one per widget, each with a line
on what it shows: the five, then On your mind, Your time and attention, Tips for today, Workspace,
This week, Flashcards and Classmates. What is saved is drawn in the page's own order, the most
urgent first, so nothing moves under a student's thumb. What asks for an answer (a session to
report, an answered self-test, the day's one message, a notice) is not a widget and always shows.
A widget turned off is not worked out when the page is drawn. "Back to the five to start" restores
them. Today's tour (D97) now shows where Today is edited, in place of the week's strip.

**Rejected.** A, three things only (what now, what today, one thing to know), with the rest in the
sections: calmer still, but the owner wanted to choose. C, one large card and a row of numbers:
each number opens a page, so the day's list and tasks are a tap further away. Dragging widgets into
another order: the proposal kept the page's order, and a grid that moves is harder to trust.

**Disclosed.** The mock-up's list of switches did not name This week or Flashcards; both start off,
like the other five, which takes the streak's strip off Today until it is turned on (Progress keeps
it). Every plan, the demo's students included, opens on the five until its student chooses.
Checked in Chromium only, at phone size in dark mode and at 1280px in light.

## D156. Themes for light and dark, and an accent

**Asked.** The owner, on 2026-11-19, of the dark pages: "propose different contrasts for dark,
maybe we can have themes for light and dark"; and of the themes proposed: "i like the themes but
i mean even for example for the doux one to allow the user to pick from a colour palette,
maintaining as primary objective to make frictionless using the app, comfortable to navigate the
sections and look at the screen, warmly welcomed on the app".

**Chosen.** Settings, Appearance keeps Automatic, Light and Dark, and adds three rows, each choice
one tap, as the rows above it:

- a light theme: Sky (the sky of the hour, D76, as before), Paper, Soft day;
- a dark theme: Night sky (as before), Calm night, Graphite, Dim, High contrast;
- an accent for both: Automatic (the sky's colour of the hour on the sky themes, D78, and each
  theme's own on the others), Blue, Teal, Green, Amber, Rose, Violet. No red: red is for exams
  and today.

Automatic switches between the two themes with the device. The themes other than the sky are flat:
the page is one colour, the cards opaque, and when both chosen are flat the sky's sheet is not
fetched. High contrast outlines each card. A theme is set on the page as `data-light`,
`data-dark` and `data-accent`; its tokens outrank the sky's on the same elements, and an accent
comes after the themes, so it wins over a theme's own.

The values (page, cards, text, secondary text, accent, text in the accent, text on the accent):

| theme | page | cards | text | secondary | accent | in the accent | on the accent |
|---|---|---|---|---|---|---|---|
| Paper | #f2f2f7 | #ffffff | #1c1c1e | #636369 | #006ae0 | #0a5fc9 | #ffffff |
| Soft day | #ebe8e3 | #f7f5f1 | #2a2824 | #6b665e | #2c64ad | #285a9c | #ffffff |
| Calm night | #10151e | #1a212d | #eef1f5 | #a9b2c1 | #2e6fd6 | #8ab4ff | #ffffff |
| Graphite | #161618 | #232326 | #f5f5f7 | #a8a8ae | #006ae0 | #4fa3ff | #ffffff |
| Dim | #1c1b19 | #262421 | #e8e3da | #aaa397 | #d9a23d | #e6b75d | #1c1b19 |
| High contrast | #000000 | #0b0b0b | #ffffff | #dcdcdc | #3d9bff | #6fb6ff | #000000 |

| accent | light: fill, text in it | dark: fill, text in it, text on it |
|---|---|---|
| Blue | #006ae0, #0a5fc9 | #006ae0, #7cb4ff, #ffffff |
| Teal | #0f766e, #0f6b64 | #2dd4bf, #5eead4, #04201c |
| Green | #15803d, #157238 | #4ade80, #86efac, #052e14 |
| Amber | #a15c07, #8f5206 | #f5b544, #f7c873, #241703 |
| Rose | #be185d, #b0164f | #f472b6, #f9a8d4, #2a0617 |
| Violet | #7c3aed, #6d28d9 | #a78bfa, #c4b5fd, #1b0f3d |

Every text keeps 4.5:1 against its page and its cards, in every theme and with every accent of its
mode, a tinted button (the accent at 12 % light, 22 % dark over a card) included; the lowest is
4.66, Soft day's secondary text on its page (`tests/test_palette.py` reads the values from
style.css and checks each).

**Rejected.** A colour wheel for any accent: most hues fail 4.5:1 on one theme or another, and a
student would have to find one that works. A palette per theme: twelve rows for a choice most
make once. Red as an accent: it says "exam" and "now" in this app.

**Disclosed.** The theme values are those of the options page the owner chose from; the accents'
are chosen, then measured. The flat themes have opaque cards whatever the glass setting (D132). The
icon of an installed app keeps the sky's colours (D74). Checked in Chromium only, at phone size and
1280px, light and dark: Paper with Teal, Soft day, Graphite, Dim, High contrast with Violet.

## D157. The calendar, cleaner, with the day's sky beside the hours

**Asked.** The owner, on 2026-11-19, with a screenshot of Notion Calendar: "you see how this
calendar from notion is much cleaner and smoother? can we replicate this with a touch of
originality? make proposals". Of the three proposed (A, Clean; B, Timetable and plan; C, Sun
ribbon), the owner chose "A + C", and for C's bar under each day "more in a progression way like
how much you got done in the day and how many points it will give you": that bar is D158's.

**Chosen.** From A, the calm of Notion Calendar in the app's face:

- a quiet header: the month and the year of the days shown ("November 2026", or "Nov – Dec 2026"
  across two), the week's number as ISO 8601 counts it, one menu for Day, 3 Days, Week and Month,
  then the arrows and Today. D59 to D156 had a segmented control of four and the first and last
  day's dates; on a phone the header is two rows, the menu at the left of the second;
- each day as "Thu 19" on one line, today a red pill;
- each title first, in its course's ink, the time under it, smaller; a small mark before a study
  session's title says its kind: ↻ a topic coming back, ? a first self-test, ✎ deadline work, ◆
  exam practice, ✓ once reported done;
- what is over faded: a class once it began, a session once it ended, its title in the secondary
  text on a paler tint;
- a solid board with hairlines: no sky behind the grid (D73's glass stays on the rest of the app);
- the courses as switches: in the sidebar on a laptop, under the month, "On the calendar", as
  Notion Calendar lists its calendars; under the grid elsewhere, the legend's chips. A course hidden
  takes its classes, sessions and exams off the calendar on that device (kept in the browser, as
  the view is); it is matched by name, so two courses that share a colour are told apart.

From C:

- a ribbon beside the hours, the sky of each hour of today (`service.sun_hours`, the day's
  position of D76 at every hour; the sky's first light at that position): indigo at night, gold
  at sunrise, blue by day, orange then violet at sunset;
- the line of now in the accent of the hour (D78) with a sun while the sun is up and a moon after,
  and the line faint across the other days, as Notion Calendar draws it;
- a moon where the study window ends: no session is planned after it.

A title is 40 % of its course's colour and 60 % of the text. Over every course colour, at a
session's tint and a lecture's, in every theme of D156 and the two before them, it reads at 4.5:1
or more, and so does a past title; the lowest is 4.77, a past violet class on Soft day's cards
(`tests/test_calendar_clean.py`). The label of now is white on the accent of its hour, 4.8:1 by
construction (D78).

**Rejected.** A's 55 % of the course's colour, as proposed: 3.78:1 for an amber session on Soft
day's cards (D156 came after the proposal). D78's accent for the ribbon: it stays between blue and
purple all day, so the morning would not be gold; the ribbon has no text, so the sky's lighter
first light is used. Fading what is over with opacity: its title fell under 4.5:1. B's free time
drawn as bands: not chosen. The legend kept under the grid on a laptop: the sidebar has the room
and lists the courses already.

**Disclosed.** The ribbon is today's sky drawn beside every day shown, for the time zone's main
city (D76). The kinds' marks are a choice; a session's kind is still said in full in its popover.
The courses hidden are kept on the device only. Checked in Chromium only, at 1440 × 900, 1280 ×
800 and phone size, light and dark.

## D158. Points for a session, and its proof of work

**Asked.** The owner, on 2026-11-19, of option C's bar under each day (D157): "more in a
progression way like how much you got done in the day and how many points it will give you if
you do it (like a study session, and you get those points if you post what you did and your
peers validate it like a proof of work, on top of an inherent bonus for completing it (because a
lazy student could simply say they studied even if they have not))".

**Chosen.** Each study session of the plan is worth points:

- 10 for doing it, on the student's word: any answer but Skipped (D25's recall answers
  included, "Nothing" too: the session happened);
- 20 more for its proof: a session post that says what was done (its note, or its line for
  anyone) or shows it (a photo), vouched for by at least one other student. A post logged from
  the session (timed with Focus, or shared from the calendar) names it; one logged by hand goes to
  the first session of its day and course not yet proven. A proven session counts as done for
  its points.

Vouching is D141's mark on a new kind of post: a reader who can see the post (its followers or
everyone, as it was shared) taps Vouch, "I believe this session happened: my word is its proof",
and can take it back; not on one's own posts, not without a handle, not on a post that says or
shows nothing (minutes alone prove nothing), and someone blocked either way is not counted. The
author sees "Vouched for by 1 classmate". The points are the student's own: no one else sees them,
and there is no ranking.

On the calendar, under each day's name, a slim bar fills with the points the day's sessions gave
against what they can give; beside each session's time, "+10" while it is to do, then what it
gave. Its popover says why and offers "Share what you did", which opens the diary's form on that
session (its course, its length, its day): saving it reports the session done, and posts it.

**Rejected.** Points for minutes: the plan sets a session's length, and a longer one is no more
proof. Points without a report: D44 counts an unreported session as done for the plan, but points
ask for the student's word. A proof from any reaction (D108): a reaction says "nice", not "I
believe it". Two vouches: a first class of the pilot may have few classmates on the app. A public
score or a ranking: the stakes (D128) and the study network (D16) are about the work, not about
beating others; a ranking invites collusion and shame.

**Disclosed.** A vouch is a classmate's word, not a check: two friends can vouch for each other's
sessions that never happened, and a post's words or photo can be borrowed. It raises the cost of
claiming a session, it does not prove it. The 10 and 20 are choices, not measured; whether points
bring more sessions, or more honest reports, is for the pilot. A post logged by hand on a day
without a session of its course proves nothing. Checked in Chromium only, at 1440 × 900 and phone
size, light and dark.

## D159. The new-task sheet's own day and time, and "Other…" in its courses

**Asked.** The owner, on 2026-11-19, with screenshots of the new-task sheet in Chromium on
Windows, dark: the system's picker for the due date, grey and unlike the app, "ugly"; and the
list of courses under the course field, '"other:" option please'.

**Chosen.**

- On a computer, the field's own calendar button is hidden and a button beside the field opens
  the app's month: the days as round buttons, Monday first, today ringed, the chosen day in the
  accent, the days before today not offered, arrows for the months; under it the usual times
  (09:00, 12:00, 14:00, 18:00, 23:59) as chips. A day keeps the time chosen, or takes 23:59, as
  the shortcuts do; a time closes the month. The field stays the form's own: it can be typed in,
  and the shortcuts (Tomorrow, In 3 days…) stay under it. On a touch screen the phone's own wheel
  opens on a tap, as before, and the button is not shown.
- The course is a list: "No course", the plan's courses, then "Other…", whose field shows when it
  is chosen and is focused; a course read from the typed line is picked in the list, or typed
  under "Other…" when it is not one of the plan's. A course typed under "Other…" wins over the one
  picked, as on the share forms (D141), and "Other…" with nothing typed is no course
  (`forms.chosen_course`). Without the script both show, the field labelled "Another course".

**Rejected.** A date library: the app has no build step and loads nothing it does not ship.
Splitting the date and the time into two system fields: each still opens the system's picker.
Chips of every course instead of a list: seven long names took three rows of the sheet on a phone.

**Disclosed.** The month and the times were checked in Chromium only, at 1280px dark and phone size
light. The rule that hides the field's own button is WebKit's and Chromium's; Firefox and Safari
were not tried, and may show their own button beside the app's. The times are a choice.

## D160. A picture for each study tip, and shorter tip cards

**Asked.** The owner, on 2026-11-19, of the tips page: "let's start working on the thumbnails for
the studies like i mentioned earlier, make proposals" (the streamlining with icons and figures
deferred earlier). Of three options (A, the study's own number drawn; B, a small scene of the
advice; C, the source at a glance), the owner chose "a mix of A and B, especially B".

**Chosen.** Each of the 26 tips has a picture on a soft tile beside it: a line drawing of what the
advice asks (a checklist ticked off, a calendar with a clock and a place, days in a row with one
missed, a cup with steam and a clock…), in the weight of the app's icons (D139), the accent on the
part that matters (`web/thumbs.py`, drawn inline by the server, classes only). Under the drawing,
A's part: the study's number where its text gives one, "138 studies", "94 tests", "18 to 254
days", in the page's language (`tips.FIGURES`, `service.tip_figure`). Fourteen tips have one.
Eleven do not because their text gives no count (a review, a laboratory study, a study in cells);
the mindset tip gives two meta-analyses, which one number would misrepresent.

The card is shorter: the label, the advice and its evidence stay in view; the limits, what the app
does about it and the sources fold under "Limits, in this app, sources", which opens without a
script. On a laptop the topic's page is wider (72rem) and its tips two to a row.

**Rejected.** Pictures from a stock set: none says what each tip asks. C's source cards: the owner
chose A and B. Shortening the evidence itself: each line was written under the tips' rules (D62),
and a shorter version would need the same review in three languages.

**Disclosed.** The drawings are a matter of taste, and a drawing can only suggest the advice; the
words carry it. Every number under a picture is checked against its tip's own text
(`tests/test_tip_pictures.py`). Folding the limits puts them one tap away, where before they were
read with the rest. Checked in Chromium only, at 1440px light and dark and at phone size.

## D161. Settings as a dashboard

**Asked.** The owner, on 2026-11-19, of Settings' six rows: "maybe we can have everything open as a
dashboard here too?" Of three options (A, six cards with their values; B, everything open with an
index; C, each part as a sentence), the owner chose A.

**Chosen.** Settings is six cards, three to a row on a laptop, two on a tablet, one on a phone. Each
keeps its head (its picture, its name, what it holds), now with "Change ›", the link to its own page
as before (D103), and under it what the part is set to, in two to four lines
(`service.settings_overview`, read from the values its page shows):

- Your week: the study hours, the most a week, the days off, the busy times;
- Exams and deadlines: the next two exams with their dates, the deadlines to do, where the
  timetable comes from;
- Keep your plan (or Your account): the address and whether it is confirmed, the passkeys, the
  recovery code;
- Reminders and the app: on or off and on how many devices, how long before a session;
- Language and appearance: the language, Automatic, Light or Dark, the two themes, the accent;
- AI and your data: AI on or off (or not on this server), whether what you use is counted, the
  tips shown or hidden.

Nothing is changed on the dashboard: each part still saves on its own page, so saving works as
before. The old anchors still lead to their part (D103).

**Rejected.** B, every form open on one page: the longest page of the app, with six forms that save
separately. C, sentences: the owner chose A; passkeys and data read poorly as a sentence.

**Disclosed.** The lines are a summary: a part's page has more (the busy times one by one, every
exam, motion and glass). Whether the app is installed on the phone is not known to the server, so it
is not shown. Checked in Chromium only, at 1440 and 1280px light and dark and at phone size.

## D162. The list and its context

**Asked.** The owner, on 2026-11-19, of pages that "leave a lot of empty room" on a laptop. Of
three options drawn on Tasks ("Calmer App Options", part 4: A, the list and its context; B, a wider
page with cards in a grid; C, a detail panel beside the list), the owner chose A, for the list
pages: Tasks, Courses, Flashcards and Community.

**Chosen.** On a wide screen each of the four pages has two columns: its own list on the left, at
about the width the page had (752px at 1440px), and on the right, in the room the centred column
left empty, what helps act on it. On a phone the right column follows the list.

- Tasks: the open tasks and the done ones; beside them, this week's time for tasks, done of
  planned, and the next one (`service.task_week`: the plan's task sessions from Monday to Sunday
  in the plan's time zone; past ones count as done unless reported skipped, as a task's own
  progress counts them), then the learning platform (its link in place and how many deadlines it
  brought, Open Moodle; the link's error; or how to paste one), then the page's tip (D135).
- Courses: the courses; beside them, importing the semester's program and the class's link (D104).
- Flashcards: the decks and a course's cards; beside them, cards from notes (D134), the tip and a
  new card. The empty list no longer says "above".
- Community: the feed; beside it, the study map's card and Studying now (D108) as before, and on a
  wide screen the student's groups with their week and up to three people at their university
  they neither follow nor asked to, same programme and year first
  (`service.community_context`, `social.classmates`). Its column comes first in the page, so a
  phone keeps Studying now above the feed as it was; on a phone the groups and classmates stay on
  the Groups tab and People, and the feed comes sooner.

The page widens to 70rem from 960px; the side column is 15 to 19rem. The two columns are a grid
whose areas place them, so Community's order in the page does not decide where its column shows.

**Rejected.** B for these pages: more cards per row suits the tips (D160) and Settings (D161), but a
list read from top to bottom loses its order across columns. C: the owner chose A; a panel that is
empty until something is chosen leaves the room empty most of the time. Showing the context column
only from 1200px, where the list keeps 580px beside the sidebar: the page jumped from two columns
at 1099px to one at 1100px, when the sidebar appears; from 960px the list is about 480px at 1100px,
narrow but wider than a phone.

**Disclosed.** On Community the side column comes before the feed in the page, so on a laptop a
keyboard reaches Studying now and the groups before the feed, as a phone always did. This week's
time for tasks counts sessions the plan has moved to its past; a session whose time has gone by
but which the plan has not yet read again counts as planned, not done. Checked in Chromium only,
at phone size and at 1000, 1100 and 1440px, light and dark.

## D163. Moodle within reach

**Asked.** The owner, on 2026-11-19: "the moodle input should be accessible so one can check
their study materials, make proposals". Of three options ("Finishing Choices", part 1: A,
buttons where they are needed; B, one Moodle page; C, the materials inside the app), the owner
chose A.

**Chosen.** Each course's page has an "On Moodle" card. Once the course is linked, it holds
four tiles: the course page, all its materials, its assignments and the student's grades, each
opening on Moodle in another tab. Before that, "Find it on Moodle" opens Moodle's own course
search with the course's name, and a field takes the course's address pasted back. The same
field takes the site alone (`moodle.example.edu`), which is kept as the student's Moodle so the
search can be offered. The focus screen shows the running course's materials and page; the
sidebar and You have a Moodle row (the dashboard, or all courses, once the site is known; before
that, Settings' Moodle in three steps, D154). Today's "Open Moodle" (D140) and the Tasks column
(D162) now use the same site.

The site is the one given, else the one of the deadlines' calendar link (D92), else a linked
course's (`service.moodle_site`). The four pages are Moodle's standard addresses with the
course's number: `course/view.php`, `course/resources.php`, `mod/assign/index.php` and
`grade/report/user/index.php`, each taking the course as `id`, as Moodle 4.5's own files read
it (checked in its source, MOODLE_405_STABLE). The app builds every address from the site and
the number; the text pasted is only read for those two. A file's or an activity's page
(`mod/resource/view.php?id=…`) is refused: its number is not the course's.

**Rejected.** B as the only way in: a page a student has to remember to visit, when the need
comes on a course or in a session. C, files listed inside the app: it means holding a key that
reads the student's whole Moodle, and D92 keeps sign-in out until a university agrees. Reading
the course's number from the calendar export: Moodle 4.5's export (`calendar/export_execute.php`)
writes each event's course as its short name (`CATEGORIES`), never its number or address, so the
student pastes it once.

**Disclosed.** Not tried on a real university's Moodle: the addresses come from Moodle 4.5's
source, and a site that changed its pages or put Moodle behind another sign-in may answer
differently. On the focus screen the materials open in another tab, which the focus clock counts
as time away (D17), as any other tab: whether reading the course's own materials should count is
the owner's to decide. Checked in Chromium only, at phone size and 1280px, light and dark.

## D164. A cover for each tip

**Asked.** The owner, on 2026-11-19, sent screenshots of the tips on their phone, "what i think
should be done properly in terms of graphics and ideas for the thumbnails". Of three options
("Finishing Choices", part 2: A, a cover strip with the drawing and the number; B, the number as
the headline; C, illustrated covers), the owner chose C.

**Chosen.** Each of the 26 tips has a small scene across the top of its card, of what its advice
asks: a book faded beside a card asked and ticked (test yourself), a week of tiles with sessions
spread and growing arcs between them (spacing), seven nights of the same sleep (keep it regular),
a list with its last item drawn into a calendar's slot (write down what is left), a balance at
rest (a mindset alone did little for grades), and so on (`web/covers.py`). The scenes are filled
shapes in the theme's classes only, the accent the student chose (D156) and the card's surface, so
they follow light, dark and every accent, and keep the Content-Security-Policy. A scene keeps its
proportions, centred, at most 148px tall; where the card is wider, the accent's faint ground fills
the sides. The text then runs the card's full width under it, two cards to a row on a laptop as
before. A study's number, where the tip's text gives one (`tips.FIGURES`, D160), is the cover's
caption in its own band under the scene: translated, never drawn in the picture, never over it.
D160's line drawings and their 68px tile are gone (`web/thumbs.py`).

**Rejected.** A, the strip with D160's drawings made larger: the owner chose C. B, the number
alone: tips without a number would have kept almost no picture. The number laid over the scene's
corner, as first built: it covered the drawing ("128 experiments" over the gift, "18 to 254
days" over the last days), so it has its own band.

**Disclosed.** The scenes are a matter of taste, drawn by hand in this repository; each was
checked against its tip's advice, not with students. The number's band makes a cover with a
number about 28px taller than one without. Checked in Chromium only, at phone size and two laptop
sizes, light and dark, with the default accent and amber.

## D165. Edit Today: each switch with its picture, and saved as it is flipped

**Asked.** The owner, on 2026-11-20, with a screenshot of Edit Today: "icons here too? our
absolute most important pillar is frictionless and ease of use for the app anywhere we can".

**Chosen.** Each row of Edit Today shows the picture its widget's head shows on Today, on the
same soft tile as You's rows (D139), so a switch and the widget it turns on look alike
(`service.TODAY_PICTURES`, in `today_widgets_view`). Two heads shared a picture with another
widget, so they changed on Today too: Up next is a target (it was the stopwatch, Focus's) and On
your mind a page with a pencil (it was the list, Tasks'); no two widgets now look alike.

Each switch is saved the moment it is flipped (`data-autosave`, `ui.js`): the form is sent in the
background, "Saved." says so under the list, and a failure says "That did not work. Try again."
Saves are sent one after another, so the last flip is the one kept. Done stays and goes back to
Today; without a script it is still what saves the page.

The owner's pillar is written in CLAUDE.md's conventions, so that it weighs on every slice: fewer
steps, nothing to confirm that need not be.

**Rejected.** Pictures of their own for the switches: a switch then looks like nothing on Today,
and the student has to learn two pictures for one thing. Saving the whole page on Done only, as
D155 did: a student who flips a switch and leaves with the back arrow or the tab bar lost the
change. A toast for each save: a flip is small, and a toast over the page for each would be
louder than the change; one quiet line is enough.

**Disclosed.** A student who flips switches and leaves before the answer comes back may lose the
last flip; the line says "Saved." only once the server has answered. Checked in Chromium only, at
phone size (dark, French) and 1280px (light): a switch flipped, the page reloaded, the switch as
left.

## D167. Lighter pages: the diary's older weeks and a course's page

**Asked.** The owner, on 2026-11-20, with screenshots on their phone. Of the diary on Progress:
"what is that list crap": its older weeks were six folds on the page's background, under them
a second "Last week" (the weekly review) that read as part of the same list. Of a course's page:
"heavy heavy heavy to watch and scroll through": every week showed all its topics and links,
and the exam's settings, the credits and the form to add a link were always open.

**Chosen.** On Progress, the diary's weeks still open (this week and the last, or the latest)
show their sessions as before; the older weeks are one card, "Earlier weeks", a row each with
its plant, its name and its sum, opening in place, the chevron at the row's end as in the app's
other lists. The weekly review comes before the diary, as "Last week, in review".

On a course's page each week is one row: its name, where it stands, its recall as a number and
its topics on one line; its readings and what to do with it (cards, links, explaining it) open
in place, and this week opens by itself. The exam shows its date; its settings fold under
"Change" and are saved as each field changes (`data-autosave`, D165), the button kept only where
scripts do not run (`<noscript>`). Stopping the course's plan is its own button under them,
never a switch that a stray tap would save. The credits, once known, fold under a row that says
them, and are saved as typed. Adding a link is one row until it is wanted. Sharing notes,
explaining an idea and classmates' notes are buttons in the material card, not a line on the
page's background.

**Rejected.** Taking the older weeks off Progress to a page of their own: one more page to find,
for a list a student opens rarely but expects to find there. A summary of the exam's settings
in its closed row: saved in the background, it would show the old values until the page is
opened again. Keeping "Save and plan" beside fields saved as they change: two ways to do one
thing.

**Disclosed.** Measured in Chromium at an iPhone 13's size on the demo's @alex (scrollHeight):
a course's page went from 3,926 to 3,046 pixels, 5.9 to 4.6 screens; Progress with "More detail"
open went from 8,292 to 8,421, a little longer, the older weeks' card having a title and two
lines a row: the diary is tidier, not shorter. Checked by eye in Chromium only, at phone size,
dark and French. A field of the exam changed and left before the server answers may be lost, as
on Edit Today (D165).
