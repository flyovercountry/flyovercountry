// Per-country sourced reference: gating predicates + verbatim rendering of each
// analysis section with its own footnotes and citations list. Data comes from
// country.reference.json, merged onto focusConfig as cc.reference =
// { refusal_analysis: { <section>: {text} }, citations: { <hex>: str } }.
//
// FIDELITY RULES (this module guards them):
//   - Render `text` VERBATIM. Never paraphrase, reorder, merge, or truncate.
//   - Escape all text safely (quotes, Arabic/Cyrillic/accented Latin, [hex] brackets).
//   - Footnotes are numbered PER ENTRY (per ToC item) by first appearance; a
//     source cited twice in one entry reuses its number within that entry.
//     One deliberate exception: the merged "What happened: Refusal
//     vs Cooperation" entry (refusal_history + cooperation_2026) numbers
//     continuously ACROSS its two sub-sections — see combinedOrdinals — so a
//     source cited in both halves still gets exactly one number, not two.
//     This is a merge of navigation/citation bookkeeping only; the prose
//     itself is untouched and the two halves stay visually distinct
//     sub-sections with their own headings.
//   - Each entry shows its own citations list; there is no site-wide list.
//   - A footnote at the end of a sentence renders AFTER the terminating period.
//   - Citation strings are opaque: never fetch/validate/rewrite the URL; the
//     `local:` archive path is dropped (non-servable, internal).
import { countryEntry, hasStats as entryHasStats } from "./data.js";
import { escapeHtml } from "./text.js";
import { domainFromUrl } from "./panel-data.js";

const SECTIONS = [
  "authorization_mechanism", "refusal_history", "cooperation_2026", "how_to_refuse",
];
// Sub-heading text used both standalone (auth/refuse, each its own ToC
// entry) and inside the merged Refusal-vs-Cooperation entry (refusal_history
// + cooperation_2026, as sub-section headings — the merge is of navigation,
// not of content, so these two labels stay as they are).
const SECTION_LABELS = {
  authorization_mechanism: "Treaties: How are flights authorized?",
  refusal_history: "Refusal history",
  cooperation_2026: "2026 Iran-war cooperation",
  how_to_refuse: "Legal: How to say no",
};
// URL slugs for the single-section entries; url.js's VIEW_SLUGS must match.
export const SECTION_SLUG = {
  authorization_mechanism: "auth", how_to_refuse: "refuse",
};
// The merged "Refusal vs Cooperation" entry: one ToC slot, two sourced
// sections (tabsFor). url.js's LEGACY_VIEW_ALIASES must match the slug.
const COMBINED_SLUG = "refusal-coop";
const COMBINED_LABEL = "What happened: Refusal vs Cooperation";
const COMBINED_SECTIONS = ["refusal_history", "cooperation_2026"];
// The per-type / per-group statistics breakdown entry.
export const MORE_STATS_SLUG = "more-stats";
const HEX = /\[([0-9a-f]{5})\]/g;
// A run of one or more [hex] codes (optionally space-separated), the whitespace
// after it, and an optional sentence terminator to move the marker(s) after.
const CITE_RUN = /(\s*)(\[[0-9a-f]{5}\](?:\s*\[[0-9a-f]{5}\])*)(\s*)([.!?]?)/g;
const PROSE_KEYS = ["general", "counterfactual"];

// ---- per-country lookup + gating predicates (single source; reuse everywhere) ----

/** Statistics available — derived from the `interaction` ordinal via
 * data.js's hasStats. */
export function hasStats(cc, fc) {
  const info = countryEntry(cc, fc);
  return !!info && entryHasStats(info);
}

/** Cited research present → the sourced analysis-section tabs. */
function hasCited(cc, fc) {
  return !!countryEntry(cc, fc)?.reference?.refusal_analysis;
}

/** Prose or an editorial headline_note present: the Summary tab. */
export function hasProse(cc, fc) {
  const info = countryEntry(cc, fc);
  return !!info && (!!info.headline_note || PROSE_KEYS.some(k => info[k]?.text || info[k]));
}

