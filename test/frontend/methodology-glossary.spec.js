import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, BASE, SHOTS } from './helpers.js';
import { PIXEL7_PORTRAIT } from '../../playwright.config.js';

// With no country selected the table of contents lists the global entries
// (global-content.js globalTabsFor): Statistics, Methodology, Glossary and
// Aircraft roles. Each opens in the same full-map reader the per-country
// sections use; the URL scheme is ?view=methodology / ?view=glossary with
// no cc. The reader has no tab-bar UI anywhere.
test.describe('Desktop Chromium 1400x900', () => {
  const tag = 'methtoc-desktop';
  let ctx, page;

  test.beforeAll(async ({ browser }) => {
    ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    page = await ctx.newPage();
  });

  test.afterAll(async () => {
    await ctx.close();
  });

  const readerOpen = () => page.locator('#reference-view:not(.hidden)');
  const readerClosed = () => page.locator('#reference-view.hidden');

  test('no-country ToC lists Statistics, Methodology, Glossary and Aircraft roles; roles opens in the reader', async () => {
    await ready(page, `${BASE}/`);
    const toc = page.locator('#info-section .toc-item');
    await expect(toc).toHaveCount(4);
    const labels = await toc.allTextContents();
    expect(labels.some(l => /^statistics/i.test(l)), labels.join('|')).toBeTruthy();
    expect(labels.some(l => /^methodology/i.test(l)), labels.join('|')).toBeTruthy();
    expect(labels.some(l => /^glossary/i.test(l)), labels.join('|')).toBeTruthy();
    expect(labels.some(l => /aircraft roles/i.test(l)), labels.join('|')).toBeTruthy();

    // Aircraft roles interleaves live aircraft counts, so it is a reader
    // tab (a button), not a link to the pre-rendered /roles.html.
    const rolesEntry = page.locator('#info-section .toc-item[data-slug="roles"]');
    expect(await rolesEntry.evaluate(el => el.tagName)).toBe('BUTTON');
    await rolesEntry.click();
    await expect(readerOpen()).toHaveCount(1);
    await expect(page.locator('#reference-view .roles-tab').first()).toBeVisible({ timeout: 10000 });
    await page.keyboard.press('Escape');
    await expect(readerClosed()).toHaveCount(1);

    // The AI-research disclaimer belongs to per-country researched entries
    // only; there is no standalone methodology footer link.
    await expect(page.locator('#info-section .toc-disclaimer')).toHaveCount(0);
    await expect(page.locator('.methodology-link')).toHaveCount(0);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-toc.png`) });
  });

  test('Methodology opens in the reader with the real document; URL is ?view=methodology with no cc', async () => {
    await ready(page, `${BASE}/`);
    const item = page.locator('#info-section .toc-item', { hasText: 'Methodology' }).first();
    await item.click();
    await expect(readerOpen()).toHaveCount(1);
    await expect(item).toHaveClass(/active/);

    await expect.poll(() => new URL(page.url()).searchParams.get('view')).toBe('methodology');
    expect(new URL(page.url()).searchParams.has('cc')).toBeFalsy();

    const prose = page.locator('.methodology-prose');
    await expect(prose).toBeVisible();
    const bodyText = await prose.textContent();
    expect(bodyText.length, 'methodology prose is the real document').toBeGreaterThan(200);
    expect(bodyText).toMatch(/ADS-B|Automatic Dependent Surveillance/i);

    await expect(page.locator('.tab-bar, .tab-btn, [role="tablist"], [role="tabpanel"]'), 'no tab-bar UI in the reader').toHaveCount(0);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-methodology-open.png`) });
  });

  test('Glossary opens as a term list; URL is ?view=glossary', async () => {
    await ready(page, `${BASE}/`);
    await page.locator('#info-section .toc-item', { hasText: 'Glossary' }).first().click();
    await expect(readerOpen()).toHaveCount(1);
    await expect.poll(() => new URL(page.url()).searchParams.get('view')).toBe('glossary');

    const terms = page.locator('.glossary-dt');
    await expect(terms.first()).toBeVisible();
    expect(await terms.count()).toBeGreaterThanOrEqual(5);
    expect(await terms.allTextContents()).toEqual(expect.arrayContaining([expect.stringMatching(/MLAT/i)]));

    const list = page.locator('.glossary-list');
    await expect(list).toHaveCount(1);
    expect(await list.evaluate(el => el.tagName)).toBe('DL');
    await page.screenshot({ path: path.join(SHOTS, `${tag}-glossary-open.png`) });
  });

  test('clicking the active entry again closes it; switching entries switches without closing', async () => {
    await ready(page, `${BASE}/`);
    const methItem = page.locator('#info-section .toc-item', { hasText: 'Methodology' }).first();
    const glossItem = page.locator('#info-section .toc-item', { hasText: 'Glossary' }).first();

    await methItem.click();
    await expect(readerOpen()).toHaveCount(1);
    await glossItem.click();
    await expect(readerOpen(), 'still open after switching').toHaveCount(1);
    await expect(glossItem).toHaveClass(/active/);
    await expect(methItem).not.toHaveClass(/active/);

    await glossItem.click();
    await expect(readerClosed()).toHaveCount(1);
  });

  test('deep link ?view=methodology opens directly; ?cc=DE&view=methodology is repaired', async () => {
    await ready(page, `${BASE}/?view=methodology`);
    await expect(readerOpen()).toHaveCount(1);
    await expect(page.locator('.methodology-prose')).toBeVisible();

    // A selected country plus a global view is a contradiction the ToC
    // never produces; checkAndRepair must surface it rather than guess.
    await page.goto(`${BASE}/?cc=DE&view=methodology`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    const items = page.locator('.param-error li');
    await expect(items).toHaveCount(1, { timeout: 30000 });
    await expect(items.first()).toHaveText(/view.*isn.t available while a country is selected/);
    const link = await page.locator('.param-error a').getAttribute('href');
    expect(link).toContain('cc=DE');
    expect(link).not.toContain('view=');
  });

  test('selecting a country via browser navigation closes an open global reader', async () => {
    await ready(page, `${BASE}/?view=glossary`);
    await expect(readerOpen()).toHaveCount(1);

    // Same mechanism as the back/forward buttons (app.js popstate), so the
    // live transition is exercised rather than a fresh load.
    await page.evaluate(() => {
      window.history.pushState(null, '', '/?cc=DE');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await expect(readerClosed()).toHaveCount(1);
    await expect(page.locator('#info-section .toc-item', { hasText: /summary|treaties|legal/i }).first()).toBeVisible();
  });

  test('zero JS errors on a global-view load', async () => {
    const errs = [];
    const p2 = await ctx.newPage();
    p2.on('pageerror', e => errs.push(String(e).slice(0, 150)));
    await ready(p2, `${BASE}/?view=glossary`);
    await expect(p2.locator('#reference-view:not(.hidden)')).toHaveCount(1);
    await p2.close();
    expect(errs).toEqual([]);
  });
});

test.describe('Mobile Portrait (412x839)', () => {
  test('no-country ToC + Methodology/Glossary readable in the expanded sheet, no overflow', async ({ browser }) => {
    const tag = 'methtoc-mobile-portrait';
    const ctx = await browser.newContext({ ...PIXEL7_PORTRAIT });
    const page = await ctx.newPage();
    await ready(page, `${BASE}/`);

    const bar = page.locator('.collapsed-bar.collapsed-sheet');
    const expanded = page.locator('#sidebar.expanded');
    await bar.tap();
    await expect(expanded).toBeVisible();

    const toc = page.locator('#info-section .toc-item');
    await expect(toc).toHaveCount(4);
    await toc.first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(SHOTS, `${tag}-toc-expanded.png`) });

    const overflow = () => page.evaluate(() => {
      const body = document.querySelector('.refview-body');
      return body.scrollWidth - body.clientWidth;
    });

    await page.locator('#info-section .toc-item', { hasText: 'Methodology' }).first().tap();
    await expect(page.locator('#reference-view:not(.hidden)')).toHaveCount(1);
    await expect(page.locator('.methodology-prose')).toBeVisible();
    expect(await overflow(), 'methodology content does not overflow horizontally').toBeLessThanOrEqual(2);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-methodology-open.png`) });

    await page.locator('.refview-close').tap();
    await expect(page.locator('#reference-view.hidden')).toHaveCount(1);
    if (await bar.isVisible()) {
      await bar.tap();
      await expect(expanded).toBeVisible();
    }
    await page.locator('#info-section .toc-item', { hasText: 'Glossary' }).first().tap();
    await expect(page.locator('.glossary-list')).toBeVisible();
    expect(await overflow(), 'glossary content does not overflow horizontally').toBeLessThanOrEqual(2);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-glossary-open.png`) });

    await ctx.close();
  });
});
