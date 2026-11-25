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

// D193: light or dark from the top bar's switch, in place: the page, its pictures, the
// demo's choice and the links that carry it change without a reload, so the reader stays
// where they are in the story. Without this script, the link reloads the page in that mode.
for (const flip of document.querySelectorAll(".mode-flip[data-mode]")) {
  flip.addEventListener("click", (event) => {
    event.preventDefault();
    const mode = flip.dataset.mode;
    document.documentElement.dataset.theme = mode;
    for (const source of document.querySelectorAll("source[data-dark]")) {
      source.media = mode === "dark" ? "all" : "not all";
    }
    for (const link of document.querySelectorAll("a[data-front-link]")) {
      const url = new URL(link.href);
      url.searchParams.set("theme", mode);
      link.href = url.href;
    }
    for (const form of document.querySelectorAll('form[action^="/demo"]')) {
      let field = form.querySelector("input[data-front-mode]");
      if (!field) {
        field = Object.assign(document.createElement("input"), { type: "hidden", name: "theme" });
        field.dataset.frontMode = "";
        form.prepend(field);
      }
      field.value = mode;
    }
    const here = new URL(window.location.href);
    here.searchParams.set("theme", mode);
    window.history.replaceState(window.history.state, "", here.href);
    const other = document.querySelector(`.mode-flip:not([data-mode="${mode}"])`);
    if (other) other.focus({ preventScroll: true });
  });
}
