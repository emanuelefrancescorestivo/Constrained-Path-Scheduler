// What every page of a plan shares, on top of forms that work without it:
//
// - Forms marked data-async are sent in the background. The server answers as it
//   would a plain form (a redirect to the page), and the parts of that page this
//   one also has (#panel, #tasks, or the form's own data-region) are put in place;
//   its notice becomes a toast, unless the form is data-quiet (a kudos tap).
// - Ticking a task off says so at once, and offers to undo.
// - The new-task sheet gets one-tap due dates, and the N key opens it.
// - A "cps:changed" event tells the plan screen to redraw its calendar.
// - Forms marked data-autosave keep each change as it is made (D165): a switch flipped
//   is sent at once, in the background, one after another so the last one wins, and a
//   line says it is saved. Their button still sends them, and does without this script.
//
// Layout and input only: what a change means is decided by the server.

import { t } from "./i18n.js";
import { carryOver, transition } from "./motion.js";

const REGIONS = ["#panel", "#tasks"];
let toastTimer = null;

export function toast(text, { bad = false, action = null } = {}) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const node = document.createElement("div");
  node.className = `toast${bad ? " bad" : ""}`;
  node.setAttribute("role", bad ? "alert" : "status");
  const words = document.createElement("span");
  words.textContent = text ? text[0].toUpperCase() + text.slice(1) : "";
  node.append(words);
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = action.label;
    button.addEventListener("click", () => {
      node.remove();
      action.run();
    });
    node.append(button);
  }
  document.body.append(node);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => leave(node), bad ? 6000 : action ? 6000 : 3500);
}

// The toast sinks away rather than vanishing (D30); without motion it just goes.
function leave(node) {
  node.classList.add("leaving");
  setTimeout(() => node.remove(), 200);
}

async function send(form, submitter, data = new FormData(form, submitter)) {
  const response = await fetch(form.action, { method: "POST", body: new URLSearchParams(data) });
  const page = new DOMParser().parseFromString(await response.text(), "text/html");
  const swaps = [];
  for (const selector of form.dataset.region ? [form.dataset.region] : REGIONS) {
    const here = document.querySelector(selector);
    const there = page.querySelector(selector);
    if (here && there) {
      there.querySelectorAll(".notice-ok").forEach((n) => n.remove()); // said by the toast
      swaps.push([here, document.adoptNode(there)]);
    }
  }
  if (swaps.length) {
    // One movement: what stays slides to its place, rings and numbers move from their
    // old values, and the part that was tapped is marked so its icon can pop (D30).
    const before = swaps.map(([here]) => here.cloneNode(true));
    await transition(() => swaps.forEach(([here, there]) => here.replaceWith(there)));
    swaps.forEach(([, there], i) => {
      carryOver(before[i], there);
      if (form.dataset.region) there.classList.add("just");
    });
  }
  const alert = page.querySelector(".alert, .error");
  const notice = page.querySelector(".notice-ok");
  return { ok: response.ok, message: response.ok ? notice && notice.textContent.trim() : alert && alert.textContent.trim() };
}

const saving = new WeakMap();
document.addEventListener("change", (event) => {
  const form = event.target.closest("form[data-autosave]");
  if (!form) return;
  const said = form.querySelector("[data-saved]");
  const save = async () => {
    try {
      // The server answers a saved form with a redirect, which is not followed here.
      const answer = await fetch(form.action, {
        method: "POST",
        body: new URLSearchParams(new FormData(form)),
        redirect: "manual",
      });
      if (answer.type !== "opaqueredirect" && !answer.ok) throw new Error(String(answer.status));
      if (said) {
        said.textContent = t("Saved.");
        said.hidden = false;
      }
    } catch {
      toast(t("That did not work. Try again."), { bad: true });
    }
  };
  saving.set(form, (saving.get(form) || Promise.resolve()).then(save));
});

