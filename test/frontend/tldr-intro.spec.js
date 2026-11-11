// The TL;DR intro card precedes the tour. Runs under every project: the
// desktop check is about content and layout, the phone projects (including
// the short iPhone 8/SE ones) are about the body scrolling while the button
// stays reachable without scrolling.
import path from 'path';
import { test, expect } from '@playwright/test';
import { BASE, SHOTS, isTouchProject, tapEl } from './helpers.js';

async function firstVisit(page, url) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('html[data-app-ready="1"]', { timeout: 90000 });
}

const EXPECTED_TITLE = 'Flyover Country';

test('intro shows first, carries the configured text, and its button starts the tour', async ({ page }) => {
  const tag = `${test.info().project.name}-tldr`;
  await firstVisit(page, `${BASE}/`);
  const overlay = page.locator('#tldr-overlay');
  await expect(overlay).toBeVisible({ timeout: 30000 });
  await expect(page.locator('#tour-overlay')).toHaveCount(0);

  await expect(overlay.locator('#tldr-title')).toHaveText(EXPECTED_TITLE);
  await expect(overlay.locator('.tldr-body li')).toHaveCount(6);
  await expect(overlay.locator('.tldr-body li').last()).toContainText("said 'no'");
  await expect(overlay.locator('.tldr-disclaimer')).toContainText('without warranties');
  const start = overlay.locator('#tldr-start');
  await expect(start).toContainText('Take the tour');

  // Geometry: card inside the viewport, button fully visible without any
  // scrolling, no horizontal overflow anywhere in the card.
  const m = await overlay.evaluate((el) => {
    const box = el.querySelector('.tldr-box').getBoundingClientRect();
    const body = el.querySelector('.tldr-body');
    const btn = el.querySelector('#tldr-start').getBoundingClientRect();
    const skip = el.querySelector('.tour-skip-top').getBoundingClientRect();
    return {
      vw: window.innerWidth, vh: window.innerHeight,
      box: { top: box.top, bottom: box.bottom, left: box.left, right: box.right },
      btn: { top: btn.top, bottom: btn.bottom, right: btn.right },
      skipBottom: skip.bottom,
      bodyScrolls: body.scrollHeight > body.clientHeight + 1,
      bodyScrollTop: body.scrollTop,
      hOverflow: body.scrollWidth > body.clientWidth + 1,
    };
  });
  expect(m.box.top, 'card below the Skip pill').toBeGreaterThanOrEqual(m.skipBottom);
  expect(m.box.bottom, 'card inside the viewport').toBeLessThanOrEqual(m.vh + 0.5);
  expect(m.box.left).toBeGreaterThanOrEqual(0);
  expect(m.box.right).toBeLessThanOrEqual(m.vw + 0.5);
  expect(m.btn.top, 'button visible without scrolling').toBeGreaterThanOrEqual(0);
  expect(m.btn.bottom).toBeLessThanOrEqual(m.vh + 0.5);
  expect(m.hOverflow, 'no horizontal overflow').toBe(false);
  expect(m.bodyScrollTop).toBe(0);
  await page.screenshot({ path: path.join(SHOTS, `${tag}-top.png`) });

  if (m.bodyScrolls) {
    // Short viewport: the body must actually scroll to its end, with the
    // scroll hint class set, and the button must not move.
    await expect(overlay.locator('.tldr-box')).toHaveClass(/tldr-scrollable/);
    await overlay.locator('.tldr-body').evaluate((b) => { b.scrollTop = b.scrollHeight; });
    const after = await overlay.evaluate((el) => {
      const body = el.querySelector('.tldr-body');
      const last = el.querySelector('.tldr-disclaimer').getBoundingClientRect();
      const btn = el.querySelector('#tldr-start').getBoundingClientRect();
      return { atEnd: body.scrollTop + body.clientHeight >= body.scrollHeight - 1,
               lastBottom: last.bottom, bodyBottom: body.getBoundingClientRect().bottom,
               btnTop: btn.top };
    });
    expect(after.atEnd).toBe(true);
    expect(after.lastBottom, 'disclaimer fully revealed at the end').toBeLessThanOrEqual(after.bodyBottom + 0.5);
    expect(after.btnTop).toBe(m.btn.top);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-scrolled.png`) });
  } else {
    await expect(overlay.locator('.tldr-box')).not.toHaveClass(/tldr-scrollable/);
  }

  await start.click();
  await expect(overlay).toHaveCount(0);
  await expect(page.locator('#tour-overlay')).toBeVisible({ timeout: 10000 });
  expect(await page.evaluate(() => localStorage.getItem('welcomed'))).toBeNull();
});

test('skipping the intro skips the tour and marks the visitor welcomed', async ({ page }) => {
  await firstVisit(page, `${BASE}/`);
  const overlay = page.locator('#tldr-overlay');
  await expect(overlay).toBeVisible({ timeout: 30000 });
  await overlay.locator('.tour-skip-top').click();
  await expect(overlay).toHaveCount(0);
  await expect(page.locator('#tour-overlay')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => localStorage.getItem('welcomed'))).toBe('1');
});

test('first visit: the card is up before the data has loaded, its button armed once ready', async ({ page }) => {
  // Hold the big routes download so "before the app is ready" is a real,
  // observable window rather than a race.
  let release;
  const gate = new Promise((r) => { release = r; });
  await page.route('**/data/routes*.geojson', async (route) => { await gate; await route.continue(); });
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  const card = page.locator('#tldr-overlay');
  await expect(card).toBeVisible({ timeout: 30000 });
  expect(await page.evaluate(() => document.documentElement.dataset.appReady)).toBeUndefined();
  const start = card.locator('#tldr-start');
  await expect(start).toBeEnabled();
  await expect(start).toContainText('Take the tour');
  await expect(card.locator('#tldr-title')).toHaveText(EXPECTED_TITLE);
  // Start the tour BEFORE the data has arrived: no loading gate anywhere.
  await start.click();
  await expect(card).toHaveCount(0);
  const tour = page.locator('#tour-overlay');
  await expect(tour).toBeVisible({ timeout: 10000 });
  expect(await page.evaluate(() => document.documentElement.dataset.appReady)).toBeUndefined();
  const stepText = await page.locator('.tour-annotation h3').first().textContent();
  release();
  await page.waitForSelector('html[data-app-ready="1"]', { timeout: 90000 });
  // The app finishing its load must not restart or dismiss the running tour.
  await page.waitForTimeout(500);
  await expect(tour).toHaveCount(1);
  expect(await page.locator('.tour-annotation h3').first().textContent()).toBe(stepText);
});

test('the (i) button opens the card standalone with a Close pill; closing leaves the tour alone', async ({ page }) => {
  await page.addInitScript(() => { localStorage.setItem('welcomed', '1'); });
  await firstVisit(page, `${BASE}/`);
  await page.locator('#breadcrumb-mode-btn').click();
  const card = page.locator('#tldr-overlay');
  await expect(card).toBeVisible({ timeout: 10000 });
  await expect(card.locator('.tour-skip-top')).toHaveText('Close');
  await expect(card.locator('#tldr-start')).toBeEnabled();
  await card.locator('.tour-skip-top').click();
  await expect(card).toHaveCount(0);
  await expect(page.locator('#tour-overlay')).toHaveCount(0);
});

test('the footer tour button shows the intro again on a returning visit', async ({ page }) => {
  await page.addInitScript(() => { localStorage.setItem('welcomed', '1'); });
  await firstVisit(page, `${BASE}/`);
  await expect(page.locator('#tldr-overlay')).toHaveCount(0);
  if (isTouchProject()) {   // the footer lives inside the collapsed sheet
    await tapEl(page.locator('.collapsed-sheet'));
    await expect(page.locator('#sidebar.expanded')).toBeVisible({ timeout: 10000 });
  }
  const tourButton = page.locator('#sidebar-footer .footer-action').first();
  await tourButton.scrollIntoViewIfNeeded();
  await tapEl(tourButton);
  await expect(page.locator('#tldr-overlay')).toBeVisible({ timeout: 10000 });
});
