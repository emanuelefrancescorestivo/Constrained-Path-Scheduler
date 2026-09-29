// The plan screen: the calendar (calendar.js) and the panel beside it. The panel
// is HTML the server renders (/p/<token>/panel); after every change it is fetched
// again, so there is one place where what it says is decided. Without this
// script, every form on the page still works, by full page loads.
import { Calendar } from "./calendar.js";

const plan = document.querySelector(".plan");
const token = plan.dataset.token;
const panel = document.getElementById("panel");
let toastTimer = null;

function toast(text, bad) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const node = document.createElement("div");
  node.className = `toast${bad ? " bad" : ""}`;
  node.setAttribute("role", bad ? "alert" : "status");
  node.textContent = text ? text[0].toUpperCase() + text.slice(1) : "";
  document.body.append(node);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.remove(), bad ? 6000 : 3500);
}

async function refreshPanel() {
  try {
    const response = await fetch(`/p/${token}/panel`);
    if (response.ok) {
      panel.innerHTML = await response.text();
      wirePanel();
    }
  } catch {
    /* the panel stays as it was; the next change redraws it */
  }
}

const calendar = new Calendar(document.getElementById("calendar"), {
  token,
  first: plan.dataset.first,
  toast,
  onChanged: refreshPanel,
  onRange: (text) => {
    document.getElementById("calendar-range").textContent = text;
  },
});

function wirePanel() {
  panel.querySelectorAll("form[data-report]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const outcome = event.submitter ? event.submitter.value : "done";
      const sid = form.action.split("/").pop();
      const word = event.submitter ? event.submitter.textContent.trim().toLowerCase() : outcome;
      await calendar.change(`sessions/${sid}/report`, { outcome }, `Recorded: ${word}. The plan has adjusted.`);
    });
  });
  panel.querySelectorAll("form[data-add-task]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const response = await fetch(form.action, { method: "POST", body: new URLSearchParams(new FormData(form)) });
      if (response.ok) {
        toast("Added. The plan has made room for it.");
        await refreshPanel();
        calendar.load();
      } else {
        // The page answered with the panel and the reason; show that panel.
        const page = new DOMParser().parseFromString(await response.text(), "text/html");
        const fresh = page.getElementById("panel");
        if (fresh) {
          panel.innerHTML = fresh.innerHTML;
          wirePanel();
        }
        toast("Not added: see the form.", true);
      }
    });
  });
}

document.getElementById("calendar-bar").hidden = false;
document.getElementById("calendar-bar").addEventListener("click", (event) => {
  const button = event.target.closest("[data-go]");
  if (!button) return;
  if (button.dataset.go === "busy") calendar.addBusy(button);
  else calendar.go(button.dataset.go);
});
wirePanel();
calendar.load();
