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

const KIND_WORDS = { "first review": "Self-test", review: "Self-test", task: "Deadline work", practice: "Exam practice" };
const OUTCOMES = [["done", "Done"], ["skipped", "Skipped"], ["struggled", "Hard"]];
// A self-test asks how much was recalled instead (DECISIONS.md D25).
const RECALL = [["forgot", "Nothing"], ["some", "Some"], ["most", "Most"], ["all", "All"], ["skipped", "Skipped"]];
const SELF_TESTS = ["review", "first review"];
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
  constructor(root, { token, first, onChanged, toast, onRange }) {
    this.root = root;
    this.token = token;
    this.first = first;
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
    window.addEventListener("resize", () => this.fitLabels());
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

  query() {
    return `start=${this.first}&days=${this.count}`;
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

  async load() {
    try {
      this.draw(await this.request("calendar.json"));
    } catch (error) {
      this.toast(error.message, true);
    }
  }

  go(where) {
    if (where === "today") this.first = this.today;
    else this.first = shiftDate(this.first, where === "back" ? -this.count : this.count);
    this.scrolled = null;
    this.load();
  }

  // A change sent to the server; the calendar it answers with is drawn.
  async change(path, body, done) {
    this.closePop();
    try {
      this.draw(await this.request(path, body));
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

  draw(data) {
    const scroller = this.root.querySelector(".cal-scroll");
    if (scroller && this.data) this.scrolled = scroller.scrollTop;
    this.data = data;
    this.loadedAt = Date.now();
    const days = data.days;
    const spans = data.items.map((i) => [i.start, i.end]).concat(
      data.activities.map((r) => [toMin(r.start), toMin(r.end) > toMin(r.start) ? toMin(r.end) : 1440]),
    );
    this.top = Math.min(7, ...spans.map(([a]) => Math.floor(a / 60))) * 60;
    this.bottom = Math.max(23, ...spans.map(([, b]) => Math.ceil(b / 60))) * 60;
    const height = (this.bottom - this.top) * PX;
    const columns = `${GUTTER}px repeat(${days.length}, minmax(0, 1fr))`;

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
    for (let m = this.top + 60; m < this.bottom; m += 60) {
      const label = h("div", { class: "cal-hour" }, toClock(m));
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
        boxes.push({ day: d, start, end, label: row.label, kind: "own", row: index });
      });
    });
    const byDay = days.map(() => []);
    for (const box of boxes) if (byDay[box.day]) byDay[box.day].push(box);
    byDay.forEach((list, d) => {
      for (const box of lanes(list)) this.columns[d].append(this.box(box, d));
    });

    const scroll = h("div", { class: "cal-scroll" }, body);
    const used = new Set(data.items.map((i) => i.color).filter((c) => c !== null && c !== undefined));
    const legend = h("div", { class: "legend", "aria-label": t("Courses") },
      data.legend.filter((l) => used.has(l.color)).map((l) => h("span", {}, h("span", { class: `dot c${l.color}` }), l.course)));
    this.root.replaceChildren(h("div", { class: "cal" }, head, scroll, legend.childElementCount ? legend : null));
    this.placeNow();
    this.fitLabels();

    if (this.scrolled !== null) scroll.scrollTop = this.scrolled;
    else {
      const focus = days.some((d) => d.date === data.now.date) ? Math.max(data.now.minute - 90, earliest) : earliest;
      scroll.scrollTop = Math.max((focus - this.top) * PX, 0);
    }
    const first = days[0].label;
    const last = days[days.length - 1].label;
    this.onRange(days.length === 1 ? first : `${first.slice(4)} – ${last.slice(4)}`, this.view);
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
    } else if (box.kind === "calendar") cls += colour;
    const tall = (to - from) * PX;
    if (tall < 34) cls += " short";
    let label = box.label;
    let short = "";
    if (box.kind === "own") {
      const kind = this.kindLabel(this.data.activities[box.row].kind);
      if (kind.toLowerCase() !== label.toLowerCase()) label = `${label} · ${kind}`;
      short = box.label;
    } else if (box.kind === "study") {
      short = box.session_kind === "task" ? box.title : box.course || box.title;
    } else if (box.kind === "calendar" && box.course) {
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

  autoscroll(clientY) {
    const scroller = this.root.querySelector(".cal-scroll");
    const rect = scroller.getBoundingClientRect();
    if (clientY < rect.top + 36) scroller.scrollTop -= 12;
    else if (clientY > rect.bottom - 36) scroller.scrollTop += 12;
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
      this.autoscroll(e.clientY);
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
        // A tap adds; a swipe scrolls, and the browser then cancels the pointer.
        const y0 = event.clientY;
        const up = (e) => {
          column.removeEventListener("pointerup", up);
          column.removeEventListener("pointercancel", off);
          if (Math.abs(e.clientY - y0) < 10) this.openCreate(d, origin, Math.min(origin + 60, 1440));
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
        : t("From your timetable. Nothing is planned at the same time.")),
    ], box.label);
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
    const name = h("input", { type: "text", value: isNew ? "" : row.label, placeholder: this.kindLabel(kind), "aria-label": t("Name") });
    const chips = this.data.kinds.map((k) => h("button", {
      type: "button",
      class: "btn btn-sm",
      "aria-pressed": k.id === kind ? "true" : "false",
      onclick: (event) => {
        kind = k.id;
        name.placeholder = k.label;
        event.currentTarget.parentElement.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b === event.currentTarget ? "true" : "false"));
      },
    }, k.label));
    const from = h("input", { type: "time", step: "900", value: row.start, "aria-label": t("From") });
    const to = h("input", { type: "time", step: "900", value: row.end === "24:00" ? "23:59" : row.end, "aria-label": t("To") });
    const weekly = h("input", { type: "checkbox", id: "weekly", checked: isNew ? true : !row.date });
    const save = () => {
      const rows = this.data.activities.map((r) => ({ ...r }));
      const edited = {
        label: name.value.trim() || this.kindLabel(kind),
        kind,
        start: from.value || row.start,
        end: to.value === "23:59" ? "24:00" : to.value || row.end,
        weekday: weekly.checked ? day.weekday : null,
        date: weekly.checked ? null : day.date,
      };
      if (isNew) rows.push(edited);
      else rows[row.index] = edited;
      this.saveActivities(rows, isNew ? t("Blocked. The plan has moved around it.") : t("Saved. The plan has moved around it."));
    };
    const form = h("form", { onsubmit: (event) => { event.preventDefault(); save(); } },
      h("div", { class: "pop-head" }, h("div", { class: "grow" },
        h("p", { class: "kicker" }, day.label),
        h("h3", {}, isNew ? t("Block this time") : t("Busy time")))),
      h("label", { for: "busy-name" }, t("What")), Object.assign(name, { id: "busy-name" }),
      h("div", { class: "kinds", role: "group", "aria-label": t("Kind") }, chips),
      h("div", { class: "row" }, h("div", {}, h("label", {}, t("From")), from), h("div", {}, h("label", {}, t("To")), to)),
      h("label", { class: "check", for: "weekly" }, weekly, t("Every {day}", { day: day.label.split(" ")[0] })),
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
        h("button", { type: "submit", class: "btn btn-primary" }, isNew ? t("Block it") : t("Save"))));
    this.openPop(anchor, form, isNew ? t("Block this time") : t("Busy time"));
  }

  openCreate(d, from, to, anchor) {
    this.activityForm({ kind: "training", start: toClock(from), end: toClock(to) }, d, true, anchor || this.columns[d]);
  }

  openActivity(index, d, anchor) {
    this.activityForm({ ...this.data.activities[index], index }, d, false, anchor);
  }

  addBusy(anchor) {
    if (!this.data) return;
    const d = Math.max(this.data.days.findIndex((day) => day.date === this.data.now.date), 0);
    this.activityForm({ kind: "training", start: "18:00", end: "19:00" }, d, true, anchor);
  }
}
