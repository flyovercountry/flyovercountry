// Shared helpers for the fly frontend Playwright suite (test/frontend/*.spec.js,
// one file per feature). The app must already be served (default
// http://127.0.0.1:8150, override via BASE_URL env var — see
// playwright.config.js).
//
// Screenshots land in test/frontend/screenshots/. Assertions are hard and
// wait on observable state (locator expectations, expect.poll) rather than
// fixed sleeps; a spec that needs a timed wait says which app constant it
// is tied to.
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';
import { test, expect } from '@playwright/test';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export const BASE = process.env.BASE_URL || 'http://127.0.0.1:8150';
export const SHOTS = path.join(__dirname, 'screenshots');
fs.mkdirSync(SHOTS, { recursive: true });

export async function ready(page, url) {
  const errors = [];
  page.on('pageerror', e => errors.push(String(e).slice(0, 150)));
  // Suppress the guided tour so it doesn't block test interaction.
  // addInitScript runs before page scripts on every navigation.
  await page.addInitScript(() => { localStorage.setItem('welcomed', '1'); });
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  // 90s, like the suite's other first-load waits: the initial data load on
  // WebKit has crossed 45s on a loaded runner.
  await page.waitForSelector('html[data-app-ready="1"]', { timeout: 90000 });
  await page.evaluate(() => {
    localStorage.setItem('welcomed', '1');
    for (const id of ['welcome-overlay', 'updating-overlay']) {
      const el = document.getElementById(id);
      if (el) el.remove();
    }
    // Dismiss any active custom tour overlay
    const tourOverlay = document.getElementById('tour-overlay');
    if (tourOverlay) tourOverlay.remove();
  });
  return errors;
}

/** Staged filter edits commit themselves after AUTO_APPLY_MS (3 s,
 * lib/stores/filters.js) and show an "Applying…" note with an "Apply now"
 * button meanwhile. Short-circuit the countdown when the note is up, then
 * wait for the note to be gone and the resulting recompute to settle. The
 * click can lose a race with the timer firing, which is the same outcome. */
export async function applyFiltersNow(page) {
  const note = page.locator('.auto-apply-note');
  if (await note.isVisible()) {
    await note.locator('.btn-apply-now').click({ timeout: 1000 }).catch(() => {});
  }
  await expect(note).toHaveCount(0, { timeout: 8000 });
  await settleMap(page);
}

export async function openFiltersUI(page) {
  await page.waitForSelector('html[data-app-ready="1"]', { timeout: 45000 });
  await page.click('#stats-section .section-header');
  await page.waitForSelector('#filter-theater-group .toggle-btn');
}

/** Resolves once the country and route layers exist and the map is idle,
 * i.e. taps on countries will actually hit rendered features. */
export async function waitForMap(page) {
  await page.waitForFunction(() => window.__map?.getLayer('country-fill')
    && window.__map?.getLayer('routes-line') && window.__map.isStyleLoaded(), null, { timeout: 60000 });
  await page.evaluate(() => new Promise(r => { const m = window.__map; if (m.loaded()) r(); else m.once('idle', r); }));
}

/** Screen position of a lng/lat on the live map, plus which country
 * polygon (if any) is rendered there and whether the map canvas is the
 * topmost element at that point (i.e. a tap there reaches the map). */
export async function mapPoint(page, lngLat) {
  return page.evaluate(([lng, lat]) => {
    const m = window.__map;
    const r = m.getContainer().getBoundingClientRect();
    const p = m.project([lng, lat]);
    const x = r.left + p.x, y = r.top + p.y;
    const el = document.elementFromPoint(x, y);
    const feat = m.queryRenderedFeatures([p.x, p.y], { layers: ['country-fill'] })[0];
    return { x, y, cc: feat?.properties?.cc || null, onCanvas: !!el && el.tagName === 'CANVAS' };
  }, lngLat);
}

/** Tap with a finger on touch projects, click with the mouse elsewhere. */
export async function tapAt(page, pt) {
  if (test.info().project.use.hasTouch) await page.touchscreen.tap(pt.x, pt.y);
  else await page.mouse.click(pt.x, pt.y);
}

/** Same, for a locator: finger tap on touch projects, mouse click elsewhere. */
export async function tapEl(locator, opts = {}) {
  if (test.info().project.use.hasTouch) await locator.tap(opts);
  else await locator.click(opts);
}

export function isTouchProject() {
  return !!test.info().project.use.hasTouch;
}

/** Wait out an apply triggered by a map tap: COMPUTING overlay gone and the
 * map idle again. */
export async function settleMap(page) {
  await page.waitForTimeout(300);
  await page.waitForFunction(() => document.getElementById('updating-overlay')?.classList.contains('hidden') !== false,
    null, { timeout: 20000 }).catch(() => {});
  await page.evaluate(() => new Promise(r => { const m = window.__map; if (m.loaded()) r(); else m.once('idle', r); }));
}

/** The country the hover fill and the selection border currently point at
 * (null when their filter is the empty-cc sentinel), plus the URL query and
 * the mobile collapsed bar's title. */
export async function countryLayerState(page) {
  return page.evaluate(() => {
    const m = window.__map;
    const ccOf = (id) => { const f = m.getFilter(id); return Array.isArray(f) && f[0] === '==' ? (f[2] || null) : null; };
    return {
      hover: ccOf('country-hover'),
      selected: ccOf('country-highlight-border'),
      url: location.search,
      bar: document.querySelector('.collapsed-title')?.textContent?.trim() || null,
    };
  });
}

// Element screenshot that tolerates a busy layout (bounded retry inside
// playwright; a failed artifact shot is logged, not fatal).
export async function elementShot(page, selector, file) {
  try {
    await page.locator(selector)
      .screenshot({ path: file, animations: 'disabled', timeout: 20000 });
  } catch (e) {
    console.log(`  WARN: screenshot ${path.basename(file)} failed: ${String(e).slice(0, 120)}`);
  }
}
