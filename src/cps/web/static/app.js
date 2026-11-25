// The plan screen: Today's widgets (the panel) or the calendar (calendar.js); since
// D140 each is its own page, where D73 drew them side by side. The panel is HTML the
// server renders (/p/<token>/panel); after every change it is fetched again, so there
// is one place where what it says is decided. Without this script, every form on the
// page still works, by full page loads.
import { Calendar } from "./calendar.js";
import { t } from "./i18n.js";
import { carryOver, transition } from "./motion.js";
import { toast } from "./ui.js";

const plan = document.querySelector(".plan");
const token = plan.dataset.token;
const panel = () => document.getElementById("panel");

async function refreshPanel() {
  if (!panel()) return; // drawing a week (D32): the calendar alone
  try {
    const response = await fetch(`/p/${token}/panel`);
    if (response.ok) {
      const html = await response.text();
      const before = panel().cloneNode(true);
      // "More" stays open if it was (D73): a task ticked inside it redraws the panel.
      const wasOpen = panel().querySelector("details[data-more]")?.open;
      // The answered session leaves its list and the others close up; the week's
      // ring and numbers move from what they were (D30).
      await transition(() => {
        panel().innerHTML = html;
        if (wasOpen) panel().querySelector("details[data-more]")?.setAttribute("open", "");
      });
      carryOver(before, panel());
      wirePanel();
    }
  } catch {
    /* the panel stays as it was; the next change redraws it */
  }
}

const bar = document.getElementById("calendar-bar");
const board = document.getElementById("calendar");
const calendar = board && new Calendar(board, {
  token,
  first: plan.dataset.first,
  today: plan.dataset.today,
  toast: (text, bad) => toast(text, { bad }),
  onChanged: refreshPanel,
  setup: plan.dataset.setup === "classes",
  onRange: (text, view, week) => {
    document.getElementById("calendar-range").textContent = text;
    document.getElementById("calendar-week").textContent = week || "";
    const pick = document.getElementById("calendar-view");
    if (pick) pick.value = String(view);
  },
  onLegend: sideCourses,
});

// The courses shown on the calendar, in the sidebar on a laptop (D157), as Notion
// Calendar lists its calendars: each a switch that shows or hides the course's classes
// and sessions. Drawn from what the calendar draws; under 1100px, where the sidebar is
// hidden, the calendar's own legend has the same switches.
function sideCourses(legend) {
  const sidebar = document.querySelector(".sidebar");
  if (!sidebar) return;
  let list = sidebar.querySelector(".side-shows");
  if (!legend.length) {
    list?.remove();
    return;
  }
  if (!list) {
    list = document.createElement("section");
    list.className = "side-shows";
    const title = document.createElement("h2");
    title.className = "side-title";
    title.textContent = t("On the calendar");
    list.append(title, document.createElement("ul"));
    sidebar.querySelector(".side-month")?.after(list);
  }
  const rows = legend.map(({ course, color, shown }) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `side-show ${color === null || color === undefined ? "cn" : `c${color}`}`;
    button.setAttribute("aria-pressed", shown ? "true" : "false");
    const box = document.createElement("span");
    box.className = "side-check";
    box.setAttribute("aria-hidden", "true");
    const name = document.createElement("span");
    name.className = "grow";
    name.textContent = course;
    button.append(box, name);
    button.addEventListener("click", () => calendar.toggleCourse(course));
    const item = document.createElement("li");
    item.append(button);
    return item;
  });
  // D192: the calendar's colours, calm or by course, the last row.
  const colour = document.createElement("button");
  colour.type = "button";
  colour.className = "side-show colour-switch";
  colour.setAttribute("aria-pressed", calendar.byCourse ? "true" : "false");
  const swatch = document.createElement("span");
  swatch.className = "side-check";
  swatch.setAttribute("aria-hidden", "true");
  const words = document.createElement("span");
  words.className = "grow";
  words.textContent = t("Colour by course");
  colour.append(swatch, words);
  colour.addEventListener("click", () => calendar.toggleColour());
  const last = document.createElement("li");
  last.append(colour);
  list.querySelector("ul").replaceChildren(...rows, last);
}

