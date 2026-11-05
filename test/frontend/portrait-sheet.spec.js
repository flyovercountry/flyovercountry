import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, BASE, SHOTS } from './helpers.js';

test.describe('Portrait Sheet (412x839)', () => {
  test('collapsed bar and expand/collapse cycling', async ({ page }) => {
    const tag = 'portrait-sheet-panel';
    await ready(page, `${BASE}/`);
    const bar = page.locator('.collapsed-bar.collapsed-sheet');
    await expect(bar).toBeVisible();
    await expect(page.locator('#sidebar')).toHaveAttribute('data-mode', 'sheet');
    await page.screenshot({ path: path.join(SHOTS, `${tag}-collapsed.png`) });

    await bar.tap();
    const expanded = page.locator('#sidebar.expanded');
    await expect(expanded).toBeVisible();
    await expect(page.locator('.panel-close-btn')).toBeVisible();
    await page.screenshot({ path: path.join(SHOTS, `${tag}-expanded.png`) });

    await page.locator('.panel-close-btn').tap();
    await expect(expanded).toHaveCount(0);
    await expect(bar).toBeVisible();
    await page.screenshot({ path: path.join(SHOTS, `${tag}-recollapsed.png`) });
  });

  test('a pre-selected country shows in the collapsed bar with its headline stat', async ({ page }) => {
    const tag = 'portrait-sheet-country-expand';
    await ready(page, `${BASE}/?cc=SK`);
    const title = page.locator('.collapsed-bar .collapsed-title');
    await expect(title).toBeVisible();
    await expect(title).not.toHaveText('Flyover Country');
    await expect(page.locator('.collapsed-bar .collapsed-subtitle')).toHaveText(/%|flight/);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-country-bar.png`) });
  });

  test('entering What-If expands the panel; exiting from the pulldown works after closing it', async ({ page }) => {
    const tag = 'portrait-sheet-whatif-expand';
    await ready(page, `${BASE}/?cc=DE`);
    // The pill is a map overlay reachable without expanding the panel; the
    // open legend overlaps it on narrow viewports, so fold the legend first.
    const legend = page.locator('#onmap-legend');
    if (!(await legend.evaluate(el => el.classList.contains('collapsed')))) {
      await page.locator('#onmap-legend-pill').tap();
      await expect(legend).toHaveClass(/collapsed/);
    }
    const pill = page.locator('#whatif-pulldown-pill');
    await expect(pill, 'DE is What-If eligible').toBeVisible();
    await pill.tap();

    const expanded = page.locator('#sidebar.expanded');
    await expect(expanded).toBeVisible();
    await expect(page.locator('body')).toHaveClass(/whatif-active/);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-whatif-expanded.png`) });

    // The expanded panel covers the pulldown; close it to reach the
    // pulldown's own collapse button.
    await page.locator('.panel-close-btn').tap();
    await expect(expanded).toHaveCount(0);
    await page.locator('#whatif-pulldown-collapse').tap();
    await expect(page.locator('body')).not.toHaveClass(/whatif-active/);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-whatif-exited.png`) });
  });

  test('filter panel opens on mobile', async ({ page }) => {
    const tag = 'portrait-sheet-filters';
    await ready(page, `${BASE}/`);
    // The mobile toolbar renders after app-ready; wait for the button to
    // be visible before tapping, so the test does not race the layout.
    await expect(page.locator('#mobile-filter-btn')).toBeVisible({ timeout: 30000 });
    await page.locator('#mobile-filter-btn').tap();
    const filterView = page.locator('.filter-view');
    await expect(filterView).toBeVisible();
    expect(await filterView.evaluate(el => el.children.length)).toBeGreaterThan(0);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-filters-open.png`) });

    await page.locator('.filter-close-x').tap();
    await expect(filterView).toHaveCount(0);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-filters-closed.png`) });
  });
});
