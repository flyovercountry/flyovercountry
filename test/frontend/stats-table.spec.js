import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, BASE, SHOTS } from './helpers.js';

// The unified stats table (panel-data.js unifiedStatsHeader/Row): a label
// column, then Flights (Sum = Crosses + Previously crossed, with operator
// cells between), Planes (Sky / Ground) and Landings (Share). Every data
// cell is a one-decimal percentage with an "N / M <unit>" tooltip.
const CELL_TOOLTIPS = [
  /^[\d,]+ \/ [\d,]+ flights$/, /^[\d,]+ \/ [\d,]+ flights$/, /^[\d,]+ \/ [\d,]+ flights$/,
  /^[\d,]+ \/ [\d,]+ aircraft$/, /^[\d,]+ \/ [\d,]+ aircraft$/,
  /^[\d,]+ \/ [\d,]+ landings \([\d,]+ distinct aircraft\)$/,
];
const GROUP_ORDER = [null, 'flights', 'op', 'flights', 'op', 'flights', 'planes', 'planes', 'landings'];

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

  test('SK headline percentage matches the global table\'s Sum cell', async () => {
    // Sum (= Crosses + Previously crossed) is the same number the headline
    // interpolates, reached through a different code path.
    await ready(page, `${BASE}/`);
    const skSum = page.locator('.stats-body .unified-stats-grid.country-stat-row[data-cc="SK"] .stat-col-narrow').first();
    await expect(skSum).toBeVisible();
    expect(await skSum.getAttribute('title')).toMatch(/^[\d,]+ \/ [\d,]+ flights$/);
    const tablePct = (await skSum.textContent()).trim().replace('%', '');

    await ready(page, `${BASE}/?cc=SK`);
    const headline = page.locator('.country-headline');
    await expect(headline).not.toContainText('…', { timeout: 10000 });
    const match = (await headline.textContent()).match(/([\d.]+)% of individual flights/);
    expect(match, 'headline carries an interpolated flights percentage').toBeTruthy();
    expect(match[1]).toBe(tablePct);
  });

  test('unified stats table: six percentage columns, tooltips, group banding, Sum = Crosses + Previously crossed', async () => {
    await ready(page, `${BASE}/?cc=SK`);
    const row = page.locator('.unified-stats-grid.stat-total').first();
    await expect(row).toBeVisible();
    const cells = row.locator('.stat-col-narrow');
    await expect(cells).toHaveCount(6);

    await expect(page.locator('.unified-stats-grid.stat-header').first()).toContainText('Role');
    const subheader = page.locator('.unified-stats-grid.stat-subheader').first();
    for (const label of ['Sum', 'Crosses', 'Previously', 'crossed', 'Sky', 'Ground', 'Share']) {
      await expect(subheader).toContainText(label);
    }

    for (let i = 0; i < 6; i++) {
      expect((await cells.nth(i).textContent()).trim(), `cell ${i}`).toMatch(/^\d+\.\d%$/);
      expect(await cells.nth(i).getAttribute('title'), `cell ${i} tooltip`).toMatch(CELL_TOOLTIPS[i]);
    }

    const groups = await row.evaluate(el => Array.from(el.children).map(c => {
      if (c.classList.contains('stat-op')) return 'op';
      return c.className.match(/stat-group-(planes|flights|landings)/)?.[1] ?? null;
    }));
    expect(groups).toEqual(GROUP_ORDER);
    expect(await row.locator('.stat-op').allTextContents()).toEqual(['=', '+']);

    const nums = (await cells.allTextContents()).slice(0, 3).map(parseFloat);
    expect(Math.abs(nums[0] - (nums[1] + nums[2])), `${nums[0]} = ${nums[1]} + ${nums[2]} within rounding`).toBeLessThanOrEqual(0.15);
  });

  test('panel table shows only the Total row; the per-group breakdown is in the reader', async () => {
    await ready(page, `${BASE}/?cc=SK`);
    const dataRows = page.locator('.unified-stats-grid:not(.stat-header):not(.stat-subheader)');
    await expect(dataRows).toHaveCount(1);
    await expect(dataRows.first().locator('.stat-label')).toHaveText('Total (All Roles)');
  });

  test('global-table country rows are keyboard targets with a visible focus ring; Enter selects', async ({ browser }) => {
    const ctx1440 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const p = await ctx1440.newPage();
    try {
      await ready(p, `${BASE}/`);
      const firstRow = p.locator('.stats-body .country-stat-row').first();
      await expect(firstRow).toHaveAttribute('role', 'button');
      await expect(firstRow).toHaveAttribute('tabindex', '0');
      expect(await firstRow.getAttribute('aria-label')).toBeTruthy();

      await firstRow.focus();
      expect(await firstRow.evaluate(el => getComputedStyle(el).outlineStyle), 'visible focus outline').toBe('solid');
      await p.screenshot({ path: path.join(SHOTS, `${tag}-1440x900-a11y-stats.png`) });

      const firstCC = await firstRow.getAttribute('data-cc');
      await p.keyboard.press('Enter');
      await expect(p.locator('.country-selection-line')).toContainText(firstCC, { timeout: 10000 });
    } finally {
      await ctx1440.close();
    }
  });

  test('zero JS errors', async () => {
    expect(errors).toEqual([]);
  });
});
