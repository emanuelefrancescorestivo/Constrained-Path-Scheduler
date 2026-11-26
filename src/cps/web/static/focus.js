// The focus page: the clock, a heartbeat, the screen kept awake, full screen.
// The heartbeat is how the server knows the page is open and visible: browsers
// stop timers on a hidden page, so a gap between beats is time away from it
// (DECISIONS.md D17). This script measures nothing itself; the server decides.
import { t } from "./i18n.js";
import { celebrate } from "./motion.js";
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
// D179: the clock shows the minutes on the page, the vine's and the diary's: the time away
// is taken off. While the page is hidden nothing runs; back on it, the time it was hidden
// is taken off at once as the server will (more than a grace, less one heartbeat), until
// the heartbeat's answer brings the server's own count.
const grace = Number(section.dataset.grace || 75);
let hiddenAt = null;
let localAway = 0;
const focusedNow = () => Math.max(0, now() - awaySeconds - localAway);
const clockText = (s) =>
  s >= 3600 ? `${Math.floor(s / 3600)}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}` : `${Math.floor(s / 60)}:${pad(s % 60)}`;

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

// The vine round the clock (D179, the owner's B; D178's stem before it): it grows with the
// minutes on the page; leaves come out and buds open as it passes them.
const horizon = Number(section.dataset.horizon || 3600);
const stemGrow = document.getElementById("vine-grow");
const stemNext = document.getElementById("stem-next");
const buds = [...document.querySelectorAll(".vine-bud[data-at]")];
const leaves = [...document.querySelectorAll(".vine-leaf[data-share]")];
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
      stemNext.textContent = stemNext.dataset.many.replace("{n}", Math.ceil((ahead - focused) / 60));
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
// The digits turn on the second itself, not up to a second late.
function tick() {
  draw();
  setTimeout(tick, 1000 - (now() % 1) * 1000 + 5);
}
function draw() {
  rhythmStep();
  const focused = Math.floor(focusedNow());
  clock.textContent = clockText(focused);
  clock.classList.toggle("is-hours", focused >= 3600);  // D207: longer digits, a smaller size
  if (nudge && nudge.hidden && Math.floor(now()) >= Number(nudge.dataset.breakAt)) nudge.hidden = false;
  reward(focused);
  growStem(focused);
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
    localAway = 0;
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

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    hiddenAt = pausing ? null : performance.now();  // a pause is never time away (D178)
    return stop();
  }
  if (hiddenAt !== null) {
    const gone = (performance.now() - hiddenAt) / 1000;
    if (gone > grace) localAway += Math.round(gone - beatEvery / 1000);  // whole seconds: the digits keep their beat
    hiddenAt = null;
    draw();
  }
  start();
});
tick();
start();

const tools = document.querySelector(".focus-tools");
if (document.fullscreenEnabled) {
  tools.hidden = false;
  document.getElementById("focus-full").addEventListener("click", () => section.requestFullscreen().catch(() => {}));
}