document.addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-async]");
  if (!form) return;
  event.preventDefault();
  if (event.submitter && event.submitter.hasAttribute("data-understand")) {
    await understand(form, event.submitter);
    return;
  }
  const task = form.closest(".task");
  const finishing = form.action.endsWith("/tasks/done") && form.elements.done && form.elements.done.value === "1";
  if (task && finishing) task.classList.add("ticking"); // the tick shows at once
  // The form's values before its buttons are disabled: a disabled button's name and
  // value are not sent, and a reaction is the button tapped (D108, AUDIT 91).
  const data = new FormData(form, event.submitter);
  form.querySelectorAll("button").forEach((b) => (b.disabled = true));
  // The button pressed shows a spinner if the answer takes more than a moment (D57).
  const pressed = event.submitter && event.submitter.classList.contains("btn") ? event.submitter : null;
  const busy = pressed && setTimeout(() => pressed.classList.add("busy"), 150);
  try {
    const answer = await send(form, event.submitter, data);
    if (!answer.ok) {
      toast(answer.message || t("That did not work. Try again."), { bad: true });
      if (task) task.classList.remove("ticking");
      return;
    }
    const sheet = form.closest("[popover]");
    if (sheet) {
      sheet.hidePopover();
      form.reset();
    }
    const name = form.elements.name ? form.elements.name.value : "";
    if (finishing) {
      toast(t("Done: {name}. Its remaining sessions are free again.", { name }), {
        action: { label: t("Undo"), run: () => reopen(form, name) },
      });
    } else if (!("quiet" in form.dataset)) {
      toast(answer.message || t("Saved."));
    }
    document.dispatchEvent(new CustomEvent("cps:changed"));
  } catch {
    toast(t("No connection to the server. Try again."), { bad: true });
    if (task) task.classList.remove("ticking");
  } finally {
    clearTimeout(busy);
    if (pressed) pressed.classList.remove("busy");
    form.querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
});

// A task typed in one line: the server reads it (rules, or the model when AI is
// on, D12) and the sheet's fields are filled in, to check before adding.
async function understand(form, button) {
  const words = form.elements.words.value.trim();
  if (!words) {
    form.elements.words.focus();
    return;
  }
  button.disabled = true;
  try {
    const answer = await fetch(button.formAction, {
      method: "POST",
      headers: { Accept: "application/json" },
      body: new URLSearchParams({ words }),
    });
    const read = await answer.json();
    if (!answer.ok) {
      toast(read.message || t("That did not work. Try again."), { bad: true });
      return;
    }
    form.elements.name.value = read.name;
    if (read.due) {
      form.elements.due.value = read.due;
      form.querySelectorAll("[data-due]").forEach((b) => b.setAttribute("aria-pressed", "false"));
    }
    if (read.hours) {
      const pill = [...form.querySelectorAll('input[name="hours"]')].find((r) => Number(r.value) === read.hours);
      form.querySelectorAll('input[name="hours"]').forEach((r) => (r.checked = r === pill));
      form.elements.hours_other.value = pill ? "" : String(read.hours);
    }
    if (read.course) pickCourse(form, read.course);
    const note = form.querySelector(".typed-note");
    note.textContent = read.note;
    note.hidden = false;
    (read.due ? form.elements.name : form.elements.due).focus();
  } catch {
    toast(t("No connection to the server. Try again."), { bad: true });
  } finally {
    button.disabled = false;
  }
}

// -- the sheet's day and time (D159) ----------------------------------------------
// The system's picker for a date and a time is grey and unlike the app (the owner's
// screenshot, 2026-11-19); this one draws the month in the app's look, with the usual
// times as chips. The field stays the form's own: it can still be typed in, and a phone's
// own wheel still opens on a tap. Layout and input only: the server reads the field.
const pad2 = (n) => String(n).padStart(2, "0");
const isoDay = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const TIMES = ["09:00", "12:00", "14:00", "18:00", "23:59"];

