// The questions about the week, with Spark (D70). Each time can be drawn on a bar
// of the day, midnight to midnight, as well as typed: the sun and the moon are when
// the student gets up and goes to bed, a block is lunch, training, a job. The fields
// stay the form; this script only fills them in, and the bar follows them. While
// setting up, Spark asks one card at a time. Without this script the page is the
// form, every card shown.
import { t } from "./i18n.js";

const SNAP = 15; // minutes
const DAY = 24 * 60;
const toMinutes = (value) => {
  const [h, m] = String(value || "").split(":").map(Number);
  return Number.isFinite(h) ? h * 60 + (m || 0) : null;
};
const toClock = (minutes) => {
  const m = ((Math.round(minutes) % DAY) + DAY) % DAY;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
};
const snap = (minutes) => Math.round(minutes / SNAP) * SNAP;
const clamp = (x, low, high) => Math.min(Math.max(x, low), high);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text) node.textContent = text;
  return node;
}

// The sun and the moon, drawn with the app's strokes.
function icon(kind) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", kind === "wake"
    ? "M12 7.5a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9zM12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M19.1 4.9l-1.8 1.8M6.7 17.3l-1.8 1.8"
    : "M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z");
  svg.append(path);
  return svg;
}

class DayBar {
  constructor(root) {
    this.root = root;
    const form = root.closest("form");
    const field = (name) => form.querySelector(`[name="${name}"]`);
    if (root.dataset.night) [this.wake, this.bed] = root.dataset.night.split(",").map(field);
    if (root.dataset.range) [this.from, this.to] = root.dataset.range.split(",").map(field);
    // The block's name: what the student typed for it, else the example.
    this.named = root.dataset.labelFrom ? field(root.dataset.labelFrom) : null;
    if (this.named) this.named.addEventListener("input", () => this.draw());
    this.track = el("div", "daybar-track");
    const ticks = el("div", "daybar-ticks");
    for (let h = 0; h <= 24; h += 6) {
      const tick = el("span", "", `${String(h).padStart(2, "0")}:00`);
      tick.style.left = `${(100 * h) / 24}%`;
      ticks.append(tick);
    }
    root.append(this.track, ticks);
    root.hidden = false;
    for (const input of [this.wake, this.bed, this.from, this.to]) {
      if (input) input.addEventListener("input", () => this.draw());
    }
    this.track.addEventListener("pointerdown", (event) => this.down(event));
    this.draw();
  }

  at(clientX) {
    const box = this.track.getBoundingClientRect();
    return snap(clamp(((clientX - box.left) / box.width) * DAY, 0, DAY));
  }

  place(node, from, to) {
    node.style.left = `${(100 * from) / DAY}%`;
    node.style.width = `${(100 * (to - from)) / DAY}%`;
  }

  draw() {
    this.track.replaceChildren();
    if (this.wake && this.bed) {
      const up = toMinutes(this.wake.value);
      const down = toMinutes(this.bed.value);
      if (up !== null && down !== null) {
        // The night, shaded: before getting up and after going to bed, or, with a
        // bedtime after midnight, between the two.
        const nights = down > up ? [[0, up], [down, DAY]] : [[down, up]];
        for (const [from, to] of nights) {
          if (to <= from) continue;
          const night = el("div", "daybar-night");
          this.place(night, from, to);
          this.track.append(night);
        }
        for (const [role, minutes] of [["wake", up], ["bed", down]]) {
          const handle = el("div", `daybar-handle ${role}`);
          handle.dataset.role = role;
          handle.style.left = `${(100 * minutes) / DAY}%`;
          handle.append(icon(role));
          this.track.append(handle);
        }
      }
    }
    if (this.from && this.to) {
      const from = toMinutes(this.from.value);
      const to = toMinutes(this.to.value);
      if (from !== null && to !== null && to > from) {
        const block = el("div", "daybar-range");
        block.dataset.role = "range";
        this.place(block, from, to);
        const name = (this.named && this.named.value.trim()) || this.root.dataset.label || "";
        block.append(el("span", "", `${name ? `${name} · ` : ""}${toClock(from)}–${toClock(to)}`));
        block.title = `${name ? `${name} · ` : ""}${toClock(from)}–${toClock(to)}`;
        // Its ends can be dragged when the block is wide enough to hold them and a
        // middle; on a phone an hour is a dozen pixels, and the block only moves.
        const wide = ((to - from) / DAY) * this.track.clientWidth >= 48;
        for (const edge of wide ? ["start", "end"] : []) {
          const grip = el("span", `daybar-edge ${edge}`);
          grip.dataset.role = edge;
          block.append(grip);
        }
        this.track.append(block);
      }
    }
  }

