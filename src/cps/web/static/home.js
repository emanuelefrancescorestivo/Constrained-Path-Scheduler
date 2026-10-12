// The start page. The time zone fields take the device's own, so that a student
// in Tokyo or New York is not planned on Paris time; without this script they keep
// their default, which can be changed by hand. The file field says which file was
// chosen, and shows when one is dragged over it (the drop itself is the browser's).
for (const field of document.querySelectorAll("[data-tz]")) {
  const summary = field.closest("details") && field.closest("details").querySelector("summary");
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (zone && !field.dataset.chosen) {
      field.value = zone;
      if (summary) summary.textContent = summary.textContent.replace(/:\s.*$/, `: ${zone}`);
    }
  } catch {
    /* an old browser: the default stays */
  }
}

const drop = document.querySelector(".drop");
if (drop) {
  const input = drop.querySelector("input[type=file]");
  const name = drop.querySelector(".drop-name");
  const show = () => {
    const file = input.files && input.files[0];
    name.textContent = file ? file.name : "";
    drop.classList.toggle("chosen", Boolean(file));
  };
  input.addEventListener("change", show);
  for (const type of ["dragenter", "dragover"]) input.addEventListener(type, () => drop.classList.add("over"));
  for (const type of ["dragleave", "drop"]) input.addEventListener(type, () => drop.classList.remove("over"));
  show();
}
