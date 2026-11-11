// The contact chip in the sidebar footer is built from the untracked
// data/contact.json ({text, link}). Each case intercepts that one request
// so every shape is exercised against the same build.
import { test, expect } from '@playwright/test';
import { ready, BASE } from './helpers.js';

const FOOTER = '#sidebar-footer';

async function serveContact(page, body) {
  await page.route('**/data/contact.json', route => {
    if (body === null) return route.fulfill({ status: 404, body: 'not found' });
    return route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(body),
    });
  });
}

test('text and link render as a chip with that text and href', async ({ page }) => {
  await serveContact(page, { text: 'Write to us', link: 'mailto:someone@example.org' });
  const errors = await ready(page, `${BASE}/`);
  const chip = page.locator(`${FOOTER} a.footer-contact`);
  await expect(chip).toHaveCount(1);
  await expect(chip).toHaveAttribute('href', 'mailto:someone@example.org');
  await expect(chip).toHaveText('Write to us');
  expect(errors).toEqual([]);
});

test('text defaults to "Contact us"; an https link such as signal.me works', async ({ page }) => {
  await serveContact(page, { link: 'https://signal.me/#eu/abc' });
  await ready(page, `${BASE}/`);
  const chip = page.locator(`${FOOTER} a.footer-contact`);
  await expect(chip).toHaveAttribute('href', 'https://signal.me/#eu/abc');
  await expect(chip).toHaveText('Contact us');
});

test('missing config: no contact element, other footer buttons intact, no errors', async ({ page }) => {
  await serveContact(page, null);
  const errors = await ready(page, `${BASE}/`);
  await expect(page.locator(`${FOOTER} .footer-contact`)).toHaveCount(0);
  await expect(page.locator(`${FOOTER} .footer-action`).first()).toContainText(/tour/i);
  await expect(page.locator(`${FOOTER} a.footer-action[href*="github.com"]`)).toHaveCount(1);
  await expect(page.locator(FOOTER)).not.toContainText(/contact|mailto|signal/i);
  expect(errors).toEqual([]);
});

for (const [label, body] of [
  ['no link', { text: 'Contact us' }],
  ['javascript: scheme', { text: 'x', link: 'javascript:alert(1)' }],
  ['plain http', { text: 'x', link: 'http://example.org/' }],
]) {
  test(`${label}: treated as missing`, async ({ page }) => {
    await serveContact(page, body);
    await ready(page, `${BASE}/`);
    await expect(page.locator(`${FOOTER} .footer-contact`)).toHaveCount(0);
  });
}
