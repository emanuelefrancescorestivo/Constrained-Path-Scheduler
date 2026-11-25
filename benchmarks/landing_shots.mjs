// The front page's screenshots (D188): the real app, on the demo's made-up class, in
// English, French and Italian, written to src/cps/web/static/landing/<lang>/.
//
//   node benchmarks/landing_shots.mjs            (Node + Playwright, not a dependency)
//
// It starts benchmarks/landing_server.py on an empty store with the app's clock held
// on a Tuesday morning (2026-11-24, 10:40 in Paris), so the pictures are the same
// whenever they are taken, waits for its students, and takes each screen as a student
// sees it: Spark's tours skipped as a student would, the card offering to install the
// app put away, motion reduced so nothing is caught half way; each in light and in dark,
// the page showing the one the reader's device asks for. A laptop's screen at
// 1280 × 800, a phone's as its browser shows it (an iPhone 13's, 390 × 664) at 1.5
// pixels to the point. JPEG at 78: what the page shows is a picture of the app, not
// its text, which stays on the page itself.
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const playwright = await import(process.env.PLAYWRIGHT || "playwright");
const { chromium, devices } = playwright.devices ? playwright : playwright.default;

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = join(ROOT, "src/cps/web/static/landing");
const PORT = Number(process.env.PORT || 8765);
const BASE = `http://127.0.0.1:${PORT}`;
const PYTHON = process.env.PYTHON || (process.platform === "win32" ? ".venv\\Scripts\\python" : ".venv/bin/python");

// Each picture: its name, the screen it is taken on, and the page of whose plan
// ("2" the second year's, "1" the first year's).
const SHOTS = [
  ["today-laptop", "laptop", "2", ""],
  ["today-phone", "phone", "2", ""],
  ["progress-phone", "phone", "2", "/progress"],
  ["community-phone", "phone", "2", "/community"],
  ["course-laptop", "laptop", "2", "/courses/0"],
  ["setup-phone", "phone", "1", "/settings?new=1"],
];
const SCREENS = {
  laptop: { viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 },
  phone: { ...devices["iPhone 13"], deviceScaleFactor: 1.5 },
};

const store = mkdtempSync(join(tmpdir(), "cps-landing-"));
const server = spawn(PYTHON, [join(ROOT, "benchmarks/landing_server.py"), store, String(PORT)], {
  cwd: ROOT,
  stdio: ["ignore", "pipe", "inherit"],
});
const plans = await new Promise((done, fail) => {
  server.on("exit", (code) => fail(new Error(`the server stopped (${code})`)));
  createInterface({ input: server.stdout }).once("line", (line) => done(JSON.parse(line)));
});
for (let i = 0; i < 120; i++) {
  try {
    if ((await fetch(`${BASE}/health`)).ok) break;
  } catch {
    /* not listening yet */
  }
  await new Promise((r) => setTimeout(r, 500));
}

const browser = await chromium.launch();
try {
  for (const [lang, byYear] of Object.entries(plans)) {
    mkdirSync(join(OUT, lang), { recursive: true });
    for (const [[name, screen, year, path], scheme] of SHOTS.flatMap((shot) => [[shot, "light"], [shot, "dark"]])) {
      const context = await browser.newContext({ ...SCREENS[screen], reducedMotion: "reduce", locale: lang, colorScheme: scheme });
      await context.addInitScript(() => {
        try {
          localStorage.setItem("cps:install-dismissed", "1");
        } catch {
          /* private browsing: the card may show */
        }
      });
      const page = await context.newPage();
      const url = `${BASE}/p/${byYear[year]}${path}`;
      await page.goto(url, { waitUntil: "networkidle" });
      const skip = page.locator("[data-tour-skip]");
      if (await skip.count()) {
        await skip.first().click();
        await page.waitForTimeout(500);
        await page.goto(url, { waitUntil: "networkidle" });
      }
      await page.waitForTimeout(600);
      const file = `${name}${scheme === "dark" ? "-dark" : ""}.jpg`;
      await page.screenshot({ path: join(OUT, lang, file), type: "jpeg", quality: 78 });
      console.log(lang, file);
      await context.close();
    }
  }
} finally {
  await browser.close();
  server.kill();
  rmSync(store, { recursive: true, force: true });
}
