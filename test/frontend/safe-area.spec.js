import path from 'path';
import { test, expect } from '@playwright/test';
import { BASE, SHOTS, ready, passIntro } from './helpers.js';

// With viewport-fit=cover (index.html) the page draws under a phone's notch
// and home indicator, and every edge-anchored control must stay clear of
// them via env(safe-area-inset-*). Playwright reports zero insets, so this
// uses Chromium's DevTools override to emulate an iPhone-14-Pro-class
// device (59px top, 34px bottom) — Chromium only, hence the mobile-chromium
// project. The zero-inset pass guards the common case: nothing may move
// when there are no insets.
const INSETS = { top: 59, left: 0, bottom: 34, right: 0 };
const NONE = { top: 0, left: 0, bottom: 0, right: 0 };

const rect = (page, sel) => page.evaluate((s) => {
  const el = document.querySelector(s);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { top: r.top, bottom: r.bottom, gapBelow: innerHeight - r.bottom, height: r.height };
}, sel);

for (const [label, insets] of [['insets 59/34', INSETS], ['no insets', NONE]]) {
  test(`edge controls stay clear of the safe area (${label})`, async ({ page, context }) => {
    test.skip(test.info().project.name !== 'mobile-chromium', 'CDP safe-area emulation is Chromium-only');
    const tag = `${test.info().project.name}-safe-area-${insets.top ? 'on' : 'off'}`;
    const cdp = await context.newCDPSession(page);
    await cdp.send('Emulation.setSafeAreaInsetsOverride', { insets });
    const T = insets.top, B = insets.bottom;

    // Tour first, on a plain navigation: ready() below installs an init
    // script that marks the visitor as welcomed, which suppresses it.
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForSelector('#loading-overlay', { state: 'detached', timeout: 90000 }).catch(() => {});
    // The intro card must itself clear the notch: its Skip pill is the same
    // element the tour uses, positioned from --sai-top.
    await expect(page.locator('#tldr-overlay')).toBeVisible({ timeout: 30000 });
    expect((await rect(page, '#tldr-overlay .tour-skip-top')).top, 'intro Skip pill below the notch').toBeGreaterThanOrEqual(T + 16 - 1);
    expect((await rect(page, '.tldr-box')).gapBelow, 'intro card above the home indicator').toBeGreaterThanOrEqual(B + 8);
    await passIntro(page);
    await expect(page.locator('#tour-overlay')).toBeVisible({ timeout: 30000 });
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.tour-annotations')).opacity === '1', null, { timeout: 5000 });
    expect((await rect(page, '.tour-skip-top')).top, 'Skip pill below the notch').toBeGreaterThanOrEqual(T + 16 - 1);
    expect((await rect(page, '.tour-nav')).gapBelow, 'tour nav above the home indicator').toBeGreaterThanOrEqual(B + 8);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-tour.png`) });

    await ready(page, `${BASE}/?cc=DE`);
    await page.waitForTimeout(1200);
    const vars = await page.evaluate(() => {
      const cs = getComputedStyle(document.documentElement);
      return { top: parseFloat(cs.getPropertyValue('--sai-top')), bottom: parseFloat(cs.getPropertyValue('--sai-bottom')) };
    });
    expect(vars, '--sai-* custom properties mirror the device insets').toEqual({ top: T, bottom: B });

    // Top edge.
    expect((await rect(page, '.maplibregl-ctrl-top-right')).top, 'zoom control below the notch').toBeGreaterThanOrEqual(T + 10 - 1);
    expect((await rect(page, '#onmap-legend')).top, 'legend below the notch').toBeGreaterThanOrEqual(Math.max(12, T) - 1);
    expect((await rect(page, '#whatif-pulldown')).top, 'What-If pulldown below the notch').toBeGreaterThanOrEqual(T + 12 - 1);

    // Bottom edge: the bar reaches the physical edge and extends through
    // the inset; its text and everything stacked above it sit above the
    // home indicator.
    const bar = await rect(page, '.collapsed-sheet');
    expect(bar.gapBelow, 'collapsed bar is flush with the bottom edge').toBeLessThanOrEqual(1);
    expect(Math.abs(bar.height - (52 + B)), 'collapsed bar = 52px + bottom inset').toBeLessThanOrEqual(1);
    expect((await rect(page, '.collapsed-text')).gapBelow, 'bar text above the home indicator').toBeGreaterThanOrEqual(B + 8);
    expect(Math.abs((await rect(page, '#mobile-toolbar')).gapBelow - (52 + B)), 'toolbar stacked above the bar').toBeLessThanOrEqual(1);
    expect(Math.abs((await rect(page, '.maplibregl-ctrl-bottom-right')).gapBelow - (52 + B)), 'MapLibre bottom controls above the bar').toBeLessThanOrEqual(1);
    expect((await rect(page, '#breadcrumb-mode-btn')).gapBelow, 'breadcrumb button above the bar').toBeGreaterThanOrEqual(52 + B + 4);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-map.png`) });

    // Expanded sheet: header below the notch.
    await page.locator('.collapsed-sheet').tap();
    await expect(page.locator('#sidebar.expanded')).toBeVisible({ timeout: 10000 });
    expect((await rect(page, '.panel-close-row')).top, 'sheet header below the notch').toBeGreaterThanOrEqual(T - 1);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-sheet.png`) });

    // Filter view: its close button below the notch.
    await page.locator('.panel-close-btn').tap();
    await expect(page.locator('#sidebar.expanded')).toHaveCount(0, { timeout: 10000 });
    await page.locator('#mobile-filter-btn').tap();
    await expect(page.locator('.filter-close-x')).toBeVisible({ timeout: 10000 });
    expect((await rect(page, '.filter-close-x')).top, 'filter close button below the notch').toBeGreaterThanOrEqual(T + 8 - 1);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-filters.png`) });
    await page.locator('.filter-close-x').tap();
    await expect(page.locator('#sidebar.expanded')).toHaveCount(0, { timeout: 10000 });

    // Reader (full-screen on phones): header below the notch.
    await page.locator('.collapsed-sheet').tap();
    await expect(page.locator('#sidebar.expanded')).toBeVisible({ timeout: 10000 });
    await page.locator('.stats-toc-item', { hasText: 'Statistics' }).first().tap();
    await expect(page.locator('#reference-view:not(.hidden)')).toHaveCount(1, { timeout: 10000 });
    expect((await rect(page, '.refview-header')).top, 'reader header below the notch').toBeGreaterThanOrEqual(T - 1);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-reader.png`) });
  });
}
