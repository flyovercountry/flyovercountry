// The "Fork the repo" chip and the methodology page's "source code" link
// come from the untracked data/repo.json. The chip is checked live by
// intercepting that one request; the methodology rewrite is a build-time
// step, so its function is exercised directly on HTML strings.
import { test, expect } from '@playwright/test';
import { ready, BASE } from './helpers.js';
import { rewriteMethodology, resolveRepoLink } from '../../frontend/apply-links.mjs';

const FOOTER = '#sidebar-footer';

async function serveRepo(page, body) {
  await page.route('**/data/repo.json', route => {
    if (body === null) return route.fulfill({ status: 404, body: 'not found' });
    return route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify(body),
    });
  });
}

test('repo.json text and link render as the footer chip', async ({ page }) => {
  await serveRepo(page, { text: 'Source', link: 'https://example.org/repo' });
  const errors = await ready(page, `${BASE}/`);
  const chip = page.locator(`${FOOTER} a.footer-repo`);
  await expect(chip).toHaveAttribute('href', 'https://example.org/repo');
  await expect(chip).toHaveText('Source');
  expect(errors).toEqual([]);
});

test('text defaults to "Fork the repo"', async ({ page }) => {
  await serveRepo(page, { link: 'https://example.org/repo' });
  await ready(page, `${BASE}/`);
  await expect(page.locator(`${FOOTER} a.footer-repo`)).toHaveText('Fork the repo');
});

test('missing repo.json: no chip, no placeholder, no errors', async ({ page }) => {
  await serveRepo(page, null);
  const errors = await ready(page, `${BASE}/`);
  await expect(page.locator(`${FOOTER} .footer-repo`)).toHaveCount(0);
  await expect(page.locator(FOOTER)).not.toContainText(/repo|fork|github/i);
  expect(errors).toEqual([]);
});

test('the built methodology page carries the deployed repo link, or no sentence', async ({ request }) => {
  const html = await (await request.get(`${BASE}/methodology.html`)).text();
  expect(html, 'no unresolved placeholder').not.toMatch(/data-repo-link href=""/);
  const cfg = await request.get(`${BASE}/data/repo.json`);
  if (cfg.ok()) {
    const link = resolveRepoLink(await cfg.json());
    expect(html).toContain(`data-repo-link href="${link}"`);
  } else {
    expect(html).not.toMatch(/data-repo-sentence/);
  }
});

test.describe('rewriteMethodology (build step)', () => {
  const page = '<p>Safe. <span data-repo-sentence>The <a data-repo-link href="" target="_blank" rel="noopener">source code</a> is public.</span></p>';

  test('fills the href from a valid link, escaping attribute characters', () => {
    const out = rewriteMethodology(page, 'https://example.org/r?a=1&b="2"');
    expect(out).toContain('data-repo-link href="https://example.org/r?a=1&amp;b=&quot;2&quot;"');
    expect(out).toContain('is public.');
  });

  test('removes the whole sentence when there is no link', () => {
    const out = rewriteMethodology(page, null);
    expect(out).toBe('<p>Safe. </p>');
  });

  test('is idempotent on an already-resolved page', () => {
    const once = rewriteMethodology(page, 'https://example.org/r');
    expect(rewriteMethodology(once, 'https://example.org/r')).toBe(once);
  });

  test('resolveRepoLink rejects missing, http and javascript links', () => {
    expect(resolveRepoLink(null)).toBeNull();
    expect(resolveRepoLink({ text: 'x' })).toBeNull();
    expect(resolveRepoLink({ link: 'http://example.org/' })).toBeNull();
    expect(resolveRepoLink({ link: 'javascript:alert(1)' })).toBeNull();
    expect(resolveRepoLink({ link: 'https://example.org/' })).toBe('https://example.org/');
  });
});
