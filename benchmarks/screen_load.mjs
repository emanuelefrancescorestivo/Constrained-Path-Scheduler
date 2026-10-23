// How much each screen of the hosted app asks of a student, measured in Chromium at a
// phone's size (390 × 844): the words on it, the things to tap or fill in, how many
// screens of scrolling, and the taps from the home page to a first plan.
//
// Two walks:
// - a new student on an empty `cps web`: the home page, an account made (D94), a
//   timetable imported, every setup screen accepted as it comes (the defaults), up to
//   Today; then each section once;
// - a student eight weeks in, @alex of `cps demo`: every page of the app.
//
// Counts are of what is visible: what a closed fold holds (its summary aside), a closed
// sheet or menu, a hidden field are not counted. "Controls" are links, buttons, fields and folds the student can
// reach on the page, the top bar and the tab bar included. "Tour" is the steps of
// Spark's tour that opened over the page on its first visit (0: none); "spark", whether
// Spark shows at all (its button or a tip card), "floating", whether its button floats.
//
// Not part of the Python tests: it needs Node.js and Playwright with Chromium (not a
// dependency of this project) and two running servers.
//
//   cps web --db <empty folder> --port 8831
//   cps demo --store <folder> --port 8830     # prints @alex's link
//   WEB=http://localhost:8831 DEMO=http://localhost:8830 TOKEN=<alex> \
//     ICS=<demo folder>/sample-semester.ics node benchmarks/screen_load.mjs [shots folder]
//
// ICS is a timetable whose weeks include today: the demo's copy of the sample semester.

const { chromium } = await import(process.env.PLAYWRIGHT || "playwright");
const WEB = process.env.WEB || "http://localhost:8831";
const DEMO = process.env.DEMO || "http://localhost:8830";
const TOKEN = process.env.TOKEN;
const ICS = process.env.ICS;
const SHOTS = process.argv[2];
if (!TOKEN || !ICS) throw new Error("TOKEN (@alex's plan in the demo) and ICS (a timetable) are required");

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const context = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  reducedMotion: "reduce",
  serviceWorkers: "block",
  locale: "en-GB",
  timezoneId: "Europe/Paris",
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

async function measure(walk, name) {
  await page.waitForTimeout(1000);
  // Spark's tour of a section opens over it the first time (D77): counted, then closed
  // the way a student would, and the page under it measured.
  // Its steps are those shown on this screen ("1 of 3"), not all it could show.
  const open = page.locator(".tour:not([hidden])");
  let tour = 0;
  if (await open.count()) {
    await page.waitForFunction(() => document.querySelector("#tour-title")?.innerText, null, { timeout: 5000 }).catch(() => {});
    // A tour of one step shows no count.
    tour = Number(((await open.locator(".tour-count").innerText()).match(/\d+\D+(\d+)/) || [0, 1])[1]);
  }
  if (await open.count()) {
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
  }
  if (SHOTS) {
    await page.screenshot({ path: `${SHOTS}/${walk}-${name}.png`, fullPage: true });
    await page.screenshot({ path: `${SHOTS}/${walk}-${name}-first.png` });
  }
  const row = await page.evaluate(() => {
    // Inside a closed fold, only its summary is on screen. Chromium still gives the rest
    // a box, so this is asked of the folds themselves (AUDIT 77: the first walk counted
    // what closed folds held).
    const folded = (el) => {
      for (let d = el.closest("details:not([open])"); d; d = d.parentElement?.closest("details:not([open])")) {
        const summary = d.querySelector(":scope > summary");
        if (!summary || !summary.contains(el)) return true;
      }
      return false;
    };
    const shown = (el) => {
      const box = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return box.width > 0 && box.height > 0 && style.visibility !== "hidden" && !el.closest("[inert]") && !folded(el);
    };
    const controls = [...document.querySelectorAll("a[href], button, input:not([type=hidden]), select, textarea, summary")].filter(shown);
    const fields = new Set(
      [...document.querySelectorAll("main input:not([type=hidden]):not([type=submit]), main select, main textarea")]
        .filter(shown)
        .map((f) => f.name || f.id),
    );
    const top = (el) => el.getBoundingClientRect().top + scrollY;
    const words = (text) => text.split(/\s+/).filter((w) => /\p{L}|\d/u.test(w)).length;
    const main = document.querySelector("main, .plan");
    const firstScreen = controls.filter((c) => top(c) < innerHeight);
    return {
      words: main ? words(main.innerText) : 0,
      controls: controls.length,
      first_screen: firstScreen.length,
      fields: fields.size,
      // Today and the calendar scroll inside their panel, not the page.
      screens: Math.round((Math.max(document.documentElement.scrollHeight, ...[...document.querySelectorAll(".panel")].filter(shown).map((p) => p.scrollHeight + p.getBoundingClientRect().top)) / innerHeight) * 10) / 10,
      sections: [...document.querySelectorAll("main h2, main h3, main summary")].filter(shown).length,
      spark: [...document.querySelectorAll(".spark-top, .spark-fab, .mascot-tip")].some(shown),
      // Spark's floating bubble, before D97 put it in the top bar: none since.
      floating: [...document.querySelectorAll(".spark-fab")].some(shown),
    };
  });
  row.tour = tour;
  row.walk = walk;
  row.page = name;
  rows.push(row);
  return row;
}

