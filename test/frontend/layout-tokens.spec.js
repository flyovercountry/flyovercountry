import { test, expect } from '@playwright/test';
import { BASE, ready } from './helpers.js';

// The responsive breakpoints are defined once, in frontend/src/tailwind.css
// (@theme). CSS media queries (`md:` utilities, style.css) and JS layout
// decisions (lib/layout.js) must agree on them; this pins the contract.
test.describe('layout tokens', () => {
  test('breakpoint tokens are emitted and the sheet/sidebar flip happens at md', async ({ page }) => {
    await ready(page, `${BASE}/`);

    const tokens = await page.evaluate(() => {
      const cs = getComputedStyle(document.documentElement);
      return {
        sm: cs.getPropertyValue('--breakpoint-sm').trim(),
        md: cs.getPropertyValue('--breakpoint-md').trim(),
        lg: cs.getPropertyValue('--breakpoint-lg').trim(),
      };
    });
    expect(tokens).toEqual({ sm: '480px', md: '900px', lg: '1100px' });

    const mode = () => page.locator('#sidebar').getAttribute('data-mode');

    // Generous windows: these assert the resulting state, not how fast the
    // resize handler runs, and a loaded runner can take seconds.
    await page.setViewportSize({ width: 899, height: 900 });
    await expect.poll(mode, { timeout: 10000 }).toBe('sheet');
    await expect(page.locator('.collapsed-sheet')).toBeVisible();

    await page.setViewportSize({ width: 900, height: 900 });
    await expect.poll(mode, { timeout: 10000 }).toBe('sidebar');
    await expect(page.locator('.collapsed-sheet')).toHaveCount(0);
  });
});
