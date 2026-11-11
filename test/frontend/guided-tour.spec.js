import path from 'path';
import { test, expect } from '@playwright/test';
import { BASE, SHOTS, passIntro } from './helpers.js';

// First-visit behaviour, so no ready() (it pre-sets "welcomed").
async function firstVisit(page, url) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('html[data-app-ready="1"]', { timeout: 90000 });
}

const welcomed = (page) => page.evaluate(() => localStorage.getItem('welcomed'));

test.describe('Guided Tour', () => {
  test('tour starts and completes on a fresh parameterless load', async ({ page }) => {
    test.slow();   // intro card plus every tour step, on emulated phones under load
    const tag = test.info().project.name + '-guided-tour';
    await firstVisit(page, `${BASE}/`);
    await passIntro(page);
    const overlay = page.locator('#tour-overlay');
    await expect(overlay).toBeVisible({ timeout: 30000 });
    await page.screenshot({ path: path.join(SHOTS, `${tag}-tour-shown.png`) });

    // Advance on the "n / N" counter changing, not on a fixed delay: a click
    // that lands mid-transition is swallowed.
    const progress = page.locator('.tour-progress');
    const next = page.locator('.tour-btn-next');
    for (let step = 1; step <= 10; step++) {
      await expect(next).toBeVisible({ timeout: 10000 });
      const before = await progress.textContent();
      const label = await next.textContent();
      await next.click({ timeout: 10000 });
      if (label === 'Explore the map') break;
      await expect(progress, `step counter advances past "${before}"`).not.toHaveText(before, { timeout: 10000 });
    }
    await expect(overlay).toHaveCount(0, { timeout: 10000 });
    await expect.poll(() => welcomed(page), { timeout: 5000 }).toBe('1');
    await page.screenshot({ path: path.join(SHOTS, `${tag}-tour-completed.png`) });
  });

  test('a parameterised first load offers the tour as a choice', async ({ page }) => {
    const tag = test.info().project.name + '-tour-choice';
    await firstVisit(page, `${BASE}/?cc=SK`);
    const tourBtn = page.locator('#tour-choice-tour');
    const skipBtn = page.locator('#tour-choice-skip');
    await expect(tourBtn).toBeVisible({ timeout: 15000 });
    await expect(skipBtn).toBeVisible();
    await page.screenshot({ path: path.join(SHOTS, `${tag}-choice.png`) });

    await skipBtn.click();
    await expect(page.locator('#welcome-overlay')).toHaveCount(0);
    await expect.poll(() => welcomed(page)).toBe('1');
    await page.screenshot({ path: path.join(SHOTS, `${tag}-skipped.png`) });
  });

  // tour.js showTourOrWelcome: a landscape phone gets the tap hint instead
  // of the tour and leaves "welcomed" unset so the tour runs on the next
  // portrait load. The tap hint appearing is the signal the decision ran.
  test('tour does not start on a landscape phone, and appears after rotating to portrait', async ({ page }) => {
    const tag = test.info().project.name + '-tour-landscape';
    await page.setViewportSize({ width: 844, height: 390 });
    await firstVisit(page, `${BASE}/`);
    await expect(page.locator('#tap-hint')).toBeAttached({ timeout: 30000 });
    await expect(page.locator('#tour-overlay')).toHaveCount(0);
    await expect(page.locator('#tldr-overlay')).toHaveCount(0);
    expect(await welcomed(page)).toBeNull();
    await page.screenshot({ path: path.join(SHOTS, `${tag}.png`) });

    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForSelector('html[data-app-ready="1"]', { timeout: 90000 });
    await expect(page.locator('#tldr-overlay')).toBeVisible({ timeout: 30000 });
    await page.screenshot({ path: path.join(SHOTS, `${tag}-after-rotate.png`) });
  });
});
