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
| **buddy card** | name, streak, the week's ring, "studied today", a Cheer button | nothing else about the friend |
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
day grid; milestones; each exam's readiness; the weekly review; study buddies, or an
invitation to add one.

**Study buddies** (`/p/<token>/buddies`). The buddy cards; "Invite" makes a code and a
link to share; "Enter a code"; the name buddies see; what is shared, in one
sentence; Leave.

**Join** (`/join/<code>`). Who invited you (their chosen name), the code, where to
enter it, and "Make your plan" for someone new.

**Settings.** Adds the language, the name buddies see, and AI: on or off, with what
is sent and to whom.

## Writing

- Second person, present tense, short sentences. French uses *tu*.
- Numbers as numerals, units spelled out once ("6 h of study").
- A button says what it does: "Add", "Cheer", "Leave", not "OK".
- No exclamation marks in the interface; at most one, in a milestone.
- Never blame: "Nothing reported yesterday" rather than "You missed yesterday".

## Accessibility

Contrast at least 4.5:1 for text in both themes; a visible focus ring; every
graphic carries its value in text; `prefers-reduced-motion` turns off the ring's
animation; pages read in order without CSS.
