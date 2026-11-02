// An invitation link (DECISIONS.md D40), or a class's link (D104): if this device has opened a plan before,
// its address is remembered on the device (pwa.js), and the page offers to join
// with it. The server only learns which plan joins when the student presses Join
// on their own plan's page.
const box = document.querySelector("[data-invite]");
const button = document.querySelector("[data-join-mine]");
let plan = null;
try {
  plan = localStorage.getItem("cps:plan");
} catch {
  /* storage blocked: the page says how to join from the plan */
}
if (box && button && plan && /^[A-Za-z0-9_-]{22,64}$/.test(plan)) {
  // A group's invitation joins it; a class's link (D104) takes its program.
  const path = box.dataset.joinPath === "class" ? "class" : "join";
  button.href = `/p/${plan}/${path}/${box.dataset.invite}`;
  button.hidden = false;
}
