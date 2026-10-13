// The page's own errors, for the owner's error log (DECISIONS.md D43): the kind,
// the message, the script and line, and the page. Never what the student typed;
// the server cuts the plan's address out of all of it. At most five a page.
//
// Loaded first on every page, so that it hears the errors of the scripts after it.

let sent = 0;

function report(kind, message, file, line, column, stack) {
  if (sent >= 5) return;
  sent += 1;
  const local = (text) => String(text || "").split(location.origin).join("");
  const body = JSON.stringify({
    kind: String(kind || "Error").slice(0, 80),
    message: String(message || "").slice(0, 300),
    file: local(file).slice(0, 120),
    line: Number(line) || 0,
    column: Number(column) || 0,
    stack: local(stack).slice(0, 1200),
    page: location.pathname,
  });
  try {
    if (!navigator.sendBeacon("/errors", new Blob([body], { type: "application/json" }))) throw new Error("not queued");
  } catch {
    fetch("/errors", { method: "POST", body, headers: { "Content-Type": "application/json" }, keepalive: true }).catch(() => {});
  }
}

addEventListener("error", (event) => {
  if (!event.message && !event.error) return;
  report(event.error?.name, event.message, event.filename, event.lineno, event.colno, event.error?.stack);
});

addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  report(reason?.name || "Unhandled rejection", reason?.message || String(reason), "", 0, 0, reason?.stack);
});