function duePicker(sheet, due) {
  const open = sheet.querySelector(".due-open");
  const panel = sheet.querySelector(".due-pick");
  if (!open || !panel) return;
  open.hidden = false;
  due.classList.add("has-pick");
  const lang = document.documentElement.lang || undefined;
  let shown = new Date();
  shown.setDate(1);
  const parts = () => {
    const [day, time] = (due.value || "").split("T");
    return { day: day || "", time: (time || "").slice(0, 5) };
  };
  const set = (day, time) => {
    due.value = `${day}T${time}`;
    due.dispatchEvent(new Event("input", { bubbles: true }));
    draw();
  };
  const button = (label, attrs, onclick) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    for (const [k, v] of Object.entries(attrs)) b.setAttribute(k, v);
    b.addEventListener("click", onclick);
    return b;
  };
  function draw() {
    const { day: chosen, time } = parts();
    const today = isoDay(new Date());
    const head = document.createElement("div");
    head.className = "due-month";
    const title = document.createElement("b");
    title.textContent = new Intl.DateTimeFormat(lang, { month: "long", year: "numeric" }).format(shown);
    const move = (n) => () => {
      shown = new Date(shown.getFullYear(), shown.getMonth() + n, 1);
      draw();
    };
    head.append(
      button("‹", { class: "btn btn-sm btn-icon", "aria-label": t("Previous month") }, move(-1)),
      title,
      button("›", { class: "btn btn-sm btn-icon", "aria-label": t("Next month") }, move(1)),
    );
    const grid = document.createElement("div");
    grid.className = "due-days";
    grid.setAttribute("role", "group");
    grid.setAttribute("aria-label", title.textContent);
    const monday = new Date(2024, 0, 1); // a Monday, for the weekdays' letters
    for (let k = 0; k < 7; k++) {
      const name = document.createElement("span");
      name.className = "due-wd";
      name.setAttribute("aria-hidden", "true");
      const d = new Date(monday);
      d.setDate(monday.getDate() + k);
      name.textContent = new Intl.DateTimeFormat(lang, { weekday: "narrow" }).format(d);
      grid.append(name);
    }
    const lead = (shown.getDay() + 6) % 7; // Monday first
    for (let k = 0; k < lead; k++) grid.append(document.createElement("span"));
    const last = new Date(shown.getFullYear(), shown.getMonth() + 1, 0).getDate();
    for (let n = 1; n <= last; n++) {
      const date = new Date(shown.getFullYear(), shown.getMonth(), n);
      const iso = isoDay(date);
      const b = button(String(n), {
        class: `due-day${iso === today ? " is-today" : ""}`,
        "aria-pressed": iso === chosen ? "true" : "false",
        "aria-label": new Intl.DateTimeFormat(lang, { weekday: "long", day: "numeric", month: "long" }).format(date),
      }, () => set(iso, time || "23:59"));
      if (iso < today) b.disabled = true; // a deadline is ahead
      grid.append(b);
    }
    const times = document.createElement("div");
    times.className = "due-times pills";
    times.setAttribute("role", "group");
    times.setAttribute("aria-label", t("Time"));
    for (const at of TIMES) {
      times.append(button(at, { class: "pill-btn", "aria-pressed": at === time ? "true" : "false" }, () => {
        set(chosen || today, at);
        close();
      }));
    }
    panel.replaceChildren(head, grid, times);
  }
  const close = () => {
    panel.hidden = true;
    open.setAttribute("aria-expanded", "false");
  };
  open.addEventListener("click", () => {
    if (!panel.hidden) return close();
    const { day } = parts();
    if (day) shown = new Date(`${day.slice(0, 7)}-01T12:00`);
    draw();
    panel.hidden = false;
    open.setAttribute("aria-expanded", "true");
  });
  sheet.addEventListener("toggle", (event) => {
    if (event.newState === "closed") close();
  });
}

// The course list's "Other…" (D159): its field shows when it is chosen, and is focused.
function courseOther(sheet) {
  const select = sheet.querySelector(".course-choice select");
  const other = sheet.querySelector(".course-choice .course-other");
  if (!select || !other) return;
  const show = () => {
    const on = select.value === select.options[select.options.length - 1].value;
    other.hidden = !on;
    if (!on) other.value = "";
    return on;
  };
  show();
  select.addEventListener("change", () => {
    if (show()) other.focus();
  });
  select.form.addEventListener("reset", () => setTimeout(show));
}

// A course read from the typed line: picked from the list when it is one of the plan's,
// else typed under "Other…".
function pickCourse(form, course) {
  const select = form.querySelector(".course-choice select");
  if (!select) {
    form.elements.course.value = course;
    return;
  }
  const found = [...select.options].find((o) => o.value.toLowerCase() === course.toLowerCase());
  const other = form.querySelector(".course-other");
  if (found && found !== select.options[select.options.length - 1]) {
    select.value = found.value;
    other.value = "";
    other.hidden = true;
  } else {
    select.value = select.options[select.options.length - 1].value;
    other.hidden = false;
    other.value = course;
  }
}

async function reopen(form, name) {
  const undo = document.createElement("form");
  undo.method = "post";
  undo.action = form.action;
  for (const [key, value] of [["name", name], ["done", "0"], ["back", form.elements.back ? form.elements.back.value : ""]]) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = key;
    input.value = value;
    undo.append(input);
  }
  const answer = await send(undo, null);
  toast(answer.ok ? t("Reopened: {name}.", { name }) : answer.message || t("Could not reopen it."), { bad: !answer.ok });
  document.dispatchEvent(new CustomEvent("cps:changed"));
}

