// The app on a phone's home screen (DECISIONS.md D33), on every page:
// - registers the service worker (sw.js), which keeps the pages for offline use;
// - says when the network is gone, and that the page shown is the last copy;
// - offers to install the app: the browser's own prompt where there is one
//   (Chrome, Edge, Android), else, on an iPhone or iPad, how to do it from Safari's
//   Share menu. Elements marked data-install... stay hidden without this script;
//   the settings page says in words how to install from the browser's menu.
// - after "Delete my plan", asks the worker to forget that plan's copies;
// - remembers, on this device only, the plan last opened, so that an invitation
//   link can offer to join with it (D40); forgets it with the plan;
// - asks for the graphite icon to install when the device is in dark mode (D74).
import { t } from "./i18n.js";

const root = document.documentElement;
const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const apple = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

try {
  if (document.body.dataset.plan) localStorage.setItem("cps:plan", document.body.dataset.plan);
  const gone = document.body.dataset.forget;
  if (gone && localStorage.getItem("cps:plan") === gone) localStorage.removeItem("cps:plan");
} catch {
  /* storage blocked: an invitation link then says how to join from the plan */
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register(`/sw.js?lang=${encodeURIComponent(root.lang || "en")}`).catch(() => {
    /* a private window, an old browser: the site works as a site */
  });
  const forget = document.body.dataset.forget;
  if (forget) {
    navigator.serviceWorker.ready.then((worker) => worker.active && worker.active.postMessage({ forget })).catch(() => {});
  }
}

// -- offline ---------------------------------------------------------------------

let banner = null;
function showOffline() {
  root.classList.add("offline");
  if (banner) return;
  banner = document.createElement("p");
  banner.className = "offline-banner";
  banner.setAttribute("role", "status");
  banner.textContent = t("You are offline. This is your plan as it was when you last opened it; changes wait for the network.");
  document.body.prepend(banner);
}
function showOnline() {
  root.classList.remove("offline");
  if (banner) banner.remove();
  banner = null;
}
addEventListener("offline", showOffline);
addEventListener("online", showOnline);
if (!navigator.onLine) showOffline();

// -- install ---------------------------------------------------------------------

const DISMISSED = "cps:install-dismissed";
const dismissed = () => {
  try {
    return Boolean(localStorage.getItem(DISMISSED));
  } catch {
    return false;
  }
};
let prompt = null;

function reveal() {
  if (standalone) return;
  document.querySelectorAll("[data-install]").forEach((b) => { b.hidden = !prompt; });
  document.querySelectorAll("[data-install-ios]").forEach((p) => { p.hidden = !(apple && !prompt); });
  const card = document.querySelector("[data-install-card]");
  if (card) card.hidden = dismissed() || !(prompt || apple);
}

addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault(); // shown when the student asks, not by the browser at once
  prompt = event;
  reveal();
});
addEventListener("appinstalled", () => {
  prompt = null;
  document.querySelectorAll("[data-install-card]").forEach((c) => { c.hidden = true; });
});

document.addEventListener("click", async (event) => {
  if (event.target.closest("[data-install]") && prompt) {
    prompt.prompt();
    await prompt.userChoice.catch(() => null);
    prompt = null;
    reveal();
  } else if (event.target.closest("[data-install-dismiss]")) {
    try {
      localStorage.setItem(DISMISSED, "1");
    } catch {
      /* not kept: it shows again next time */
    }
    const card = document.querySelector("[data-install-card]");
    if (card) card.hidden = true;
  }
});

if (standalone) root.classList.add("standalone");
reveal();

// -- the icon a phone installs (D74) -----------------------------------------------
// White, or graphite in dark mode. A plan set to Light or Dark has its own in the
// page already; a page that follows the device asks for the graphite one here while
// the device is dark, so that it is the one installed. Once installed it stays.
if (!root.dataset.theme) {
  const darkMode = window.matchMedia("(prefers-color-scheme: dark)");
  const follow = () => {
    const manifest = document.querySelector('link[rel="manifest"]');
    if (manifest) {
      const url = new URL(manifest.href);
      if (darkMode.matches) url.searchParams.set("look", "dark");
      else url.searchParams.delete("look");
      manifest.href = url.pathname + url.search;
    }
    const touch = document.querySelector('link[rel="apple-touch-icon"]');
    if (touch) touch.href = `/static/icons/apple-touch-icon${darkMode.matches ? "-dark" : ""}.png`;
  };
  follow();
  darkMode.addEventListener("change", follow);
}
