// The TL;DR intro card precedes the tour. Runs under every project: the
// desktop check is about content and layout, the phone projects (including
// the short iPhone 8/SE ones) are about the body scrolling while the button
// stays reachable without scrolling.
import path from 'path';
import { test, expect } from '@playwright/test';
import { BASE, SHOTS } from './helpers.js';

async function firstVisit(page, url) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('html[data-app-ready="1"]', { timeout: 90000 });
}

const EXPECTED_TITLE = 'TL;DR Flyover Country';

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

test('the footer tour button shows the intro again on a returning visit', async ({ page }) => {
  await page.addInitScript(() => { localStorage.setItem('welcomed', '1'); });
  await firstVisit(page, `${BASE}/`);
  await expect(page.locator('#tldr-overlay')).toHaveCount(0);
  const tourButton = page.locator('#sidebar-footer .footer-action').first();
  await tourButton.scrollIntoViewIfNeeded();
  await tourButton.click();
  await expect(page.locator('#tldr-overlay')).toBeVisible({ timeout: 10000 });
});
