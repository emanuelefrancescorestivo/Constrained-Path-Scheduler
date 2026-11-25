// D203: the front page's scenes, played. Each scene is drawn complete in the page; with motion
// on, this script sets its parts back (`ls-armed`) and, as the scene comes into view, brings
// each part in at its time (data-at, in milliseconds): numbers count up (data-count, from that
// number), bursts of confetti (data-burst) and taps (data-tap) mark the moments that reward.
// It plays once; scrolled away and back, it plays again. With motion off, or without this
// script, every scene stays as drawn, complete. Nothing loops (D93).
const root = document.documentElement;
const still =
  matchMedia("(prefers-reduced-motion: reduce)").matches || root.dataset.motion === "reduce";

const COLOURS = 6; // the confetti's colours, .ls-confetti.c0 to .c5 in the style sheet

function burst(part, card) {
  const a = part.getBoundingClientRect();
  const c = card.getBoundingClientRect();
  const x = a.left - c.left + a.width / 2;
  const y = a.top - c.top + a.height / 2;
  for (let k = 0; k < 14; k++) {
    const bit = document.createElement("i");
    bit.className = `ls-confetti c${k % COLOURS}`;
    const angle = (Math.PI * 2 * k) / 14 + Math.random() * 0.4;
    const reach = 46 + Math.random() * 46;
    bit.style.left = `${x}px`;
    bit.style.top = `${y}px`;
    bit.style.setProperty("--dx", `${Math.cos(angle) * reach}px`);
    bit.style.setProperty("--dy", `${Math.sin(angle) * reach - 18}px`);
    bit.style.setProperty("--r", `${Math.round(Math.random() * 540 - 270)}deg`);
    card.appendChild(bit);
    setTimeout(() => bit.remove(), 1200);
  }
}

function count(el) {
  const text = el.dataset.text;
  const match = text.match(/\d+(?:[.,]\d+)?/);
  if (!match) return;
  const to = Number(match[0].replace(",", "."));
  const from = Number(el.dataset.count);
  const ms = Number(el.dataset.ms || 900);
  const start = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - start) / ms);
    const eased = 1 - (1 - k) ** 3;
    el.textContent = text.replace(match[0], String(Math.round(from + (to - from) * eased)));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

if (!still && "IntersectionObserver" in window) {
  for (const scene of document.querySelectorAll("[data-scene]")) {
    const card = scene.querySelector(".ls-card") || scene;
    const parts = [...scene.querySelectorAll("[data-at]")];
    const counters = [...scene.querySelectorAll("[data-count]")];
    const moves = [...scene.querySelectorAll("[data-from]")]; // a style that goes from one value to its own
    for (const c of counters) c.dataset.text = c.textContent;
    let timers = [];
    const arm = () => {
      timers.forEach(clearTimeout);
      timers = [];
      scene.classList.add("ls-armed");
      for (const p of parts) p.classList.remove("on");
      for (const c of counters) c.textContent = c.dataset.text.replace(/\d+(?:[.,]\d+)?/, c.dataset.count);
      for (const m of moves) m.style.setProperty(m.dataset.prop, m.dataset.from);
    };
    const go = (part) => {
      part.classList.add("on");
      if (part.dataset.count !== undefined) count(part);
      if (part.dataset.prop) part.style.setProperty(part.dataset.prop, part.dataset.to);
      if (part.hasAttribute("data-burst")) burst(part, card);
    };
    const play = () => {
      arm();
      for (const p of parts) timers.push(setTimeout(() => go(p), Number(p.dataset.at)));
    };
    arm();
    let shown = false;
    new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting && !shown) {
            shown = true;
            play();
          } else if (!e.isIntersecting && shown) {
            shown = false;
            arm();
          }
        }
      },
      { threshold: 0.35 },
    ).observe(scene);
  }
}
