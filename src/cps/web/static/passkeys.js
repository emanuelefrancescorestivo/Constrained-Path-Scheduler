// Passkeys (DECISIONS.md D35): adding one in Settings, signing in with one on the
// home page. The server makes every decision (the options, the checks); this script
// only hands the browser's answer back. Buttons marked data-passkey-... stay hidden
// without it, or where the browser has no passkeys; the recovery code works without
// any script.
import { t } from "./i18n.js";

const supported = typeof window.PublicKeyCredential === "function" && navigator.credentials;

const bytes = (text) => {
  const base = text.replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base + "=".repeat((4 - (base.length % 4)) % 4));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
};
const text = (buffer) => {
  if (!buffer) return null;
  let raw = "";
  for (const b of new Uint8Array(buffer)) raw += String.fromCharCode(b);
  return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
};

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body === undefined ? "{}" : JSON.stringify(body),
  });
  const answer = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(answer.message || t("That did not work. Try again."));
  return answer;
}

function say(status, message) {
  if (!status) return;
  status.textContent = message;
  status.hidden = !message;
}

// A cancelled or timed-out prompt is the student's choice, not an error to explain.
const cancelled = (error) => error && (error.name === "NotAllowedError" || error.name === "AbortError");

async function add(button) {
  const status = document.querySelector("[data-passkey-status]");
  button.disabled = true;
  say(status, "");
  try {
    const options = await post(button.dataset.passkeyAdd);
    options.challenge = bytes(options.challenge);
    options.user.id = bytes(options.user.id);
    options.excludeCredentials = options.excludeCredentials.map((c) => ({ ...c, id: bytes(c.id) }));
    const made = await navigator.credentials.create({ publicKey: options });
    const answer = await post(button.dataset.passkeySave, {
      id: made.id,
      type: made.type,
      response: {
        clientDataJSON: text(made.response.clientDataJSON),
        attestationObject: text(made.response.attestationObject),
      },
    });
    location.assign(answer.go);
  } catch (error) {
    if (error && error.name === "InvalidStateError") say(status, t("This device already has a passkey for your plan."));
    else if (!cancelled(error)) say(status, error.message || t("That did not work. Try again."));
    button.disabled = false;
  }
}

async function signIn(button) {
  const status = document.querySelector("[data-passkey-status]");
  button.disabled = true;
  say(status, "");
  try {
    const options = await post("/signin/options");
    options.challenge = bytes(options.challenge);
    const got = await navigator.credentials.get({ publicKey: options });
    const answer = await post("/signin", {
      id: got.id,
      type: got.type,
      response: {
        clientDataJSON: text(got.response.clientDataJSON),
        authenticatorData: text(got.response.authenticatorData),
        signature: text(got.response.signature),
        userHandle: text(got.response.userHandle),
      },
    });
    location.assign(answer.go);
  } catch (error) {
    if (!cancelled(error)) say(status, error.message || t("That did not work. Try again."));
    button.disabled = false;
  }
}

if (supported) {
  document.querySelectorAll("[data-passkey-add], [data-passkey-signin]").forEach((b) => { b.hidden = false; });
  document.querySelectorAll("[data-passkey-unsupported]").forEach((p) => { p.hidden = true; });
  document.addEventListener("click", (event) => {
    const adding = event.target.closest("[data-passkey-add]");
    if (adding) add(adding);
    const signing = event.target.closest("[data-passkey-signin]");
    if (signing) signIn(signing);
  });
}