const rows = [];

// ---------------------------------------------------------------- a new student
// An account first (D94): the address, the password, the button; then the timetable,
// and setting up as it now goes (D98): the exams, the week with its plan, Today.
let taps = 0;
await page.goto(`${WEB}/?lang=en`);
await measure("new", "home");
let account = 0;
if (await page.locator("form.signup").count()) {
  await page.fill("form.signup input[name=email]", `walk${Date.now()}@example.org`);
  await page.fill("form.signup input[name=password]", "a long walk through the app");
  account = 3; // the two fields, then the button
  await Promise.all([page.waitForURL(/made=/, { timeout: 60_000 }), page.click("form.signup button[type=submit]")]);
}
taps += account;
await page.setInputFiles("#file", ICS);
taps += 2; // the file chooser, then the button
await Promise.all([page.waitForURL(/\/p\//, { timeout: 120_000 }), page.click("form.way:not(.way-draw) button[type=submit]")]);
const plan = new URL(page.url()).pathname.split("/")[2];
await measure("new", "1-your-exams");
taps += 1;
await Promise.all([page.waitForURL(/\/(week|routine)\?new=1/, { timeout: 120_000 }), page.click("main .btn-primary:visible")]);
// Before D98 the questions about the week came next, one card at a time (D70), and
// the calendar's links after the week: walked as they come, so that the same script
// measures the app before and after.
if (/routine/.test(page.url())) {
  for (let card = 1; card <= 12; card++) {
    await measure("new", `2-your-week-card-${card}`);
    const next = page.locator("main button.btn-primary:visible").first();
    const label = (await next.innerText()).trim();
    taps += 1;
    if (/see your week/i.test(label)) {
      await Promise.all([page.waitForURL(/\/week/, { timeout: 120_000 }), next.click()]);
      break;
    }
    await next.click();
  }
}
await measure("new", "2-your-week");
taps += 1;
await Promise.all([page.waitForURL(new RegExp(`/p/${plan}(/feed\\?new=1)?$`), { timeout: 120_000 }), page.click("[data-tour=setup-next]")]);
if (/\/feed/.test(page.url())) {
  await measure("new", "2-your-calendar");
  taps += 1;
  await Promise.all([page.waitForURL(new RegExp(`/p/${plan}$`), { timeout: 120_000 }), page.click(`main a.btn-primary[href="/p/${plan}"]`)]);
}
await measure("new", "3-today");
const toToday = taps;
for (const [name, path] of [["calendar", "/week"], ["courses", "/courses"], ["community", "/community"], ["you", "/you"], ["focus", "/focus"], ["progress", "/progress"], ["settings", "/settings"]]) {
  await page.goto(`${WEB}/p/${plan}${path}`);
  await measure("new", name);
}

// ---------------------------------------------------------- eight weeks in (@alex)
const PAGES = {
  today: "", calendar: "/week", tasks: "/tasks", courses: "/courses", course: "/courses/0",
  focus: "/focus", community: "/community", notes: "/notes", groups: "/groups",
  progress: "/progress", trends: "/trends", cards: "/cards", tips: "/tips", coach: "/coach",
  weekly: "/weekly", settings: "/settings", science: "/science", import: "/courses/import", you: "/you",
};
for (const [name, path] of Object.entries(PAGES)) {
  await page.goto(`${DEMO}/p/${TOKEN}${path}`);
  await measure("alex", name);
}

console.table(rows.map(({ walk, page, words, controls, first_screen, fields, screens, sections, tour, spark, floating }) => ({ walk, page, words, controls, first_screen, fields, screens, sections, tour, spark, floating })));
console.log(`taps from the home page to Today, accepting every default: ${toToday} (${account} of them for the account)`);
console.log(`pages with a script error: ${errors.length}`);
await browser.close();