// A change from Today (D140), where there is no calendar to send it and redraw: the
// same request, then the widgets again.
async function change(path, body, said) {
  if (calendar) return calendar.change(path, body, said);
  try {
    const response = await fetch(`/p/${token}/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let answer = {};
    try {
      answer = await response.json();
    } catch {
      /* said below */
    }
    if (!response.ok) throw new Error(answer.message || t("The server answered {status}.", { status: response.status }));
    if (said) toast(said);
    await refreshPanel();
  } catch (error) {
    toast(error.message, { bad: true });
  }
}

// Today's widgets packed as a masonry (the owner, 2026-11-19: "too many blank void empty
// spaces"): in two or three columns each widget spans as many rows of HUB_ROW pixels as
// it is tall, with the gap, so a short widget no longer leaves a tall neighbour's row
// empty under it. Layout only: without this script the grid is as before, each row as
// tall as its tallest widget. Measured again when a widget or the page changes size.
const HUB_ROW = 2;
let hubWatch = null;
function packHub() {
  hubWatch?.disconnect();
  const hub = panel()?.querySelector(".hub");
  if (!hub) return;
  let queued = false;
  const pack = () => {
    queued = false;
    const style = getComputedStyle(hub);
    const columns = style.gridTemplateColumns.split(" ").filter(Boolean).length;
    if (columns < 2) {
      hub.classList.remove("is-packed");
      for (const widget of hub.children) widget.style.removeProperty("grid-row-end");
      return;
    }
    const gap = parseFloat(style.getPropertyValue("--hub-gap")) || 0;
    hub.classList.add("is-packed");
    for (const widget of hub.children) {
      const tall = widget.getBoundingClientRect().height;
      widget.style.gridRowEnd = `span ${Math.max(1, Math.ceil((tall + gap) / HUB_ROW))}`;
    }
  };
  hubWatch = new ResizeObserver(() => {
    if (!queued) { queued = true; requestAnimationFrame(pack); }
  });
  hubWatch.observe(hub);
  for (const widget of hub.children) hubWatch.observe(widget);
}

// The reports in the panel go through the calendar where there is one, which redraws
// itself; on Today, through `change`.
function wirePanel() {
  if (!panel()) return;
  packHub();
  panel().querySelectorAll("form[data-report]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const outcome = event.submitter ? event.submitter.value : "done";
      const sid = form.action.split("/").pop();
      const word = event.submitter ? event.submitter.textContent.trim().toLowerCase() : outcome;
      const said = form.hasAttribute("data-recall") && outcome !== "skipped"
        ? t("Recorded. Your exam forecast now uses what you recalled.")
        : t("Recorded: {word}. The plan has adjusted.", { word });
      await change(`sessions/${sid}/report`, { outcome }, said);
    });
  });
}

// A task added, finished or deleted elsewhere on the page (ui.js): redraw.
document.addEventListener("cps:changed", () => {
  wirePanel();
  if (calendar) calendar.load(true);
});

if (bar) bar.hidden = false;
document.getElementById("calendar-view")?.addEventListener("change", (event) => {
  calendar.setView(Number(event.target.value));
});
bar?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-go]");
  if (!button) return;
  if (button.dataset.go === "busy") calendar.addBusy(button);
  else calendar.go(button.dataset.go);
});
// The arrow keys move through days, as in Calendar; T goes to today.
document.addEventListener("keydown", (event) => {
  if (!calendar) return;
  if (event.metaKey || event.ctrlKey || event.altKey || event.target.closest("input, textarea, select, [popover]:popover-open, .pop")) return;
  if (event.key === "ArrowLeft") calendar.go("back");
  else if (event.key === "ArrowRight") calendar.go("forward");
  else if (event.key === "t") calendar.go("today");
  else return;
  event.preventDefault();
});
wirePanel();
if (calendar) calendar.load();