// -- Spark, floating (D71) --------------------------------------------------------
// The bubble is a link to the coach; where the browser knows popovers, it opens
// Spark's sheet instead.
const spark = document.querySelector("[data-spark]");
const sparkSheet = document.getElementById("spark-help");
if (spark && sparkSheet && "popover" in HTMLElement.prototype) {
  spark.addEventListener("click", (event) => {
    event.preventDefault();
    sparkSheet.showPopover();
  });
}

// -- the new-task sheet --------------------------------------------------------

const sheet = document.getElementById("quick-add");
// A browser without the popover attribute (before 2024) cannot open the sheet:
// the + buttons go to the deadlines in Settings instead.
if (sheet && !("popover" in HTMLElement.prototype)) {
  const plan = window.location.pathname.match(/^\/p\/[^/]+/);
  document.querySelectorAll("[popovertarget=quick-add]").forEach((button) =>
    button.addEventListener("click", () => {
      if (plan) window.location.assign(`${plan[0]}/settings/exams#deadlines`);
    }),
  );
} else if (sheet) {
  const due = sheet.querySelector('input[name="due"]');
  const shortcuts = sheet.querySelector(".quick-dates");
  shortcuts.hidden = false;
  shortcuts.addEventListener("click", (event) => {
    const button = event.target.closest("[data-due]");
    if (!button) return;
    const day = new Date();
    day.setDate(day.getDate() + Number(button.dataset.due));
    const pad = (n) => String(n).padStart(2, "0");
    // A deadline "on" a day is the end of it.
    due.value = `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}T23:59`;
    shortcuts.querySelectorAll("[data-due]").forEach((b) => b.setAttribute("aria-pressed", b === button ? "true" : "false"));
  });
  due.addEventListener("input", () => shortcuts.querySelectorAll("[data-due]").forEach((b) => b.setAttribute("aria-pressed", "false")));
  duePicker(sheet, due);
  courseOther(sheet);
  const other = sheet.querySelector('input[name="hours_other"]');
  other.addEventListener("input", () => {
    if (other.value) sheet.querySelectorAll('input[name="hours"]').forEach((r) => (r.checked = false));
  });
  sheet.querySelectorAll('input[name="hours"]').forEach((r) => r.addEventListener("change", () => (other.value = "")));
  // Return in the one-line field reads the line, rather than adding a task without a name.
  const words = sheet.querySelector('input[name="words"]');
  words.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      sheet.querySelector("[data-understand]").click();
    }
  });
  sheet.addEventListener("toggle", (event) => {
    if (event.newState === "open") setTimeout(() => sheet.querySelector('input[name="name"]').focus(), 30);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "n" || event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target;
    if (target.closest("input, textarea, select, [contenteditable], [popover]:popover-open")) return;
    event.preventDefault();
    sheet.showPopover();
  });
}

// Menus close when something else is clicked, as a menu does.
document.addEventListener("click", (event) => {
  document.querySelectorAll("details.menu[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.open = false;
  });
});

// -- step 5 (D39, D40): suggestions as one types, copying and sharing a link ---------

// A field marked data-suggest asks the server for suggestions as the student types
// (universities, D39) and offers them through its <datalist>. Without this script
// the field is a plain text field.
for (const field of document.querySelectorAll("input[data-suggest]")) {
  const list = field.list;
  if (!list) continue;
  let timer = null;
  let asked = "";
  field.addEventListener("input", () => {
    clearTimeout(timer);
    const query = field.value.trim();
    if (query.length < 2 || query === asked) return;
    timer = setTimeout(async () => {
      asked = query;
      try {
        const answer = await fetch(`${field.dataset.suggest}?q=${encodeURIComponent(query)}`, { headers: { Accept: "application/json" } });
        if (!answer.ok) return;
        list.replaceChildren(
          ...(await answer.json()).map((u) => {
            const option = document.createElement("option");
            option.value = u.name;
            option.label = [u.flag, u.domain].filter(Boolean).join(" ");
            return option;
          }),
        );
      } catch {
        /* offline: the field still takes what is typed */
      }
    }, 160);
  });
}

