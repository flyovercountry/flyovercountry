import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, openFiltersUI, applyFiltersNow, BASE, SHOTS } from './helpers.js';

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

  const catalog = () => page.evaluate(() => fetch('/data/role_catalog.json').then(r => r.json()));
  const groupChecked = () => page.locator('.filter-view .group-check').evaluateAll(cbs => cbs.map(cb => cb.checked));

  test('theater toggle: Tasking / Ever / Off, with a single "Show excluded only" switch', async () => {
    await ready(page, `${BASE}/`);
    await openFiltersUI(page);
    const theater = page.locator('#filter-theater-group .toggle-btn');
    await expect(theater).toHaveText(['Tasking', 'Ever', 'Off']);
    await expect(page.locator('#filter-theater-group .toggle-btn.active')).toHaveAttribute('data-value', 'tasking');

    const excluded = page.locator('#filter-inc-group .toggle-btn');
    await expect(excluded).toHaveCount(1);
    await expect(excluded).toHaveText('Show excluded only');
    await expect(excluded).toBeVisible();
    await expect(excluded).not.toHaveClass(/active/);

    await page.click('#filter-theater-group .toggle-btn[data-value="off"]');
    await expect(excluded, 'hidden while theater is Off').toBeHidden();
    await page.click('#filter-theater-group .toggle-btn[data-value="tasking"]');
    await expect(excluded).toBeVisible();
    await expect(excluded, 'reset to off when theater is re-enabled').not.toHaveClass(/active/);
    await page.screenshot({ path: path.join(SHOTS, `${tag}-filters-drawer.png`) });

    await page.click('#filter-theater-group .toggle-btn[data-value="ever"]');
    await applyFiltersNow(page);
    await expect.poll(() => page.url()).toContain('fm=ever');
  });

  test('role-group checkboxes follow role_catalog.json order and "on" flags', async () => {
    await ready(page, `${BASE}/`);
    const cat = await catalog();
    await openFiltersUI(page);
    await expect(page.locator('.filter-view .role-group')).toHaveCount(cat.groups.length);
    await expect(page.locator('.filter-view .group-label')).toHaveText(cat.groups.map(g => g.label));
    expect(await groupChecked()).toEqual(cat.groups.map(g => !!g.on));
  });

  test('?roles=1_2 checks exactly groups 1 and 2; unchecking a group drops it from the URL', async () => {
    await ready(page, `${BASE}/?roles=1_2`);
    const cat = await catalog();
    await openFiltersUI(page);
    const expected = cat.groups.map(g => g.id === 1 || g.id === 2);
    expect(await groupChecked()).toEqual(expected);
    const itemStates = await page.locator('.filter-view .role-group').evaluateAll(divs =>
      divs.map(d => [...d.querySelectorAll('.role-item input')].map(cb => cb.checked)));
    itemStates.forEach((states, i) => {
      expect(states, `${cat.groups[i].label} role items all ${expected[i]}`).toEqual(states.map(() => expected[i]));
    });

    // Group 2 (Refuelling) has a single role; unchecking it empties the group.
    const group2 = page.locator('.filter-view .role-group').nth(cat.groups.findIndex(g => g.id === 2));
    await expect(group2.locator('.role-item input')).toHaveCount(1);
    await group2.locator('.role-item input').first().uncheck();
    await applyFiltersNow(page);
    await expect.poll(() => page.url()).toMatch(/[?&]roles=1(&|$)/);
  });

  // DateRangePicker's change handlers ignore an emptied field instead of
  // feeding NaN into the staged range. A native <input type=date> only ever
  // exposes "" or a valid date, so clearing is the reachable stand-in for
  // garbage. A cleared field alone never dirties the form, so a genuine
  // end-date edit is made alongside it; both outcomes are asserted.
  test('an emptied date input is restored, a real edit next to it still applies', async () => {
    await ready(page, `${BASE}/`);
    await openFiltersUI(page);
    const startInput = page.locator('.filter-dates-row .filter-date').first();
    const endInput = page.locator('.filter-dates-row .filter-date').nth(1);
    const startBefore = await startInput.inputValue();
    expect(startBefore).toBeTruthy();
    expect(await endInput.inputValue()).toBeTruthy();

    const earlierEnd = await endInput.evaluate((el) => {
      const d = new Date(el.value + 'T00:00:00Z');
      d.setUTCDate(d.getUTCDate() - 1);
      const iso = d.toISOString().slice(0, 10);
      return iso >= el.min ? iso : el.min;
    });
    await endInput.evaluate((el, v) => {
      el.value = v;
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }, earlierEnd);
    await startInput.evaluate(el => {
      el.value = '';
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await expect(startInput).toHaveValue(startBefore);

    await applyFiltersNow(page);
    await expect.poll(() => page.url()).toContain(`end=${earlierEnd}`);
    // Attached, not visible: if the "Apply now" click lost the race to the
    // auto-apply timer the filter view stays open over the stats body.
    await expect(page.locator('#stats-section .stats-body .country-stat-row').first(), 'stats survive the apply').toBeAttached();
  });

  test('zero JS errors', async () => {
    expect(errors).toEqual([]);
  });
});
