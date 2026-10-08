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