// D154: setting up's sentence. A value closes once chosen, so the sentence reads again.
document.addEventListener("change", (event) => {
  const pick = event.target.closest("details.pick");
  if (pick && event.target.type === "radio") pick.open = false;
});

// D154: the Moodle export page from its site's address as it is typed: every Moodle
// keeps it at <site>/calendar/export.php. Without this script the steps say where it
// is in Moodle's own words.
for (const field of document.querySelectorAll("input[data-moodle-site]")) {
  const box = field.closest("[data-moodle]");
  const open = box.querySelector("[data-moodle-open]");
  box.hidden = false;
  field.addEventListener("input", () => {
    const site = field.value.trim().replace(/^https?:\/\//i, "").replace(/\/+$/, "");
    const fine = /^[a-z0-9.-]+\.[a-z]{2,}(:\d{2,5})?(\/[\w.~-]+)*$/i.test(site);
    open.hidden = !fine;
    if (fine) open.href = `https://${site}/calendar/export.php`;
  });
}

// D154: a learning platform's link, pasted, shows the deadlines it brings before
// anything is saved; the server reads it and answers with the list.
for (const field of document.querySelectorAll("input[data-preview]")) {
  const out = document.getElementById(field.getAttribute("aria-describedby"));
  let timer = null;
  let asked = "";
  const ask = async () => {
    const url = field.value.trim();
    if (url === asked || !/^https?:\/\/\S+$/i.test(url)) return;
    asked = url;
    out.textContent = t("Reading the link…");
    try {
      const answer = await fetch(field.dataset.preview, { method: "POST", body: new URLSearchParams({ url }) });
      out.innerHTML = answer.ok ? await answer.text() : "";
    } catch {
      out.textContent = t("No connection to the server. Try again.");
    }
  };
  field.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(ask, 500);
  });
}

// Copy and Share buttons appear only where they work.
for (const button of document.querySelectorAll("[data-copy]")) button.hidden = !(navigator.clipboard && window.isSecureContext);
for (const button of document.querySelectorAll("[data-share]")) button.hidden = typeof navigator.share !== "function";

document.addEventListener("click", async (event) => {
  const copy = event.target.closest("[data-copy]");
  if (copy) {
    const source = document.querySelector(copy.dataset.copy);
    try {
      await navigator.clipboard.writeText(source.value || source.textContent);
      toast(t("Copied."));
    } catch {
      source.select && source.select();
    }
  }
  const share = event.target.closest("[data-share]");
  if (share) {
    try {
      await navigator.share({ text: share.dataset.shareText });
    } catch {
      /* the student closed the share sheet */
    }
  }
});

// The sky follows the sun (D73, D76): the server paints the sky of this moment in
// the plan's day; a page left open (a long focus session above all) asks the server
// every five minutes where the day is now, and moves the sky, the app's icon in the
// top bar and the tab (D74) and the day of the week on. Decoration: nothing waits on
// it, and without a network the page keeps the sky it has.
const plan = document.body.dataset.plan;
if (plan && document.body.dataset.sun) {
  setInterval(async () => {
    let now;
    try {
      const answer = await fetch(`/look/${plan}`, { cache: "no-store" });
      if (!answer.ok) return;
      now = await answer.json();
    } catch {
      return;
    }
    const body = document.body.dataset;
    if (String(now.sun) === body.sun && String(now.weekday) === body.day && now.sky === body.sky) return;
    body.sky = now.sky;
    body.day = String(now.weekday);
    body.sun = String(now.sun);
    // The new sky's sheet goes in beside the old one, which leaves once it has loaded:
    // changing the old one's address would show style.css's own sky for a moment.
    const sheet = document.querySelector("link[data-sky-sheet]");
    if (sheet instanceof HTMLLinkElement) {
      const next = /** @type {HTMLLinkElement} */ (sheet.cloneNode());
      next.href = `/sky/${now.sun}.css`;
      next.addEventListener("load", () => sheet.remove(), { once: true });
      next.addEventListener("error", () => next.remove(), { once: true });
      sheet.after(next);
    }
    const icon = `/icon/week-${now.weekday}-${now.sun}-${body.iconMode}.svg`;
    for (const element of document.querySelectorAll("[data-live-icon]")) {
      if (element instanceof HTMLLinkElement) element.href = icon;
      else element.src = icon;
    }
  }, 5 * 60 * 1000);
}

