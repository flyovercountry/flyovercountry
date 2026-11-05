import path from 'path';
import { test, expect } from '@playwright/test';
import { BASE, SHOTS, ready, waitForMap, mapPoint, tapAt, tapEl, isTouchProject,
         settleMap, countryLayerState, applyFiltersNow } from './helpers.js';

// End-to-end journeys: what a person actually does, driven with a finger
// on the touch projects and a mouse on desktop, asserting on observable
// state (URL, selection layers, visible panels) and leaving a screenshot
// at every step for human review. Runs on desktop, Pixel 7, iPhone 13 and
// iPhone SE (playwright.config.js).
// Candidate countries in preference order; the journey uses the first one
// whose map point is on bare canvas at this viewport (on a 320px phone the
// legend covers Germany's point, for instance).
const COUNTRIES = [['DE', [10.4, 51.1]], ['PL', [19.1, 52.1]], ['FR', [2.3, 46.6]], ['IT', [12.5, 42.5]], ['ES', [-3.7, 40.4]]];
const SEAS = [[3.0, 56.5], [-6.0, 46.0], [17.0, 37.0], [-2.0, 49.5]];

const shot = (page, name) =>
  page.screenshot({ path: path.join(SHOTS, `${test.info().project.name}-journey-${name}.png`) });

async function pickCountry(page) {
  for (const [cc, ll] of COUNTRIES) {
    const pt = await mapPoint(page, ll);
    if (pt.cc === cc && pt.onCanvas) return { cc, ...pt };
  }
  throw new Error('no candidate country is tappable on bare canvas at this viewport');
}

async function openSea(page) {
  for (const ll of SEAS) {
    const pt = await mapPoint(page, ll);
    if (!pt.cc && pt.onCanvas) return pt;
  }
  throw new Error('no open-sea point free of UI at this viewport');
}

// On the sheet layout the panel starts collapsed to the bottom bar.
async function expandSheetIfCollapsed(page) {
  const bar = page.locator('.collapsed-sheet');
  if (await bar.isVisible()) {
    await tapEl(bar);
    await expect(page.locator('#sidebar.expanded')).toBeVisible({ timeout: 10000 });
  }
}

async function collapseSheetIfExpanded(page) {
  const close = page.locator('.panel-close-btn');
  if (await close.isVisible()) {
    await tapEl(close);
    await expect(page.locator('#sidebar.expanded')).toHaveCount(0, { timeout: 10000 });
  }
}

