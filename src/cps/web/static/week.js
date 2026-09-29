// The Week page: the calendar to drag on (week_calendar.js), fed from
// /p/<token>/calendar.json and saved to /p/<token>/activities. Without this
// script the page is a list of days, and busy times are typed in Settings.
import mount from "./week_calendar.js";

const root = document.getElementById("calendar");
const token = root.dataset.token;
const status = document.getElementById("calendar-status");
const range = document.getElementById("calendar-range");
const narrow = window.matchMedia("(max-width: 640px)");
let first = root.dataset.first;
let count = narrow.matches ? 3 : 7;
let saving = Promise.resolve();

function shift(iso, days) {
  const d = new Date(iso + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function say(text, isError) {
  status.textContent = text;
  status.classList.toggle("error", !!isError);
}

function draw(data) {
  for (const item of data.items) if (item.id) item.href = `/s/${token}/${item.id}`;
  root.querySelectorAll(".calendar-fallback").forEach((n) => n.remove());
  mount({ data: { ...data, editable: true }, parentElement: root, setTriggerValue: (_, rows) => save(rows) });
  const days = data.days;
  range.textContent = days.length === 1 ? days[0].label : `${days[0].label} – ${days[days.length - 1].label}`;
  if (data.error && !data.planned) say(data.error, true);
}

async function answer(response) {
  let body = {};
  try {
    body = await response.json();
  } catch {
    /* not JSON: said below */
  }
  if (!response.ok) throw new Error(body.message || `The server answered ${response.status}.`);
  return body;
}

async function load() {
  try {
    draw(await answer(await fetch(`/p/${token}/calendar.json?start=${first}&days=${count}`)));
  } catch (error) {
    say(error.message, true);
  }
}

function save(rows) {
  say("Saving…");
  saving = saving.then(async () => {
    try {
      const response = await fetch(`/p/${token}/activities?start=${first}&days=${count}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(rows),
      });
      const data = await answer(response);
      draw(data);
      say(data.error ? data.error : "Saved. The plan has changed to match.", !!data.error);
    } catch (error) {
      say(`Not saved: ${error.message}`, true);
      await load();
    }
  });
}

document.getElementById("calendar-earlier").addEventListener("click", () => {
  first = shift(first, -count);
  load();
});
document.getElementById("calendar-later").addEventListener("click", () => {
  first = shift(first, count);
  load();
});
narrow.addEventListener("change", () => {
  count = narrow.matches ? 3 : 7;
  load();
});
document.getElementById("calendar-tools").hidden = false;
load();
