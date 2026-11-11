import path from 'path';
import { test, expect } from '@playwright/test';
import { BASE, SHOTS, ready, waitForMap, mapPoint, tapAt, settleMap, countryLayerState } from './helpers.js';

// Country selection on the map must behave the same for a finger and a
// mouse: tap a country to select it, tap it again to deselect, and tap
// anywhere with no selectable country (sea, off-limits) to deselect. On
// touch there must be no hover fill left behind — a finger never "leaves"
// a country, so a hover highlight would stick to the last-tapped country
// and read as "still selected" after the deselect (the bug this covers).
const DE = [10.4, 51.1];
const SEA_CANDIDATES = [[3.0, 56.5], [-6.0, 46.0], [17.0, 37.0], [-2.0, 49.5]];

async function germany(page) {
  const pt = await mapPoint(page, DE);
  expect(pt.cc, 'Germany renders under the test point').toBe('DE');
  expect(pt.onCanvas, 'the map canvas is on top at the test point').toBeTruthy();
  return pt;
}

async function openSea(page) {
  for (const ll of SEA_CANDIDATES) {
    const pt = await mapPoint(page, ll);
    if (!pt.cc && pt.onCanvas) return pt;
  }
  throw new Error('no open-sea point free of UI found at this viewport');
}

test.describe('country select / deselect', () => {
  test('second tap on the selected country deselects it completely', async ({ page }) => {
    const tag = `${test.info().project.name}-deselect-same-country`;
    await ready(page, `${BASE}/`);
    await waitForMap(page);
    const de = await germany(page);

    await tapAt(page, de);
    await settleMap(page);
    let s = await countryLayerState(page);
    expect(s.selected, 'first tap selects').toBe('DE');
    expect(s.url, 'URL carries the selection').toContain('cc=DE');
    await page.screenshot({ path: path.join(SHOTS, `${tag}-1-selected.png`) });

    await tapAt(page, de);
    await settleMap(page);
    s = await countryLayerState(page);
    expect(s.selected, 'second tap clears the selection border').toBeNull();
    expect(s.url, 'second tap clears the URL').not.toContain('cc=');
    if (test.info().project.use.hasTouch) {
      expect(s.hover, 'no hover fill left on the country after a touch deselect').toBeNull();
    }
    await page.screenshot({ path: path.join(SHOTS, `${tag}-2-deselected.png`) });
  });

  test('tap on open sea deselects; with nothing selected it is a no-op', async ({ page }) => {
    const tag = `${test.info().project.name}-deselect-sea`;
    await ready(page, `${BASE}/`);
    await waitForMap(page);
    const de = await germany(page);
    const sea = await openSea(page);

    await tapAt(page, de);
    await settleMap(page);
    expect((await countryLayerState(page)).selected).toBe('DE');

    await tapAt(page, sea);
    await settleMap(page);
    let s = await countryLayerState(page);
    expect(s.selected, 'sea tap deselects the selected country').toBeNull();
    expect(s.url).not.toContain('cc=');
    if (test.info().project.use.hasTouch) expect(s.hover).toBeNull();
    await page.screenshot({ path: path.join(SHOTS, `${tag}-after-sea-tap.png`) });

    await tapAt(page, sea);
    await settleMap(page);
    s = await countryLayerState(page);
    expect(s.selected, 'sea tap with nothing selected changes nothing').toBeNull();
    expect(s.url).not.toContain('cc=');
  });
});
