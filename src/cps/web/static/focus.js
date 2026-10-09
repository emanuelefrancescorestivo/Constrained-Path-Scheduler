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

const sweep = document.getElementById("focus-sweep");
const pad = (n) => String(n).padStart(2, "0");

// The ring round the clock goes round once a minute (D30), in step with the seconds:
// its animation starts as far into the minute as the session is.
function syncSweep() {
  if (!sweep) return;
  const seconds = elapsed + (performance.now() - since) / 1000;
  sweep.style.animationDelay = `-${(seconds % 60).toFixed(2)}s`;
}
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
    syncSweep();
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
syncSweep();
start();

const tools = document.querySelector(".focus-tools");
if (document.fullscreenEnabled) {
  tools.hidden = false;
  document.getElementById("focus-full").addEventListener("click", () => section.requestFullscreen().catch(() => {}));
}
