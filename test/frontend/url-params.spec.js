import { test, expect } from '@playwright/test';
import { ready, BASE } from './helpers.js';

// Any invalid or unknown query param shows a blocking repair overlay
// (.param-error inside #loading-box) listing each problem in plain language
// with a single link to a fully valid repaired URL.
test.describe('Desktop Chromium 1400x900', () => {
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

  async function loadOverlay(qs) {
    await page.goto(`${BASE}/${qs}`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    const overlay = page.locator('.param-error');
    await expect(overlay, `${qs} shows the repair overlay`).toBeVisible({ timeout: 30000 });
    await expect(page.locator('#loading-overlay'), 'loading overlay stays up behind the repair overlay').toBeVisible();
    await expect(overlay.locator('a'), 'exactly one repair link').toHaveCount(1);
    return overlay;
  }

  async function followRepair(overlay) {
    const href = await overlay.locator('a').getAttribute('href');
    expect(href).toBeTruthy();
    await ready(page, `${BASE}${href}`);
    await expect(page.locator('.param-error'), `repaired URL ${href} loads clean`).toHaveCount(0);
    return href;
  }

  test('?cc=DE selects Germany', async () => {
    await ready(page, `${BASE}/?cc=DE`);
    await expect(page.locator('#sidebar')).toContainText('Germany');
  });

  test('unrecognised theater filter value', async () => {
    const overlay = await loadOverlay('?fm=theater');
    const items = await overlay.locator('li').allTextContents();
    expect(items).toHaveLength(1);
    expect(items[0]).toMatch(/fm/);
    expect(items[0]).toMatch(/not a recognized theater filter/);
    const href = await followRepair(overlay);
    expect(href).not.toContain('fm=theater');
  });

  test('removed displacement filter value fm=disp', async () => {
    const overlay = await loadOverlay('?fm=disp');
    const items = await overlay.locator('li').allTextContents();
    expect(items).toHaveLength(1);
    expect(items[0]).toMatch(/fm/);
    await followRepair(overlay);
  });

  test('malformed roles, unknown param and bad cc: one problem each, all stripped', async () => {
    const overlay = await loadOverlay('?roles=1;2&bogus=1&cc=ZZ');
    const items = await overlay.locator('li').allTextContents();
    expect(items).toHaveLength(3);
    expect(items.some(t => /roles/.test(t)), 'roles problem listed').toBeTruthy();
    expect(items.some(t => /bogus/.test(t)), 'unknown param listed').toBeTruthy();
    expect(items.some(t => /cc/.test(t)), 'cc problem listed').toBeTruthy();
    const href = await followRepair(overlay);
    expect(href).not.toContain('bogus');
    expect(href).not.toContain('cc=ZZ');
    expect(href).not.toMatch(/roles=1;2/);
  });

  test('ex must be exactly "1"', async () => {
    const overlay = await loadOverlay('?ex=yes');
    const items = await overlay.locator('li').allTextContents();
    expect(items).toHaveLength(1);
    expect(items[0]).toMatch(/ex/);
    expect(items[0]).toMatch(/not a valid explore-mode value/);
    const href = await followRepair(overlay);
    expect(href).not.toContain('ex=yes');
  });

  test('duplicate roles tokens are disclosed and collapsed', async () => {
    const overlay = await loadOverlay('?roles=1_1');
    const items = await overlay.locator('li').allTextContents();
    expect(items).toHaveLength(1);
    expect(items[0]).toMatch(/roles/);
    expect(items[0]).toMatch(/more than once/);
    const href = await followRepair(overlay);
    expect(href).toMatch(/roles=1(&|$)/);
    expect(href).not.toMatch(/roles=1_1/);
  });

  // Ukraine, Belarus and Russia are display-only entries in
  // countries.modes.json (interaction 0). url.js's isCcSelectable rejects
  // them, so ?cc=UA takes the same path as any unknown code and must never
  // open a country panel through a raw-cc name fallback.
  test('?cc=UA (display-only) is rejected like an unknown cc', async () => {
    const overlay = await loadOverlay('?cc=UA');
    const items = await overlay.locator('li').allTextContents();
    expect(items).toHaveLength(1);
    expect(items[0]).toMatch(/cc/);
    expect(items[0]).toMatch(/not a country this map covers/);
    const href = await followRepair(overlay);
    expect(href).not.toContain('cc=UA');
    await expect(page.locator('.country-sel-text').first()).toContainText('No country selected');
  });

  test('zero JS errors', async () => {
    expect(errors).toEqual([]);
  });
});
