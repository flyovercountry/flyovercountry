import { test, expect } from '@playwright/test';
import { ready, BASE } from './helpers.js';

// Clicking a hovered flight while Explore is active opens the aircraft's
// adsbexchange trace in a new tab (map.js click handler); Explore must then
// turn itself off instead of staying armed.
test.describe('Desktop Chromium 1400x900', () => {
  let ctx, page;

  test.beforeAll(async ({ browser }) => {
    ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    page = await ctx.newPage();
  });

  test.afterAll(async () => {
    await ctx.close();
  });

  const exploreBtn = () => page.locator('#explore-flights-btn');
  const hint = () => page.locator('#explore-hint');

  async function enterExplore() {
    await page.click('#explore-flights-btn');
    await expect(exploreBtn()).toHaveClass(/active/);
  }

  async function mapCentre() {
    const box = await page.locator('#map').boundingBox();
    expect(box).toBeTruthy();
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  }

  test('Explore deselects itself after opening a flight', async () => {
    // The first flight click raises a one-time external-link confirmation;
    // pre-acknowledge it so the click reaches window.open.
    await page.addInitScript(() => { localStorage.setItem('adsbe-external-ack', '1'); });
    await ready(page, `${BASE}/`);
    // Stub window.open: no real tab, no dependency on adsbexchange.com. The
    // truthy return takes map.js's "it actually opened" path (popup-blocker
    // guard) that gates the deselect callback.
    await page.evaluate(() => {
      window.__openedUrls = [];
      window.open = (url) => { window.__openedUrls.push(url); return { closed: false }; };
    });
    await enterExplore();

    // Screen midpoint of a rendered route; tiles may still be rendering, so poll.
    const point = await page.waitForFunction(() => {
      if (!window.__map?.getLayer?.('routes-line')) return null;
      const feats = window.__map.queryRenderedFeatures(undefined, { layers: ['routes-line'] });
      if (!feats.length) return null;
      const geom = feats[0].geometry;
      const coords = geom.type === 'LineString' ? geom.coordinates : geom.coordinates[0];
      if (!coords?.length) return null;
      const p = window.__map.project(coords[Math.floor(coords.length / 2)]);
      if (p.x < 0 || p.y < 0 || p.x > window.innerWidth || p.y > window.innerHeight) return null;
      return { x: p.x, y: p.y };
    }, null, { timeout: 15000 }).then(h => h.jsonValue());
    const mapBox = await page.locator('#map').boundingBox();
    const x = mapBox.x + point.x, y = mapBox.y + point.y;

    // map.js handleHover debounces mousemove by 30 ms before setting
    // hoveredId; the click must land after that.
    await page.mouse.move(x, y);
    await page.waitForTimeout(200);
    await page.mouse.click(x, y);

    await expect.poll(() => page.evaluate(() => window.__openedUrls.length), 'window.open called').toBeGreaterThan(0);
    expect(await page.evaluate(() => window.__openedUrls[0])).toContain('adsbexchange.com');
    await expect(exploreBtn()).not.toHaveClass(/active/);
    await expect.poll(() => page.evaluate(() => window.__map.getCanvas().style.cursor), 'trace hover cursor gone').not.toBe('pointer');
  });

  // A bare click (no prior dwell) fires its mousemove inside handleHover's
  // 30 ms debounce, so hoveredId is unset when the click handler runs and
  // map.js bails before onFlightOpen, whatever is under the pointer.
  test('Explore stays active when nothing was hovered', async () => {
    await ready(page, `${BASE}/`);
    await enterExplore();
    const c = await mapCentre();
    await page.mouse.click(c.x, c.y);
    await expect(exploreBtn()).toHaveClass(/active/);
  });

  test('Explore shows an on-map hint, records ex=1 in the URL, and Escape exits it', async () => {
    await ready(page, `${BASE}/`);
    await expect(hint()).toHaveCount(0);

    await enterExplore();
    await expect(hint()).toBeVisible();
    await expect(hint()).toContainText('Esc to exit');
    await expect(hint()).toHaveAttribute('role', 'status');
    await expect(hint()).toHaveAttribute('aria-live', 'polite');
    await expect.poll(() => page.url()).toContain('ex=1');

    await page.keyboard.press('Escape');
    await expect(exploreBtn()).not.toHaveClass(/active/);
    await expect(hint()).toBeHidden();
    await expect.poll(() => page.url()).not.toContain('ex=1');
  });

  test('loading ?ex=1 enters Explore after data load', async () => {
    await ready(page, `${BASE}/?ex=1`);
    await expect(exploreBtn()).toHaveClass(/active/);
    await expect(hint()).toBeVisible();
  });

  test('a miss-click in Explore flashes "no flight" feedback and reverts', async () => {
    await ready(page, `${BASE}/`);
    await enterExplore();
    await expect(hint()).toBeVisible();

    // flashExploreMissHint (app.js) swaps text and class together and
    // reverts both 1500 ms later; an observer armed before the click records
    // both states regardless of how late the test thread is scheduled.
    await page.evaluate(() => {
      const result = { flashed: false, flashText: '', reverted: false, finalText: '' };
      const read = () => {
        const el = document.getElementById('explore-hint');
        if (!el) return;
        const flashing = el.classList.contains('explore-hint-flash')
          && /No flight under the cursor/.test(el.textContent || '');
        if (flashing) {
          result.flashed = true;
          result.flashText = el.textContent || '';
        } else if (result.flashed && !result.reverted) {
          result.reverted = true;
          result.finalText = el.textContent || '';
        }
      };
      new MutationObserver(read).observe(document.body,
        { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['class'] });
      window.__exploreMiss = result;
    });

    const c = await mapCentre();
    await page.mouse.click(c.x, c.y);
    await expect.poll(() => page.evaluate(() => window.__exploreMiss.flashed), { timeout: 3000 }).toBe(true);
    await expect.poll(() => page.evaluate(() => window.__exploreMiss.reverted), { timeout: 4000 }).toBe(true);
    const r = await page.evaluate(() => window.__exploreMiss);
    expect(r.flashText).toMatch(/No flight under the cursor/);
    expect(r.finalText).toMatch(/Hover a route/);
  });
});
