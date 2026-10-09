// Motion (DECISIONS.md D30): movement that shows what changed, and a little joy when
// something is achieved. Everything here decorates pages that are complete without
// it: the numbers are already written, the rings already drawn, the page already
// changed. Nothing moves when the device asks for reduced motion, or when the plan's
// setting is "Reduced" (data-motion="reduce" on <html>).
//
// - Numbers marked data-count count up the first time they are seen.
// - After a part of a page is replaced (ui.js, app.js), its rings and numbers move
//   from their old values to the new ones instead of jumping (carryOver), and the
//   swap is one view transition, in which elements marked data-vt slide to their
//   new places (transition).
// - Something marked data-celebrate="key" sends up a burst of confetti, once per key
//   on this device.
// - The CSS's entrance animations run on a page's first paint only: the class
//   "settled" on <html> turns them off once the page has arrived.

const root = document.documentElement;
const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
const EASE_OUT = "cubic-bezier(0.22, 1, 0.36, 1)";

export const still = () => reduce.matches || root.dataset.motion === "reduce";

// ----------------------------------------------------------------- numbers

const NUMBER = /-?\d+(?:[.,]\d+)?/;

function parse(text) {
  const found = text.match(NUMBER);
  if (!found) return null;
  const part = found[0].split(/[.,]/)[1] || "";
  return {
    value: Number(found[0].replace(",", ".")),
    start: found.index,
    length: found[0].length,
    separator: found[0].includes(",") ? "," : ".",
    decimals: part.length,
  };
}

// Counts the number in `node` from `from` to the number written, keeping the words
// around it and the page's decimal separator; ends on the text exactly as written.
export function countUp(node, from = 0, duration = 900) {
  if (still() || node.children.length) return;
  const text = node.textContent;
  const number = parse(text);
  if (!number || number.value === from) return;
  const before = text.slice(0, number.start);
  const after = text.slice(number.start + number.length);
  const began = performance.now();
  const frame = (now) => {
    const t = Math.min(1, (now - began) / duration);
    const eased = 1 - (1 - t) ** 3;
    const value = (from + (number.value - from) * eased).toFixed(number.decimals);
    node.textContent = before + value.replace(".", number.separator) + after;
    if (t < 1) requestAnimationFrame(frame);
    else node.textContent = text;
  };
  requestAnimationFrame(frame);
}

// ------------------------------------------------------- swaps, carried over

function pairs(before, after, selector) {
  const olds = [...before.querySelectorAll(selector)];
  const keyed = new Map(olds.filter((n) => n.dataset.key).map((n) => [n.dataset.key, n]));
  return [...after.querySelectorAll(selector)]
    .map((node, i) => [node.dataset.key ? keyed.get(node.dataset.key) : olds[i], node])
    .filter(([old]) => old);
}

// `before` is the old content (a copy is enough), `after` the new one in the page.
export function carryOver(before, after) {
  if (!before || !after || still()) return;
  for (const [old, now] of pairs(before, after, ".ring-arc")) {
    const from = old.getAttribute("stroke-dasharray");
    const to = now.getAttribute("stroke-dasharray");
    if (from && to && from !== to) {
      now.animate([{ strokeDasharray: from }, { strokeDasharray: to }], { duration: 700, easing: EASE_OUT });
    }
  }
  for (const [old, now] of pairs(before, after, "[data-count]")) {
    const from = parse(old.textContent);
    if (from && old.textContent !== now.textContent) countUp(now, from.value, 600);
  }
}

// View transitions need unique names; the elements to follow carry data-vt.
function nameAll(scope) {
  const seen = new Set();
  scope.querySelectorAll("[data-vt]").forEach((node) => {
    const name = `vt-${node.dataset.vt.replace(/[^\w-]/g, "")}`;
    node.style.viewTransitionName = seen.has(name) ? "none" : name;
    seen.add(name);
  });
}

const typed = typeof ViewTransition !== "undefined" && "types" in ViewTransition.prototype;

// Runs `update` (a synchronous change of the page) as one view transition where the
// browser has them, so what stays slides to its new place and what goes fades.
export function transition(update) {
  if (still() || !document.startViewTransition) {
    update();
    return Promise.resolve();
  }
  nameAll(document);
  const run = () => {
    update();
    nameAll(document);
  };
  const moving = typed
    ? document.startViewTransition({ update: run, types: ["swap"] })
    : document.startViewTransition(run);
  moving.ready.catch(() => {}); // skipped (a name twice, a hidden page): the change is made anyway
  moving.finished.catch(() => {});
  return moving.updateCallbackDone.catch(() => {});
}

