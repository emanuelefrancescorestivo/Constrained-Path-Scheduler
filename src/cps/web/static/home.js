// The start page: the time zone field takes the device's own, so that a student
// in Tokyo or New York is not planned on Paris time. Without this script the field
// keeps its default and can be changed by hand.
const field = document.getElementById("tz");
const summary = field && field.closest("details") && field.closest("details").querySelector("summary");
try {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (field && zone && !field.dataset.chosen) {
    field.value = zone;
    if (summary) summary.textContent = summary.textContent.replace(/:\s.*$/, `: ${zone}`);
  }
} catch {
  /* an old browser: the default stays */
}
