// Ukraine, Belarus and Russia are permissiveness-0 entries with interaction
// level 0 (countries.modes.json): they share the permissiveness-0 fill
// colour with AT/CH for geographic context but cannot be selected and have
// no stats row. Bosnia (BA) and Serbia (RS) are absent
// from both the config and the generated boundaries. ?cc=UA's rejection is
// covered in url-params.spec.js.
import { test, expect } from '@playwright/test';
import { ready, BASE, SHOTS } from './helpers.js';

const DISPLAY_ONLY = ['UA', 'BY', 'RU'];
const PERMISSIVENESS_0 = ['AT', 'CH', 'UA', 'BY', 'RU'];
const DROPPED = ['BA', 'RS'];

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

  const fetchJson = (file) => page.evaluate(async (url) => (await fetch(url)).json(), `${BASE}/data/${file}`);

  test('UA/BY/RU share the permissiveness-0 fill colour; BA/RS are gone; display-only countries have no stats row', async () => {
    await ready(page, `${BASE}/`);

    // Cross-checked against AT rather than a hard-coded hex, so a palette
    // change cannot silently desynchronise the two.
    const colorExpr = await page.evaluate(() => window.__map.getPaintProperty('country-fill', 'fill-color'));
    const colorOf = (cc) => { const i = colorExpr.indexOf(cc); return i >= 0 ? colorExpr[i + 1] : null; };
    const level0Color = colorOf('AT');
    expect(level0Color, 'AT has a fill-color in the match expression').toBeTruthy();
    for (const cc of PERMISSIVENESS_0) expect(colorOf(cc), `${cc} shares AT's colour`).toBe(level0Color);
    expect(colorOf('GB'), 'a different permissiveness level has a different colour').not.toBe(level0Color);

    const modes = await fetchJson('countries.modes.json');
    for (const cc of DROPPED) expect(Object.hasOwn(modes.countries, cc), `${cc} absent from countries.modes.json`).toBeFalsy();
    for (const cc of DISPLAY_ONLY) {
      const info = modes.countries[cc];
      expect(info, `${cc} is a countries.modes.json entry`).toBeTruthy();
      expect(info.interaction, `${cc} is display-only`).toBe(0);
    }

    const boundaries = await fetchJson('boundaries.geojson');
    const boundaryCcs = new Set(boundaries.features.map(f => f.properties.cc));
    for (const cc of DROPPED) expect(boundaryCcs.has(cc), `${cc} has no boundary polygon`).toBeFalsy();

    // The panel's own global table is capped at the top 8 by activity, so
    // absence there is not proof; the reader's Statistics entry is uncapped.
    await page.locator('#info-section .toc-item', { hasText: 'Statistics' }).first().click();
    const rows = page.locator('#reference-view .country-stat-row');
    await expect(rows.first()).toBeVisible();
    expect(await rows.count(), 'the uncapped table has more than 8 rows').toBeGreaterThan(8);
    for (const cc of DISPLAY_ONLY) {
      await expect(page.locator(`#reference-view .country-stat-row[data-cc="${cc}"]`), `${cc} has no stats row`).toHaveCount(0);
    }
    await page.keyboard.press('Escape');
    await expect(page.locator('#reference-view.hidden')).toHaveCount(1);
  });

  test('clicking UA/BY/RU on the map selects nothing', async () => {
    await ready(page, `${BASE}/`);
    const boundaries = await fetchJson('boundaries.geojson');
    const centroidOf = (cc) => {
      const f = boundaries.features.find(x => x.properties.cc === cc);
      const pts = [];
      const walk = (c) => Array.isArray(c[0]) ? c.forEach(walk) : pts.push(c);
      walk(f.geometry.coordinates);
      return [pts.reduce((a, c) => a + c[0], 0) / pts.length, pts.reduce((a, c) => a + c[1], 0) / pts.length];
    };

    const mapBox = await page.locator('#map').boundingBox();
    expect(mapBox).toBeTruthy();

    const tested = [];
    for (const cc of DISPLAY_ONLY) {
      // project() and queryRenderedFeatures() in one evaluate: a MapLibre
      // Point round-tripped through Playwright's serialisation no longer
      // queries the point, only the whole viewport.
      const probe = await page.evaluate((c) => {
        const p = window.__map.project(c);
        const feats = window.__map.queryRenderedFeatures(p, { layers: ['country-fill'] });
        return { x: p.x, y: p.y, hitCc: feats[0]?.properties?.cc || null };
      }, centroidOf(cc));
      const onScreen = probe.x >= 0 && probe.x <= mapBox.width && probe.y >= 0 && probe.y <= mapBox.height;
      // A centroid outside the default view, or one whose pixel lands on a
      // neighbouring polygon, would not exercise the non-interactivity path.
      if (!onScreen || probe.hitCc !== cc) continue;

      await page.mouse.click(mapBox.x + probe.x, mapBox.y + probe.y);
      tested.push(cc);
      await expect(page.locator('.country-sel-text').first()).toContainText('No country selected');
      expect(page.url(), `clicking ${cc} adds no cc=`).not.toContain('cc=');
      await expect(page.locator('.deselect-country-btn')).toHaveCount(0);
    }
    expect(tested.length, 'at least one of UA/BY/RU was clickable at the default view').toBeGreaterThan(0);
    await page.screenshot({ path: `${SHOTS}/eastred-map-noninteractive.png` });
  });

  test('zero JS errors', async () => {
    expect(errors).toEqual([]);
  });
});
