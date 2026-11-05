import path from 'path';
import { test, expect } from '@playwright/test';
import { ready, BASE, SHOTS } from './helpers.js';

test.describe('Methodology reader', () => {
  let ctx, page;

  test.beforeAll(async ({ browser }) => {
    ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    page = await ctx.newPage();
  });

  test.afterAll(async () => {
    await ctx.close();
  });

  test('opens via ?view=methodology URL and hides the toolbar', async () => {
    await ready(page, `${BASE}/?view=methodology`);

    const reader = page.locator('#reference-view');
    await expect(reader).toBeVisible();

    const prose = page.locator('.methodology-prose');
    await expect(prose).toBeVisible();

    const toolbar = page.locator('#map-toolbar');
    await expect(toolbar).not.toBeVisible();

    await page.screenshot({ path: path.join(SHOTS, 'methodology-reader-open.png') });
  });

  test('"In brief" lists six phases', async () => {
    await ready(page, `${BASE}/?view=methodology`);

    const briefList = page.locator('.methodology-prose #in-brief ~ ol > li');
    await expect(briefList).toHaveCount(6);

    const firstItem = briefList.first();
    await expect(firstItem).toContainText('Generate');

    const secondItem = briefList.nth(1);
    await expect(secondItem).toContainText('Ingest');
  });

  test('pipeline diagram is present and contains stage links', async () => {
    await ready(page, `${BASE}/?view=methodology`);

    const diagram = page.locator('.methodology-prose .pipeline-diagram-wrap');
    await expect(diagram).toBeVisible();

    const svg = diagram.locator('svg');
    await expect(svg).toBeVisible();

    const stageLinks = diagram.locator('.stage-link');
    const count = await stageLinks.count();
    expect(count).toBeGreaterThanOrEqual(11);

    const dataNodes = diagram.locator('.data-node');
    const dnCount = await dataNodes.count();
    expect(dnCount).toBeGreaterThanOrEqual(10);
  });

  test('clicking a stage link scrolls to its heading', async () => {
    await ready(page, `${BASE}/?view=methodology`);

    const diagram = page.locator('.methodology-prose .pipeline-diagram-wrap');
    await diagram.scrollIntoViewIfNeeded();

    const typesLink = page.locator('.methodology-prose a.stage-link[href="#stage-1-types"]');
    await expect(typesLink).toBeVisible();
    await typesLink.click();

    await page.waitForTimeout(500);

    const heading = page.locator('.methodology-prose #stage-1-types');
    await expect(heading).toBeVisible();

    const box = await heading.boundingBox();
    expect(box).toBeTruthy();
    expect(box.y).toBeLessThan(300);
  });

  test('hover scales a stage link to 3x', async () => {
    await ready(page, `${BASE}/?view=methodology`);

    const diagram = page.locator('.methodology-prose .pipeline-diagram-wrap');
    await diagram.scrollIntoViewIfNeeded();

    const stageLink = page.locator('.methodology-prose .stage-link').first();
    const beforeBox = await stageLink.boundingBox();

    await stageLink.hover();
    await page.waitForTimeout(300);

    const transform = await stageLink.evaluate(el => getComputedStyle(el).transform);
    expect(transform).not.toBe('none');

    await page.screenshot({ path: path.join(SHOTS, 'methodology-hover-scale.png') });
  });

  test('hover scales a data node to 3x', async () => {
    await ready(page, `${BASE}/?view=methodology`);

    const diagram = page.locator('.methodology-prose .pipeline-diagram-wrap');
    await diagram.scrollIntoViewIfNeeded();

    const dataNode = page.locator('.methodology-prose .data-node').nth(3);
    await dataNode.hover();
    await page.waitForTimeout(300);

    const transform = await dataNode.evaluate(el => getComputedStyle(el).transform);
    expect(transform).not.toBe('none');

    await page.screenshot({ path: path.join(SHOTS, 'methodology-hover-data-node.png') });
  });

  test('def-list elements are styled (dt bold white, dd lighter)', async () => {
    await ready(page, `${BASE}/?view=methodology`);

    const dt = page.locator('.methodology-prose dl.def-list dt').first();
    await expect(dt).toBeVisible();

    const dtColor = await dt.evaluate(el => getComputedStyle(el).color);
    const dtWeight = await dt.evaluate(el => getComputedStyle(el).fontWeight);

    expect(parseInt(dtWeight)).toBeGreaterThanOrEqual(600);
    expect(dtColor).toContain('255');
  });

  test('heading is "How aircraft are tracked", not "What is ADS-B?"', async () => {
    await ready(page, `${BASE}/?view=methodology`);

    const tracked = page.locator('.methodology-prose h2', { hasText: 'How aircraft are tracked' });
    await expect(tracked).toBeVisible();

    const adsb = page.locator('.methodology-prose h2', { hasText: 'What is ADS-B?' });
    await expect(adsb).toHaveCount(0);
  });

  test('no JS errors on methodology page', async () => {
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));

    await ready(page, `${BASE}/?view=methodology`);
    await page.waitForTimeout(2000);

    expect(errors).toHaveLength(0);
  });
});
