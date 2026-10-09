// The plan screen: the calendar (calendar.js) and the panel beside it. The panel
// is HTML the server renders (/p/<token>/panel); after every change it is fetched
// again, so there is one place where what it says is decided. Without this
// script, every form on the page still works, by full page loads.
import { Calendar } from "./calendar.js";
import { t } from "./i18n.js";
import { carryOver, transition } from "./motion.js";
import { toast } from "./ui.js";

const plan = document.querySelector(".plan");
const token = plan.dataset.token;
const panel = () => document.getElementById("panel");

async function refreshPanel() {
  try {
    const response = await fetch(`/p/${token}/panel`);
    if (response.ok) {
      const html = await response.text();
      const before = panel().cloneNode(true);
      // The answered session leaves its list and the others close up; the week's
      // ring and numbers move from what they were (D30).
      await transition(() => {
        panel().innerHTML = html;
      });
      carryOver(before, panel());
      wirePanel();
    }
  } catch {
    /* the panel stays as it was; the next change redraws it */
  }
}

const bar = document.getElementById("calendar-bar");
const calendar = new Calendar(document.getElementById("calendar"), {
  token,
  first: plan.dataset.first,
  toast: (text, bad) => toast(text, { bad }),
  onChanged: refreshPanel,
  onRange: (text, view) => {
    document.getElementById("calendar-range").textContent = text;
    bar.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-pressed", Number(b.dataset.view) === view ? "true" : "false"));
  },
});

// The reports in the panel go through the calendar, which redraws itself.
function wirePanel() {
  panel().querySelectorAll("form[data-report]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const outcome = event.submitter ? event.submitter.value : "done";
      const sid = form.action.split("/").pop();
      const word = event.submitter ? event.submitter.textContent.trim().toLowerCase() : outcome;
      const said = form.hasAttribute("data-recall") && outcome !== "skipped"
        ? t("Recorded. Your exam forecast now uses what you recalled.")
        : t("Recorded: {word}. The plan has adjusted.", { word });
      await calendar.change(`sessions/${sid}/report`, { outcome }, said);
    });
  });
}

// A task added, finished or deleted elsewhere on the page (ui.js): redraw.
document.addEventListener("cps:changed", () => {
  wirePanel();
  calendar.load();
});

bar.hidden = false;
bar.addEventListener("click", (event) => {
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
  if (event.metaKey || event.ctrlKey || event.altKey || event.target.closest("input, textarea, select, [popover]:popover-open, .pop")) return;
  if (event.key === "ArrowLeft") calendar.go("back");
  else if (event.key === "ArrowRight") calendar.go("forward");
  else if (event.key === "t") calendar.go("today");
  else return;
  event.preventDefault();
});
wirePanel();
calendar.load();
