// D198: on a page of the study plan, read until when a focus session runs (the page says it in
// `data-focus-until` on <body>, empty when none) and tell the extension. Nothing else is read:
// not the plan, not the page's words, not its address beyond this site's origin.
const until = document.body && document.body.dataset.focusUntil;
if (until !== undefined) {
  chrome.runtime.sendMessage({ kind: "focus", until, back: `${location.origin}${location.pathname.split("/").slice(0, 3).join("/")}/focus` });
}
