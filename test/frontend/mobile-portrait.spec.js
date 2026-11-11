import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, BASE, SHOTS } from './helpers.js';

test.describe('Mobile Portrait', () => {
  const bar = (page) => page.locator('.collapsed-bar.collapsed-sheet');

  async function expandSheet(page) {
    await bar(page).tap();
    await expect(page.locator('#sidebar.expanded')).toBeVisible();
  }

  test('portrait smoke', async ({ page }) => {
    const tag = test.info().project.name + '-portrait';
    const errors = await ready(page, `${BASE}/`);
    await expect(page.locator('#map')).toBeVisible();
    await expect(page.locator('#mobile-toolbar')).toBeVisible();
    expect(errors).toEqual([]);
    await page.screenshot({ path: path.join(SHOTS, `${tag}.png`) });
  });

  test('unified stats table tap-reveal (touch)', async ({ page }) => {
    const tag = test.info().project.name + '-tap';
    await ready(page, `${BASE}/?cc=SK`);
    await expandSheet(page);
    const cell = page.locator('.unified-stats-grid.stat-total .stat-col-narrow').first();
    await expect(cell).toBeVisible();
    await page.screenshot({ path: path.join(SHOTS, `${tag}-before.png`) });

    await cell.tap();
    await expect(cell).toHaveClass(/expanded/);
    await expect(cell.locator('.stat-col-detail')).toHaveCount(1);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-after.png`) });

    // stat-tap.js ignores a second toggle on the same cell within
    // DEBOUNCE_MS (600) so a tap's synthesized click cannot undo it.
    await page.waitForTimeout(700);
    await cell.tap();
    await expect(cell.locator('.stat-col-detail')).toHaveCount(0);
  });

  test('"Previously crossed" two-line label stays within its cell and column-aligned', async ({ page }) => {
    const tag = test.info().project.name + '-colnames';
    await ready(page, `${BASE}/?cc=SK`);
    await expandSheet(page);
    const subCell = page.locator('.unified-stats-grid.stat-subheader .stat-subhead-2line').first();
    await expect(subCell).toBeVisible();
    expect((await subCell.textContent()).replace(/\s+/g, ' ').trim()).toBe('Previously crossed');
    expect(await subCell.evaluate(el => el.scrollWidth - el.clientWidth), 'label overflow').toBeLessThanOrEqual(1);

    // The header, subheader and data rows are separate grids sharing one
    // template; their value columns must land on the same x positions.
    const xs = (sel) => page.locator(sel).evaluateAll(els => els.map(el => Math.round(el.getBoundingClientRect().x)));
    expect(await xs('.unified-stats-grid.stat-subheader .stat-col-narrow'))
      .toEqual(await xs('.unified-stats-grid.stat-total .stat-col-narrow'));

    const subHeight = await page.locator('.unified-stats-grid.stat-subheader').first().evaluate(el => el.getBoundingClientRect().height);
    expect(subHeight).toBeGreaterThan(0);
    expect(subHeight).toBeLessThan(32);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-subheader.png`) });
  });

  test('ToC entry collapses the panel and hides the toolbar; tapping the bar closes the reader and re-expands', async ({ page }) => {
    const tag = test.info().project.name + '-toc';
    await ready(page, `${BASE}/?cc=DE`);
    await expandSheet(page);
    await page.locator('#info-section .toc-item', { hasText: 'Treaties' }).first().tap();

    await expect(page.locator('#reference-view:not(.hidden)')).toHaveCount(1);
    await expect(page.locator('#sidebar.expanded')).toHaveCount(0);
    await expect(bar(page)).toBeVisible();
    await expect(page.locator('.collapsed-subtitle'), 'bar names the open section').toHaveText(/treaties/i);
    await expect(page.locator('.mobile-toolbar')).toBeHidden();
    await page.screenshot({ path: path.join(SHOTS, `${tag}-reading.png`) });

    await bar(page).tap();
    await expect(page.locator('#reference-view.hidden')).toHaveCount(1);
    await expect(page.locator('#sidebar.expanded')).toBeVisible();
    await expect(page.locator('#info-section .toc-item').first()).toBeVisible();
    await page.screenshot({ path: path.join(SHOTS, `${tag}-back-to-toc.png`) });
  });

  // The breadcrumb info button sits exactly on the attribution's white
  // circle and covers it; the basemap credit is published in
  // methodology.html instead (a licence condition).
  test('attribution control starts collapsed and is covered by the info button', async ({ page }) => {
    const tag = test.info().project.name + '-attrib';
    await ready(page, `${BASE}/`);
    const attrib = page.locator('.maplibregl-ctrl-attrib');
    await expect(attrib).not.toHaveAttribute('open', /.*/);
    expect((await attrib.boundingBox()).width, 'collapsed attribution is icon-sized').toBeLessThan(40);

    expect(await page.evaluate(() => {
      const c = document.querySelector('.maplibregl-ctrl-attrib').getBoundingClientRect();
      const i = document.getElementById('breadcrumb-mode-btn').getBoundingClientRect();
      return i.left <= c.left && i.top <= c.top && i.right >= c.right && i.bottom >= c.bottom;
    }), 'info button fully covers the attribution circle').toBeTruthy();

    await expect(page.locator('#breadcrumb-mode-btn')).toHaveAttribute('aria-label', 'Methodology');
    await page.screenshot({ path: path.join(SHOTS, `${tag}.png`) });
  });

  test('basemap credit is published in the methodology page', async ({ page }) => {
    await page.goto(`${BASE}/methodology.html`, { waitUntil: 'domcontentloaded' });
    const body = await page.locator('body').innerText();
    expect(body).toMatch(/CARTO/);
    expect(body).toMatch(/OpenStreetMap/);
  });

  test('tier legend starts collapsed on phones; the pill expands it', async ({ page }) => {
    const tag = test.info().project.name + '-legend';
    await ready(page, `${BASE}/`);
    const legend = page.locator('#onmap-legend');
    await expect(legend).toBeVisible();
    await expect(legend).toHaveClass(/collapsed/);

    await page.locator('#onmap-legend-pill').tap();
    await expect(legend).not.toHaveClass(/collapsed/);
    expect(await page.locator('#onmap-legend-tiers .legend-tier').count()).toBeGreaterThan(0);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-expanded.png`) });
  });
});
