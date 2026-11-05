// How much of each screen is left empty as the three explaining pages are scrolled
// (D118): How it works (/garden), the science behind it (/science) and How the code
// works (/how), in Chromium at a laptop's sizes (1440 × 900, 1280 × 800) and a phone's
// (390 × 844), with motion on, so that their pinned scenes run.
//
// From the top of a page to its end, 150px at a time, each row of the window under the
// top bar is "filled" when a visible heading, paragraph, list item, figure, card, chart
// or canvas covers it (an element faded below 5 % opacity, hidden or not displayed does
// not count). Per page: its length in screens, the share of a screen left empty on
// average, the share of positions where more than half the screen is empty, and the
// tallest empty band seen, in pixels.
//
// Not part of the Python tests: it needs Node.js and Playwright with Chromium (not a
// dependency of this project) and a running server.
//
//   cps web --db <empty folder> --port 8831
//   WEB=http://localhost:8831 node benchmarks/scroll_flow.mjs
//
// Chromium draws the garden's WebGL with its software renderer here (SwiftShader).

const { chromium } = await import(process.env.PLAYWRIGHT || "playwright");
const WEB = process.env.WEB || "http://localhost:8831";
const SIZES = [[1440, 900], [1280, 800], [390, 844]];
const PAGES = ["/garden", "/science", "/how"];
const STEP = 150;

const browser = await chromium.launch({
  ...(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {}),
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});

function measure() {
  const top = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--topbar")) || 52;
  const height = innerHeight;
  const rows = new Uint8Array(height);
  const kinds = "main h1, main h2, main h3, main h4, main p, main li, main svg, main canvas, main .card, "
    + "main figure, main .gd-scene, main nav, main .chat";
  for (const el of document.querySelectorAll(kinds)) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.bottom < top || r.top > height) continue;
    let seen = true;
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.visibility === "hidden" || cs.display === "none" || Number(cs.opacity) < 0.05) {
        seen = false;
        break;
      }
    }
    if (!seen) continue;
    for (let y = Math.max(top, Math.floor(r.top)); y < Math.min(height, Math.ceil(r.bottom)); y++) rows[y] = 1;
  }
  let empty = 0, run = 0, tallest = 0;
  for (let y = top; y < height; y++) {
    if (rows[y]) run = 0;
    else {
      empty++;
      run++;
      tallest = Math.max(tallest, run);
    }
  }
  return { empty: empty / (height - top), tallest };
}

for (const [width, height] of SIZES) {
  for (const path of PAGES) {
    const page = await browser.newPage({ viewport: { width, height } });
    await page.goto(WEB + path, { waitUntil: "networkidle" });
    const total = await page.evaluate(() => document.documentElement.scrollHeight);
    const seen = [];
    for (let y = 0; y < total - height; y += STEP) {
      await page.evaluate((y) => window.scrollTo({ top: y, behavior: "instant" }), y);
      await page.waitForTimeout(60);
      seen.push(await page.evaluate(measure));
    }
    const mean = seen.reduce((a, s) => a + s.empty, 0) / seen.length;
    const half = seen.filter((s) => s.empty > 0.5).length / seen.length;
    console.log(
      `${width}×${height} ${path.padEnd(9)} ${(total / height).toFixed(1).padStart(5)} screens`
      + `  empty ${Math.round(100 * mean).toString().padStart(3)} %`
      + `  half-empty positions ${Math.round(100 * half).toString().padStart(3)} %`
      + `  tallest gap ${Math.max(...seen.map((s) => s.tallest))}px`,
    );
    await page.close();
  }
}
await browser.close();
