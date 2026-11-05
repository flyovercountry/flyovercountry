import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, BASE, SHOTS } from './helpers.js';

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

  test('initial load', async () => {
    await ready(page, `${BASE}/`);
    await expect(page.locator('#map')).toBeVisible();
    await expect(page.locator('#sidebar')).toBeVisible();
    await expect(page.locator('.stats-summary')).toContainText(/flight/i);
    expect(page.url()).not.toContain('roles=');
    await expect(page.locator('#show-subsection .toggle-btn')).toHaveText(['Flights', 'Landings']);
  });

  test('country stats table shows the top 8 interactive countries; clicking a row selects it', async () => {
    await ready(page, `${BASE}/`);
    const modes = await page.evaluate(base => fetch(`${base}/data/countries.modes.json`).then(r => r.json()), BASE);
    const eligible = Object.values(modes.countries).filter(c => (c.interaction ?? 0) >= 2).length;

    const rows = page.locator('.stats-body .country-stat-row');
    await expect(rows).toHaveCount(Math.min(8, eligible));
    const firstCC = await rows.first().getAttribute('data-cc');
    expect(firstCC).toBeTruthy();
    // The label cell, not the row centre: percentage cells toggle the
    // tap-reveal instead of selecting.
    await rows.first().locator('.stat-label').click();
    await expect(page.locator('.country-selection-line')).toContainText(firstCC, { timeout: 10000 });
    await page.screenshot({ path: path.join(SHOTS, `${tag}-country.png`) });
  });

  test('zero JS errors', async () => {
    expect(errors).toEqual([]);
  });
});
