import { test, expect } from '@playwright/test';
import { ready, waitForMap, mapPoint, BASE } from './helpers.js';

const DE = [10.4, 51.1];

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

  const twitter = () => page.locator('#share-menu-list [data-sharer="twitter"]');

  async function openShare() {
    await page.click('#share-menu-trigger');
    await expect(page.locator('#share-menu-list')).toBeVisible();
  }

  test('Share menu: vendored sharer.js, items, hashtags', async () => {
    await ready(page, `${BASE}/`);
    await expect(page.locator('#share-menu-trigger')).toBeVisible();
    expect(await page.evaluate(() => fetch('vendor/sharer.min.js', { cache: 'no-store' }).then(r => r.status))).toBe(200);
    expect(await page.evaluate(() => typeof window.Sharer)).toMatch(/^(object|function)$/);

    await openShare();
    await expect(page.locator('#share-btn'), 'Copy link').toBeVisible();
    await expect(page.locator('#embed-btn'), 'Embed').toBeVisible();
    await expect(twitter()).toHaveCount(1);
    await expect(page.locator('#share-menu-list [data-sharer="bluesky"]')).toHaveCount(1);
    await expect(page.locator('#share-mastodon')).toHaveCount(1);

    await expect(twitter()).toHaveAttribute('data-hashtags', 'flyovercountry');
    await expect(twitter()).toHaveAttribute('data-url', /^https?:\/\//);

    const hasNativeShare = await page.evaluate(() => typeof navigator.share === 'function');
    expect(await page.locator('#native-share-btn').isVisible(), 'native share item matches navigator.share').toBe(hasNativeShare);
    await page.keyboard.press('Escape');
    await expect(page.locator('#share-menu-list')).toBeHidden();
  });

  test('country hashtags come from the country\'s configured short names', async () => {
    await ready(page, `${BASE}/?cc=ES`);
    await openShare();
    await expect(twitter()).toHaveAttribute('data-hashtags', /(^|,)flyovercountrySpain(,|$)/);
    await page.keyboard.press('Escape');
  });

  // share-links.js: updateShareTargets must run on the menu's open() as well
  // as on view/country change, so a country selected by a map click (not a
  // ?cc= reload) is reflected in the social buttons' data-url.
  test('Share menu reflects a country selected by map click without a reload', async () => {
    await ready(page, `${BASE}/`);
    await waitForMap(page);
    const pt = await mapPoint(page, DE);
    expect(pt.cc, 'Germany rendered under the probe point').toBe('DE');
    expect(pt.onCanvas).toBeTruthy();
    await page.mouse.click(pt.x, pt.y);
    await expect.poll(() => page.url(), { timeout: 15000 }).toContain('cc=DE');

    await openShare();
    await expect(twitter()).toHaveAttribute('data-url', /cc=DE/);
    await page.keyboard.press('Escape');
  });

  test('zero JS errors', async () => {
    expect(errors).toEqual([]);
  });
});
