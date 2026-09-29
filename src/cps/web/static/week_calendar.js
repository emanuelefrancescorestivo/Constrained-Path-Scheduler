// A week calendar for the study planner: drag to block time, click to edit.
// Used by the hosted app (cps.web, static/week.js) and the Streamlit page
// (widgets/), from this one file.
//
// Pure presentation. It receives the week's days, read-only items (calendar
// events, exams, study sessions) and the person's own activities as rows in the
// same shape `cps.service` accepts as busy rows, and it sends the whole edited
// list of rows back as the trigger value "edit". It computes nothing about
// studying: what an activity means for the plan is decided in Python.

const SNAP = 15; // minutes
const PX_PER_MIN = 0.8; // 48 px an hour
const DEFAULT_MINUTES = 60;

function toMinutes(clock) {
  if (!clock) return 0;
  const [h, m] = String(clock).split(":").map(Number);
  return h * 60 + (m || 0);
}

function toClock(minutes) {
  const m = Math.max(0, Math.min(24 * 60, Math.round(minutes)));
  if (m === 24 * 60) return "24:00";
  return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
}

function snap(minutes) {
  return Math.round(minutes / SNAP) * SNAP;
}

function sameWeekday(row, day) {
  return !!row.weekday && String(row.weekday).slice(0, 3).toLowerCase() === day.weekday.toLowerCase();
}

function appliesTo(row, day) {
  return row.date ? row.date === day.date : sameWeekday(row, day);
}

// Side-by-side lanes for overlapping boxes in one day column.
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

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    // Through the CSSOM, which a Content-Security-Policy without 'unsafe-inline'
    // allows; a style attribute set as text would be refused.
    else if (k === "style") node.style.cssText = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  if (text !== undefined) node.textContent = text;
  return node;
}