  set(input, minutes) {
    input.value = toClock(minutes);
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  down(event) {
    if (event.button !== 0) return;
    event.preventDefault();
    const role = (event.target.closest("[data-role]") || {}).dataset?.role || "new";
    if (role === "new" && !this.from) return; // the day's bar: the sun, the moon, lunch
    const start = this.at(event.clientX);
    const from0 = toMinutes(this.from && this.from.value);
    const to0 = toMinutes(this.to && this.to.value);
    this.track.setPointerCapture(event.pointerId);
    this.track.classList.add("dragging");
    const move = (e) => {
      const here = this.at(e.clientX);
      if (role === "wake" || role === "bed") this.set(role === "wake" ? this.wake : this.bed, here % DAY);
      else if (role === "range") {
        const length = to0 - from0;
        const from = clamp(from0 + here - start, 0, DAY - SNAP - length);
        this.set(this.from, from);
        this.set(this.to, from + length);
      } else if (role === "start") this.set(this.from, Math.min(here, to0 - SNAP));
      else if (role === "end") this.set(this.to, Math.min(Math.max(here, from0 + SNAP), DAY - SNAP));
      else {
        // Drawn from where the finger went down to where it is.
        this.set(this.from, Math.min(start, here));
        this.set(this.to, Math.min(Math.max(start, here, Math.min(start, here) + SNAP), DAY - SNAP));
      }
      this.draw();
    };
    const up = (e) => {
      this.track.removeEventListener("pointermove", move);
      this.track.removeEventListener("pointerup", up);
      this.track.removeEventListener("pointercancel", up);
      this.track.classList.remove("dragging");
      // A tap on the bar, not a drag: an hour from there.
      if (role === "new" && Math.abs(this.at(e.clientX) - start) < SNAP) {
        const from = Math.min(start, DAY - 60 - SNAP);
        this.set(this.from, from);
        this.set(this.to, from + 60);
        this.draw();
      }
    };
    this.track.addEventListener("pointermove", move);
    this.track.addEventListener("pointerup", up);
    this.track.addEventListener("pointercancel", up);
  }
}

document.querySelectorAll(".daybar").forEach((root) => new DayBar(root));

// -- Spark asks one card at a time, while setting up ---------------------------

const form = document.querySelector("form.routine");
const guide = document.querySelector(".routine-guide");
const cards = form ? [...form.querySelectorAll("section.question")] : [];
// After an error the page shows every card, with what was typed and what is wrong.
if (form && guide && cards.length && form.querySelector('input[name="new"]') && !document.querySelector(".alert")) {
  const actions = form.querySelector(".sticky-actions");
  const says = guide.querySelector(".guide-says");
  const counter = guide.querySelector(".guide-step");
  const spark = guide.querySelector("svg.mascot");
  let step = 0;
  const show = (index) => {
    step = index;
    cards.forEach((card, i) => (card.hidden = i !== index));
    const card = cards[index];
    says.textContent = card.dataset.say || "";
    counter.textContent = t("Question {n} of {total}", { n: index + 1, total: cards.length });
    spark.setAttribute("class", `mascot is-${index === cards.length - 1 ? "cheer" : card.dataset.mood || "think"}`);
    actions.hidden = index !== cards.length - 1;
    window.scrollTo({ top: 0 });
  };
  cards.forEach((card, i) => {
    const nav = el("div", "guide-nav");
    if (i > 0) {
      const back = el("button", "btn btn-plain", t("Back"));
      back.type = "button";
      back.addEventListener("click", () => show(i - 1));
      nav.append(back);
    }
    if (i < cards.length - 1) {
      const next = el("button", "btn btn-primary", t("Next"));
      next.type = "button";
      next.addEventListener("click", () => {
        // A card's own fields are checked before moving on, as the form would.
        const wrong = [...card.querySelectorAll("input")].find((input) => !input.checkValidity());
        if (wrong) wrong.reportValidity();
        else show(i + 1);
      });
      nav.append(next);
    }
    card.append(nav);
  });
  guide.hidden = false;
  form.classList.add("guided");
  show(0);
}
