import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, elementShot, BASE, SHOTS } from './helpers.js';

// Headline template (common.texts.json templates.headline_intro):
//   "If {name} had refused, it could have affected {{acft}}% of
//    aircraft{{flights_and_landings}}."
// {{flights_and_landings}} is either " and ~X% of individual flights" or
// ", ~X% of individual flights and more than ~Y% of landings".
const HEADLINE = (name) =>
  new RegExp(`^If ${name} had refused, it could have affected \\d+(\\.\\d+)?% of aircraft(,| and) ~\\d+(\\.\\d+)?% of individual flights`);

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

  const whatIfActive = () => page.evaluate(() => document.body.classList.contains('whatif-active'));

  // The headline renders "…" until the sighting index has loaded.
  async function loadedHeadline() {
    const headline = page.locator('.country-headline');
    await expect(headline).toBeVisible();
    await expect(headline).not.toContainText('…', { timeout: 10000 });
    return (await headline.textContent()).trim();
  }

  test('country headline uses the template with interpolated percentages; no closing line in the panel', async () => {
    await ready(page, `${BASE}/?cc=DE`);
    const headline = await loadedHeadline();
    expect(headline).toMatch(HEADLINE('Germany'));
    expect(headline).not.toMatch(/\{\{|\}\}/);
    // The closing line lives in the Summary reader entry, not the panel.
    await expect(page.locator('.country-headline .headline-closing')).toHaveCount(0);
  });

  test('headline still renders under a later ?start=', async () => {
    await ready(page, `${BASE}/?cc=SK&start=2026-03-14`);
    const headline = await loadedHeadline();
    expect(headline).toMatch(HEADLINE('Slovakia'));
    expect(headline).not.toMatch(/\{\{|\}\}/);
  });

  test('sightings load; the What-If section stays hidden until entered via the Refuse? pill', async () => {
    await ready(page, `${BASE}/?cc=DE`);
    const sightings = await page.evaluate(async () => {
      const r = await fetch('/data/sightings/DE.json');
      return r.ok ? Object.keys(await r.json()).length : -1;
    });
    expect(sightings, 'sightings/DE.json aircraft count').toBeGreaterThan(0);
    await expect(page.locator('#whatif-section')).toHaveClass(/hidden/);
    await expect(page.locator('#whatif-pulldown-pill')).toBeVisible();
    await page.screenshot({ path: path.join(SHOTS, `${tag}-whatif.png`) });
  });

  test('What-If mode: summary sentences and role table', async () => {
    await ready(page, `${BASE}/?cc=DE`);
    await page.click('#whatif-pulldown-pill');
    await expect.poll(whatIfActive).toBe(true);
    // The role table shows 0.0% until the sighting index arrives.
    await expect.poll(() => page.evaluate(() => parseFloat(document.querySelector('.whatif-role-row .stat-col')?.textContent || '0')),
      { message: 'role table populated from the sighting index', timeout: 30000 }).toBeGreaterThan(0);

    const summary = (await page.locator('.whatif-summary').textContent()).trim();
    // Sentence 1 names the denominator (all flights after the date) and
    // rules out the per-country misreading; sentence 2 covers previously
    // crossed planes; the closing line ends it.
    expect(summary).toMatch(/% of all flights tracked after that date could not have happened the way they did/);
    expect(summary).toMatch(/not [\d.]+% of the flights over Germany, but of the whole dataset for the period/);
    expect(summary).toMatch(/and another [\d.]+% of flights were of planes that had flown over Germany at least once since \d{4}-\d{2}-\d{2}\./);
    expect(summary.endsWith('Can you stop this war?')).toBeTruthy();

    const headers = await page.locator('.whatif-role-header .stat-col').allTextContents();
    expect(headers).toEqual(expect.arrayContaining(['Crosses', 'Previously crossed']));
    for (const el of await page.locator('.whatif-role-header .stat-col').all()) {
      expect(await el.getAttribute('title'), 'header tooltip').toBeTruthy();
    }
    expect(await page.locator('.whatif-role-header .stat-label').getAttribute('title'), 'Role header tooltip').toBeTruthy();

    const cells = page.locator('.whatif-role-row .stat-col');
    expect(await cells.count()).toBeGreaterThan(0);
    for (const t of await cells.allTextContents()) expect(t.trim()).toMatch(/%$/);
    for (const el of await cells.all()) expect(await el.getAttribute('title')).toMatch(/\d.* \/ \d.* flights/);

    await elementShot(page, '#whatif-section', path.join(SHOTS, `${tag}-whatif-panel.png`));
  });

  test('the Refuse? pulldown sits in the toolbar, in the same group as Explore', async () => {
    await ready(page, `${BASE}/?cc=SK`);
    expect(await page.evaluate(() => {
      const mount = document.getElementById('toolbar-whatif-mount');
      return !!(mount && document.getElementById('map-toolbar')?.contains(mount));
    }), '#toolbar-whatif-mount is inside #map-toolbar').toBeTruthy();

    const pillBox = await page.locator('#whatif-pulldown-pill').boundingBox();
    const exploreBox = await page.locator('#explore-flights-btn').boundingBox();
    expect(Math.abs(pillBox.y - exploreBox.y), 'same toolbar row').toBeLessThan(20);
    // Membership of the data group is the contract; neighbour order within
    // it is not.
    expect(await page.evaluate(() => {
      const g = document.querySelector('#map-toolbar .toolbar-group[data-group="data"]');
      return !!(g && g.contains(document.getElementById('whatif-pulldown-pill')) && g.contains(document.getElementById('explore-flights-btn')));
    }), 'pill and Explore share the data group').toBeTruthy();
  });

  test('Escape and the collapse button both exit What-If', async () => {
    await ready(page, `${BASE}/?cc=SK`);
    await page.click('#whatif-pulldown-pill');
    await expect.poll(whatIfActive).toBe(true);

    await page.keyboard.press('Escape');
    await expect.poll(whatIfActive, { message: 'Escape exits What-If' }).toBe(false);
    await expect(page.locator('#whatif-pulldown-pill')).toBeVisible();

    // The pulldown stays interactive while What-If is active (whatif.js
    // excludes #toolbar-whatif-mount from the disabled overlay).
    await page.click('#whatif-pulldown-pill');
    await expect.poll(whatIfActive).toBe(true);
    const collapseBtn = page.locator('#whatif-pulldown-collapse');
    await expect(collapseBtn).toBeVisible();
    await expect(collapseBtn).not.toHaveClass(/disabled-overlay/);
    await collapseBtn.click();
    await expect.poll(whatIfActive, { message: 'collapse button exits What-If' }).toBe(false);
  });

  test('zero JS errors', async () => {
    expect(errors).toEqual([]);
  });
});
