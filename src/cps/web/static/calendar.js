// The hosted app's calendar. Layout and input only: it draws what
// /p/<token>/calendar.json says, sends each change to the server, and draws the
// calendar the server answers with. What a change means for the plan is decided
// in Python (cps.service), never here.
//
// Mouse: drag on empty time to block it, drag a busy time or a study session to
// move it, pull a busy time's lower edge to resize it, click anything for details.
// Touch: tap empty time to block it, tap anything for details (moving a session
// is done there, with a date and a time); a swipe scrolls.

const SNAP = 15; // minutes
const PX = 0.9; // pixels per minute: 54 px an hour
const GUTTER = 52; // px
import { t } from "./i18n.js";
import { still } from "./motion.js";

const KIND_WORDS = { "first review": "Self-test", review: "Self-test", task: "Deadline work", practice: "Exam practice" };
const OUTCOMES = [["done", "Done"], ["skipped", "Skipped"], ["struggled", "Hard"]];
// A self-test asks how much was recalled instead (DECISIONS.md D25).
const RECALL = [["forgot", "Nothing"], ["some", "Some"], ["most", "Most"], ["all", "All"], ["skipped", "Skipped"]];
const SELF_TESTS = ["review", "first review"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]; // as the server names them
const MARKS = { done: "✓", skipped: "–", struggled: "!", forgot: "!", some: "~", most: "✓", all: "✓" };

