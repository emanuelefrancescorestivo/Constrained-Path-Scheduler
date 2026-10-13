// Reminders on this device (DECISIONS.md D38), in Settings: asks the browser for
// permission to notify, subscribes to its push service with the server's key, and
// hands the subscription to the server; or the reverse. The server decides when a
// reminder is due and what it says; the service worker (sw.js) shows it.
// Without this script, or where the browser has no push, the page says so, and
// the list of devices and the timing still work as forms.
import { t } from "./i18n.js";

const box = document.querySelector("[data-push]");
const apple = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window && window.isSecureContext;

const part = (name) => box.querySelector(`[data-push-${name}]`);
const show = (name, on = true) => { const el = part(name); if (el) el.hidden = !on; };

function say(message) {
  const status = part("status");
  status.textContent = message;
  status.hidden = !message;
}

function bytes(text) {
  const base = text.replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base + "=".repeat((4 - (base.length % 4)) % 4));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function idOf(endpoint) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return Array.from(new Uint8Array(digest).slice(0, 8), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  const answer = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(answer.message || t("That did not work. Try again."));
  return answer;
}

async function start() {
  show("unsupported", false);
  const worker = await navigator.serviceWorker.ready;
  const current = await worker.pushManager.getSubscription();
  const known = (box.dataset.devices || "").split(",").filter(Boolean);
  const here = current && known.includes(await idOf(current.endpoint));
  show("here", Boolean(here));
  show("stop", Boolean(here));
  show("off", !here);
  show("on", !here);

  const on = part("on");
  const stop = part("stop");
  on.addEventListener("click", async () => {
    on.disabled = true;
    say("");
    try {
      if ((await Notification.requestPermission()) !== "granted") {
        say(t("Notifications are blocked for this site: allow them in your browser's settings, then try again."));
        on.disabled = false;
        return;
      }
      let made = current;
      try {
        made = made || (await worker.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes(box.dataset.key) }));
      } catch {
        // The browser's own message is in its words, not the student's: say ours.
        throw new Error(t("This browser could not turn reminders on. Try again, or in another browser."));
      }
      const answer = await post(box.dataset.save, made.toJSON());
      location.assign(answer.go);
    } catch (error) {
      say(error.message || t("That did not work. Try again."));
      on.disabled = false;
    }
  });
  stop.addEventListener("click", async () => {
    stop.disabled = true;
    try {
      const answer = await post(box.dataset.remove, { endpoint: current.endpoint });
      await current.unsubscribe().catch(() => {});
      location.assign(answer.go);
    } catch (error) {
      say(error.message || t("That did not work. Try again."));
      stop.disabled = false;
    }
  });
}

if (box) {
  if (supported) {
    start().catch(() => say(t("That did not work. Try again.")));
  } else if (apple && !standalone) {
    show("unsupported", false);
    show("install");
  }
}
