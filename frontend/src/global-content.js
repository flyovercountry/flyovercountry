// The reader's Methodology and Glossary entries are extracted from
// /methodology.html (frontend/src/static/methodology.html), the one place
// that text is maintained. The standalone page stays published: it is the
// target of panel-data.js's theater-filter footnote link and works without
// JS. Fetched lazily and cached: only visitors who open an entry pay for it.
const METHODOLOGY_URL = "/methodology.html";

// Same shape as reference.js's tabsFor(cc) entries (slug/label/kind) so the
// reader treats global and country entries identically.
export const GLOBAL_TABS = [
  { slug: "statistics", label: "Statistics", kind: "global-stats" },
  { slug: "methodology", label: "Methodology", kind: "methodology" },
  { slug: "glossary", label: "Glossary", kind: "glossary" },
  // Rendered in the reader rather than linking to roles.html because it
  // interleaves live aircraft counts; roles.html stays as the no-JS fallback.
  { slug: "roles", label: "Aircraft roles", kind: "roles" },
];

/** Table-of-contents entries when no country is selected: always all four. */
export function globalTabsFor() {
  return GLOBAL_TABS;
}

/** `kind` values that mark an entry as global; InfoSection uses this to find
 * where the "General (not specific to ...)" run begins. */
export const GLOBAL_TAB_KINDS = new Set(["global-stats", "methodology", "glossary", "roles"]);

/** Global entries that stay reachable while a country is selected. The
 * global Statistics entry is excluded because tabsFor(cc) already carries the
 * country's own Statistics; Aircraft roles is reachable only from the
 * no-country ToC. */
export function generalTabsFor() {
  return GLOBAL_TABS.filter(t => t.kind === "methodology" || t.kind === "glossary");
}

let cache = null;      // { methodologyHtml, glossaryTerms } once loaded
let loading = null;    // in-flight promise, so concurrent callers share one fetch

/** Resolved content, or null until the fetch completes (renderFromStore is
 * synchronous). */
export function getGlobalContent() {
  return cache;
}

/** Start or join the fetch+parse. Never rejects: a failed fetch yields
 * empty content. */
export function ensureGlobalContent() {
  if (cache) return Promise.resolve(cache);
  if (!loading) {
    loading = fetch(METHODOLOGY_URL)
      .then(r => r.text())
      .then(parseGlobalContent)
      .catch(() => ({ methodologyHtml: "", glossaryTerms: [] }))
      .then(data => { cache = data; loading = null; return data; });
  }
  return loading;
}

/** Split methodology.html into the Methodology entry (the document minus
 * its page chrome and its Glossary section) and the Glossary entry (that
 * section's term list as data; GlossaryTab renders a definition list). */
function parseGlobalContent(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const container = doc.querySelector(".container");
  if (!container) return { methodologyHtml: "", glossaryTerms: [] };

  for (const sel of [".back-link", "h1", ".subtitle", ".tab-bar"]) {
    container.querySelector(sel)?.remove();
  }

  const glossarySection = container.querySelector("#glossary");
  const glossaryTerms = glossarySection ? extractTerms(glossarySection) : [];
  // The heading and the <ul> after it.
  glossarySection?.nextElementSibling?.remove();
  glossarySection?.remove();

  // The page navigator links to sections by id; drop any link whose
  // target is not part of this entry (the Glossary, split off above), so
  // the reader never shows a dead link.
  for (const a of container.querySelectorAll(".phase-rail a[href^='#']")) {
    const id = a.getAttribute("href").slice(1);
    if (!container.querySelector(`[id="${id}"]`)) a.remove();
  }

  return { methodologyHtml: container.innerHTML.trim(), glossaryTerms };
}

/** <h2 id="glossary"> followed by <ul><li><span class="glossary-term">Term
 * </span> &mdash; definition.</li>...</ul> -> [{term, definitionHtml}].
 * Definitions stay HTML; methodology.html is our own static asset. */
function extractTerms(heading) {
  const list = heading.nextElementSibling;
  if (!list || list.tagName !== "UL") return [];
  return Array.from(list.children).map(li => {
    const termEl = li.querySelector(".glossary-term");
    const term = termEl?.textContent?.trim() || "";
    const clone = li.cloneNode(true);
    clone.querySelector(".glossary-term")?.remove();
    // Strip the "Term — " separator left once the term span is removed.
    const definitionHtml = clone.innerHTML.replace(/^\s*(&mdash;|—|-)\s*/, "").trim();
    return { term, definitionHtml };
  }).filter(t => t.term);
}

// The generated role taxonomy (backend/pipeline/gen/roles_page.py) for the
// in-app roles view, fetched lazily like the methodology content.
const ROLES_URL = "/roles.reference.json";
let rolesCache = null;
let rolesLoading = null;

export function getRolesData() { return rolesCache; }

export function ensureRolesData() {
  if (rolesCache) return Promise.resolve(rolesCache);
  if (!rolesLoading) {
    rolesLoading = fetch(ROLES_URL)
      .then(r => r.json())
      .catch(() => null)
      .then(d => { rolesCache = d; rolesLoading = null; return d; });
  }
  return rolesLoading;
}
