import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, BASE, SHOTS } from './helpers.js';
import { PIXEL7_PORTRAIT } from '../../playwright.config.js';

// The reader's "See more statistics" entry (slug more-stats) shows two
// breakdown tables for the selected country — by aircraft type and by role
// group — each with its own daily histogram, neither capped to a top-N nor
// folded into an "Other" bucket.
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

  // Every data row of one breakdown table (by heading): label, selected
  // state, and the Sum / Sky numerators from the cells' tooltips.
  async function readBreakdownTable(headingText) {
    const block = page.locator('.breakdown-block', { has: page.locator('.breakdown-heading', { hasText: headingText }) });
    const rows = block.locator('.unified-stats-grid.breakdown-row');
    await expect(rows.first()).toBeVisible();
    const out = [];
    for (const row of await rows.all()) {
      const cols = row.locator('.stat-col-narrow');
      const flights = (await cols.nth(0).getAttribute('title')).match(/^([\d,]+) \/ [\d,]+ flights$/);
      const crossed = (await cols.nth(3).getAttribute('title')).match(/^([\d,]+) \/ [\d,]+ aircraft$/);
      out.push({
        label: (await row.locator('.stat-label').textContent()).trim(),
        selected: await row.evaluate(el => el.classList.contains('selected')),
        flights: flights ? parseInt(flights[1].replace(/,/g, ''), 10) : null,
        crossed: crossed ? parseInt(crossed[1].replace(/,/g, ''), 10) : null,
        el: row,
      });
    }
    return out;
  }

  test('"Statistics" button below the stats table opens the reader with both tables', async () => {
    await ready(page, `${BASE}/?cc=SK`);
    const button = page.locator('.stats-toc-item', { hasText: 'Statistics' });
    // The stats panel renders asynchronously after the country selection
    // settles; wait for the button to be visible, not just present in DOM.
    await expect(button).toBeVisible({ timeout: 30000 });
    await button.click();
    await expect(readerOpen()).toHaveCount(1);
    await expect.poll(() => page.url()).toContain('view=more-stats');

    const headings = await page.locator('.breakdown-heading').allTextContents();
    expect(headings).toEqual(expect.arrayContaining([expect.stringMatching(/aircraft types/i), expect.stringMatching(/role groups/i)]));
    await expect(page.locator('.breakdown-block').first().locator('.unified-stats-grid.stat-header')).toContainText('Model');
    await expect(page.locator('.breakdown-block').nth(1).locator('.unified-stats-grid.stat-header')).toContainText('Role group');

    // Same six-column shape as the panel table: Flights (Sum / Crosses /
    // Previously crossed), Planes (Sky / Ground), Landings (Share).
    await expect(page.locator('.unified-stats-grid.breakdown-row').first().locator('.stat-col-narrow')).toHaveCount(6);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-morestats-open.png`) });
  });

  test('both tables: no top-N cap, no Other bucket, sorted by flights descending', async () => {
    await ready(page, `${BASE}/?cc=DE&view=more-stats`);
    await expect(readerOpen()).toHaveCount(1);
    for (const heading of ['Aircraft types', 'Role groups']) {
      const rows = await readBreakdownTable(heading);
      expect(rows.length, `${heading} rows`).toBeGreaterThan(0);
      expect(rows.map(r => r.label)).not.toEqual(expect.arrayContaining([expect.stringMatching(/^other$/i)]));
      for (let i = 1; i < rows.length; i++) {
        expect(rows[i - 1].flights, `${heading}: row ${i - 1} (${rows[i - 1].label}) >= row ${i} (${rows[i].label})`)
          .toBeGreaterThanOrEqual(rows[i].flights);
      }
    }
  });

  test('type table: Sum numerator = Crosses + Previously crossed', async () => {
    await ready(page, `${BASE}/?cc=DE&view=more-stats`);
    await expect(readerOpen()).toHaveCount(1);
    // Breakdown percentages use per-column denominators, so the identity
    // holds in the tooltip numerators, not the displayed percentages.
    const rows = page.locator('.breakdown-block').first().locator('.unified-stats-grid.breakdown-row');
    await expect(rows.first()).toBeVisible();
    const n = Math.min(3, await rows.count());
    for (let i = 0; i < n; i++) {
      const nums = await rows.nth(i).evaluate(el =>
        Array.from(el.querySelectorAll('.stat-col-narrow')).slice(0, 3)
          .map(c => parseInt((c.getAttribute('title') || '').replace(/,/g, '').match(/^(\d+) \//)?.[1] ?? 'NaN', 10)));
      expect(nums[0], `row ${i}: ${nums[0]} = ${nums[1]} + ${nums[2]}`).toBe(nums[1] + nums[2]);
    }
  });

  test('role-group table has an Airlift row; the dataset has flights holding several Airlift roles at once', async () => {
    await ready(page, `${BASE}/?cc=SK&view=more-stats`);
    await expect(readerOpen()).toHaveCount(1);
    // Group-level counting must union a flight's roles, not sum them; that
    // only matters if such flights exist, so the data contract is checked
    // alongside the row. Roles are per hex (hex_roles.json).
    const overlapExample = await page.evaluate(async (base) => {
      const [cat, flights, hexRoles] = await Promise.all([
        fetch(`${base}/data/role_catalog.json`).then(r => r.json()),
        fetch(`${base}/data/flights.json`).then(r => r.json()),
        fetch(`${base}/data/hex_roles.json`).then(r => r.json()),
      ]);
      const roleIds = new Set(cat.groups.find(g => g.slug === 'airlift').roles);
      for (let i = 0; i < flights.n; i++) {
        if ((hexRoles[flights.hex[i]] || []).filter(r => roleIds.has(r)).length > 1) return flights.id[i];
      }
      return null;
    }, BASE);
    expect(overlapExample, 'a flight with 2+ Airlift-group roles exists').not.toBeNull();
    await expect(page.locator('.breakdown-block').nth(1).locator('.unified-stats-grid.breakdown-row', { hasText: /airlift/i })).toHaveCount(1);
  });

  test('row click swaps that table\'s histogram and moves the highlight; the other table is unaffected', async () => {
    await ready(page, `${BASE}/?cc=DE&view=more-stats`);
    await expect(readerOpen()).toHaveCount(1);
    const typeRows = await readBreakdownTable('Aircraft types');
    expect(typeRows.length, 'DE has at least two aircraft types').toBeGreaterThanOrEqual(2);
    expect(typeRows[0].selected, 'the top row starts selected').toBeTruthy();
    expect(typeRows[1].selected).toBeFalsy();

    const typeBlock = page.locator('.breakdown-block').first();
    await expect(typeBlock.locator('.daily-chart-label')).toHaveText(typeRows[0].label);

    // The label cell, not a numeric cell (those toggle the tap-reveal).
    await typeRows[1].el.locator('.stat-label').click();
    await expect(typeRows[1].el).toHaveClass(/selected/);
    await expect(typeRows[0].el).not.toHaveClass(/selected/);
    await expect(typeBlock.locator('.daily-chart-label')).toHaveText(typeRows[1].label);

    const groupRows = await readBreakdownTable('Role groups');
    expect(groupRows[0].selected, 'the role-group table keeps its own selection').toBeTruthy();
    await page.screenshot({ path: path.join(SHOTS, `${tag}-morestats-row-clicked.png`) });
  });

  test('zero JS errors', async () => {
    expect(errors).toEqual([]);
  });
});

// Slovenia has the fewest aircraft types of any stats-enabled country;
// Germany the most.
test('breakdown tables fit without horizontal overflow (mobile)', async ({ browser }) => {
  const tag = 'mobile-morestats';
  const ctx = await browser.newContext({ ...PIXEL7_PORTRAIT });
  const page = await ctx.newPage();
  for (const cc of ['SI', 'DE']) {
    await ready(page, `${BASE}/?cc=${cc}&view=more-stats`);
    await expect(page.locator('#reference-view:not(.hidden)')).toHaveCount(1);
    await expect(page.locator('.unified-stats-grid.breakdown-row').first()).toBeVisible();
    const overflow = await page.evaluate(() => {
      const body = document.getElementById('refview-body');
      return body.scrollWidth - body.clientWidth;
    });
    expect(overflow, `${cc} reader body horizontal overflow`).toBeLessThanOrEqual(1);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-${cc}.png`), fullPage: true });
  }
  await ctx.close();
});