/** Analysis-section keys with text, in fixed order — each becomes its own tab. */
function availableSections(cc) {
  const ra = countryEntry(cc)?.reference?.refusal_analysis;
  if (!ra) return [];
  return SECTIONS.filter(s => ra[s]?.text);
}

/** A country's reader entries in fixed order: Summary, Statistics, then
 * each researched section, with refusal_history and cooperation_2026
 * folded into one combined entry. The ToC and the reader both read this. */
export function tabsFor(cc) {
  const tabs = [];
  if (hasProse(cc)) tabs.push({ slug: "background", label: "Summary", kind: "background" });
  if (hasStats(cc)) {
    tabs.push({ slug: MORE_STATS_SLUG, label: "Statistics", kind: "stats-breakdown" });
  }

  const present = new Set(availableSections(cc));
  if (present.has("authorization_mechanism")) {
    tabs.push({
      slug: SECTION_SLUG.authorization_mechanism,
      label: SECTION_LABELS.authorization_mechanism,
      kind: "section", section: "authorization_mechanism",
    });
  }
  const combinedPresent = COMBINED_SECTIONS.filter(sec => present.has(sec));
  if (combinedPresent.length > 0) {
    tabs.push({
      slug: COMBINED_SLUG, label: COMBINED_LABEL,
      kind: "combined-section", sections: combinedPresent,
    });
  }
  if (present.has("how_to_refuse")) {
    tabs.push({
      slug: SECTION_SLUG.how_to_refuse,
      label: SECTION_LABELS.how_to_refuse,
      kind: "section", section: "how_to_refuse",
    });
  }
  return tabs;
}

// ---- citation string parsing (opaque; display only) ----

/** Split "Title/pinpoint — URL — local: <path>" into parts, tolerant of a missing
 * URL and of em-dashes inside the title (title parts are rejoined). */
