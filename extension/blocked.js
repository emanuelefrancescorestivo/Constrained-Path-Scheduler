// The calm page: what is paused, until when, and the way back to the session.
for (const node of document.querySelectorAll("[data-i18n]")) {
  const words = chrome.i18n.getMessage(node.dataset.i18n);
  if (words) node.textContent = words;
}
chrome.storage.local.get(["until", "back"]).then(({ until, back }) => {
  if (until) {
    const time = new Date(until).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const line = document.getElementById("until");
    line.textContent = chrome.i18n.getMessage("blockedUntil", [time]);
    line.hidden = false;
  }
  if (back && /^https?:\/\//.test(back)) {
    const link = document.getElementById("back");
    link.href = back;
    link.hidden = false;
  }
});
