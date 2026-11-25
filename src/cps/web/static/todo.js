// D195: the to-do page as a document. The new line empties and keeps the focus once it is
// added (the list itself is put in place by ui.js); Tab makes it a step of the line above;
// Enter in a line saves it (data-autosave, on change) and goes to the new line. Without this
// script every form is sent as it is, and the page comes back.
//
// D202: "/" at the start of the new line offers the kinds (read from the line of keys under
// it, so the words are the page's own); Ctrl+Enter ticks the line being edited; Ctrl+B and
// Ctrl+I wrap the selection in ** and *; a line is dragged by its grip to another place, and
// a card on the board to another day's column. Each change is a form sent as ui.js sends
// them, so the page is put in place the same way.
const line = document.getElementById("todo-line");
const form = document.getElementById("todo-new");

function send(action, fields) {
  // A form like the page's own, sent in the background by ui.js.
  const f = document.createElement("form");
  f.method = "post";
  f.action = action;
  f.dataset.async = "";
  f.dataset.quiet = "";
  f.dataset.region = "#todo, #todo-count, #todo-side";
  f.hidden = true;
  for (const [name, value] of Object.entries(fields)) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    f.appendChild(input);
  }
  document.body.appendChild(f);
  f.requestSubmit();
  setTimeout(() => f.remove(), 4000);
}

if (line && form) {
  let sent = false;
  form.addEventListener("submit", () => (sent = true), true);
  document.addEventListener("cps:changed", () => {
    if (!sent) return;
    sent = false;
    line.value = "";
    line.focus();
  });

  // The "/" menu: one button per kind, from the keys the page lists.
  const menu = document.createElement("div");
  menu.className = "todo-slash";
  menu.hidden = true;
  menu.setAttribute("role", "listbox");
  for (const key of document.querySelectorAll("#todo-keys > span")) {
    const kbd = key.querySelector("kbd");
    const mark = kbd ? kbd.textContent.trim() : "";
    const prefix = { "#": "# ", ">": "> ", "?": "? ", "-": "- " }[mark];
    if (!prefix) continue;
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("role", "option");
    b.innerHTML = "";
    const k = document.createElement("kbd");
    k.textContent = mark;
    b.append(k, document.createTextNode(" " + key.textContent.replace(mark, "").trim()));
    b.addEventListener("click", () => {
      line.value = prefix;
      menu.hidden = true;
      line.focus();
    });
    menu.appendChild(b);
  }
  form.after(menu);
  line.addEventListener("input", () => {
    menu.hidden = line.value !== "/";
  });
  line.addEventListener("blur", () => setTimeout(() => (menu.hidden = true), 150));

  line.addEventListener("keydown", (event) => {
    if (event.key === "Escape") menu.hidden = true;
    if (event.key === "ArrowDown" && !menu.hidden) {
      event.preventDefault();
      menu.querySelector("button")?.focus();
    }
    if (event.key !== "Tab") return;
    event.preventDefault();
    line.value = line.value.startsWith("- ") ? line.value.slice(2) : `- ${line.value}`;
  });
}

document.addEventListener("keydown", (event) => {
  const field = event.target.closest && event.target.closest("input[data-line], #todo-line");
  if (!field) return;
  const mod = event.ctrlKey || event.metaKey;
  // Ctrl+B, Ctrl+I: the selection between ** or *.
  if (mod && (event.key === "b" || event.key === "i")) {
    event.preventDefault();
    const mark = event.key === "b" ? "**" : "*";
    const [a, z] = [field.selectionStart, field.selectionEnd];
    field.value = field.value.slice(0, a) + mark + field.value.slice(a, z) + mark + field.value.slice(z);
    field.setSelectionRange(a + mark.length, z + mark.length);
    return;
  }
  if (field.id === "todo-line" || event.key !== "Enter") return;
  event.preventDefault();
  if (mod) {
    // Ctrl+Enter: the line done.
    const row = field.closest(".todo-row");
    row?.querySelector(".todo-tick button")?.click();
    return;
  }
  field.blur(); // the change saves it
  if (line) line.focus();
});

// Dragging: a line by its grip to another place; a card on the board to another column.
let dragged = null;
document.addEventListener("pointerdown", (event) => {
  const grip = event.target.closest && event.target.closest(".todo-grip");
  if (grip) grip.closest(".todo-row").draggable = true;
});
document.addEventListener("dragstart", (event) => {
  const item = event.target.closest && event.target.closest(".todo-row[data-pos], .todo-cardrow");
  if (!item) return;
  dragged = item;
  item.classList.add("is-dragging");
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", item.dataset.id);
});
document.addEventListener("dragend", () => {
  if (dragged) {
    dragged.classList.remove("is-dragging");
    if (dragged.classList.contains("todo-row")) dragged.draggable = false;
  }
  dragged = null;
  document.querySelectorAll(".is-drop-above, .todo-col.is-over").forEach((n) => n.classList.remove("is-drop-above", "is-over"));
});
document.addEventListener("dragover", (event) => {
  if (!dragged) return;
  const target = dragged.classList.contains("todo-row")
    ? event.target.closest && event.target.closest(".todo-row[data-pos]")
    : event.target.closest && event.target.closest(".todo-col[data-drop]");
  if (!target || target === dragged) return;
  if (target.classList.contains("todo-col") && !target.dataset.drop) return;
  event.preventDefault();
  document.querySelectorAll(".is-drop-above, .todo-col.is-over").forEach((n) => n.classList.remove("is-drop-above", "is-over"));
  target.classList.add(target.classList.contains("todo-col") ? "is-over" : "is-drop-above");
});
document.addEventListener("drop", (event) => {
  if (!dragged) return;
  const sheet = document.getElementById("todo");
  const token = sheet && sheet.dataset.token;
  const row = event.target.closest && event.target.closest(".todo-row[data-pos]");
  const col = event.target.closest && event.target.closest(".todo-col[data-drop]");
  event.preventDefault();
  if (dragged.classList.contains("todo-row") && row && row !== dragged) {
    const from = Number(dragged.dataset.pos);
    const at = Number(row.dataset.pos);
    // Before the line it is dropped on.
    send(`/p/${token}/todo/${dragged.dataset.id}/change`, { how: "to", to: String(from < at ? at - 1 : at) });
  } else if (dragged.classList.contains("todo-cardrow") && col && col.dataset.drop) {
    send(`/p/${token}/todo/${dragged.dataset.id}/day`, { day: col.dataset.drop, back: "board" });
  }
});
document.addEventListener("pointerdown", (event) => {
  const card = event.target.closest && event.target.closest(".todo-cardrow");
  if (card && !event.target.closest("button, a, input")) card.draggable = true;
});