// -- Settings' old anchors (D103) -------------------------------------------------
// Settings was one page; a link or bookmark to one of its parts by its anchor (#ai,
// #reminders…) now lands on the list, and goes on to the part's own page. Without
// this, the list.

const anchored = window.location.hash.slice(1);
if (anchored) {
  const row = [...document.querySelectorAll(".settings-list a[data-anchors]")].find((a) =>
    a.dataset.anchors.split(" ").includes(anchored),
  );
  if (row) window.location.replace(`${row.getAttribute("href")}#${anchored}`);
}

// -- the app's own clock for a time of day (D172) -------------------------------------
// The system's list of times is grey and unlike the app (the owner's screenshot of
// setting up's busy times, 2026-11-20: "could be done better"). On a computer each time
// field marked data-clock becomes a text field with the day's hours and the quarters as
// chips under it, as the new-task sheet's times are (D159); typing still works, and the
// server reads "HH:MM" either way. A phone keeps its own wheel. Layout and input only.
const CLOCK_TIME = /^([01]?\d|2[0-3]):?([0-5]\d)$/;

function clockField(input) {
  if (input.dataset.clockReady || !window.matchMedia("(pointer: fine)").matches) return;
  input.dataset.clockReady = "1";
  input.type = "text";
  input.inputMode = "numeric";
  input.autocomplete = "off";
  input.maxLength = 5;
  input.placeholder = input.placeholder || "18:00";
  input.parentElement.classList.add("clock-field");
  const panel = document.createElement("div");
  panel.className = "clock-pick";
  panel.hidden = true;
  input.setAttribute("aria-controls", (panel.id = `${input.id || input.name}-clock`));
  input.setAttribute("aria-expanded", "false");
  input.after(panel);
  // A tap on a chip keeps the field focused, so the panel stays until a quarter is chosen.
  panel.addEventListener("pointerdown", (event) => event.preventDefault());
  const now = () => {
    const found = CLOCK_TIME.exec(input.value.trim());
    return found ? { h: found[1].padStart(2, "0"), m: found[2] } : { h: "", m: "" };
  };
  const set = (h, m) => {
    input.value = `${h}:${m}`;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    draw();
  };
  const chip = (label, pressed, onclick) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "pill-btn";
    b.textContent = label;
    b.setAttribute("aria-pressed", pressed ? "true" : "false");
    b.addEventListener("click", onclick);
    return b;
  };
  function draw() {
    const { h, m } = now();
    const hours = document.createElement("div");
    hours.className = "clock-hours";
    hours.setAttribute("role", "group");
    hours.setAttribute("aria-label", t("Hour"));
    for (let n = 0; n < 24; n++) {
      const hh = String(n).padStart(2, "0");
      hours.append(chip(hh, hh === h, () => set(hh, m || "00")));
    }
    const quarters = document.createElement("div");
    quarters.className = "clock-minutes";
    quarters.setAttribute("role", "group");
    quarters.setAttribute("aria-label", t("Minutes"));
    for (const mm of ["00", "15", "30", "45"]) {
      quarters.append(chip(`:${mm}`, mm === m, () => {
        set(h || "09", mm);
        close();
        input.focus();
      }));
    }
    panel.replaceChildren(hours, quarters);
  }
  const open = () => {
    if (!panel.hidden) return;
    draw();
    panel.hidden = false;
    input.setAttribute("aria-expanded", "true");
    // Under a field low on the screen, brought up, once the browser has done its own
    // scrolling for the focused field (done at once, its smooth scroll was cut short).
    requestAnimationFrame(() => panel.scrollIntoView({ block: "nearest" }));
  };
  const close = () => {
    panel.hidden = true;
    input.setAttribute("aria-expanded", "false");
  };
  input.addEventListener("focus", open);
  input.addEventListener("click", open);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });
  input.addEventListener("blur", () => {
    // Typed as 1830 or 9:5: written the way the server reads it.
    const found = CLOCK_TIME.exec(input.value.trim());
    if (found) input.value = `${found[1].padStart(2, "0")}:${found[2]}`;
  });
  input.parentElement.addEventListener("focusout", (event) => {
    if (!input.parentElement.contains(event.relatedTarget)) close();
  });
}

document.querySelectorAll("input[data-clock]").forEach(clockField);
document.addEventListener("focusin", (event) => {
  if (event.target instanceof HTMLInputElement && event.target.matches("input[data-clock]")) clockField(event.target);
});
