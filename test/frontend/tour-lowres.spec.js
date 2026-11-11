import path from 'path';
import { test, expect } from '@playwright/test';
import { BASE, SHOTS, passIntro } from './helpers.js';

// Runs under the short-phone WebKit projects (iPhone 8 375x667, iPhone SE
// 320x568 — see playwright.config.js). On those the tallest tour step's
// text is taller than the screen. The box must stay inside the viewport
// and Back/Next must be visible WITHOUT scrolling (the nav is pinned below
// the scrolling text), and when the text does overflow the box must say
// so visually (tour.js flags it `tour-scrollable`: fade + rule).
test('tour: every step fits the viewport with Back/Next visible without scrolling', async ({ page }) => {
  const tag = `${test.info().project.name}-tour-lowres`;
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('#loading-overlay', { state: 'detached', timeout: 90000 }).catch(() => {});
  await passIntro(page);

  const overlay = page.locator('#tour-overlay');
  await expect(overlay, 'tour shows on first visit').toBeVisible({ timeout: 30000 });

  for (let step = 1; step <= 10; step++) {
    const ann = page.locator('.tour-annotation', { has: page.locator('.tour-nav') }).first();
    await expect(ann).toBeVisible({ timeout: 5000 });
    // The step fades in (tour.js toggles .tour-annotations opacity 0 -> 1
    // over 220ms); measure and shoot only once it is fully painted.
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.tour-annotations')).opacity === '1',
      null, { timeout: 5000 });
    await page.waitForTimeout(250);

    const m = await ann.evaluate((el) => {
      const r = el.getBoundingClientRect();
      const nb = el.querySelector('.tour-btn-next').getBoundingClientRect();
      const body = el.querySelector('.tour-body');
      return {
        top: r.top, bottom: r.bottom, vh: innerHeight,
        left: r.left, right: r.right, vw: innerWidth,
        nextTop: nb.top, nextBottom: nb.bottom, nextLeft: nb.left, nextRight: nb.right,
        scrollable: body.scrollHeight > body.clientHeight + 1,
        flagged: el.classList.contains('tour-scrollable'),
        scrollTop: body.scrollTop,
      };
    });
    expect(m.top, `step ${step}: box top inside the viewport (top=${Math.round(m.top)})`).toBeGreaterThanOrEqual(0);
    expect(m.bottom, `step ${step}: box bottom inside the viewport (bottom=${Math.round(m.bottom)}, vh=${m.vh})`).toBeLessThanOrEqual(m.vh + 1);
    expect(m.left >= 0 && m.right <= m.vw + 1,
      `step ${step}: box inside the viewport horizontally (x=${Math.round(m.left)}..${Math.round(m.right)}, vw=${m.vw})`).toBeTruthy();
    expect(m.nextLeft >= 0 && m.nextRight <= m.vw + 1,
      `step ${step}: Next button fully on screen horizontally (x=${Math.round(m.nextLeft)}..${Math.round(m.nextRight)}, vw=${m.vw})`).toBeTruthy();
    expect(m.scrollTop, `step ${step}: not scrolled yet`).toBe(0);
    expect(m.nextTop >= 0 && m.nextBottom <= m.vh + 1,
      `step ${step}: Next visible without scrolling (y=${Math.round(m.nextTop)}..${Math.round(m.nextBottom)}, vh=${m.vh})`).toBeTruthy();
    if (m.scrollable) {
      expect(m.flagged, `step ${step}: overflowing text is flagged as scrollable`).toBeTruthy();
    }
    await page.screenshot({ path: path.join(SHOTS, `${tag}-step${step}.png`) });

    const next = page.locator('.tour-btn-next');
    const label = await next.textContent();
    await next.tap();
    if (/Explore/.test(label)) break;
    await page.waitForTimeout(350);
  }

  await expect(overlay, 'tour completes and is dismissed').toHaveCount(0, { timeout: 5000 });
  await page.screenshot({ path: path.join(SHOTS, `${tag}-done.png`) });
});