test.describe('journeys', () => {
  test('first visit: the tour runs to the end and does not come back', async ({ page }) => {
    await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForSelector('html[data-app-ready="1"]', { timeout: 90000 });
    const overlay = page.locator('#tour-overlay');
    await expect(overlay, 'tour shows on a first, parameterless visit').toBeVisible({ timeout: 30000 });

    const progress = page.locator('.tour-progress');
    const next = page.locator('.tour-btn-next');
    for (let step = 1; step <= 10; step++) {
      await expect(next).toBeVisible({ timeout: 10000 });
      await page.waitForFunction(() => getComputedStyle(document.querySelector('.tour-annotations')).opacity === '1',
        null, { timeout: 5000 });
      await shot(page, `tour-step${step}`);
      const before = (await progress.textContent()) || '';
      const label = (await next.textContent()) || '';
      await tapEl(next);
      if (/Explore/.test(label)) break;
      await expect(progress, `step counter advances past "${before}"`).not.toHaveText(before, { timeout: 10000 });
    }
    await expect(overlay, 'tour dismissed').toHaveCount(0, { timeout: 10000 });
    await expect.poll(() => page.evaluate(() => localStorage.getItem('welcomed')), { timeout: 5000 }).toBe('1');
    await shot(page, 'tour-done');

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForSelector('html[data-app-ready="1"]', { timeout: 60000 });
    await page.waitForTimeout(1500);
    await expect(overlay, 'tour does not reappear on the next visit').toHaveCount(0);
  });

  test('pick a country, read about it, go back, let it go', async ({ page }) => {
    await ready(page, `${BASE}/`);
    await waitForMap(page);
    const country = await pickCountry(page);
    const { cc } = country;

    await tapAt(page, country);
    await settleMap(page);
    expect((await countryLayerState(page)).selected).toBe(cc);
    expect(page.url()).toContain(`cc=${cc}`);
    await shot(page, 'country-selected');

    if (isTouchProject()) {
      const bar = page.locator('.collapsed-sheet');
      await expect(bar).not.toContainText('Flyover Country');
      await expect(bar).toHaveClass(/has-country/);
    }
    await expandSheetIfCollapsed(page);
    await expect(page.locator('.country-selection-line')).toContainText(`(${cc})`);
    await expect(page.locator('.unified-stats-grid.stat-total')).toBeVisible();
    await shot(page, 'country-panel');

    const stats = page.locator('.stats-toc-item', { hasText: 'Statistics' }).first();
    await tapEl(stats);
    await expect(page.locator('#reference-view:not(.hidden)')).toHaveCount(1, { timeout: 10000 });
    await expect.poll(() => page.url()).toContain('view=more-stats');
    await expect(page.locator('#reference-view .breakdown-heading').first()).toBeVisible({ timeout: 10000 });
    await shot(page, 'reader-open');

    await tapEl(page.locator('#refview-close'));
    await expect(page.locator('#reference-view.hidden')).toHaveCount(1, { timeout: 10000 });
    await expect.poll(() => page.url()).not.toContain('view=');
    await collapseSheetIfExpanded(page);
    await shot(page, 'reader-closed');

    const sea = await openSea(page);
    await tapAt(page, sea);
    await settleMap(page);
    expect((await countryLayerState(page)).selected, 'tap on the sea deselects').toBeNull();
    expect(page.url()).not.toContain('cc=');
    await shot(page, 'deselected');

    await page.goBack();
    await expect.poll(() => page.url(), { timeout: 10000 }).toContain(`cc=${cc}`);
    await expect.poll(async () => (await countryLayerState(page)).selected,
      { message: 'browser Back restores the selection', timeout: 15000 }).toBe(cc);
    await shot(page, 'back-restored');
  });

  test('narrow the data with a filter and share a link that carries it', async ({ page }) => {
    await ready(page, `${BASE}/`);
    await waitForMap(page);

    const trigger = page.locator(isTouchProject() ? '#mobile-filter-btn' : '#toolbar-filter-btn');
    await expect(trigger).toBeVisible();
    await tapEl(trigger);
    const checks = page.locator('.filter-view .group-check');
    await expect(checks.first()).toBeVisible({ timeout: 10000 });
    await shot(page, 'filters-open');

    // Turn off the first role group that is on.
    const n = await checks.count();
    let toggled = -1;
    for (let i = 0; i < n; i++) {
      if (await checks.nth(i).isChecked()) { await tapEl(checks.nth(i)); toggled = i; break; }
    }
    expect(toggled, 'there was a role group to turn off').toBeGreaterThanOrEqual(0);
    await expect(checks.nth(toggled)).not.toBeChecked();
    await applyFiltersNow(page);
    await expect.poll(() => page.url(), { message: 'URL carries the role filter', timeout: 10000 }).toContain('roles=');
    await expect(trigger.locator('.filter-badge'), 'trigger shows the R badge').toContainText('R');
    await shot(page, 'filter-applied');

    // "Apply now" applies AND closes the filter view (controls.js onApply);
    // only the auto-apply path leaves it open. Close it if it is still there.
    const closeX = page.locator('.filter-close-x');
    if (await closeX.isVisible()) await tapEl(closeX);
    await expect(page.locator('.filter-view')).toHaveCount(0, { timeout: 10000 });
    // On the sheet layout "Apply now" leaves the sheet open on the stats
    // content (only the close button collapses it); the Share trigger lives
    // in the toolbar underneath, so put the sheet away first.
    await collapseSheetIfExpanded(page);

    await tapEl(page.locator('#share-menu-trigger'));
    await expect(page.locator('#share-menu-list')).toBeVisible({ timeout: 10000 });
    const shareUrl = await page.locator('#share-menu-list [data-sharer="twitter"]').getAttribute('data-url');
    expect(shareUrl, 'share link carries the filter').toContain('roles=');
    await shot(page, 'share-open');
    await page.keyboard.press('Escape');
  });

  test('ask "what if this country refused" and come back out', async ({ page }) => {
    await ready(page, `${BASE}/?cc=DE`);
    await waitForMap(page);
    const pill = page.locator('#whatif-pulldown-pill');
    await expect(pill, 'DE is What-If eligible').toBeVisible({ timeout: 10000 });

    // On narrow viewports the open legend overlaps the pill; fold it.
    const legendPill = page.locator('#onmap-legend-pill');
    if (isTouchProject() && await legendPill.isVisible()
        && !(await page.locator('#onmap-legend.collapsed').count())) {
      await tapEl(legendPill);
    }
    await tapEl(pill);
    await expect.poll(() => page.evaluate(() => document.body.classList.contains('whatif-active')),
      { message: 'What-If mode entered', timeout: 10000 }).toBe(true);
    if (isTouchProject()) await expect(page.locator('#sidebar.expanded')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('#whatif-section')).toBeVisible({ timeout: 10000 });
    await shot(page, 'whatif-on');

    await collapseSheetIfExpanded(page);
    await tapEl(page.locator('#whatif-pulldown-collapse'));
    await expect.poll(() => page.evaluate(() => document.body.classList.contains('whatif-active')),
      { message: 'What-If mode exited', timeout: 10000 }).toBe(false);
    await shot(page, 'whatif-off');
  });
});
