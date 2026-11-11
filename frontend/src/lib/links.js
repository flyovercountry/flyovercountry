// Per-deployment links come from UNTRACKED files in data/in/config/frontend/
// (see README "Contact and repository links"):
//
//   contact.json  { "text": "Contact us",    "link": "mailto:..." | "https://..." }
//   repo.json     { "text": "Fork the repo", "link": "https://github.com/..." }
//
// They are copied into data/out/data/ by the build like the rest of that
// directory and fetched at runtime. `link` is required and must be a
// mailto: or https: URL; `text` is the button label and has a default per
// file. When a file is absent or invalid the corresponding affordance
// renders blank: no button, no link, no placeholder text.
//
// backend/pipeline/gen/roles_page.py (contact) and frontend/apply-links.mjs
// (repo link in methodology.html) apply the same rules at build time; keep
// them in sync with resolveLink below.

const ALLOWED_SCHEMES = ["mailto:", "https:"];

export const CONTACT_DEFAULT_TEXT = "Contact us";
export const REPO_DEFAULT_TEXT = "Fork the repo";

/** Normalise raw config into { text, href } or null when there is nothing
 * valid to show. */
export function resolveLink(raw, defaultText) {
  if (!raw || typeof raw !== "object") return null;
  const link = String(raw.link || "").trim();
  if (!ALLOWED_SCHEMES.some((s) => link.toLowerCase().startsWith(s))) return null;
  const text = String(raw.text || "").trim() || defaultText;
  return { text, href: link };
}

async function loadLink(name, defaultText, fetchImpl) {
  try {
    const res = await fetchImpl(`/data/${name}.json`);
    if (!res.ok) return null;
    return resolveLink(await res.json(), defaultText);
  } catch {
    return null;
  }
}

/** null on 404 or any failure, which the callers render as "no link". */
export function loadContact(fetchImpl = fetch) {
  return loadLink("contact", CONTACT_DEFAULT_TEXT, fetchImpl);
}

export function loadRepo(fetchImpl = fetch) {
  return loadLink("repo", REPO_DEFAULT_TEXT, fetchImpl);
}
