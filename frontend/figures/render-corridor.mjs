// Render frontend/src/static/tour-corridor.jpg from the running app.
//
// The phone tour's step 2 ("A narrow corridor") shows this picture because
// the phone map is too zoomed in to show a Europe-wide corridor. It is the
// desktop map at that tour step, with the tour's red/green country
// override, cropped to the map. Run with `./run figures`, which executes
// this in the playwright container (BASE_URL set by compose) and mounts
// frontend/src/static/ writable; then rebuild with `./run serve`.
import { chromium } from "@playwright/test";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const BASE = process.env.BASE_URL || "http://127.0.0.1:8150";
const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "static", "tour-corridor.jpg");

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
try {
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForSelector('html[data-app-ready="1"]', { timeout: 90000 });

  // First visit: the TL;DR card is up; its button starts the tour.
  const start = page.locator("#tldr-start");
  await start.waitFor({ state: "visible", timeout: 30000 });
  await start.click();
  await page.locator("#tour-overlay").waitFor({ state: "visible", timeout: 10000 });
  await page.locator(".tour-btn-next").click();   // step 1 -> step 2
  await page.locator(".tour-annotation h3", { hasText: /corridor/i }).first()
    .waitFor({ state: "visible", timeout: 10000 });

  // Only the map and its country override should remain visible.
  await page.addStyleTag({ content:
    "#tour-overlay, #breadcrumb-mode-btn, .maplibregl-ctrl, #toolbar-mount, " +
    "#desktop-toolbar, #onmap-legend { visibility: hidden !important; }" });
  await page.waitForTimeout(1500);   // tiles and the fill transition settle

  const map = await page.locator("#map").boundingBox();
  await page.screenshot({
    path: OUT, type: "jpeg", quality: 82,
    clip: { x: map.x + 40, y: map.y + 40, width: map.width - 80, height: map.height - 80 },
  });
  console.log(`wrote ${OUT}`);
} finally {
  await browser.close();
}
