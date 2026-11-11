import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, BASE, SHOTS } from './helpers.js';

test.describe('Svelte direct-render sections', () => {
  test('StatsSection, InfoSection and SidebarFooter render inside ContentPanel', async ({ page }) => {
    await ready(page, `${BASE}/`);
    const sidebar = page.locator('#sidebar');

    const stats = sidebar.locator('#stats-section');
    await expect(stats).toBeVisible();
    await expect(stats.locator('.section-label')).not.toHaveText('');
    expect(await stats.locator('.stats-body .country-stat-row').count()).toBeGreaterThan(0);

    const info = sidebar.locator('#info-section');
    await expect(info).toBeVisible();
    await expect(info.locator('.section-label')).toHaveText('Info');

    const footer = sidebar.locator('#sidebar-footer');
    await expect(footer).toBeVisible();
    await expect(footer).toContainText(/contact|Explore/);

    await page.screenshot({ path: path.join(SHOTS, 'svelte-direct-render-sections.png') });
  });

  test('section headers are span.section-label, never <label>', async ({ page }) => {
    await ready(page, `${BASE}/`);
    await expect(page.locator('#sidebar label.section-label')).toHaveCount(0);
    expect(await page.locator('#sidebar span.section-label').count()).toBeGreaterThanOrEqual(2);
  });
});
