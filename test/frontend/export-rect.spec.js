import { test, expect } from '@playwright/test';
import { ready, BASE } from './helpers.js';

test.describe('Desktop Chromium', () => {
  test('no toolbar button overflows a 1024px-wide viewport', async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 1024, height: 768 } });
    const page = await ctx.newPage();
    try {
      await ready(page, `${BASE}/?cc=SK`);
      const buttons = page.locator('#map-toolbar button');
      expect(await buttons.count()).toBeGreaterThan(0);
      const rightEdges = await buttons.evaluateAll(els => els.map(el => ({
        label: el.textContent.trim(), right: el.getBoundingClientRect().right,
      })));
      expect(rightEdges.filter(b => b.right > 1024)).toEqual([]);
    } finally {
      await ctx.close();
    }
  });

  test('select-area export: drag downloads a CSV, Esc cancels', async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    const page = await ctx.newPage();
    try {
      await ready(page, `${BASE}/`);
      const hint = page.locator('#rect-select-hint');

      await page.click('#export-menu-trigger');
      await expect(page.locator('#rect-export-btn')).toBeVisible();
      await page.click('#rect-export-btn');
      await expect(hint).toBeVisible();
      await expect.poll(() => page.evaluate(() => window.__map.getCanvas().style.cursor)).toBe('crosshair');

      const box = await page.locator('#map').boundingBox();
      const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
      // The CSV is built client-side from the full flight set; the download
      // event can take tens of seconds on a loaded runner.
      const [download] = await Promise.all([
        page.waitForEvent('download', { timeout: 45000 }),
        (async () => {
          await page.mouse.move(cx - 130, cy - 90);
          await page.mouse.down();
          await page.mouse.move(cx + 130, cy + 90, { steps: 12 });
          await page.mouse.up();
        })(),
      ]);
      expect(download.suggestedFilename()).toMatch(/\.csv$/);
      await expect(hint, 'select mode ends after the export').toBeHidden();

      await page.click('#export-menu-trigger');
      await page.click('#rect-export-btn');
      await expect(hint).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(hint, 'Esc cancels select mode').toBeHidden();
    } finally {
      await ctx.close();
    }
  });
});
