// The focus page: the clock, a heartbeat, the screen kept awake, full screen.
// The heartbeat is how the server knows the page is open and visible: browsers
// stop timers on a hidden page, so a gap between beats is time away from it
// (DECISIONS.md D17). This script measures nothing itself; the server decides.
import { t } from "./i18n.js";
import { celebrate, resting } from "./motion.js";
import { toast } from "./ui.js";

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
// The break nudge (D63): the server says after how long; it is in the page,
// hidden, and shown when the clock gets there.
const nudge = document.querySelector(".focus-break[data-break-at]");
// The rewards (D67): each at a second of focused time, the time on the page (the
// clock less the time away, which the server measures); the server marks those
// already reached, this script marks the rest as they come, and shows the next.
const rewards = [...document.querySelectorAll(".focus-rewards [data-at]")];
const rewardCard = document.querySelector(".focus-reward");
let awaySeconds = Number(section.dataset.away || 0);
const pad = (n) => String(n).padStart(2, "0");

// The rhythm (D178, the owner's 3A): 25 minutes of work, then a 5-minute pause by itself.
// The clock stops while the pause counts down; at either edge the page tells the server
// at once (a heartbeat), which keeps the same rule and has the last word.
const rhythm = section.dataset.rhythm === "1";
const workSeconds = Number(section.dataset.work || 1500);
const pauseSeconds = Number(section.dataset.pause || 300);
const pausePanel = document.getElementById("focus-pause");
const pauseClock = document.getElementById("pause-clock");
let pausing = section.classList.contains("is-pausing");
let pauseEnds = performance.now() + Number(section.dataset.pauseLeft || 0) * 1000;
let block = Number(section.dataset.block || 0);
// The clock: work only. While pausing it stands still at `elapsed`.
const now = () => (pausing ? elapsed : elapsed + (performance.now() - since) / 1000);

function setPausing(on) {
  pausing = on;
  section.classList.toggle("is-pausing", on);
  if (pausePanel) pausePanel.hidden = !on;
}

function rhythmStep() {
  if (!rhythm) return;
  const at = performance.now();
  if (!pausing) {
    const inBlock = block + (at - since) / 1000;
    if (inBlock >= workSeconds) {
      elapsed = now() - (inBlock - workSeconds);
      since = at;
      pauseEnds = at + (pauseSeconds - (inBlock - workSeconds)) * 1000;
      setPausing(true);
      beat();
    }
  } else {
    const left = Math.max(0, Math.ceil((pauseEnds - at) / 1000));
    if (pauseClock) pauseClock.textContent = `${Math.floor(left / 60)}:${pad(left % 60)}`;
    if (left <= 0) {
      since = at;
      block = 0;
      setPausing(false);
      beat();
    }
  }
}

// The stem (D178, the owner's 2C): it grows with the focused time; leaves come out and buds
// open as it passes them.
const horizon = Number(section.dataset.horizon || 3600);
const stemGrow = document.getElementById("stem-grow");
const stemNext = document.getElementById("stem-next");
const buds = [...document.querySelectorAll(".stem-bud[data-at]")];
const leaves = [...document.querySelectorAll(".stem-leaf[data-share]")];
function growStem(focused) {
  if (!stemGrow) return;
  const share = Math.min(1, Math.max(0, focused / horizon));
  stemGrow.setAttribute("stroke-dasharray", `${(share * 100).toFixed(1)} 100`);
  leaves.forEach((leaf) => leaf.classList.toggle("is-out", share >= Number(leaf.dataset.share)));
  let ahead = null;
  buds.forEach((bud) => {
    const open = focused >= Number(bud.dataset.at);
    bud.classList.toggle("is-open", open);
    if (!open && ahead === null) ahead = Number(bud.dataset.at);
  });
  if (stemNext) {
    if (ahead === null) stemNext.textContent = stemNext.dataset.all;
    else {
      const n = Math.ceil((ahead - focused) / 60);
      stemNext.textContent = (n === 1 ? stemNext.dataset.one : stemNext.dataset.many).replace("{n}", n);
    }
  }
}

