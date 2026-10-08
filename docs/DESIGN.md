# Design system and screens

*How the hosted app looks and why. The rules come from Apple's Human Interface
Guidelines, which are public; nothing is copied from Apple's or any other app's
artwork. The code is `src/cps/web/static/style.css` (tokens and components) and
`src/cps/web/templates/` (screens). Screenshots are retaken by a browser run
whenever a screen changes, and live in `docs/`.*

## Principles

1. **One question per screen.** Today answers "what now?"; Calendar "when?"; Tasks
   "what is due?"; Progress "am I keeping up?"; Settings "how should it plan?".
2. **Calm, then clear.** Grey grouped backgrounds, white cards, one accent colour
   for what can be tapped, colour for meaning only (course colours; red for exams,
   today and risk; green for done).
3. **Facts, not cheerleading.** "4 of 6 sessions this week", not "Amazing!!". A
   number the student can check against the calendar beats an adjective.
4. **Works without its scripts.** Every form posts; every page reads. Scripts add
   dragging, background saves and toasts.
5. **Phone first, keyboard welcome.** 44-pixel targets, a tab bar on a phone; N, T
   and the arrow keys on a desk.

## Tokens

All in `:root` of `style.css`, redefined for dark mode.

| group | tokens | values (light / dark) |
|---|---|---|
| backgrounds | `--bg`, `--surface`, `--elevated`, `--material` | `#f2f2f7` / `#000`, `#fff` / `#1c1c1e`, translucent bars |
| labels | `--text`, `--muted`, `--faint` | black, 60 % and 30 % grey; white and greys in dark |
| tint | `--accent`, `--accent-soft` | `#007aff` / `#0a84ff` |
| meaning | `--danger`, `--warn`, `--ok`, `--now` | red (exams, today, risk), orange (soon), green (done) |
| courses | `--c0` … `--c6`, `--c-none` | seven colours checked for colour-blind separation in both themes; no red |
| shape | `--radius` 12, `--radius-sm` 10, `--radius-lg` 16 | |
| type | system font: San Francisco on Apple devices, Segoe UI on Windows, Roboto on Android; 15 px body, titles at 2.1 rem | |
| progress | `--ring` (the ring's colour: `--ok` when complete, `--accent` otherwise) | |

## Components

Existing: top bar with a segmented control, tab bar, cards, inset lists, stat
tiles, badges, progress bars (`w0`…`w100` classes, no inline style), pills, sheets
(native `popover`), the toast capsule, the calendar.

Added in this stage:

| component | what it shows | notes |
|---|---|---|
| **ring** | sessions done of planned, this week | an SVG circle with `pathLength="100"` whose `stroke-dasharray` attribute carries the share (an SVG attribute, not a style, so the Content-Security-Policy's ban on inline style holds); the number is written beside it, so the ring is never the only carrier of the value |
| **streak chip** | the streak in days, a flame-free mark | words, not emoji; a best streak beside it on Progress |
| **day grid** | the weeks since the plan began (four at least, counting weeks to come; twelve at most), one square per day: studied (green, three shades by sessions), forgiven, missed, rest, future | a `<table>` with a text label per cell for screen readers; colour plus shape (a dot for forgiven) |
| **milestone row** | reached milestones, and the next one with "2 days to go" | no badges, no unlock animation |
| **review card** | last week's numbers, one paragraph, one suggestion | "written by AI" when it was |
| **post card** | a focus session (course, what was done, time, effort, progress, whether the focus was checked, note, photos) or an explanation; the author's avatar, handle and university; Kudos (or "I got it" on an explanation) and Comment | the counts are of kudos and comments on this post, never of followers; a kudos tap is sent in the background and the bar is replaced in place |
| **avatar** | the handle's first letter on one of the seven course colours | no photos of people, so no faces to moderate |
| **person row** | avatar, handle, university and programme, one action (Accept, Unfollow, Remove) | an inset list, as in iOS Settings |
| **typed task field** | one line in the new-task sheet: type a sentence, the fields below fill in | the fields stay editable; nothing is saved without "Add" |

## Screens

**Today** (`/p/<token>`). Title and date; a strip with the streak and the week's ring;
"Now" or "Up next" with what to do; sessions waiting for a report; on Mondays and
Tuesdays, last week's review card until it is opened; cheers received; tasks;
exams. On a wide screen the calendar is beside it.

**Calendar** (`/p/<token>/week`). Day, 3 Days, Week; drag to move a session, draw to
add a busy time. Unchanged in this stage.

**Tasks** (`/p/<token>/tasks`). Unchanged, except the new-task sheet's typed field.

**Progress** (`/p/<token>/progress`, new tab). The ring and the streak, large; the
day grid; "Start focus"; the diary (every session logged, whoever sees it);
milestones; each exam's readiness; the weekly review.

**Focus** (`/p/<token>/focus`, the tab bar's centre). A course, or the plan's next
session, and Start; then the full-screen timer and Finish; then the log (D17).

**Community** (`/p/<token>/community`, new tab; D16). Without a handle, a card that
says what joining means and "Choose a handle", above the feeds. A segmented control:
Following (one's own shared posts and those of people one follows) and Explore
(everyone-posts, filtered by university, programme, course and kind). "People" with
the number of follow requests, and "Explain something".

**Post** (`/p/<token>/post/<id>`). The card, then its comments and a comment box;
the author of a comment and the author of the post can delete it.

**Profile** (`/p/<token>/profile`). Handle, university, programme, a line about
oneself, "I am 15 or older", a link to the guidelines; "Leave the network" keeps
the diary.

**A person** (`/p/<token>/u/<handle>`). Avatar, handle, university and programme,
bio; Follow, Requested, or Following; "Follows you"; the posts the viewer may see.
No follower counts.

**People** (`/p/<token>/people`). Search by handle, university or programme; those
asking to follow (Accept, Decline); following; followers (Remove); requests waiting.

**Explain it simply** (`/p/<token>/explain`; D19). The idea, its course, the
explanation, a sketch; for everyone by default.

**Tab bar.** Today, Calendar, Focus, Community, Progress: five, as iOS allows. Tasks
leaves the phone's tab bar for Community; it stays in the wide screen's top bar,
under Today's "Tasks, See all", and behind the + button.

**Settings.** Adds the language, and AI: on or off, with what is sent and to whom.

## Writing

- Second person, present tense, short sentences. French uses *tu*.
- Numbers as numerals, units spelled out once ("6 h of study").
- A button says what it does: "Add", "Follow", "Leave the network", not "OK".
- No exclamation marks in the interface; at most one, in a milestone.
- Never blame: "Nothing reported yesterday" rather than "You missed yesterday".

## Accessibility

Contrast at least 4.5:1 for text in both themes; a visible focus ring; every
graphic carries its value in text; `prefers-reduced-motion` turns off the ring's
animation; pages read in order without CSS.
