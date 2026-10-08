// The focus page: the clock, a heartbeat, the screen kept awake, full screen.
// The heartbeat is how the server knows the page is open and visible: browsers
// stop timers on a hidden page, so a gap between beats is time away from it
// (DECISIONS.md D17). This script measures nothing itself; the server decides.
import { t } from "./i18n.js";

const section = document.querySelector(".focus");
const token = section.dataset.token;
const beatEvery = Number(section.dataset.beat || 30) * 1000;
const clock = document.getElementById("focus-timer");
const away = document.getElementById("focus-away");
let elapsed = Number(section.dataset.elapsed || 0);
let since = performance.now();
let beatTimer = null;
let lock = null;

const pad = (n) => String(n).padStart(2, "0");
function draw() {
  const seconds = Math.floor(elapsed + (performance.now() - since) / 1000);
  clock.textContent = `${Math.floor(seconds / 3600)}:${pad(Math.floor((seconds % 3600) / 60))}:${pad(seconds % 60)}`;
}

async function beat() {
  try {
    const answer = await fetch(`/p/${token}/focus/beat`, { method: "POST", keepalive: true });
    if (!answer.ok) return stop();
    const state = await answer.json();
    elapsed = state.elapsed;
    since = performance.now();
    if (state.interruptions) {
      away.textContent = ` · ${t(state.interruptions === 1 ? "left the app {n} time ({minutes} min)" : "left the app {n} times ({minutes} min)", { n: state.interruptions, minutes: state.away_minutes })}`;
    }
  } catch {
    /* offline: the gap is counted when the connection comes back */
  }
}

function start() {
  if (beatTimer === null) beatTimer = setInterval(beat, beatEvery);
  beat();
  keepAwake();
}

function stop() {
  clearInterval(beatTimer);
  beatTimer = null;
}

async function keepAwake() {
  try {
    if ("wakeLock" in navigator && !lock) {
      lock = await navigator.wakeLock.request("screen");
      lock.addEventListener("release", () => (lock = null));
    }
  } catch {
    /* not allowed here: the screen may sleep, which the beats will show */
  }
}

document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
setInterval(draw, 1000);
draw();
start();

const tools = document.querySelector(".focus-tools");
if (document.fullscreenEnabled) {
  tools.hidden = false;
  document.getElementById("focus-full").addEventListener("click", () => section.requestFullscreen().catch(() => {}));
}
