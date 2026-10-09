// The review screen's keys (DECISIONS.md D29): space or Enter shows the answer, 1 to 4
// answer. Without this script the page works with taps: the answer is a <details>
// and each answer a submit button.
const answer = document.getElementById("flash-answer");

document.addEventListener("keydown", (event) => {
  if (!answer || event.altKey || event.ctrlKey || event.metaKey) return;
  if (event.target.closest("input, textarea, select")) return;
  if (!answer.open && (event.key === " " || event.key === "Enter")) {
    event.preventDefault();
    answer.open = true;
    return;
  }
  const button = answer.open && answer.querySelector(`[data-key="${event.key}"]`);
  if (button) {
    event.preventDefault();
    button.click();
  }
});
