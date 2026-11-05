import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, BASE, SHOTS } from './helpers.js';

// Reader sections are picked from the table of contents rendered in
// #info-section; the reader is a full-map overlay (beside the panel on
// desktop) with only a title and a close button — no country pulldown, no
// tab bar. The merged "What happened: Refusal vs Cooperation" entry has the
// slug "refusal-coop"; ?view=history and ?view=coop alias to it
// (url.js LEGACY_VIEW_ALIASES). "See more statistics" is covered in
// stats-breakdown.spec.js.
test.describe('Desktop Chromium 1400x900', () => {
  const tag = 'desktop-chromium';
  let ctx, page, errors;

  test.beforeAll(async ({ browser }) => {
    ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    page = await ctx.newPage();
    errors = [];
    page.on('pageerror', e => errors.push(String(e).slice(0, 150)));
  });

  test.afterAll(async () => {
    await ctx.close();
  });

  const readerOpen = () => page.locator('#reference-view:not(.hidden)');
  const readerClosed = () => page.locator('#reference-view.hidden');

  test('table of contents renders in the panel; Statistics is a button below the stats table; disclaimer precedes the researched entries', async () => {
    await ready(page, `${BASE}/?cc=DE`);
    const toc = page.locator('#info-section .toc-item');
    await expect(toc.first()).toBeVisible();
    const labels = await toc.allTextContents();
    expect(labels.length, `ToC entries for DE: ${labels.join('|')}`).toBeGreaterThanOrEqual(4);
    expect(labels.some(l => /^summary/i.test(l))).toBeTruthy();
    expect(labels.some(l => /treaties.*authorized/i.test(l))).toBeTruthy();
    expect(labels.some(l => /what happened.*refusal vs cooperation/i.test(l))).toBeTruthy();
    expect(labels.some(l => /legal.*how to say no/i.test(l))).toBeTruthy();
    expect(labels.some(l => /statistics/i.test(l)), 'Statistics is not a ToC entry').toBeFalsy();
    await expect(page.locator('.stats-toc-item', { hasText: 'Statistics' })).toHaveCount(1);

    // The AI-research disclaimer sits after Summary and before the first
    // researched entry.
    await expect(page.locator('#info-section .toc-disclaimer')).toHaveCount(1);
    const order = await page.evaluate(() =>
      Array.from(document.querySelector('#info-section .toc-list').children).map(el => el.className.split(' ')[0]));
    expect(order.slice(0, 2), `ToC order: ${order.join(',')}`).toEqual(['toc-item', 'toc-disclaimer']);

    await expect(page.locator('.refview-country-select, select[aria-label="Select country"]')).toHaveCount(0);
  });

  test('ToC click opens the reader beside the panel; URL updates immediately; Esc closes and returns focus', async () => {
    await ready(page, `${BASE}/?cc=DE`);
    const treatiesItem = page.locator('#info-section .toc-item', { hasText: 'Treaties' }).first();
    await treatiesItem.click();

    await expect(readerOpen()).toHaveCount(1);
    await expect(page.locator('#sidebar'), 'panel stays visible beside the reader').toBeVisible();
    await expect(treatiesItem).toHaveClass(/active/);
    await expect.poll(() => page.url()).toContain('view=auth');

    expect(await page.locator('.cite-marker').count(), 'in-section footnotes').toBeGreaterThan(0);
    expect(await page.locator('.ref-citations .cite-entry').count(), 'Sources list').toBeGreaterThan(0);
    await page.locator('.cite-marker').first().click();
    await expect(page.locator('.cite-entry.cite-selected')).toHaveCount(1);
    await expect.poll(() => page.url()).toContain('cite=');
    await page.screenshot({ path: path.join(SHOTS, `${tag}-toc-reader-open.png`) });

    await page.keyboard.press('Escape');
    await expect(readerClosed()).toHaveCount(1);
    await expect.poll(() => page.url()).not.toContain('view=');
    await expect.poll(() => page.evaluate(() => document.activeElement?.dataset?.slug),
      { message: 'focus returns to the just-closed ToC entry' }).toBe('auth');
  });

  test('clicking the active ToC entry again closes it; a different entry switches without closing', async () => {
    await ready(page, `${BASE}/?cc=DE&view=auth`);
    const combinedItem = page.locator('#info-section .toc-item', { hasText: 'What happened' }).first();
    await expect(readerOpen(), 'deep link opens the reader on load').toHaveCount(1);

    await combinedItem.click();
    await expect(readerOpen(), 'switching entries keeps the reader open').toHaveCount(1);
    await expect(combinedItem).toHaveClass(/active/);
    await expect.poll(() => page.url()).toContain('view=refusal-coop');

    await combinedItem.click();
    await expect(readerClosed(), 're-clicking the active entry closes the reader').toHaveCount(1);
  });

  test('clicking elsewhere in the panel closes the reader but still performs its own action', async () => {
    await ready(page, `${BASE}/?cc=DE&view=auth`);
    await expect(readerOpen()).toHaveCount(1);

    await page.click('#stats-section .section-header');
    await expect(readerClosed(), 'the Filters trigger closes the reader').toHaveCount(1);
    await expect(page.locator('.filter-view, #filter-theater-group .toggle-btn').first(),
      'the Filters trigger still opened the filter view').toBeVisible();
  });

  // The merged entry: two distinct sub-sections sharing ONE continuously
  // numbered bibliography — a source cited in both halves appears once in
  // Sources and reuses its number. PL is a real-data case with two such
  // shared sources (country.reference.json hex codes 0d7a8 and 8b5e6).
  test('merged Refusal-vs-Cooperation entry: distinct sub-headings, one continuous shared bibliography', async () => {
    await ready(page, `${BASE}/?cc=PL`);
    await page.locator('#info-section .toc-item', { hasText: 'What happened' }).first().click();
    await expect(readerOpen()).toHaveCount(1);
    await expect.poll(() => page.url()).toContain('view=refusal-coop');

    const subHeadings = page.locator('#reference-view .refview-subh');
    await expect(subHeadings).toHaveCount(2);
    await expect(subHeadings).toHaveText(['Refusal history', '2026 Iran-war cooperation']);

    // Marker ordinals per sub-section, grouped by the nearest preceding h4.
    const bySub = await page.evaluate(() => {
      const prose = document.querySelector('#reference-view .refview-prose');
      let cur = null;
      const out = {};
      for (const el of Array.from(prose.children)) {
        if (el.tagName === 'H4') { cur = el.textContent.trim(); out[cur] = out[cur] || []; continue; }
        if (el.classList.contains('ref-citations')) break;
        el.querySelectorAll('.cite-marker').forEach(m => out[cur]?.push(m.textContent.trim()));
      }
      return out;
    });
    const rh = bySub['Refusal history'] || [];
    const co = bySub['2026 Iran-war cooperation'] || [];
    expect(rh.length, 'refusal-history markers').toBeGreaterThan(0);
    expect(co.length, 'cooperation markers').toBeGreaterThan(0);

    const shared = co.filter(o => rh.includes(o));
    expect(shared.length, `an ordinal is reused across both sub-sections (rh=${rh}, co=${co})`).toBeGreaterThan(0);

    const unique = new Set([...rh, ...co]);
    await expect(page.locator('#reference-view .ref-citations .cite-entry'),
      'one Sources entry per unique ordinal').toHaveCount(unique.size);

    const badges = (await page.locator('#reference-view .cite-badge').allTextContents()).map(Number);
    expect([...badges].sort((a, b) => a - b), 'badges are a continuous 1..N run')
      .toEqual(Array.from({ length: badges.length }, (_, i) => i + 1));
    await page.screenshot({ path: path.join(SHOTS, `${tag}-merged-refusal-coop.png`) });

    // A reused marker in the second sub-section resolves to the single,
    // deduplicated source entry. Exact match: "1" must not match "10".
    const reusedOrd = shared[0];
    await page.locator('#reference-view .cite-marker', { hasText: new RegExp(`^${reusedOrd}$`) }).last().click();
    const selected = page.locator('#reference-view .cite-entry.cite-selected');
    await expect(selected).toHaveCount(1);
    await expect(selected).toHaveAttribute('data-ord', reusedOrd);
  });

  test('legacy history/coop slugs open the merged entry; an unknown view slug shows the repair overlay', async () => {
    await ready(page, `${BASE}/?cc=PL&view=history`);
    await expect(page.locator('.param-error')).toHaveCount(0);
    await expect(page.locator('#info-section .toc-item[data-slug="refusal-coop"].active')).toHaveCount(1);

    await ready(page, `${BASE}/?cc=PL&view=coop`);
    await expect(page.locator('.param-error')).toHaveCount(0);
    await expect(page.locator('#info-section .toc-item[data-slug="refusal-coop"].active')).toHaveCount(1);

    // The param-error path returns before the map reaches "idle", so
    // #loading-overlay never detaches and ready() would hang; navigate
    // directly and wait for the overlay.
    await page.goto(`${BASE}/?cc=PL&view=stats`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    const overlay = page.locator('.param-error');
    await expect(overlay).toHaveCount(1, { timeout: 30000 });
    const items = await overlay.locator('li').allTextContents();
    expect(items.some(t => /stats/.test(t) && /not a view/.test(t)), `overlay items: ${items.join(' | ')}`).toBeTruthy();
  });

  test('deep link with view + cite opens the reader pre-highlighted', async () => {
    await ready(page, `${BASE}/?cc=DE&view=history&cite=1`);
    await expect(readerOpen()).toHaveCount(1);
    await expect(page.locator('#info-section .toc-item', { hasText: 'What happened' }).first()).toHaveClass(/active/);
    await expect(page.locator('.cite-entry.cite-selected')).toHaveCount(1);
  });

  test('zero JS errors', async () => {
    expect(errors).toEqual([]);
  });
});
