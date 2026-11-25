// D195: the to-do page as a document. The new line empties and keeps the focus once it is
// added (the list itself is put in place by ui.js); Tab makes it a step of the line above;
// Enter in a line saves it (data-autosave, on change) and goes to the new line. Without this
// script every form is sent as it is, and the page comes back.
const line = document.getElementById("todo-line");
const form = document.getElementById("todo-new");
if (line && form) {
  let sent = false;
  form.addEventListener("submit", () => (sent = true), true);
  document.addEventListener("cps:changed", () => {
    if (!sent) return;
    sent = false;
    line.value = "";
    line.focus();
  });
  line.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    event.preventDefault();
    line.value = line.value.startsWith("- ") ? line.value.slice(2) : `- ${line.value}`;
  });
}
document.addEventListener("keydown", (event) => {
  const field = event.target.closest && event.target.closest("input[data-line]");
  if (!field || event.key !== "Enter") return;
  event.preventDefault();
  field.blur(); // the change saves it
  if (line) line.focus();
});
