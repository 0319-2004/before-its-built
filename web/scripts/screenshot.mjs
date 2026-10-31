/**
 * テストページを開いてスクリーンショットを撮り、
 * コンソールエラーと失敗したネットワークリクエストを報告する。
 *
 *   node scripts/screenshot.mjs [set] [outPath]
 *   node scripts/screenshot.mjs lod1 /tmp/lod1.png
 *
 * vite dev server が動いている前提（BASE_URL で変更可）。
 */
import { chromium } from "playwright";

const set = process.argv[2] || "lod1";
const out = process.argv[3] || `shot-${set}.png`;
const base = process.env.BASE_URL || "http://localhost:5173";
// タイルの読み込みを待つ時間。重い tileset ほど長めに。
const settleMs = Number(process.env.SETTLE_MS || 25000);

// Playwright 同梱の chromium は未ダウンロードなことが多いので、
// 既にあるシステムの Chrome を使う（CHANNEL=chromium で同梱版に切替）。
const channel = process.env.CHANNEL || "chrome";
const browser = await chromium.launch({
  channel: channel === "chromium" ? undefined : channel,
  args: ["--use-gl=angle", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

const consoleErrors = [];
const pageErrors = [];
const failed = [];

page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});
page.on("pageerror", (e) => pageErrors.push(e.stack || e.message));
page.on("requestfailed", (r) =>
  failed.push(`${r.failure()?.errorText} ${r.url().slice(0, 140)}`),
);

// 種類ごとに数える。tileset.json だけ来て b3dm が来ていない、
// という状態を見分けたいので分けておく。
const counts = {};
page.on("response", (r) => {
  const u = r.url();
  const kind = /tileset\.json/.test(u)
    ? "tileset.json"
    : /\.b3dm/.test(u)
      ? "b3dm"
      : /\.pnts/.test(u)
        ? "pnts"
        : null;
  if (!kind) return;
  const key = `${kind} ${r.status()}`;
  counts[key] = (counts[key] || 0) + 1;
});

const url = `${base}/?set=${set}${process.env.EXTRA_QS || ""}`;
console.log(`開く: ${url}`);
await page.goto(url, { waitUntil: "networkidle", timeout: 60000 }).catch((e) => {
  console.log(`goto 警告: ${e.message}`);
});

await page.waitForTimeout(settleMs);

const diag = await page.evaluate(() => window.__diag ?? null);
await page.screenshot({ path: out });

console.log("\n==== 結果 ====");
console.log(`screenshot: ${out}`);
console.log(`diag:`, JSON.stringify(diag, null, 2));
console.log(`3D Tiles リクエスト:`, counts);
console.log(`\nページ例外 (${pageErrors.length}):`);
pageErrors.slice(0, 10).forEach((e) => console.log("  " + e.split("\n")[0]));
console.log(`\nconsole.error (${consoleErrors.length}):`);
consoleErrors.slice(0, 20).forEach((e) => console.log("  " + e.slice(0, 300)));
console.log(`\n失敗したリクエスト (${failed.length}):`);
failed.slice(0, 20).forEach((e) => console.log("  " + e));

await browser.close();
