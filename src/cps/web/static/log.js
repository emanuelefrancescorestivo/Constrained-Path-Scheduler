// The log form: photos made smaller and re-encoded in the browser before they are
// sent (DECISIONS.md D18). Drawing a photo on a canvas and saving it as JPEG keeps
// the picture and drops its metadata, the phone's GPS position among it. Without
// this script the form still sends the files, and the server strips metadata.
import { t } from "./i18n.js";
import { toast } from "./ui.js";

const form = document.getElementById("log-form");
const MAX_SIDE = 1600;

async function shrink(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
}

form.addEventListener("submit", async (event) => {
  const input = form.querySelector('input[type="file"]');
  if (!input.files.length || !window.createImageBitmap) return; // the plain form does it
  event.preventDefault();
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    const data = new FormData(form);
    data.delete("photos");
    for (const file of [...input.files].slice(0, 4)) {
      data.append("photos", await shrink(file), "photo.jpg");
    }
    const answer = await fetch(form.action, { method: "POST", body: data });
    if (answer.redirected) {
      window.location.assign(answer.url);
      return;
    }
    const page = new DOMParser().parseFromString(await answer.text(), "text/html");
    const alert = page.querySelector(".alert");
    toast(alert ? alert.textContent.trim() : t("That did not work. Try again."), { bad: true });
  } catch {
    toast(t("A photo could not be read. Try a JPEG or a PNG."), { bad: true });
  } finally {
    button.disabled = false;
  }
});
