import path from 'path';
import { test, expect } from '@playwright/test';
import { BASE, SHOTS, ready, waitForMap, mapPoint, tapAt } from './helpers.js';

// On the phone layout the panel is a 52px bar at the bottom. A selection
// flashes the bar once and leaves the chevron in the accent colour while a
// country is selected, so the eye is drawn to the thing that expands.
const DE = [10.4, 51.1];

test('collapsed bar signals it opens once a country is selected', async ({ page }) => {
  const tag = `${test.info().project.name}-panel-affordance`;
  await ready(page, `${BASE}/`);
  await waitForMap(page);

  const bar = page.locator('.collapsed-sheet');
  await expect(bar).toBeVisible();
  const chevronStroke = () => bar.evaluate(el => getComputedStyle(el.querySelector('.collapsed-chevron')).stroke);
  await expect(bar).not.toHaveClass(/has-country/);
  const before = await chevronStroke();
  await page.screenshot({ path: path.join(SHOTS, `${tag}-0-before.png`) });

  const de = await mapPoint(page, DE);
  expect(de.cc).toBe('DE');
  // The cue is a 1.6 s one-shot class that starts once the tap's filter
  // apply has run; an observer armed before the tap records it however
  // late the test thread is scheduled.
  await bar.evaluate(el => {
    window.__attentionSeen = false;
    new MutationObserver(() => { if (el.classList.contains('attention')) window.__attentionSeen = true; })
      .observe(el, { attributes: true, attributeFilter: ['class'] });
  });
  await tapAt(page, de);

  await expect(bar).toHaveClass(/has-country/, { timeout: 10000 });
  await expect.poll(() => page.evaluate(() => window.__attentionSeen),
    { message: 'one-shot attention cue plays on selection', timeout: 10000 }).toBe(true);
  await page.screenshot({ path: path.join(SHOTS, `${tag}-1-flash.png`) });
  await expect(bar.locator('.collapsed-title')).toContainText('Germany');
  expect(await chevronStroke(), 'chevron takes the accent colour').not.toBe(before);

  await expect(bar, 'cue is one-shot, not a permanent animation').not.toHaveClass(/attention/, { timeout: 4000 });
  await expect(bar).toHaveClass(/has-country/);
  await page.screenshot({ path: path.join(SHOTS, `${tag}-2-settled.png`) });
});
