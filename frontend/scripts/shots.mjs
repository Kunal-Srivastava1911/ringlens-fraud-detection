// Smoke-test every route in a real browser and save screenshots.
//   node scripts/shots.mjs [outDir] [theme]
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://localhost:5180";
const out = process.argv[2] ?? "screenshots";
const theme = process.argv[3] ?? "dark";
mkdirSync(out, { recursive: true });

const api = async (p) => (await fetch(`${BASE}${p}`)).json();
const top = (await api("/api/providers?limit=1")).rows[0]._id;
const ring = (await api("/api/rings"))[0]?._id;

const routes = [
  ["command-center", "/"], ["network", "/network"], ["rings", "/rings"], ["ring-detail", `/rings/${ring}`],
  ["providers", "/providers"], ["provider-detail", `/providers/${top}`], ["claims", "/claims"],
  ["pipeline", "/pipeline"], ["lab", "/lab"], ["internals", "/internals"],
];

const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
await ctx.addInitScript((t) => localStorage.setItem("rl-theme", t), theme);
let failures = 0;
for (const [name, path] of routes) {
  const page = await ctx.newPage();
  // The console scrolls inside <main>, so give scrolling pages a tall viewport instead of fullPage.
  await page.setViewportSize({ width: 1600, height: name === "network" ? 1000 : Number(process.env.TALL ?? 2700) });
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(BASE + path, { waitUntil: "networkidle" }).catch((e) => errors.push(String(e)));
  await page.waitForTimeout(name === "network" ? 6000 : 2500);
  await page.screenshot({ path: `${out}/${name}-${theme}.png` });
  const status = errors.length ? `ERRORS (${errors.length})` : "ok";
  if (errors.length) failures++;
  console.log(`${status.padEnd(12)} ${name.padEnd(16)} ${path}`);
  for (const e of errors.slice(0, 4)) console.log("    ", e.slice(0, 300));
  await page.close();
}
await browser.close();
process.exit(failures ? 1 : 0);