// Cheers (D178, the owner's 4C): who cheered the student on, said once each as it comes.
const cheersLine = document.getElementById("focus-cheers");
const cheered = new Set((section.dataset.cheers || "").split(" ").filter(Boolean));
function sayCheers(handles) {
  if (!Array.isArray(handles) || !cheersLine) return;
  handles.filter((h) => !cheered.has(h)).forEach((h) => {
    cheered.add(h);
    toast(cheersLine.dataset.one.replace("{handle}", h));
  });
  if (!handles.length) return;
  const names = handles.map((h) => `@${h}`).join(", ");
  cheersLine.textContent =
    handles.length === 1
      ? cheersLine.dataset.one.replace("{handle}", handles[0])
      : cheersLine.dataset.many.replace("{names}", names);
  cheersLine.hidden = false;
}
const still = () =>
  document.documentElement.dataset.motion === "reduce" || matchMedia("(prefers-reduced-motion: reduce)").matches;

// The ring round the clock goes round once a minute (D30) and closes as the minute's
// digits turn over: it is drawn from the same clock as the digits, on every frame, so
// a beat that corrects the clock moves both together. (It was a CSS animation whose
// start was shifted at every beat, which pushed it out of step, AUDIT 72.) Under
// reduced motion it stays a still, full circle.
// While the page rests (D93) it moves with the digits, once a second, instead of on
// every frame: a session left open for an hour costs a slow device little.
function sweepTo() {
  if (sweep && !still()) sweep.style.strokeDashoffset = String(100 - ((now() % 60) / 60) * 100);
}
let sweeping = false;
function sweepFrame() {
  sweepTo();
  sweeping = !resting();
  if (sweeping) requestAnimationFrame(sweepFrame);
}
document.addEventListener("cps:wake", () => {
  if (!sweeping) requestAnimationFrame(sweepFrame);
});
// The digits turn on the second itself, not up to a second late.
function tick() {
  draw();
  setTimeout(tick, 1000 - (now() % 1) * 1000 + 5);
}
function draw() {
  rhythmStep();
  if (!sweeping) sweepTo();
  const seconds = Math.floor(now());
  clock.textContent = `${Math.floor(seconds / 3600)}:${pad(Math.floor((seconds % 3600) / 60))}:${pad(seconds % 60)}`;
  if (nudge && nudge.hidden && seconds >= Number(nudge.dataset.breakAt)) nudge.hidden = false;
  reward(seconds - awaySeconds);
  growStem(seconds - awaySeconds);
}

function reward(focused) {
  rewards.forEach((item, i) => {
    if (item.classList.contains("is-reached") || focused < Number(item.dataset.at)) return;
    item.classList.add("is-reached");
    item.hidden = false;
    if (rewards[i + 1]) rewards[i + 1].hidden = false;
    if (!rewardCard) return;
    document.getElementById("focus-reward-words").textContent = item.dataset.cheer;
    document.getElementById("focus-reward-next").textContent = item.dataset.next;
    rewardCard.hidden = false;
    celebrate(rewardCard);
  });
}

async function beat() {
  try {
    const answer = await fetch(`/p/${token}/focus/beat`, { method: "POST", keepalive: true });
    if (!answer.ok) return stop();
    const state = await answer.json();
    elapsed = state.elapsed;
    awaySeconds = Number(state.away || 0);
    since = performance.now();
    if (rhythm) {
      // The server's rhythm has the last word (D178).
      block = Number(state.block || 0);
      if (state.pausing) pauseEnds = since + Number(state.pause_left || 0) * 1000;
      setPausing(Boolean(state.pausing));
    }
    sayCheers(state.cheers);
    draw();
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
tick();
requestAnimationFrame(sweepFrame);
start();

const tools = document.querySelector(".focus-tools");
if (document.fullscreenEnabled) {
  tools.hidden = false;
  document.getElementById("focus-full").addEventListener("click", () => section.requestFullscreen().catch(() => {}));
}