const toMin = (clock) => {
  const [h, m] = String(clock || "0:0").split(":").map(Number);
  return h * 60 + (m || 0);
};
const toClock = (minutes) => {
  const m = Math.max(0, Math.min(1440, Math.round(minutes)));
  return m === 1440 ? "24:00" : `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
};
const snap = (minutes) => Math.round(minutes / SNAP) * SNAP;
const narrow = () => window.matchMedia("(max-width: 900px)").matches;
const shiftDate = (iso, days) => {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function pinIcon() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("class", "pin");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", "M10.5 1.5 14.5 5.5 12 6.5 9.5 9 10 12.5 8.5 14 5.8 11.3 2.5 14.5 1.5 13.5 4.7 10.2 2 7.5 3.5 6 7 6.5 9.5 4Z");
  svg.append(path);
  return svg;
}

// Side-by-side lanes for boxes that overlap in one day.
function lanes(boxes) {
  boxes.sort((a, b) => a.start - b.start || b.end - a.end);
  let group = [];
  let groupEnd = -1;
  const flush = () => {
    const ends = [];
    for (const box of group) {
      let lane = ends.findIndex((end) => end <= box.start);
      if (lane < 0) {
        lane = ends.length;
        ends.push(0);
      }
      ends[lane] = box.end;
      box.lane = lane;
    }
    for (const box of group) box.lanes = ends.length;
    group = [];
  };
  for (const box of boxes) {
    if (box.start >= groupEnd && group.length) flush();
    group.push(box);
    groupEnd = Math.max(groupEnd, box.end);
  }
  if (group.length) flush();
  return boxes;
}

export class Calendar {
  constructor(root, { token, first, onChanged, toast, onRange, setup = false }) {
    this.root = root;
    this.token = token;
    this.first = first;
    // Drawing a week by hand (D32): what is drawn is a class unless said otherwise.
    this.setup = setup;
    // A strip of days that scrolls sideways, a day at a time, as Google Calendar and
    // Notion Calendar do (D68): the days shown, and a few more on each side drawn
    // ahead; near either end it is drawn again around the day shown first. Drawing
    // a week by hand shows its week alone.
    this.strip = !setup;
    this.settling = null;
    this.today = first;
    this.onChanged = onChanged || (() => {});
    this.toast = toast || (() => {});
    this.onRange = onRange || (() => {});
    this.data = null;
    this.scrolled = null;
    this.pop = null;
    this.loadedAt = Date.now();
    this.view = this.savedView();
    window.matchMedia("(max-width: 900px)").addEventListener("change", () => {
      this.view = this.savedView();
      this.load();
    });
    let resized = null;
    window.addEventListener("resize", () => {
      clearTimeout(resized);
      // A column is a share of the width: drawn again when the width changes.
      resized = setTimeout(() => (this.strip && this.data ? this.draw(this.data) : this.fitLabels()), 120);
    });
    setInterval(() => this.placeNow(), 60 * 1000);
  }

  // Day, 3 days or a week: the viewer's last choice on this device, else what the
  // screen suits. Kept in the browser only; an unavailable storage just forgets.
  savedView() {
    try {
      const kept = Number(window.localStorage.getItem(`cps-view-${narrow() ? "narrow" : "wide"}`));
      if ([1, 3, 7].includes(kept)) return kept;
    } catch {
      /* private browsing, blocked storage: use the default */
    }
    return narrow() ? 3 : 7;
  }

  setView(days) {
    this.view = days;
    try {
      window.localStorage.setItem(`cps-view-${narrow() ? "narrow" : "wide"}`, String(days));
    } catch {
      /* not kept; the choice still applies now */
    }
    this.scrolled = null;
    this.load();
  }

  get count() {
    return this.view;
  }

  // Days drawn on each side of those shown: a week, room for a long swipe.
  get buffer() {
    return this.strip ? 7 : 0;
  }

  query() {
    return `start=${shiftDate(this.first, -this.buffer)}&days=${this.count + 2 * this.buffer}`;
  }

  async request(path, body) {
    const response = await fetch(`/p/${this.token}/${path}?${this.query()}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let answer = {};
    try {
      answer = await response.json();
    } catch {
      /* said below */
    }
    if (!response.ok) throw new Error(answer.message || t("The server answered {status}.", { status: response.status }));
    return answer;
  }

  // `moving`: after a change elsewhere on the page (a task added), the sessions the
  // plan moved slide to their new places; a new week or day is just drawn.
  async load(moving = false) {
    try {
      this.draw(await this.request("calendar.json"), moving);
    } catch (error) {
      this.toast(error.message, true);
    }
  }

  go(where) {
    const target = where === "today" ? this.today : shiftDate(this.first, where === "back" ? -this.count : this.count);
    const index = this.data && this.strip ? this.data.days.findIndex((day) => day.date === target) : -1;
    const scroller = this.root.querySelector(".cal-scroll");
    // Within the days drawn, the strip slides there; further, it is drawn there.
    if (index >= 0 && scroller && index + this.count <= this.data.days.length) {
      this.first = target;
      scroller.scrollTo({ left: index * this.column, behavior: still() ? "auto" : "smooth" });
      return;
    }
    this.first = target;
    this.load();
  }

  // Where the strip came to rest: its first whole day is the first shown. Near
  // either end of what is drawn, the strip is drawn again around it.
  settle(scroller) {
    if (!this.data || !this.column) return;
    const days = this.data.days;
    const index = Math.min(Math.max(Math.round(scroller.scrollLeft / this.column), 0), days.length - this.count);
    if (index === this.shown) return; // where it was drawn: the page set it there
    this.shown = index;
    this.first = days[index].date;
    this.announce(index);
    if (index < 2 || index > days.length - this.count - 2) {
      this.scrolled = scroller.scrollTop;
      this.load();
    }
  }

  // The range in the bar: the days shown, not all those drawn.
  announce(index) {
    const days = this.data.days;
    const first = days[index].label;
    const last = days[Math.min(index + this.count, days.length) - 1].label;
    this.onRange(this.count === 1 ? first : `${first.slice(4)} – ${last.slice(4)}`, this.view);
  }

  // A change sent to the server; the calendar it answers with is drawn.
  async change(path, body, done) {
    this.closePop();
    try {
      this.draw(await this.request(path, body), true);
      if (done) this.toast(done);
      this.onChanged();
    } catch (error) {
      this.toast(error.message, true);
      if (this.data) this.draw(this.data);
    }
  }

  saveActivities(rows, done) {
    const clean = rows.map(({ label, kind, weekday, date, start, end }) => ({
      label, kind, weekday: weekday || null, date: date || null, start, end,
    }));
    return this.change("activities", clean, done);
  }

  // -- drawing ---------------------------------------------------------------

  draw(data, moving = false) {
    const scroller = this.root.querySelector(".cal-scroll");
    if (scroller && this.data) this.scrolled = scroller.scrollTop;
    const before = moving ? this.positions() : null;
    this.data = data;
    this.loadedAt = Date.now();
    const days = data.days;
    // The whole day, midnight to midnight, as Apple Calendar draws it (D59): an
    // early class, a late shift or a night out is on the grid like the rest. It
    // still opens at the study window, or near now on today (below), and the hours
    // outside the window are shaded.
    this.top = 0;
    this.bottom = 24 * 60;
    const height = (this.bottom - this.top) * PX;
    // In the strip a column is a share of the width (the days shown share it);
    // the days drawn ahead lie beyond the edges. The width is the calendar's box,
    // which its days cannot widen (CSS: min-width 0, overflow hidden).
    const width = (this.root.getBoundingClientRect().width || window.innerWidth) - GUTTER;
    this.column = this.strip ? Math.max(width / this.count, 40) : 0;
    const track = this.strip ? `${this.column}px` : "minmax(0, 1fr)";
    const columns = `${GUTTER}px repeat(${days.length}, ${track})`;

    const head = h("div", { class: "cal-head" }, h("div"));
    head.style.gridTemplateColumns = columns;
    days.forEach((day) => {
      const [wd, dn] = [day.label.slice(0, 3), day.date.slice(8, 10).replace(/^0/, "")];
      const weekend = day.weekday === "Sat" || day.weekday === "Sun";
      head.append(
        h("div", {
          class: `cal-day${day.date === data.now.date ? " today" : ""}${day.in_horizon ? "" : " out"}${weekend ? " weekend" : ""}`,
        }, h("span", { class: "wd" }, wd), h("span", { class: "dn" }, dn)),
      );
    });

    const body = h("div", { class: "cal-body" });
    body.style.gridTemplateColumns = columns;
    body.style.height = `${height}px`;
    const gutter = h("div", { class: "cal-gutter" });
    this.gutter = gutter;
    for (let m = this.top; m < this.bottom; m += 60) {
      // Midnight is named too, set under the top edge rather than across it.
      const label = h("div", { class: `cal-hour${m === this.top ? " first" : ""}` }, toClock(m));
      label.style.top = `${(m - this.top) * PX}px`;
      gutter.append(label);
    }
    body.append(gutter);

    const [earliest, latest] = data.window.map((x) => x * 60);
    this.columns = days.map((day, d) => {
      const column = h("div", {
        class: `cal-col${day.date === data.now.date ? " today" : ""}${day.in_horizon ? " can-add" : ""}`,
        "data-day": String(d),
      });
      for (let m = this.top; m < this.bottom; m += 30) {
        const line = h("div", { class: `cal-line${m % 60 ? " half" : ""}` });
        line.style.top = `${(m - this.top) * PX}px`;
        column.append(line);
      }
      const shade = (from, to) => {
        if (to <= from) return;
        const block = h("div", { class: "cal-closed" });
        block.style.top = `${(from - this.top) * PX}px`;
        block.style.height = `${(to - from) * PX}px`;
        column.append(block);
      };
      shade(this.top, Math.min(earliest, this.bottom));
      shade(Math.max(latest, this.top), this.bottom);
      if (day.in_horizon) this.wireColumn(column, d);
      body.append(column);
      return column;
    });

    // Boxes: the timetable, exams and sessions from the server, and the
    // student's own busy times expanded on the days they fall on.
    const boxes = data.items.map((item) => ({ ...item }));
    data.activities.forEach((row, index) => {
      days.forEach((day, d) => {
        const applies = row.date ? row.date === day.date : row.weekday === day.weekday;
        if (!applies) return;
        const start = toMin(row.start);
        const end = toMin(row.end) > start ? toMin(row.end) : 1440;
        boxes.push({ day: d, start, end, label: row.label, kind: "own", row: index, color: row.color, own: row.kind });
      });
    });
    const byDay = days.map(() => []);
    for (const box of boxes) if (byDay[box.day]) byDay[box.day].push(box);
    byDay.forEach((list, d) => {
      for (const box of lanes(list)) this.columns[d].append(this.box(box, d));
    });

    // The day names ride along with the columns, held at the top; the hours, at the
    // left (CSS: sticky).
    const scroll = h("div", { class: `cal-scroll${this.strip ? " days-strip" : ""}` }, head, body);
    const used = new Set(data.items.concat(data.activities).map((i) => i.color).filter((c) => c !== null && c !== undefined));
    const legend = h("div", { class: "legend", "aria-label": t("Courses") },
      data.legend.filter((l) => used.has(l.color)).map((l) => h("span", {}, h("span", { class: `dot c${l.color}` }), l.course)));
    // An empty week to draw on says how, until the first class is in (D32).
    const empty = this.setup && !data.activities.length
      ? h("div", { class: "cal-empty", "aria-hidden": "true" },
        h("div", { class: "cal-empty-card" },
          h("b", {}, t("Drag on the calendar to add your first class")),
          h("span", {}, narrow() ? t("Tap the hour it starts; you can change it after.") : t("From its start to its end, on its day; you can change it after."))))
      : null;
    this.root.replaceChildren(h("div", { class: "cal" }, scroll, empty, legend.childElementCount ? legend : null));
    this.placeNow();
    this.fitLabels();

    if (this.scrolled !== null) scroll.scrollTop = this.scrolled;
    else {
      // Drawing a week starts where classes do, in the morning; a plan, at now.
      const focus = !this.setup && days.some((d) => d.date === data.now.date) ? Math.max(data.now.minute - 90, earliest) : earliest;
      scroll.scrollTop = Math.max((focus - this.top) * PX, 0);
    }
    let shown = 0;
    if (this.strip) {
      shown = Math.max(days.findIndex((day) => day.date === this.first), 0);
      this.shown = shown;
      scroll.scrollLeft = shown * this.column;
      const rest = () => this.settle(scroll);
      // Where `scrollend` is known it says when the strip rests; elsewhere, a pause.
      if ("onscrollend" in window) scroll.addEventListener("scrollend", rest);
      else scroll.addEventListener("scroll", () => {
        clearTimeout(this.settling);
        this.settling = setTimeout(rest, 160);
      });
    }
    this.announce(shown);
    if (before) this.slide(before);
  }

  // -- motion (DECISIONS.md D30) -------------------------------------------------
  // After a change the plan may move sessions. A session's id changes with its time,
  // so blocks are paired by title, in order: the k-th "Self-test: Algebra 3" before
  // is the k-th after. Each slides from where it was to where it is (FLIP); a block
  // that was not there grows in. Layout only: the server has already decided.

  positions() {
    const found = new Map();
    this.root.querySelectorAll(".ev-study[data-key]").forEach((node) => {
      const list = found.get(node.dataset.key) || [];
      list.push(node.getBoundingClientRect());
      found.set(node.dataset.key, list);
    });
    return found;
  }

  slide(before) {
    if (still()) return;
    const ease = "cubic-bezier(0.22, 1, 0.36, 1)";
    this.root.querySelectorAll(".ev-study[data-key]").forEach((node) => {
      const was = (before.get(node.dataset.key) || []).shift();
      const now = node.getBoundingClientRect();
      if (!was) {
        node.animate([{ opacity: 0, transform: "scale(0.9)" }, { opacity: 1, transform: "none" }], { duration: 320, easing: ease });
        return;
      }
      const dx = was.left - now.left;
      const dy = was.top - now.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1 && Math.abs(was.height - now.height) < 1) return;
      node.animate(
        [{ transform: `translate(${dx}px, ${dy}px)`, zIndex: 5 }, { transform: "none", zIndex: 5 }],
        { duration: 560, easing: ease },
      );
    });
  }

  placeNow() {
    if (!this.data || !this.columns) return;
    this.root.querySelectorAll(".cal-now, .cal-now-label").forEach((n) => n.remove());
    const d = this.data.days.findIndex((day) => day.date === this.data.now.date);
    const minute = this.data.now.minute + (Date.now() - this.loadedAt) / 60000;
    if (d < 0 || minute < this.top || minute > this.bottom) return;
    const line = h("div", { class: "cal-now", "aria-hidden": "true" });
    line.style.top = `${(minute - this.top) * PX}px`;
    this.columns[d].append(line);
    // As Apple Calendar does: the time itself, in red, in the hour gutter.
    const label = h("div", { class: "cal-now-label", "aria-hidden": "true" }, toClock(Math.floor(minute)));
    label.style.top = `${(minute - this.top) * PX}px`;
    this.gutter.append(label);
    this.root.querySelectorAll(".cal-hour").forEach((hour) => {
      const near = Math.abs(parseFloat(hour.style.top) - (minute - this.top) * PX) < 12;
      hour.style.visibility = near ? "hidden" : "";
    });
  }

  // What a block shows when its title does not fit (AUDIT.md item 39): in a narrow
  // column the short name (the course, not "Self-test: …" or the room); lines
  // clamped to the block's height, the last one ending in an ellipsis; and if a
  // single word is wider than the block, one line with an ellipsis instead of a
  // word cut in two. The full title is in the popover and the tooltip.
  fitLabels() {
    if (!this.columns || !this.columns.length) return;
    const narrowColumns = this.columns[0].clientWidth < 120;
    const ruler = (this.ruler = this.ruler || document.createElement("canvas").getContext("2d"));
    this.root.querySelectorAll(".ev:not(.ghost)").forEach((node) => {
      const label = node.querySelector(".l");
      if (!label) return;
      label.textContent = narrowColumns && node.dataset.short ? node.dataset.short : node.dataset.full;
      node.classList.remove("tight");
      const style = getComputedStyle(label);
      ruler.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
      const room = node.clientWidth - parseFloat(getComputedStyle(node).paddingLeft) - parseFloat(getComputedStyle(node).paddingRight);
      const widest = Math.max(...label.textContent.split(/\s+/).map((w) => ruler.measureText(w).width));
      if (widest > room + 0.5) {
        node.classList.add("tight");
        label.style.webkitLineClamp = "";
        return;
      }
      const time = node.querySelector(".t");
      const timeHeight = time && getComputedStyle(time).display !== "none" ? time.offsetHeight : 0;
      const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.25;
      const lines = Math.max(1, Math.floor((node.clientHeight - 6 - timeHeight) / lineHeight));
      label.style.webkitLineClamp = String(lines);
    });
  }

  box(box, d) {
    const from = Math.max(box.start, this.top);
    const to = Math.min(box.end, this.bottom);
    const colour = box.color === null || box.color === undefined ? "" : ` c${box.color}`;
    let cls = `ev ev-${box.kind}`;
    if (box.kind === "study") {
      cls += colour || " cn";
      if (box.done) cls += " past";
      if (box.reported) cls += ` reported-${box.reported}`;
      if (box.movable) cls += " movable";
    } else if (["calendar", "skipped", "quiz", "mock"].includes(box.kind)) cls += colour;
    else if (box.kind === "partial") cls += ` ev-exam${colour}`;
    else if (box.kind === "own" && box.own === "class") cls += ` ev-class${colour}`;
    const tall = (to - from) * PX;
    if (tall < 34) cls += " short";
    let label = box.label;
    let short = "";
    if (box.kind === "own") {
      const kind = this.kindLabel(this.data.activities[box.row].kind);
      // A class is its course, as in a timetable; other busy times say what they are.
      if (box.own !== "class" && kind.toLowerCase() !== label.toLowerCase()) label = `${label} · ${kind}`;
      short = box.label;
    } else if (box.kind === "study") {
      short = box.session_kind === "task" ? box.title : box.course || box.title;
    } else if (["calendar", "skipped", "quiz", "mock", "partial"].includes(box.kind) && box.course) {
      short = box.course;
    }
    const span = `${toClock(box.start)}–${toClock(box.end)}`;
    const node = h("div", {
      class: cls,
      tabindex: "0",
      role: "button",
      title: `${label}\n${span}`,
      "data-full": label,
      "data-short": short || null,
      "data-key": box.kind === "study" ? `${box.session_kind || ""}|${box.title || label}` : null,
      "aria-label": t("{label}, {day}, {start} to {end}", { label, day: this.data.days[d].label, start: toClock(box.start), end: toClock(box.end) }),
    },
    h("span", { class: "t" }, span),
    h("span", { class: "l" }, label));
    node.style.top = `${(from - this.top) * PX}px`;
    node.style.height = `${Math.max(tall - 2, 16)}px`;
    node.style.left = `calc(${(100 * box.lane) / box.lanes}% + 2px)`;
    node.style.width = `calc(${100 / box.lanes}% - 4px)`;
    if (box.pinned) node.append(pinIcon());
    if (box.reported) node.append(h("span", { class: "mark", "aria-hidden": "true" }, MARKS[box.reported] || ""));
    if (box.kind === "own") node.append(h("div", { class: "grip", "aria-hidden": "true" }));

    const open = () => {
      if (box.kind === "study") this.openSession(box, node);
      else if (box.kind === "own") this.openActivity(box.row, d, node);
      else this.openInfo(box, d, node);
    };
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
    const draggable = box.kind === "own" || (box.kind === "study" && box.movable);
    node.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.stopPropagation();
      // A finger on a study session or the timetable scrolls; only a busy time
      // is dragged by touch (its box refuses to scroll the page).
      const touchDrag = box.kind === "own";
      if (!draggable || (event.pointerType === "touch" && !touchDrag)) {
        const x0 = event.clientX;
        const y0 = event.clientY;
        const up = (e) => {
          node.removeEventListener("pointerup", up);
          if (Math.abs(e.clientX - x0) + Math.abs(e.clientY - y0) < 8) open();
        };
        node.addEventListener("pointerup", up);
        return;
      }
      this.drag(event, node, box, d, open);
    });
    return node;
  }

  kindLabel(kind) {
    return (this.data.kinds.find((k) => k.id === kind) || { label: t("Busy") }).label;
  }

  // -- dragging ----------------------------------------------------------------

  minuteAt(column, clientY) {
    return this.top + (clientY - column.getBoundingClientRect().top) / PX;
  }

  dayAt(clientX) {
    for (let d = 0; d < this.columns.length; d++) {
      const rect = this.columns[d].getBoundingClientRect();
      if (clientX >= rect.left && clientX < rect.right) return d;
    }
    return null;
  }

  autoscroll(clientY, clientX = null) {
    const scroller = this.root.querySelector(".cal-scroll");
    const rect = scroller.getBoundingClientRect();
    if (clientY < rect.top + 36) scroller.scrollTop -= 12;
    else if (clientY > rect.bottom - 36) scroller.scrollTop += 12;
    // A block dragged to the strip's edge carries it along, a few pixels at a time
    // (snapping is off meanwhile, or the strip would spring back).
    if (!this.strip || clientX === null) return;
    scroller.classList.add("dragging");
    if (clientX < rect.left + GUTTER + 28) scroller.scrollLeft -= 10;
    else if (clientX > rect.right - 28) scroller.scrollLeft += 10;
  }

  endDrag() {
    const scroller = this.root.querySelector(".cal-scroll");
    if (scroller) scroller.classList.remove("dragging");
  }

  drag(event, node, box, d0, open) {
    event.preventDefault();
    const resizing = event.target.classList.contains("grip");
    const length = box.end - box.start;
    const grab = this.minuteAt(this.columns[d0], event.clientY) - box.start;
    const x0 = event.clientX;
    const y0 = event.clientY;
    let moved = false;
    let d = d0;
    let start = box.start;
    let end = box.end;
    // Listened to on the window, not the box: moving the box to another day's
    // column takes it out of the page for an instant, and the browser then drops
    // its pointer capture, so the release would never reach it.
    const pointer = event.pointerId;
    const move = (e) => {
      if (e.pointerId !== pointer) return;
      if (!moved && Math.abs(e.clientX - x0) + Math.abs(e.clientY - y0) < 5) return;
      moved = true;
      this.closePop();
      node.classList.add("dragging");
      this.autoscroll(e.clientY, resizing ? null : e.clientX);
      if (resizing) {
        end = Math.min(Math.max(snap(this.minuteAt(this.columns[d], e.clientY)), start + SNAP), 1440);
      } else {
        const over = this.dayAt(e.clientX);
        if (over !== null && this.data.days[over].in_horizon) d = over;
        start = Math.min(Math.max(snap(this.minuteAt(this.columns[d], e.clientY) - grab), 0), 1440 - length);
        end = start + length;
        if (node.parentElement !== this.columns[d]) this.columns[d].append(node);
        node.style.left = "2px";
        node.style.width = "calc(100% - 4px)";
      }
      node.style.top = `${(start - this.top) * PX}px`;
      node.style.height = `${(end - start) * PX - 2}px`;
      node.querySelector(".t").textContent = `${toClock(start)}–${toClock(end)}`;
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      this.endDrag();
    };
    const up = (e) => {
      if (e.pointerId !== pointer) return;
      stop();
      if (!moved) {
        open();
        return;
      }
      node.classList.remove("dragging");
      if (d === d0 && start === box.start && end === box.end) return;
      node.classList.add("saving");
      const day = this.data.days[d];
      if (box.kind === "study") {
        this.change(`sessions/${box.id}/move`, { to: `${day.date}T${toClock(start)}` },
          t("Moved. It stays there; the rest of the plan has made room."));
      } else {
        const rows = this.data.activities.map((r) => ({ ...r }));
        const row = rows[box.row];
        row.start = toClock(start);
        row.end = toClock(end);
        if (row.date) row.date = day.date;
        else row.weekday = day.weekday;
        this.saveActivities(rows, t("Saved. The plan has moved around it."));
      }
    };
    const cancel = (e) => {
      if (e.pointerId !== pointer) return;
      stop();
      this.draw(this.data);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
  }

  wireColumn(column, d) {
    column.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || event.target !== column) return;
      const origin = snap(this.minuteAt(column, event.clientY));
      if (event.pointerType === "touch") {
        // A tap adds; a swipe scrolls, up and down or across the days, and the
        // browser then cancels the pointer.
        const x0 = event.clientX;
        const y0 = event.clientY;
        const up = (e) => {
          column.removeEventListener("pointerup", up);
          column.removeEventListener("pointercancel", off);
          if (Math.abs(e.clientY - y0) < 10 && Math.abs(e.clientX - x0) < 10) this.openCreate(d, origin, Math.min(origin + 60, 1440));
        };
        const off = () => {
          column.removeEventListener("pointerup", up);
          column.removeEventListener("pointercancel", off);
        };
        column.addEventListener("pointerup", up);
        column.addEventListener("pointercancel", off);
        return;
      }
      event.preventDefault();
      this.closePop();
      const ghost = h("div", { class: "ev ghost" }, h("span", { class: "t" }));
      column.append(ghost);
      let from = origin;
      let to = origin + SNAP;
      const drawGhost = () => {
        ghost.style.top = `${(from - this.top) * PX}px`;
        ghost.style.height = `${(to - from) * PX}px`;
        ghost.style.left = "2px";
        ghost.style.width = "calc(100% - 4px)";
        ghost.firstChild.textContent = `${toClock(from)}–${toClock(to)}`;
      };
      drawGhost();
      column.setPointerCapture(event.pointerId);
      const move = (e) => {
        this.autoscroll(e.clientY);
        const here = Math.min(Math.max(snap(this.minuteAt(column, e.clientY)), this.top), this.bottom);
        from = Math.min(origin, here);
        to = Math.max(origin, here);
        if (to - from < SNAP) to = from + SNAP;
        drawGhost();
      };
      const stop = () => {
        column.removeEventListener("pointermove", move);
        column.removeEventListener("pointerup", up);
        column.removeEventListener("pointercancel", stop);
      };
      const up = () => {
        stop();
        if (to - from <= SNAP) to = Math.min(from + 60, 1440);
        drawGhost();
        this.openCreate(d, from, to, ghost);
      };
      column.addEventListener("pointermove", move);
      column.addEventListener("pointerup", up);
      column.addEventListener("pointercancel", () => {
        stop();
        ghost.remove();
      });
    });
  }

  // -- popovers ----------------------------------------------------------------

  closePop() {
    if (!this.pop) return;
    const { node, backdrop, cleanup } = this.pop;
    this.pop = null;
    cleanup();
    node.remove();
    if (backdrop) backdrop.remove();
    this.root.querySelectorAll(".ev.ghost").forEach((g) => g.remove());
  }

  openPop(anchor, content, label) {
    this.closePop();
    const sheet = narrow();
    const node = h("div", { class: `pop${sheet ? " sheet" : ""}`, role: "dialog", "aria-label": label }, content);
    const backdrop = sheet ? h("div", { class: "backdrop" }) : null;
    if (backdrop) document.body.append(backdrop);
    document.body.append(node);
    if (!sheet && anchor) {
      const rect = anchor.getBoundingClientRect();
      const width = node.offsetWidth;
      const heightPx = node.offsetHeight;
      let left = rect.right + 8;
      if (left + width > window.innerWidth - 8) left = rect.left - width - 8;
      if (left < 8) left = Math.min(Math.max(rect.left, 8), window.innerWidth - width - 8);
      const top = Math.min(Math.max(rect.top, 8), window.innerHeight - heightPx - 8);
      node.style.left = `${left}px`;
      node.style.top = `${top}px`;
    }
    const keys = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        this.closePop();
        if (anchor && anchor.focus) anchor.focus();
      }
    };
    const outside = (event) => {
      if (!node.contains(event.target)) this.closePop();
    };
    document.addEventListener("keydown", keys);
    setTimeout(() => document.addEventListener("pointerdown", outside), 0);
    this.pop = {
      node,
      backdrop,
      cleanup: () => {
        document.removeEventListener("keydown", keys);
        document.removeEventListener("pointerdown", outside);
      },
    };
    const focus = node.querySelector("input, button");
    if (focus && !sheet) focus.focus();
    return node;
  }

  openInfo(box, d, anchor) {
    const day = this.data.days[d];
    const colour = box.color === null || box.color === undefined ? "" : `c${box.color}`;
    this.openPop(anchor, [
      h("div", { class: `pop-head ${colour}` },
        h("span", { class: "colour-bar" }),
        h("div", { class: "grow" },
          h("p", { class: "kicker" }, `${day.label} · ${toClock(box.start)}–${toClock(box.end)}`),
          h("h3", {}, box.label))),
      h("p", { class: "muted" }, box.kind === "exam"
        ? t("An exam from your timetable. Practice sessions are planned in the two weeks before it.")
        : box.kind === "commute"
          ? t("Your trip to university, before the day's first class and after its last. Change its length in Settings.")
          : box.kind === "lunch"
            ? t("Lunch, kept free every day. Change its time in Settings.")
          : box.kind === "skipped"
            ? (box.weekly ? t("You skip this class every week: its time is free for the plan.") : t("You skip this class: its time is free for the plan."))
            : box.kind === "quiz"
              ? t("A quiz in class. An hour to prepare for it is in your tasks.")
              : box.kind === "mock"
                ? t("A mock exam in class: it counts as exam practice for its course.")
                : box.kind === "partial"
                  ? `${t("A partial exam: practice is planned in the days before it.")} ${box.exempt ? t("Its material leaves the final exam.") : t("Its material stays for the final exam.")}`
                  : t("From your timetable. Nothing is planned at the same time.")),
      box.catch_up && box.kind === "skipped" ? h("p", { class: "hint" }, t("Its catch-up is in your tasks: {name}.", { name: box.catch_up })) : null,
      ...this.lectureActions(box),
    ], box.label);
  }

  // A class of the timetable is the student's to skip, once or every week, and to
  // catch up on alone (D60); the server decides, the calendar asks.
  lectureActions(box) {
    const tests = ["quiz", "mock", "partial"];
    if (!["calendar", "skipped", ...tests].includes(box.kind) || !box.at) return [];
    const send = (action, every_week, done, exempt = false) => () =>
      this.change("lecture.json", { at: box.at, ref: box.ref, action, every_week, exempt }, done);
    if (box.kind === "skipped") {
      return [h("div", { class: "pop-actions" }, h("button", {
        type: "button",
        class: "btn btn-sm btn-tinted",
        onclick: send("restore", false, t("Back in your week.")),
      }, box.weekly ? t("Restore every week") : t("Restore")))];
    }
    if (tests.includes(box.kind)) {
      return box.past ? [] : [h("div", { class: "pop-actions" }, h("button", {
        type: "button",
        class: "btn btn-sm",
        onclick: send("restore", false, t("Back to an ordinary class.")),
      }, t("It's an ordinary class")))];
    }
    if (box.past) return [];
    // A test announced in class (D61): the plan prepares for it.
    const exempt = h("input", { type: "checkbox" });
    const test = [
      h("hr"),
      h("p", { class: "label" }, t("Is it a test?")),
      h("div", { class: "pop-actions stacked" },
        h("button", { type: "button", class: "btn btn-sm", onclick: send("quiz", false, t("Marked as a quiz. An hour to prepare is in your tasks.")) },
          t("Pop-up quiz")),
        h("button", { type: "button", class: "btn btn-sm", onclick: send("mock", false, t("Marked as a mock exam. It counts as exam practice.")) },
          t("Mock exam")),
        h("button", { type: "button", class: "btn btn-sm", onclick: () => send("partial", false, t("Marked as a partial exam. Practice is planned before it."), exempt.checked)() },
          t("Partial exam"))),
      h("label", { class: "check check-long" }, exempt, ` ${t("Passing it takes its material off the final exam")}`),
    ];
    return [
      h("hr"),
      h("p", { class: "label" }, t("Can't go?")),
      h("div", { class: "pop-actions stacked" },
        h("button", { type: "button", class: "btn btn-sm btn-tinted", onclick: send("self", false, t("Skipped. A catch-up as long as the class is in your tasks.")) },
          t("Skip it and study it on my own")),
        h("button", { type: "button", class: "btn btn-sm", onclick: send("skip", false, t("Skipped. Its time is free for the plan.")) },
          t("Skip this one")),
        h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: send("skip", true, t("Skipped every week from this one on.")) },
          t("Skip it every week"))),
      ...test,
    ];
  }

  openSession(box, anchor) {
    const colour = box.color === null || box.color === undefined ? "cn" : `c${box.color}`;
    const d = this.data.days.findIndex((day) => day.date === box.at.slice(0, 10));
    const day = d >= 0 ? this.data.days[d].label : box.at.slice(0, 10);
    const kind = t(KIND_WORDS[box.session_kind] || "Study");
    const parts = [
      h("div", { class: `pop-head ${colour}` },
        h("span", { class: "colour-bar" }),
        h("div", { class: "grow" },
          h("p", { class: "kicker" }, `${kind} · ${day} · ${toClock(box.start)}–${toClock(box.end)}`),
          h("h3", {}, box.label),
          box.title && !box.label.endsWith(box.title) ? h("p", { class: "muted" }, box.title) : null,
          box.pinned ? h("span", { class: "badge badge-accent" }, t("Placed by you")) : null)),
      box.detail ? h("p", {}, box.detail) : null,
      box.why ? h("p", { class: "hint" }, box.why) : null,
    ];
    if (box.started) {
      const recall = SELF_TESTS.includes(box.session_kind);
      parts.push(h("hr"), h("p", { class: "label" }, recall ? t("How much could you recall, without your notes?") : t("How did it go?")),
        h("div", { class: `outcomes${recall ? " recall" : ""}` }, (recall ? RECALL : OUTCOMES).map(([value, word]) => h("button", {
          type: "button",
          class: "btn btn-sm",
          "aria-pressed": box.reported === value ? "true" : "false",
          onclick: () => this.change(`sessions/${box.id}/report`, { outcome: value },
            recall && value !== "skipped"
              ? t("Recorded. Your exam forecast now uses what you recalled.")
              : t("Recorded: {word}. The plan has adjusted.", { word: t(word).toLowerCase() })),
        }, t(word)))));
    }
    if (box.movable) {
      const date = h("input", { type: "date", value: box.at.slice(0, 10), "aria-label": t("Date") });
      const time = h("input", { type: "time", step: "900", value: toClock(box.start), "aria-label": t("Start") });
      parts.push(h("hr"), h("p", { class: "label" }, t("Move it")),
        h("div", { class: "row" }, h("div", {}, date), h("div", {}, time)),
        h("p", { class: "hint" }, narrow() ? t("It stays where you put it.") : t("Or drag it on the calendar. It stays where you put it.")),
        h("div", { class: "pop-actions" },
          box.pinned ? h("button", {
            type: "button",
            class: "btn btn-ghost left",
            onclick: () => this.change(`sessions/${box.id}/unpin`, {}, t("The plan chooses its time again.")),
          }, t("Let the plan choose")) : null,
          h("button", {
            type: "button",
            class: "btn btn-primary",
            onclick: () => this.change(`sessions/${box.id}/move`, { to: `${date.value}T${time.value}` },
              t("Moved. It stays there; the rest of the plan has made room.")),
          }, t("Move"))));
    }
    parts.push(h("p", { class: "hint" }, h("a", { href: `/s/${this.token}/${box.id}` }, t("Open this session's page"))));
    this.openPop(anchor, parts, box.label);
  }

  activityForm(row, d, isNew, anchor) {
    const day = this.data.days[d];
    let kind = row.kind || "other";
    const hint = (k) => (k === "class" ? t("Course, e.g. Algebra") : this.kindLabel(k));
    // The courses already drawn or planned, to pick again for a second class.
    const courses = h("datalist", { id: "cal-courses" }, this.data.legend.map((l) => h("option", { value: l.course })));
    const name = h("input", { type: "text", value: isNew ? "" : row.label, placeholder: hint(kind), "aria-label": t("Name"), list: "cal-courses", autocomplete: "off" });
    const title = h("h3", {}, isNew ? (kind === "class" ? t("Add a class") : t("Block this time")) : kind === "class" ? t("Class") : t("Busy time"));
    const chips = this.data.kinds.map((k) => h("button", {
      type: "button",
      class: "btn btn-sm",
      "aria-pressed": k.id === kind ? "true" : "false",
      onclick: (event) => {
        kind = k.id;
        name.placeholder = hint(k.id);
        if (isNew) title.textContent = kind === "class" ? t("Add a class") : t("Block this time");
        event.currentTarget.parentElement.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b === event.currentTarget ? "true" : "false"));
      },
    }, k.label));
    // Which day: the one drawn on, or any other of the seven from the first shown,
    // even in a 3-day view (adding with the button, or on a phone, starts on today).
    const lang = document.documentElement.lang || "en";
    const week = Array.from({ length: 7 }, (_, i) => {
      const date = shiftDate(this.data.days[0].date, i);
      const shown = this.data.days.find((x) => x.date === date);
      if (shown) return { ...shown, short: shown.label.split(" ")[0] };
      const at = new Date(`${date}T12:00:00Z`);
      return {
        date,
        weekday: WEEKDAYS[at.getUTCDay()],
        label: at.toLocaleDateString(lang, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }),
        short: at.toLocaleDateString(lang, { weekday: "short", timeZone: "UTC" }),
      };
    });
    const dayPick = h("select", { id: "busy-day", "aria-label": t("Day") },
      week.map((x, i) => h("option", { value: String(i), selected: i === d }, x.label)));
    const chosen = () => week[Number(dayPick.value)] || week[d];
    const from = h("input", { type: "time", step: "900", value: row.start, "aria-label": t("From") });
    const to = h("input", { type: "time", step: "900", value: row.end === "24:00" ? "23:59" : row.end, "aria-label": t("To") });
    const weekly = h("input", { type: "checkbox", id: "weekly", checked: isNew ? true : !row.date });
    const every = h("span", {}, t("Every {day}", { day: day.label.split(" ")[0] }));
    const kicker = h("p", { class: "kicker" }, day.label);
    dayPick.addEventListener("change", () => {
      every.textContent = t("Every {day}", { day: chosen().short });
      kicker.textContent = chosen().label;
    });
    const save = () => {
      const when = chosen();
      const rows = this.data.activities.map((r) => ({ ...r }));
      const edited = {
        label: name.value.trim() || this.kindLabel(kind),
        kind,
        start: from.value || row.start,
        end: to.value === "23:59" ? "24:00" : to.value || row.end,
        weekday: weekly.checked ? when.weekday : null,
        date: weekly.checked ? null : when.date,
      };
      if (isNew) rows.push(edited);
      else rows[row.index] = edited;
      const weekday = when.short;
      let said = isNew ? t("Blocked. The plan has moved around it.") : t("Saved. The plan has moved around it.");
      if (this.setup) said = edited.weekday ? t("Added. It comes back every {day}.", { day: weekday }) : t("Added, on this day only.");
      this.saveActivities(rows, isNew ? said : t("Saved."));
    };
    const form = h("form", { onsubmit: (event) => { event.preventDefault(); save(); } },
      h("div", { class: "pop-head" }, h("div", { class: "grow" },
        kicker,
        title)),
      h("label", { for: "busy-name" }, t("What")), Object.assign(name, { id: "busy-name" }), courses,
      h("div", { class: "kinds", role: "group", "aria-label": t("Kind") }, chips),
      h("label", { for: "busy-day" }, t("Day")), dayPick,
      h("div", { class: "row" }, h("div", {}, h("label", {}, t("From")), from), h("div", {}, h("label", {}, t("To")), to)),
      h("label", { class: "check", for: "weekly" }, weekly, every),
      h("div", { class: "pop-actions" },
        isNew ? null : h("button", {
          type: "button",
          class: "btn btn-ghost left",
          onclick: () => {
            const rows = this.data.activities.filter((_, i) => i !== row.index);
            this.saveActivities(rows, t("Removed. That time is free again."));
          },
        }, t("Delete")),
        h("button", { type: "button", class: "btn", onclick: () => this.closePop() }, t("Cancel")),
        h("button", { type: "submit", class: "btn btn-primary" }, isNew ? (this.setup ? t("Add") : t("Block it")) : t("Save"))));
    this.openPop(anchor, form, title.textContent);
    if (this.setup && isNew) name.focus();
  }

  // What a new busy time is at first: a class while drawing a week, else training.
  get newKind() {
    return this.setup ? "class" : "training";
  }

  openCreate(d, from, to, anchor) {
    this.activityForm({ kind: this.newKind, start: toClock(from), end: toClock(to) }, d, true, anchor || this.columns[d]);
  }

  openActivity(index, d, anchor) {
    this.activityForm({ ...this.data.activities[index], index }, d, false, anchor);
  }

  addBusy(anchor) {
    if (!this.data) return;
    const d = Math.max(this.data.days.findIndex((day) => day.date === this.data.now.date), 0);
    const [start, end] = this.setup ? ["09:00", "11:00"] : ["18:00", "19:00"];
    this.activityForm({ kind: this.newKind, start, end }, d, true, anchor);
  }
}
