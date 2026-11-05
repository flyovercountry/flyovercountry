import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, BASE, SHOTS } from './helpers.js';

test('the sidebar footer\'s tour button restarts the tour; the skip pill closes it', async ({ page }) => {
  const tag = 'footer-tour-retrigger';
  await ready(page, `${BASE}/`);
  const tourButton = page.locator('#sidebar-footer .footer-action').first();
  await tourButton.scrollIntoViewIfNeeded();
  await expect(tourButton).toBeVisible();
  await expect(tourButton).toContainText(/tour/i);

  await tourButton.click();
  const overlay = page.locator('#tour-overlay');
  await expect(overlay).toBeVisible({ timeout: 10000 });
  await page.screenshot({ path: path.join(SHOTS, `${tag}-tour-started.png`) });

  await page.locator('.tour-close').click();
  await expect(overlay).toHaveCount(0, { timeout: 10000 });
  await page.screenshot({ path: path.join(SHOTS, `${tag}-tour-closed.png`) });
});
