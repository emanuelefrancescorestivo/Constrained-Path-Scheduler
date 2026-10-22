// What the hosted app's pages cost a device (DECISIONS.md D93), measured in Chromium.
//
// For each page: the whole browser's CPU (renderer, compositor and graphics, as a
// share of one core) while the page sits open, first while it is lively (1 to 9 s
// after it arrives) and then while it is read (15 to 25 s, after ambient motion has
// rested); and, with the CPU slowed four times (Lighthouse's mid-tier phone), the share
// of frames slower than a 60 Hz screen allows while the page scrolls. Headless
// Chromium draws without a graphics chip, so blurring and compositing run on the CPU:
// a stand-in for a computer or phone with a weak one. Each page is measured as it is
// (the glass "Automatic": see-through unless this device turns it solid, which "became"
// says) and with the glass forced solid.
//
// Not part of the Python tests: it needs Node.js and Playwright with Chromium (not a
// dependency of this project), a running `cps demo`, and Linux (it reads /proc).
//
//   cps demo --port 8000          # in another terminal; showcase.json names @alex's plan
//   TOKEN=<alex's token> node benchmarks/page_cost.mjs
//
// Environment: BASE (default http://localhost:8000), TOKEN (required), PLAYWRIGHT (the
// module's path if not installed where Node finds it), CHROMIUM (an executable).

import fs from "node:fs";

const { chromium } = await import(process.env.PLAYWRIGHT || "playwright");
const BASE = process.env.BASE || "http://localhost:8000";
const TOKEN = process.env.TOKEN;
if (!TOKEN) throw new Error("TOKEN: a plan's token, e.g. @alex's from the demo's showcase.json");
const PAGES = {
  home: "/",
  today: `/p/${TOKEN}`,
  courses: `/p/${TOKEN}/courses`,
  course: `/p/${TOKEN}/courses/0`,
  tips: `/p/${TOKEN}/tips?topic=brain`,
  focus: `/p/${TOKEN}/focus`,
};
const SOLID = "*,*::before,*::after{backdrop-filter:none!important;-webkit-backdrop-filter:none!important}";
const HZ = 100; // clock ticks a second in /proc/<pid>/stat

function cpuTicks() {
  let ticks = 0;
  for (const pid of fs.readdirSync("/proc").filter((p) => /^\d+$/.test(p))) {
    try {
      const command = fs.readFileSync(`/proc/${pid}/cmdline`, "utf8");
      if (!/chrom/i.test(command)) continue;
      const fields = fs.readFileSync(`/proc/${pid}/stat`, "utf8").split(") ")[1].split(" ");
      ticks += Number(fields[11]) + Number(fields[12]); // user and system time
    } catch {
      /* the process ended while read */
    }
  }
  return ticks;
}

const percent = (ticks, seconds) => `${Math.round(((ticks / HZ) / seconds) * 100)}%`;
const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const context = (options = {}) =>
  browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, serviceWorkers: "block", bypassCSP: true, ...options });

const rows = [];
for (const [name, path] of Object.entries(PAGES)) {
  for (const glass of ["automatic", "solid"]) {
    const row = { page: name, glass };
    // CPU while the page sits open.
    let ctx = await context();
    let page = await ctx.newPage();
    await page.goto(BASE + path, { waitUntil: "load" });
    if (glass === "solid") await page.addStyleTag({ content: SOLID });
    await page.waitForTimeout(1000);
    let a = cpuTicks();
    await page.waitForTimeout(8000);
    row.lively = percent(cpuTicks() - a, 8);
    await page.waitForTimeout(6000);
    a = cpuTicks();
    await page.waitForTimeout(10000);
    row.reading = percent(cpuTicks() - a, 10);
    // On "Automatic", whether this device turned the glass solid by itself (D93).
    row.became = (await page.evaluate(() => document.documentElement.dataset.glass)) || "see-through";
    await ctx.close();
    // Frames while it scrolls, the CPU slowed four times.
    ctx = await context();
    page = await ctx.newPage();
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
    await page.goto(BASE + path, { waitUntil: "load" });
    if (glass === "solid") await page.addStyleTag({ content: SOLID });
    await page.waitForTimeout(1500);
    const gaps = await page.evaluate(
      () =>
        new Promise((done) => {
          const out = [];
          let last = performance.now();
          const end = last + 3000;
          const step = (now) => {
            out.push(now - last);
            last = now;
            window.scrollBy(0, 12);
            if (now < end) requestAnimationFrame(step);
            else done(out);
          };
          requestAnimationFrame(step);
        }),
    );
    row.slow_frames = `${Math.round((100 * gaps.filter((g) => g > 17.5).length) / gaps.length)}%`;
    await ctx.close();
    rows.push(row);
  }
}
console.table(rows);
await browser.close();
