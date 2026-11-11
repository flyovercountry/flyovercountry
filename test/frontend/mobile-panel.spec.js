// Phone bottom edge: the collapsed panel bar is flush with the viewport
// bottom, the toolbar stacks directly above it, and anything that opens out
// of the toolbar paints over the bar. Measured (boundingBox, elementFromPoint)
// rather than read from the stylesheet: .mobile-toolbar is a stacking
// context, so z-index values inside it are not comparable with the bar's.
import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, BASE, SHOTS } from './helpers.js';
import { IPHONE13_PORTRAIT } from '../../playwright.config.js';

test('mobile portrait: collapsed bar flush at bottom, menus paint above it', async ({ browser }) => {
  const ctx = await browser.newContext({ ...IPHONE13_PORTRAIT });
  const page = await ctx.newPage();
  const errors = await ready(page, `${BASE}/`);
  const vh = await page.evaluate(() => window.innerHeight);

  const bar = await page.locator('.collapsed-bar').boundingBox();
  expect(bar).toBeTruthy();
  expect(Math.abs(vh - (bar.y + bar.height)), 'bar flush with the bottom edge').toBeLessThanOrEqual(1);

  const tb = await page.locator('.mobile-toolbar').boundingBox();
  expect(tb).toBeTruthy();
  expect((tb.y + tb.height) - bar.y, 'toolbar does not overlap the bar').toBeLessThanOrEqual(1);

  // The tap-a-country hint, when shown, sits above the toolbar.
  const hint = await page.locator('#tap-hint').boundingBox().catch(() => null);
  if (hint) expect((hint.y + hint.height) - tb.y, 'tap hint clears the toolbar').toBeLessThanOrEqual(1);
  await page.screenshot({ path: path.join(SHOTS, 'mobilepanel-after-portrait.png') });

  // The Share sheet is fixed to bottom:0 and genuinely covers the bar's
  // band; the DOM must report the sheet as topmost there.
  await page.locator('#share-menu-trigger').tap();
  await expect(page.locator('#share-menu-list')).toBeVisible();
  const topmost = await page.evaluate(([x, y]) => {
    const el = document.elementFromPoint(x, y);
    return { inShare: !!el?.closest('#share-menu-list'), inBar: !!el?.closest('.collapsed-bar') };
  }, [195, bar.y + bar.height / 2]);
  expect(topmost.inShare, 'Share sheet paints above the collapsed bar').toBeTruthy();
  expect(topmost.inBar).toBeFalsy();
  if (hint) {
    const atHint = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.id === 'tap-hint', [hint.x + hint.width / 2, hint.y + hint.height / 2]);
    expect(atHint, 'tap hint does not float over the open sheet').toBeFalsy();
  }
  await page.screenshot({ path: path.join(SHOTS, 'mobilepanel-after-share-open.png') });

  expect(errors).toEqual([]);
  await ctx.close();
});

test('info button opens the TL;DR card, whose pill reads Close', async ({ page }) => {
  const errors = await ready(page, `${BASE}/`);
  await page.locator('#breadcrumb-mode-btn').tap();
  const card = page.locator('#tldr-overlay');
  await expect(card).toBeVisible();
  await expect(card.locator('.tour-skip-top')).toHaveText('Close');
  await card.locator('.tour-skip-top').tap();
  await expect(card).toHaveCount(0);
  await expect(page.locator('#tour-overlay')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('info button is hidden while the sheet is expanded', async ({ page }) => {
  await ready(page, `${BASE}/`);
  const btn = page.locator('#breadcrumb-mode-btn');
  await expect(btn).toBeVisible();
  await page.locator('.collapsed-sheet').tap();
  await expect(page.locator('#sidebar')).toHaveClass(/expanded/);
  await expect(btn).toBeHidden();
  await page.locator('.panel-close-btn').tap();
  await expect(btn).toBeVisible();
});
