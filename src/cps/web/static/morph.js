// The explaining pages' pictures that turn into the real thing (D122). The owner chose, for
// the science page and the code page, "one picture that turns into the other,
// everywhere": each figure is first a picture of the page's story (a new city's streets,
// a map's roads) and becomes the real chart as its scene is scrolled through.
//
// Every element of such a figure (`.mo-figure`) is drawn by the server in its real state,
// which is what a page without this script shows; its `data-k` holds keyframes over the
// scene, [progress, {attribute: value}] in order, the first the story's and the last the
// real one. This script only reads between them, as the page is scrolled: numbers are
// eased from one keyframe to the next, and a list of numbers (a line's points) number by
// number. Layout only: every value is the server's, and it sets attributes, never a style.
//
// Where the browser does not tie animations to scrolling, or with reduced motion, nothing
// moves: the figure shows the real thing, and two buttons switch it to the story's
// picture and back at once.

const root = document.documentElement;
const figures = [...document.querySelectorAll(".mo-figure")];
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches || root.dataset.motion === "reduce";
const timelines = typeof CSS !== "undefined" && CSS.supports && CSS.supports("animation-timeline: view()");
const moving = !reduce && timelines;
const NUMBER = /-?\d+(?:\.\d+)?/g;

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const ease = (t) => t * t * (3 - 2 * t);

let topbar = 0;
const readTopbar = () => {
  topbar = parseFloat(getComputedStyle(root).getPropertyValue("--topbar")) || 0;
};

// A value between two: numbers eased, a string of numbers number by number (its other
// characters kept), anything else switched half-way.
function between(a, b, t) {
  if (typeof a === "number" && typeof b === "number") return +(a + (b - a) * t).toFixed(2);
  const sa = String(a), sb = String(b);
  const na = sa.match(NUMBER), nb = sb.match(NUMBER);
  if (na && nb && na.length === nb.length) {
    let i = 0;
    return sb.replace(NUMBER, (v) => {
      const from = +na[i++];
      return String(+(from + (+v - from) * t).toFixed(1));
    });
  }
  return t < 0.5 ? sa : sb;
}

class Figure {
  constructor(el) {
    this.el = el;
    this.pin = el.closest(".code-pin");
    this.story = el.closest(".code-story");
    this.parts = [...el.querySelectorAll("[data-k]")].map((n) => ({ n, k: JSON.parse(n.dataset.k), last: "" }));
    this.mode = 1;
    this.drawn = -1;
  }

  // Where the page is in this figure's scene, 0 to 1, as the stylesheet's --pin
  // timeline is: the pinned stage's track, or on a laptop the whole story's (D120).
  progress() {
    if (!moving) return this.mode;
    const track = this.pin && getComputedStyle(this.pin).display === "contents" ? this.story : this.pin;
    if (!track) return 1;
    const r = track.getBoundingClientRect();
    return clamp((topbar - r.top) / Math.max(1, r.height - (innerHeight - topbar)));
  }

  draw() {
    const p = this.progress();
    if (Math.abs(p - this.drawn) < 0.001) return;
    this.drawn = p;
    for (const part of this.parts) {
      const k = part.k;
      let i = 0;
      while (i < k.length - 1 && k[i + 1][0] <= p) i++;
      const [p0, a] = k[i];
      const [p1, b] = k[Math.min(i + 1, k.length - 1)];
      const t = p1 > p0 ? ease(clamp((p - p0) / (p1 - p0))) : 1;
      for (const name of Object.keys(b)) {
        const value = String(between(a[name], b[name], t));
        if (part.n.getAttribute(name) !== value) part.n.setAttribute(name, value);
      }
    }
  }

  // Without motion: the real thing, and two buttons to see the story's picture.
  offer() {
    const bar = this.el.querySelector(".mo-toggle");
    if (!bar) return;
    bar.hidden = false;
    for (const button of bar.querySelectorAll("button[data-to]")) {
      button.addEventListener("click", () => {
        this.mode = Number(button.dataset.to);
        for (const b of bar.querySelectorAll("button[data-to]")) b.setAttribute("aria-pressed", String(b === button));
        this.draw();
      });
    }
  }
}

function main() {
  if (!figures.length) return;
  readTopbar();
  const all = figures.map((el) => new Figure(el));
  if (!moving) {
    for (const f of all) f.offer();
    return;
  }
  const seen = new Set();
  let frame = 0;
  const tick = () => {
    frame = 0;
    for (const f of seen) f.draw();
  };
  const wake = () => {
    if (!frame) frame = requestAnimationFrame(tick);
  };
  const watch = new IntersectionObserver((entries) => {
    for (const e of entries) {
      const f = all[figures.indexOf(e.target)];
      if (e.isIntersecting) seen.add(f);
      else seen.delete(f);
    }
    wake();
  }, { rootMargin: "200px 0px" });
  for (const el of figures) watch.observe(el);
  // Every figure in its place at once, wherever the page opens.
  for (const f of all) f.draw();
  addEventListener("scroll", wake, { passive: true });
  addEventListener("resize", () => {
    readTopbar();
    for (const f of all) f.drawn = -1;
    wake();
  });
}

main();
