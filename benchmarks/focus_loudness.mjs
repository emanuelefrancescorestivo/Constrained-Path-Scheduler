// How loud each of the focus page's sounds is at the page's output (DECISIONS.md D207, D210),
// measured in Chromium: each sound is tapped in turn at the page's middle volume, and eight
// seconds of the signal after the page's own limiter are recorded, from 4.5 s after the tap
// (past the fade in and a recording's download), and measured by ffmpeg's EBU R 128 meter.
// Prints each sound's loudness and how many LU it sits from binaural, the one sound still made
// by the browser; sound.js's TRIM and RECORDED_TRIM are set from it. Eight seconds of a piece
// of music are not the whole piece: within a couple of LU of it.
//
// Not part of the Python tests: it needs Node.js and Playwright with Chromium (not a
// dependency of this project), ffmpeg, and a running `cps demo`.
//
//   cps demo --port 8000          # in another terminal; showcase.json names @alex's plan
//   TOKEN=<alex's token> node benchmarks/focus_loudness.mjs
//
// Environment: BASE (default http://localhost:8000), TOKEN (required), PLAYWRIGHT (the
// module's path if not installed where Node finds it). With OFFLINE=1 the recordings are
// refused, so the browser's own versions play: what a device offline before a first fetch hears.

import { spawnSync } from "node:child_process";

function loudness(samples, rate) {
  // ffmpeg's EBU R 128 meter over interleaved stereo float samples: the integrated loudness.
  const run = spawnSync(
    "ffmpeg",
    ["-hide_banner", "-nostats", "-f", "f32le", "-ar", String(rate), "-ac", "2", "-i", "-", "-af", "ebur128", "-f", "null", "-"],
    { input: Buffer.from(samples.buffer) },
  );
  const summary = run.stderr.toString().split("Summary:").pop();
  return Number(summary.split("I:")[1].split("LUFS")[0]);
}

const { chromium } = await import(process.env.PLAYWRIGHT || "playwright");
const base = process.env.BASE || "http://localhost:8000";
const token = process.env.TOKEN;
if (!token) throw new Error("TOKEN: a plan's token, @alex's in showcase.json");

const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
await page.addInitScript(() => {
  // The page's limiter is the last node before the speakers: listen after it.
  const make = AudioContext.prototype.createDynamicsCompressor;
  AudioContext.prototype.createDynamicsCompressor = function () {
    const node = make.call(this);
    // A tap after it that keeps what it hears while asked to (ScriptProcessor: no file to load).
    const tap = this.createScriptProcessor(4096, 2, 2);
    window.__heard = null;
    tap.onaudioprocess = (event) => {
      if (window.__heard) for (let ch = 0; ch < 2; ch++) window.__heard[ch].push(...event.inputBuffer.getChannelData(ch));
    };
    const mute = this.createGain();
    mute.gain.value = 0;
    node.connect(tap).connect(mute).connect(this.destination);
    window.__rate = this.sampleRate;
    return node;
  };
});
if (process.env.OFFLINE) await page.route("**/sounds/*.mp3", (route) => route.abort());
await page.goto(`${base}/p/${token}/focus`);
if (await page.$('form[action$="/focus/start"]')) {
  await Promise.all([
    page.waitForNavigation(),
    page.evaluate(() => document.querySelector('form[action$="/focus/start"]').requestSubmit()),
  ]);
}
await page.evaluate(() => {
  document.querySelectorAll(".tour, .tour-layer, .tour-dim").forEach((e) => e.remove());
  const volume = document.getElementById("sound-volume");
  volume.value = "50";
  volume.dispatchEvent(new Event("input"));
  document.querySelector(".sound-pick").open = true;
});
const keys = await page.$$eval("[data-sound-key]", (chips) => chips.map((c) => c.dataset.soundKey));
const rows = [];
for (const key of keys) {
  await page.click(`[data-sound-key="${key}"]`);
  await page.waitForTimeout(4500);
  const heard = await page.evaluate(async () => {
    window.__heard = [[], []];
    await new Promise((done) => setTimeout(done, 8000));
    const [left, right] = window.__heard;
    window.__heard = null;
    return { left, right, rate: window.__rate };
  });
  const n = Math.min(heard.left.length, heard.right.length);
  const samples = new Float32Array(2 * n);
  for (let i = 0; i < n; i++) {
    samples[2 * i] = heard.left[i];
    samples[2 * i + 1] = heard.right[i];
  }
  const lufs = loudness(samples, heard.rate);
  const recorded = await page.$eval(`[data-sound-key="${key}"]`, (c) => Boolean(c.dataset.src));
  rows.push({ key, lufs, kind: recorded && !process.env.OFFLINE ? "recording" : "browser" });
}
const binaural = rows.find((r) => r.key === "binaural").lufs;
for (const r of rows) {
  const lu = r.lufs - binaural;
  console.log(`${r.key.padEnd(10)} ${r.kind.padEnd(9)} ${r.lufs.toFixed(1)} LUFS  ${lu >= 0 ? "+" : ""}${lu.toFixed(1)} LU from binaural`);
}
await browser.close();