function parseCitation(raw) {
  const title = [];
  let url = null;
  for (const part of String(raw).split(" — ")) {
    const t = part.trim();
    if (/^https?:\/\//i.test(t)) url = t;
    else if (/^local:/i.test(t)) { /* archive path — intentionally dropped */ }
    else title.push(t);
  }
  return { title: title.join(" — "), url };
}

// ---- per-section rendering ----

/** Number the citation codes across the given texts continuously by first
 * appearance; a source cited in an earlier text keeps its ordinal later. */
function combinedOrdinals(texts) {
  const ordinalByCode = new Map();
  const orderedCodes = [];
  for (const text of texts) {
    HEX.lastIndex = 0;
    let m;
    while ((m = HEX.exec(text)) !== null) {
      if (!ordinalByCode.has(m[1])) {
        ordinalByCode.set(m[1], ordinalByCode.size + 1);
        orderedCodes.push(m[1]);
      }
    }
  }
  return { ordinalByCode, orderedCodes };
}

/** One footnote marker. `target` is the id of the matching citation entry
 * in the same tab. */
function marker(target, ord) {
  return `<sup class="cite-ref"><button type="button" class="cite-marker" ` +
    `data-target="${escapeHtml(target)}" aria-label="Footnote ${ord}">${ord}</button></sup>`;
}

/** Render one paragraph, escaping text and turning each [hex] run into marker(s).
 * End-of-sentence runs render the terminator first, then the marker(s). */
function renderParaWithCites(para, ordinalByCode, idPrefix) {
  let html = "", last = 0, m;
  CITE_RUN.lastIndex = 0;
  while ((m = CITE_RUN.exec(para)) !== null) {
    const [full, , run, trailWs, term] = m;
    html += escapeHtml(para.slice(last, m.index));   // text before the (dropped) leading ws
    const codes = run.match(/[0-9a-f]{5}/g) || [];
    const markers = codes.map(c => {
      const ord = ordinalByCode.get(c);
      return ord ? marker(`${idPrefix}-${ord}`, ord) : "";
    }).join("");
    if (term) html += escapeHtml(term) + markers;      // footnote AFTER the period
    else html += markers + escapeHtml(trailWs);         // mid-sentence: attach to word
    last = m.index + full.length;
  }
  html += escapeHtml(para.slice(last));
  return html;
}

/** One text split into `{html}` paragraphs, with [hex] runs turned into
 * footnote markers against the given (already-numbered) ordinal map. Shared
 * by sectionData (one text) and combinedSectionData (one call per part). */
function paragraphsFor(text, ordinalByCode, idPrefix) {
  return String(text).split(/\n\n+/).map(p => ({
    html: renderParaWithCites(p, ordinalByCode, idPrefix),
  }));
}

/** The citations list for a numbered set of codes, resolved against the
 * country's (per-country, not per-section) citations map. Shared by
 * sectionData and combinedSectionData. */
function citationsFor(orderedCodes, ordinalByCode, citationsMap, idPrefix) {
  return orderedCodes.map(code => {
    const ord = ordinalByCode.get(code);
    const { title, url } = parseCitation((citationsMap || {})[code] || "");
    return { ord, code, title, url, id: `${idPrefix}-${ord}` };
  });
}

/** Structured data for one analysis section (for Svelte rendering). */
export function sectionData(cc, sectionKey) {
  const ref = countryEntry(cc)?.reference;
  const txt = ref?.refusal_analysis?.[sectionKey]?.text;
  if (!txt) return null;
  const { ordinalByCode, orderedCodes } = combinedOrdinals([txt]);
  const idPrefix = `cite-${cc}-${sectionKey}`;
  const paragraphs = paragraphsFor(txt, ordinalByCode, idPrefix);
  const citations = citationsFor(orderedCodes, ordinalByCode, ref.citations, idPrefix);
  return { paragraphs, citations, idPrefix };
}

/** Structured data for the merged Refusal-vs-Cooperation entry: `parts`
 * (heading + paragraphs each) and one citations list numbered
 * continuously across them. */
export function combinedSectionData(cc, sectionKeys) {
  const ref = countryEntry(cc)?.reference;
  const ra = ref?.refusal_analysis;
  if (!ra) return null;
  const present = sectionKeys.filter(k => ra[k]?.text);
  if (!present.length) return null;

  const idPrefix = `cite-${cc}-${COMBINED_SLUG}`;
  const { ordinalByCode, orderedCodes } = combinedOrdinals(present.map(k => ra[k].text));
  const parts = present.map(k => ({
    heading: SECTION_LABELS[k],
    paragraphs: paragraphsFor(ra[k].text, ordinalByCode, idPrefix),
  }));
  const citations = citationsFor(orderedCodes, ordinalByCode, ref.citations, idPrefix);
  return { parts, citations, idPrefix };
}

// Sources are plain URL strings or { url, title } objects; normalise to the
// latter.
function normalizeSources(sources) {
  return (sources || []).map(src =>
    typeof src === "string"
      ? { url: src, title: domainFromUrl(src) }
      : { url: src.url, title: src.title || domainFromUrl(src.url) }
  );
}

/** Structured data for the Summary tab: the editorial headline_note as an
 * unheaded lead, then the counterfactual and background prose. */
export function backgroundData(cc) {
  const info = countryEntry(cc);
  if (!info) return null;
  const sections = [];
  if (info.headline_note) {
    sections.push({ heading: null, text: info.headline_note, sources: [] });
  }
  if (info.counterfactual?.text) {
    sections.push({
      heading: "If it denied overflight, then:",
      text: info.counterfactual.text,
      sources: normalizeSources(info.counterfactual.sources),
    });
  }
  if (info.general?.text) {
    sections.push({
      heading: "Background",
      text: info.general.text,
      sources: normalizeSources(info.general.sources),
    });
  }
  return sections.length ? sections : null;
}