export default function (component) {
  const { data, setTriggerValue, parentElement } = component;
  const editable = !!data.editable;
  const days = data.days || [];
  const kinds = data.kinds || [];
  const kindLabel = Object.fromEntries(kinds.map((k) => [k.id, k.label]));
  // The visible hours stretch to show anything earlier or later than usual.
  const spans = (data.items || []).map((i) => [i.start, i.end]).concat(
    (data.activities || []).map((r) => [toMinutes(r.start), toMinutes(r.end) > toMinutes(r.start) ? toMinutes(r.end) : 24 * 60]),
  );
  const firstHour = Math.min((data.hours || [7, 23])[0], ...spans.map(([a]) => Math.floor(a / 60)));
  const lastHour = Math.max((data.hours || [7, 23])[1], ...spans.map(([, b]) => Math.ceil(b / 60)));
  const [earliest, latest] = data.window || [0, 24];
  const top = firstHour * 60;
  const bottom = lastHour * 60;
  const height = (bottom - top) * PX_PER_MIN;
  const rows = (data.activities || []).map((r) => ({ ...r }));
  const memory = (window.__cpsWeekCalendar = window.__cpsWeekCalendar || {});
  let currentKind = memory.kind && kindLabel[memory.kind] ? memory.kind : (kinds[0] || {}).id;

  let root = parentElement.querySelector(".wc");
  if (root) root.remove();
  root = el("div", { class: "wc" + (editable ? " wc-editable" : "") });
  parentElement.appendChild(root);

  const commit = (focusIndex) => {
    memory.focus = focusIndex;
    setTriggerValue("edit", rows.map(({ label, kind, weekday, date, start, end }) => ({
      label, kind, weekday: weekday || null, date: date || null, start, end,
    })));
  };

  // -- toolbar ---------------------------------------------------------------
  if (editable) {
    const bar = el("div", { class: "wc-bar", role: "toolbar", "aria-label": "Activity type for new blocks" });
    bar.appendChild(el("span", { class: "wc-hint" }, "Drag on a day to add:"));
    for (const kind of kinds) {
      const chip = el("button", {
        type: "button",
        class: "wc-chip wc-k-" + kind.id + (kind.id === currentKind ? " wc-on" : ""),
        "aria-pressed": kind.id === currentKind ? "true" : "false",
      }, kind.label);
      chip.addEventListener("click", () => {
        currentKind = memory.kind = kind.id;
        bar.querySelectorAll(".wc-chip").forEach((c) => {
          const on = c === chip;
          c.classList.toggle("wc-on", on);
          c.setAttribute("aria-pressed", on ? "true" : "false");
        });
      });
      bar.appendChild(chip);
    }
    const add = el("button", { type: "button", class: "wc-add" }, "+ Add with the keyboard");
    add.addEventListener("click", () => {
      const index = days.findIndex((d) => d.in_horizon);
      create(Math.max(index, 0), 18 * 60, 19 * 60);
    });
    bar.appendChild(add);
    root.appendChild(bar);
  }

  // -- grid ------------------------------------------------------------------
  const grid = el("div", { class: "wc-grid" });
  grid.style.gridTemplateColumns = `46px repeat(${days.length}, minmax(0, 1fr))`;
  root.appendChild(grid);
  grid.appendChild(el("div", { class: "wc-corner" }));
  days.forEach((day) =>
    grid.appendChild(el("div", { class: "wc-head" + (day.in_horizon ? "" : " wc-out") }, day.label)),
  );
  const gutter = el("div", { class: "wc-gutter", style: `height:${height}px` });
  for (let h = firstHour; h < lastHour; h++) {
    gutter.appendChild(el("div", { class: "wc-hour", style: `top:${(h * 60 - top) * PX_PER_MIN}px` }, `${String(h).padStart(2, "0")}:00`));
  }
  grid.appendChild(gutter);

  const columns = days.map((day, index) => {
    const column = el("div", {
      class: "wc-col" + (day.in_horizon ? "" : " wc-out"),
      style: `height:${height}px`,
      "data-day": String(index),
      "aria-label": day.label,
    });
    for (let h = firstHour; h < lastHour; h++) {
      column.appendChild(el("div", { class: "wc-line", style: `top:${(h * 60 - top) * PX_PER_MIN}px` }));
    }
    // Hours outside the study window: the planner never uses them.
    if (earliest * 60 > top) {
      column.appendChild(el("div", { class: "wc-closed", style: `top:0;height:${(earliest * 60 - top) * PX_PER_MIN}px` }));
    }
    if (latest * 60 < bottom) {
      column.appendChild(el("div", { class: "wc-closed", style: `top:${(latest * 60 - top) * PX_PER_MIN}px;height:${(bottom - latest * 60) * PX_PER_MIN}px` }));
    }
    grid.appendChild(column);
    return column;
  });

  // -- boxes -----------------------------------------------------------------
  const boxes = [];
  for (const item of data.items || []) {
    boxes.push({ day: item.day, start: item.start, end: item.end, label: item.label, kind: item.kind, href: item.href });
  }
  rows.forEach((row, index) => {
    days.forEach((day, d) => {
      if (!appliesTo(row, day)) return;
      const start = toMinutes(row.start);
      let end = toMinutes(row.end);
      if (end <= start) end = 24 * 60; // runs past midnight: shown to the end of the day
      boxes.push({ day: d, start, end, label: row.label || kindLabel[row.kind] || "Busy", kind: row.kind || "other", row: index });
    });
  });
  const byDay = days.map(() => []);
  for (const box of boxes) if (byDay[box.day]) byDay[box.day].push(box);
  byDay.forEach((list, d) => {
    for (const box of lanes(list)) {
      const from = Math.max(box.start, top);
      const to = Math.min(box.end, bottom);
      if (to <= from) continue;
      const own = box.row !== undefined;
      const node = el("div", {
        class: `wc-box wc-k-${box.kind}` + (own ? " wc-own" : ""),
        style: `top:${(from - top) * PX_PER_MIN}px;height:${Math.max((to - from) * PX_PER_MIN, 12)}px;` +
          `left:calc(${(100 * box.lane) / box.lanes}% + 2px);width:calc(${100 / box.lanes}% - 4px)`,
        title: `${box.label} ${toClock(box.start)}–${toClock(box.end)}`,
        tabindex: own && editable ? "0" : null,
        role: own && editable ? "button" : null,
        "aria-label": `${box.label}, ${days[d].label}, ${toClock(box.start)} to ${toClock(box.end)}` +
          (own && editable ? ". Enter to edit, Delete to remove, arrows to move" : ""),
      });
      node.appendChild(el("span", { class: "wc-time" }, `${toClock(box.start)}–${toClock(box.end)}`));
      node.appendChild(el("span", { class: "wc-label" }, box.label));
      if (box.href) {
        // A study session opens its page, where it can be reported.
        node.classList.add("wc-link");
        node.setAttribute("role", "link");
        node.setAttribute("tabindex", "0");
        node.addEventListener("pointerdown", (event) => event.stopPropagation());
        node.addEventListener("click", () => window.location.assign(box.href));
        node.addEventListener("keydown", (event) => {
          if (event.key === "Enter") window.location.assign(box.href);
        });
      }
      if (own && editable) {
        node.dataset.row = String(box.row);
        node.dataset.day = String(d);
        node.appendChild(el("div", { class: "wc-handle", "aria-hidden": "true" }));
        wireBox(node, box);
      }
      columns[d].appendChild(node);
    }
  });

  if (editable) columns.forEach((column, d) => wireColumn(column, d));

  if (memory.focus !== undefined && memory.focus !== null) {
    const target = root.querySelector(`.wc-own[data-row="${memory.focus}"]`);
    if (target) target.focus();
    memory.focus = null;
  }

  // -- interactions ----------------------------------------------------------
  function minuteAt(column, clientY) {
    const rect = column.getBoundingClientRect();
    return top + (clientY - rect.top) / PX_PER_MIN;
  }

  function dayAt(clientX) {
    for (let d = 0; d < columns.length; d++) {
      const rect = columns[d].getBoundingClientRect();
      if (clientX >= rect.left && clientX < rect.right) return d;
    }
    return null;
  }

  function place(row, d, start, end) {
    row.start = toClock(start);
    row.end = toClock(end);
    if (row.date) row.date = days[d].date;
    else row.weekday = days[d].weekday;
  }

  function create(d, start, end) {
    const row = { label: kindLabel[currentKind] || "Busy", kind: currentKind, weekday: days[d].weekday, date: null };
    place(row, d, start, end);
    rows.push(row);
    openEditor(rows.length - 1, d, true);
  }

  function wireColumn(column, d) {
    column.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || event.target.closest(".wc-box") || !days[d].in_horizon) return;
      event.preventDefault();
      const origin = snap(minuteAt(column, event.clientY));
      const ghost = el("div", { class: `wc-box wc-ghost wc-k-${currentKind}` });
      column.appendChild(ghost);
      let from = origin;
      let to = origin + SNAP;
      const draw = () => {
        ghost.style.top = `${(from - top) * PX_PER_MIN}px`;
        ghost.style.height = `${(to - from) * PX_PER_MIN}px`;
        ghost.textContent = `${toClock(from)}–${toClock(to)}`;
      };
      draw();
      column.setPointerCapture(event.pointerId);
      const move = (e) => {
        const here = Math.min(Math.max(snap(minuteAt(column, e.clientY)), top), bottom);
        from = Math.min(origin, here);
        to = Math.max(origin, here);
        if (to - from < SNAP) to = from + SNAP;
        draw();
      };
      const stop = () => {
        column.removeEventListener("pointermove", move);
        column.removeEventListener("pointerup", up);
        column.removeEventListener("pointercancel", stop);
        ghost.remove();
      };
      const up = () => {
        stop();
        // A click or a tap without a drag gives a one-hour block starting there.
        if (to - from <= SNAP) to = Math.min(from + DEFAULT_MINUTES, bottom);
        create(d, from, to);
      };
      // On a touch screen a vertical swipe scrolls the page, and the browser
      // cancels the pointer: that is a scroll, not a new block.
      column.addEventListener("pointermove", move);
      column.addEventListener("pointerup", up);
      column.addEventListener("pointercancel", stop);
    });
  }

  function wireBox(node, box) {
    const index = box.row;
    const row = rows[index];
    const length = box.end - box.start;
    node.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      const resizing = event.target.classList.contains("wc-handle");
      const column = columns[box.day];
      const grab = minuteAt(column, event.clientY) - box.start;
      const x0 = event.clientX;
      const y0 = event.clientY;
      let moved = false;
      let d = box.day;
      let start = box.start;
      let end = box.end;
      node.setPointerCapture(event.pointerId);
      const move = (e) => {
        if (!moved && Math.abs(e.clientX - x0) + Math.abs(e.clientY - y0) < 4) return;
        moved = true;
        node.classList.add("wc-dragging");
        if (resizing) {
          end = Math.min(Math.max(snap(minuteAt(column, e.clientY)), start + SNAP), 24 * 60);
        } else {
          const over = dayAt(e.clientX);
          if (over !== null && days[over].in_horizon) d = over;
          start = Math.min(Math.max(snap(minuteAt(columns[d], e.clientY) - grab), 0), 24 * 60 - length);
          end = start + length;
          if (node.parentElement !== columns[d]) columns[d].appendChild(node);
        }
        node.style.top = `${(start - top) * PX_PER_MIN}px`;
        node.style.height = `${(end - start) * PX_PER_MIN}px`;
        node.querySelector(".wc-time").textContent = `${toClock(start)}–${toClock(end)}`;
      };
      const up = () => {
        node.removeEventListener("pointermove", move);
        node.removeEventListener("pointerup", up);
        node.removeEventListener("pointercancel", up);
        if (!moved) {
          openEditor(index, box.day, false);
          return;
        }
        place(row, d, start, end);
        commit(index);
      };
      node.addEventListener("pointermove", move);
      node.addEventListener("pointerup", up);
      node.addEventListener("pointercancel", up);
    });
    node.addEventListener("keydown", (event) => {
      const key = event.key;
      if (key === "Enter" || key === " ") {
        event.preventDefault();
        openEditor(index, box.day, false);
      } else if (key === "Delete" || key === "Backspace") {
        event.preventDefault();
        rows.splice(index, 1);
        commit(null);
      } else if (key === "ArrowUp" || key === "ArrowDown") {
        event.preventDefault();
        const step = key === "ArrowUp" ? -SNAP : SNAP;
        if (event.shiftKey) place(row, box.day, box.start, Math.min(Math.max(box.end + step, box.start + SNAP), 24 * 60));
        else {
          const start = Math.min(Math.max(box.start + step, 0), 24 * 60 - length);
          place(row, box.day, start, start + length);
        }
        commit(index);
      } else if (key === "ArrowLeft" || key === "ArrowRight") {
        event.preventDefault();
        const d = box.day + (key === "ArrowLeft" ? -1 : 1);
        if (d >= 0 && d < days.length && days[d].in_horizon) {
          place(row, d, box.start, box.end);
          commit(index);
        }
      }
    });
  }

  function openEditor(index, d, isNew) {
    root.querySelectorAll(".wc-editor").forEach((e) => e.remove());
    const row = rows[index];
    const editor = el("div", { class: "wc-editor", role: "dialog", "aria-label": "Edit activity" });
    const label = el("input", { type: "text", value: row.label || "", "aria-label": "Name" });
    const kind = el("select", { "aria-label": "Type" });
    for (const k of kinds) {
      const option = el("option", { value: k.id }, k.label);
      if (k.id === row.kind) option.selected = true;
      kind.appendChild(option);
    }
    const start = el("input", { type: "time", step: "900", value: row.start, "aria-label": "Start" });
    const end = el("input", { type: "time", step: "900", value: row.end === "24:00" ? "23:59" : row.end, "aria-label": "End" });
    const repeat = el("input", { type: "checkbox", id: "wc-repeat", checked: !row.date });
    const repeatLabel = el("label", { for: "wc-repeat" }, `Every ${days[d].weekday}`);
    const done = el("button", { type: "button", class: "wc-done" }, "Done");
    const remove = el("button", { type: "button", class: "wc-delete" }, "Delete");
    const line = (...nodes) => {
      const div = el("div", { class: "wc-line-form" });
      nodes.forEach((n) => div.appendChild(n));
      return div;
    };
    editor.append(
      line(label),
      line(kind),
      line(start, el("span", {}, "to"), end),
      line(repeat, repeatLabel),
      line(remove, done),
    );
    const finish = (keep) => {
      if (!editor.isConnected) return;
      editor.remove();
      document.removeEventListener("pointerdown", outside, true);
      if (!keep) {
        rows.splice(index, 1);
        commit(null);
        return;
      }
      const previousKind = kindLabel[row.kind];
      row.kind = kind.value;
      const typed = label.value.trim();
      row.label = typed && typed !== previousKind ? typed : kindLabel[row.kind] || typed || "Busy";
      const from = toMinutes(start.value || row.start);
      let to = toMinutes(end.value || row.end);
      if (end.value === "23:59") to = 24 * 60;
      if (repeat.checked) {
        row.weekday = days[d].weekday;
        row.date = null;
      } else {
        row.date = days[d].date;
        row.weekday = null;
      }
      row.start = toClock(from);
      row.end = toClock(to > from ? to : Math.min(from + SNAP, 24 * 60));
      commit(index);
    };
    const cancel = () => {
      editor.remove();
      document.removeEventListener("pointerdown", outside, true);
      if (isNew) rows.splice(index, 1);
      if (isNew) commit(null);
    };
    const outside = (event) => {
      if (!event.composedPath().includes(editor)) finish(true);
    };
    done.addEventListener("click", () => finish(true));
    remove.addEventListener("click", () => finish(false));
    editor.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        cancel();
      } else if (event.key === "Enter" && event.target.tagName !== "BUTTON") {
        event.preventDefault();
        finish(true);
      }
    });
    const column = columns[d];
    const offset = (Math.max(toMinutes(row.start), top) - top) * PX_PER_MIN;
    editor.style.top = `${column.offsetTop + Math.min(offset, height - 180)}px`;
    const left = column.offsetLeft + column.offsetWidth + 4;
    editor.style.left = `${left + 230 > grid.offsetWidth ? column.offsetLeft - 234 : left}px`;
    grid.appendChild(editor);
    setTimeout(() => document.addEventListener("pointerdown", outside, true), 0);
    label.focus();
    label.select();
  }
}
