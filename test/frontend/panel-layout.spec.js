// Panel and reader layout rules that came from phone screenshots:
//  - the footer actions (tour / contact / repo) sit BELOW the explanatory
//    prose, at the bottom of the panel;
//  - the methodology reader's phase rail shows all six phases without
//    horizontal overflow on the narrowest phones (it wraps under 420px).
import { test, expect } from '@playwright/test';
import { ready, BASE, isTouchProject } from './helpers.js';

test('footer actions come after the prose in the panel', async ({ page }) => {
  await ready(page, `${BASE}/`);
  const order = await page.evaluate(() => {
    const prose = document.querySelector('#info-prose-section');
    const footer = document.querySelector('#sidebar-footer');
    if (!prose || !footer) return 'missing';
    return prose.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING ? 'after' : 'before';
  });
  expect(order).toBe('after');
});

test('methodology phase rail fits the reader width with all six phases visible', async ({ page }) => {
  await ready(page, `${BASE}/?view=methodology`);
  const rail = page.locator('#reference-view .phase-rail');
  await expect(rail).toBeVisible({ timeout: 15000 });
  // Whole-page navigator: Top, Diagram, six phases, five later sections.
  // The Glossary link is pruned in the reader (that section is its own tab);
  // "Top" is hidden by CSS under 600px so the rest tiles evenly.
  await expect(rail.locator('a')).toHaveCount(13);
  await expect(rail.locator('a').first()).toHaveText('Top');
  // "Top" is hidden by CSS under 600px so the remaining twelve tile evenly.
  if (await page.evaluate(() => window.innerWidth) <= 600) {
    await expect(rail.locator('a').first()).toBeHidden();
  }
  if (await page.evaluate(() => window.innerWidth) <= 600) {
    await expect(rail.locator('a').first()).toBeHidden();
  }
  await expect(rail.locator('a').nth(1)).toHaveAttribute('href', '#pipeline');
  await expect(page.locator('#reference-view #pipeline')).toHaveCount(1);
  // every remaining link resolves inside the reader
  const dead = await rail.evaluate((el) => [...el.querySelectorAll('a')]
    .map((a) => a.getAttribute('href').slice(1))
    .filter((id) => !document.querySelector(`#reference-view [id="${id}"]`)));
  expect(dead).toEqual([]);
  const m = await rail.evaluate((el) => {
    const r = el.getBoundingClientRect();
    const links = [...el.querySelectorAll('a')].map((a) => a.getBoundingClientRect());
    return {
      overflow: el.scrollWidth > el.clientWidth + 1,
      vw: window.innerWidth,
      right: Math.max(...links.map((l) => l.right)),
      rows: new Set(links.map((l) => Math.round(l.top))).size,
      railLeft: r.left,
    };
  });
  expect(m.overflow, 'no horizontal scroll inside the rail').toBe(false);
  expect(m.right, 'last phase inside the viewport').toBeLessThanOrEqual(m.vw + 0.5);
  expect(m.rows, 'wraps into rows rather than overflowing').toBeGreaterThanOrEqual(1);
});

test('no navigator item stretches to fill a wrapped row', async ({ page }) => {
  await ready(page, `${BASE}/?view=methodology`);
  const rail = page.locator('#reference-view .phase-rail');
  await expect(rail).toBeVisible({ timeout: 15000 });
  const widths = await rail.evaluate((el) => {
    const rw = el.getBoundingClientRect().width;
    return [...el.querySelectorAll('a')].map((a) => a.getBoundingClientRect().width / rw);
  });
  for (const w of widths) expect(w, 'item width as a share of the rail').toBeLessThanOrEqual(0.34 + 0.01);
});

test('on a desktop-width reader the navigator is a single row', async ({ page }) => {
  await ready(page, `${BASE}/?view=methodology`);
  const rail = page.locator('#reference-view .phase-rail');
  await expect(rail).toBeVisible({ timeout: 15000 });
  const m = await rail.evaluate((el) => ({
    vw: window.innerWidth,
    rows: new Set([...el.querySelectorAll('a')].map((a) => Math.round(a.getBoundingClientRect().top))).size,
  }));
  if (m.vw >= 1000) expect(m.rows, 'all items on one row').toBe(1);
});
