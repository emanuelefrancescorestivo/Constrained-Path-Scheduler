// What every page of a plan shares, on top of forms that work without it:
//
// - Forms marked data-async are sent in the background. The server answers as it
//   would a plain form (a redirect to the page), and the parts of that page this
//   one also has (#panel, #tasks, or the form's own data-region) are put in place;
//   its notice becomes a toast, unless the form is data-quiet (a kudos tap).
// - Ticking a task off says so at once, and offers to undo.
// - The new-task sheet gets one-tap due dates, and the N key opens it.
// - A "cps:changed" event tells the plan screen to redraw its calendar.
//
// Layout and input only: what a change means is decided by the server.

import { t } from "./i18n.js";

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
  toastTimer = setTimeout(() => node.remove(), bad ? 6000 : action ? 6000 : 3500);
}

async function send(form, submitter) {
  const data = new FormData(form, submitter);
  const response = await fetch(form.action, { method: "POST", body: new URLSearchParams(data) });
  const page = new DOMParser().parseFromString(await response.text(), "text/html");
  for (const selector of form.dataset.region ? [form.dataset.region] : REGIONS) {
    const here = document.querySelector(selector);
    const there = page.querySelector(selector);
    if (here && there) {
      there.querySelectorAll(".notice-ok").forEach((n) => n.remove()); // said by the toast
      here.replaceWith(there);
    }
  }
  const alert = page.querySelector(".alert, .error");
  const notice = page.querySelector(".notice-ok");
  return { ok: response.ok, message: response.ok ? notice && notice.textContent.trim() : alert && alert.textContent.trim() };
}

document.addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-async]");
  if (!form) return;
  event.preventDefault();
  const task = form.closest(".task");
  const finishing = form.action.endsWith("/tasks/done") && form.elements.done && form.elements.done.value === "1";
  if (task && finishing) task.classList.add("ticking"); // the tick shows at once
  form.querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    const answer = await send(form, event.submitter);
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
    form.querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
});

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

// -- the new-task sheet --------------------------------------------------------

const sheet = document.getElementById("quick-add");
// A browser without the popover attribute (before 2024) cannot open the sheet:
// the + buttons go to the deadlines in Settings instead.
if (sheet && !("popover" in HTMLElement.prototype)) {
  const settings = document.querySelector('a[href$="/settings"]');
  document.querySelectorAll("[popovertarget=quick-add]").forEach((button) =>
    button.addEventListener("click", () => {
      if (settings) window.location.assign(`${settings.getAttribute("href")}#deadlines`);
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
  const other = sheet.querySelector('input[name="hours_other"]');
  other.addEventListener("input", () => {
    if (other.value) sheet.querySelectorAll('input[name="hours"]').forEach((r) => (r.checked = false));
  });
  sheet.querySelectorAll('input[name="hours"]').forEach((r) => r.addEventListener("change", () => (other.value = "")));
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
