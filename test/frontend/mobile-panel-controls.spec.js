import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, BASE, SHOTS } from './helpers.js';

const DESKTOP = { viewport: { width: 1440, height: 900 } };
const expandBar = page => page.locator('.collapsed-sheet').click();

test('legend is collapsed on the phone and expanded on desktop', async ({ page, browser }) => {
  const errors = await ready(page, `${BASE}/`);
  await expect(page.locator('#onmap-legend')).toHaveClass(/collapsed/);
  await page.screenshot({ path: path.join(SHOTS, 'after-a-phone-legend-collapsed.png') });
  expect(errors).toEqual([]);

  const ctx = await browser.newContext(DESKTOP);
  const desk = await ctx.newPage();
  const deskErrors = await ready(desk, `${BASE}/`);
  await expect(desk.locator('#onmap-legend')).not.toHaveClass(/collapsed/);
  await expect(desk.locator('#onmap-legend-tiers')).toContainText('Not analyzed');
  await desk.screenshot({ path: path.join(SHOTS, 'after-e-desktop-legend.png') });
  expect(deskErrors).toEqual([]);
  await ctx.close();
});

test('legend lists a flat Not analyzed row', async ({ page }) => {
  await ready(page, `${BASE}/`);
  await page.locator('#onmap-legend-pill, #onmap-legend button').first().click();
  const rows = page.locator('#onmap-legend-tiers .legend-tier');
  const last = rows.last();
  await expect(last).toContainText('Not analyzed');
  const sw = await last.locator('.legend-swatch').evaluate(el => {
    const s = getComputedStyle(el);
    return { border: s.borderTopWidth + s.borderTopStyle, bg: s.backgroundColor };
  });
  const first = await rows.first().locator('.legend-swatch').evaluate(el => {
    const s = getComputedStyle(el);
    return s.borderTopWidth + s.borderTopStyle;
  });
  expect(sw.border).toBe(first);
});

test('expanded sheet shows Regular active for a selected country', async ({ page }) => {
  await ready(page, `${BASE}/?cc=DE`);
  await expandBar(page);
  const group = page.locator('.mode-toggle');
  await expect(group.locator('.toggle-btn')).toHaveCount(2);
  await expect(group.locator('.toggle-btn.active')).toHaveText('Regular');
  await expect(group.getByText('Refusal')).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, 'after-c-phone-sheet-regular.png') });
});

test('Refusal segment reflects wi and Regular clears it', async ({ page }) => {
  await ready(page, `${BASE}/?cc=DE&wi=2026-02-28`);
  const group = page.locator('.mode-toggle');
  await expect(group.locator('.toggle-btn.active')).toHaveText('Refusal');
  await page.screenshot({ path: path.join(SHOTS, 'after-d-phone-sheet-refusal.png') });
  await group.getByText('Regular').click();
  await expect(group.locator('.toggle-btn.active')).toHaveText('Regular');
  await expect.poll(() => page.evaluate(() => location.search)).not.toContain('wi=');
  await expect(page.locator('#sidebar')).toHaveClass(/expanded/);
  await group.getByText('Refusal').click();
  await expect(group.locator('.toggle-btn.active')).toHaveText('Refusal');
  await expect(page.locator('#sidebar')).toHaveClass(/expanded/);
  await expect.poll(() => page.evaluate(() => location.search)).toContain('wi=');
});

test('collapsed bar shows grabber and hint until first expansion', async ({ page }) => {
  await ready(page, `${BASE}/`);
  const bar = page.locator('.collapsed-sheet');
  await expect(bar.locator('.collapsed-grabber')).toBeVisible();
  await expect(bar.locator('.collapsed-subtitle')).toContainText('Tap for details');
  expect((await bar.boundingBox()).height).toBeGreaterThanOrEqual(52);
  await page.screenshot({ path: path.join(SHOTS, 'after-b-phone-collapsed-bar.png') });
  await bar.click();
  await page.locator('.panel-close-btn').click();
  await expect(bar.locator('.collapsed-subtitle')).not.toContainText('Tap for details');
});
