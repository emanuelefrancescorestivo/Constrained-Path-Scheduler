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
  onRange: (text, view) => {
    document.getElementById("calendar-range").textContent = text;
    bar.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-pressed", Number(b.dataset.view) === view ? "true" : "false"));
  },
});

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

// The reports in the panel go through the calendar where there is one, which redraws
// itself; on Today, through `change`.
function wirePanel() {
  if (!panel()) return;
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
bar?.addEventListener("click", (event) => {
  const view = event.target.closest("[data-view]");
  if (view) {
    calendar.setView(Number(view.dataset.view));
    return;
  }
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
