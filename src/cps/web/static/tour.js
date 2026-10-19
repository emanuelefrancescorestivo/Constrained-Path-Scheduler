// Spark's tour of a section (DECISIONS.md D77), the first time it is opened. The page
// sends the steps (what each lights, what Spark says) only while the tour has not been
// seen; this script dims the page, lights each step's part of it and puts Spark's
// bubble beside it, then tells the server once the tour is finished or skipped. A step
// whose part is not on screen (a phone has no top bar's links, a new plan nothing to
// report) is passed over. Layout and input only: the server decides which tour, and
// what it says.

const root = document.getElementById("tour");
const PAD = 6; // px of light around the part shown
const GAP = 14; // px between it and the bubble
const MARGIN = 12; // px kept free at the window's edges

const visible = (node) => {
  const box = node.getBoundingClientRect();
  return box.width > 0 && box.height > 0 && getComputedStyle(node).visibility !== "hidden";
};
const find = (selector) => [...document.querySelectorAll(selector)].find((node) => !root.contains(node) && visible(node)) || null;
const still = () =>
  document.documentElement.dataset.motion === "reduce" || matchMedia("(prefers-reduced-motion: reduce)").matches;

if (root) start(root);

function start(root) {
  const steps = [...root.querySelectorAll(".tour-steps > li")].map((li) => ({
    target: li.dataset.target,
    mood: li.dataset.mood || "idle",
    title: li.querySelector("b").textContent,
    text: li.querySelector("span").textContent,
  }));
  const hole = root.querySelector(".tour-hole");
  const bubble = root.querySelector(".tour-bubble");
  const spark = bubble.querySelector(".mascot");
  const title = root.querySelector("#tour-title");
  const text = root.querySelector("#tour-text");
  const count = root.querySelector(".tour-count");
  const back = root.querySelector("[data-tour-back]");
  const next = root.querySelector("[data-tour-next]");
  const nextWord = next.textContent;
  let index = -1;
  let shown = []; // the steps whose part is on screen, worked out when the tour opens
  let target = null;
  let frame = 0;
  let glide = 0;
  let before = null; // what had the focus, given back at the end
  const quieted = [];

  // The calendar and the panel are drawn after the page loads: wait for them.
  const open = () => {
    shown = steps.filter((step) => find(step.target));
    if (!shown.length) return; // nothing to show here (yet): the tour stays unseen
    before = document.activeElement;
    for (const node of document.body.children) {
      if (node !== root && !node.inert) {
        node.inert = true; // the rest of the page waits while Spark speaks
        quieted.push(node);
      }
    }
    root.hidden = false;
    document.addEventListener("keydown", keys);
    go(0);
    frame = requestAnimationFrame(follow);
  };

  const go = (i) => {
    index = i;
    const step = shown[index];
    target = find(step.target);
    if (!target) {
      // Gone since the tour opened (the panel redrawn): the next one, or the end.
      return index + 1 < shown.length ? go(index + 1) : finish();
    }
    target.scrollIntoView({ block: "center", inline: "nearest", behavior: still() ? "auto" : "smooth" });
    spark.setAttribute("class", `mascot is-${step.mood}`);
    title.textContent = step.title;
    text.textContent = step.text;
    count.textContent = shown.length > 1 ? root.dataset.count.replace("{n}", index + 1).replace("{total}", shown.length) : "";
    back.hidden = index === 0;
    next.textContent = index === shown.length - 1 ? next.dataset.last : nextWord;
    root.classList.remove("moved");
    void root.offsetWidth; // restart the bubble's small entrance
    root.classList.add("moved", "gliding");
    clearTimeout(glide);
    glide = setTimeout(() => root.classList.remove("gliding"), 420);
    next.focus({ preventScroll: true });
  };

  // Every frame while open: the part may move (scrolling, the window resized, the
  // calendar drawn late), so the light and the bubble follow it.
  const follow = () => {
    if (target && target.isConnected) place(target.getBoundingClientRect());
    frame = requestAnimationFrame(follow);
  };

  const place = (box) => {
    const width = innerWidth;
    const height = innerHeight;
    const left = Math.max(box.left - PAD, 4);
    const top = Math.max(box.top - PAD, 4);
    const right = Math.min(box.right + PAD, width - 4);
    const bottom = Math.min(box.bottom + PAD, height - 4);
    const radius = parseFloat(getComputedStyle(target).borderTopLeftRadius) || 0;
    Object.assign(hole.style, {
      left: `${left}px`,
      top: `${top}px`,
      width: `${Math.max(right - left, 0)}px`,
      height: `${Math.max(bottom - top, 0)}px`,
      borderRadius: `${Math.min(radius + PAD, 28)}px`,
    });
    const bw = bubble.offsetWidth;
    const bh = bubble.offsetHeight;
    let y;
    if (height - bottom >= bh + GAP + MARGIN) y = bottom + GAP; // below
    else if (top >= bh + GAP + MARGIN) y = top - GAP - bh; // above
    else y = height - bh - MARGIN; // the part fills the screen: at its foot, over it
    let x = (left + right) / 2 - bw / 2;
    x = Math.min(Math.max(x, MARGIN), width - bw - MARGIN);
    bubble.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  };

  const finish = () => {
    cancelAnimationFrame(frame);
    document.removeEventListener("keydown", keys);
    root.hidden = true;
    for (const node of quieted) node.inert = false;
    if (before && before.focus) before.focus({ preventScroll: true });
    const body = new FormData();
    body.set("name", root.dataset.name);
    // keepalive: the answer may arrive after the student has moved to another page.
    fetch(`/p/${document.body.dataset.plan}/tour`, { method: "POST", body, keepalive: true }).catch(() => {});
  };

  const keys = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      finish();
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      next.click();
    } else if (event.key === "ArrowLeft" && index > 0) {
      event.preventDefault();
      go(index - 1);
    } else if (event.key === "Tab") {
      // Keep the focus on the bubble's buttons.
      const buttons = [...bubble.querySelectorAll("button")].filter((b) => !b.hidden);
      const at = buttons.indexOf(document.activeElement);
      event.preventDefault();
      buttons[(at + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length].focus();
    }
  };

  next.addEventListener("click", () => (index + 1 < shown.length ? go(index + 1) : finish()));
  back.addEventListener("click", () => index > 0 && go(index - 1));
  root.querySelector("[data-tour-skip]").addEventListener("click", finish);

  const begin = () => setTimeout(open, 700);
  if (document.readyState === "complete") begin();
  else addEventListener("load", begin, { once: true });
}