// ------------------------------------------------------------- celebrations

function firstTime(key) {
  try {
    const name = `cps:celebrated:${key}`;
    if (localStorage.getItem(name)) return false;
    localStorage.setItem(name, "1");
    return true;
  } catch {
    return false; // without storage, no celebration rather than one on every visit
  }
}

function colours() {
  const style = getComputedStyle(root);
  return ["--c0", "--c1", "--c2", "--c3", "--c4", "--c6", "--accent"]
    .map((name) => style.getPropertyValue(name).trim())
    .filter(Boolean);
}

// A burst of confetti from `node` (or the top of the screen), about a second and a
// half, on a canvas nobody can click or read.
export function celebrate(node) {
  if (still()) return;
  const canvas = document.createElement("canvas");
  canvas.className = "confetti";
  canvas.setAttribute("aria-hidden", "true");
  document.body.append(canvas);
  const ratio = window.devicePixelRatio || 1;
  canvas.width = innerWidth * ratio;
  canvas.height = innerHeight * ratio;
  const pen = canvas.getContext("2d");
  pen.scale(ratio, ratio);
  const box = node && node.getBoundingClientRect();
  const visible = box && box.bottom > 0 && box.top < innerHeight;
  const x = visible ? box.left + box.width / 2 : innerWidth / 2;
  const y = visible ? box.top + box.height / 2 : innerHeight / 3;
  const palette = colours();
  const bits = Array.from({ length: 90 }, (_, i) => {
    const angle = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 0.9;
    const speed = 6 + Math.random() * 7;
    return {
      x,
      y,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      spin: (Math.random() - 0.5) * 0.4,
      turn: Math.random() * Math.PI,
      w: 5 + Math.random() * 4,
      h: 8 + Math.random() * 6,
      colour: palette[i % palette.length] || "#2a78d6",
    };
  });
  const began = performance.now();
  const LIFE = 1600;
  const frame = (now) => {
    const age = now - began;
    pen.clearRect(0, 0, innerWidth, innerHeight);
    pen.globalAlpha = Math.max(0, Math.min(1, (LIFE - age) / 400));
    for (const b of bits) {
      b.vx *= 0.985;
      b.vy = b.vy * 0.985 + 0.32;
      b.x += b.vx;
      b.y += b.vy;
      b.turn += b.spin;
      pen.save();
      pen.translate(b.x, b.y);
      pen.rotate(b.turn);
      pen.scale(1, Math.cos(b.turn * 2)); // the flutter of a piece of paper
      pen.fillStyle = b.colour;
      pen.fillRect(-b.w / 2, -b.h / 2, b.w, b.h);
      pen.restore();
    }
    if (age < LIFE) requestAnimationFrame(frame);
    else canvas.remove();
  };
  requestAnimationFrame(frame);
}

// Every new key is remembered; several at once (milestones reached together) make
// one burst, from the first.
function celebrateIn(scope) {
  const nodes = [...(scope.matches("[data-celebrate]") ? [scope] : []), ...scope.querySelectorAll("[data-celebrate]")];
  const fresh = nodes.filter((node) => firstTime(node.dataset.celebrate));
  if (fresh.length) celebrate(fresh[0]);
}

// ------------------------------------------------------------------ the page

// Numbers count up when they first come into view (a tile further down the page
// counts when it is scrolled to, not unseen at load).
if (!still() && "IntersectionObserver" in window) {
  const watcher = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      watcher.unobserve(entry.target);
      countUp(entry.target);
    }
  });
  document.querySelectorAll("[data-count]").forEach((node) => watcher.observe(node));
}

celebrateIn(document.documentElement);
// Parts of the page replaced later (a report that completes the week) can carry a
// celebration too.
new MutationObserver((changes) => {
  for (const change of changes) {
    change.addedNodes.forEach((node) => {
      if (node.nodeType === 1 && !node.classList.contains("confetti")) celebrateIn(node);
    });
  }
}).observe(document.body, { childList: true, subtree: true });

// Entrance animations are for a page's arrival only.
const settle = () => setTimeout(() => root.classList.add("settled"), 1400);
if (document.readyState === "complete") settle();
else addEventListener("load", settle, { once: true });
