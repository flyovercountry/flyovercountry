// Which reader section is open. Shared by the reader overlay
// (reference-view.js renders content from it), the in-panel table of
// contents (InfoSection.svelte) and the mobile sheet wiring
// (ContentPanel.svelte), which must not import reference-view.js's graph.
import { writable, get } from "svelte/store";
import { tabsFor } from "../../reference.js";
import { globalTabsFor, generalTabsFor } from "../../global-content.js";

/** The reader's tab list for a country (its own entries, then the general
 * methodology/glossary ones) or the global list when no country is selected.
 * openReader() resolves a `view` slug against this same list, so the ToC
 * and the reader cannot disagree. */
export function readerTabsFor(cc) {
  return cc ? [...tabsFor(cc), ...generalTabsFor()] : globalTabsFor();
}

export const readerState = writable({
  open: false, cc: null, tabs: [], slug: null, cite: null, markerOrd: null,
});

/** `{open:false}` or `{open:true, cc, view, cite}`, the shape app.js's
 * syncUrl()/refreshPanel() consume. */
export function getReaderState() {
  const s = get(readerState);
  return s.open ? { open: true, cc: s.cc, view: s.slug, cite: s.cite } : { open: false };
}

// `?cite=N` arrives as a string; SectionTab.svelte compares it to
// `citations[].ord` with `===`.
function normalizeCite(cite) {
  return cite == null ? null : Number(cite);
}

/** Open on `cc`, or switch section if already open on the same country
 * (reference-view.js uses the `switched` flag to avoid stealing focus).
 * `cc` null means the global entries; that is an open state, distinct from
 * `{open: false}`. Returns {opened, switched}. */
export function openReader(cc, opts = {}) {
  const tabs = readerTabsFor(cc);
  if (!tabs.length) return { opened: false, switched: false };
  const s = get(readerState);
  if (s.open && s.cc === cc) {
    return { opened: selectSection(opts.view ?? s.slug, { cite: normalizeCite(opts.cite) }), switched: true };
  }
  const wanted = tabs.find(t => t.slug === opts.view);
  const slug = wanted ? wanted.slug : tabs[0].slug;
  readerState.set({ open: true, cc: cc || null, tabs, slug, cite: normalizeCite(opts.cite), markerOrd: null });
  return { opened: true, switched: false };
}

/** Switch the active section of an already-open reader. No-op (returns
 * false) if the reader isn't open or `slug` isn't one of its tabs. */
function selectSection(slug, { cite = null } = {}) {
  let ok = false;
  readerState.update(s => {
    if (!s.open) return s;
    const tab = s.tabs.find(t => t.slug === slug) || s.tabs[0];
    if (!tab) return s;
    ok = true;
    return { ...s, slug: tab.slug, cite, markerOrd: null };
  });
  return ok;
}

/** Highlight (or clear) a citation within the active section, without
 * switching sections. */
export function setCiteState(cite, markerOrd) {
  readerState.update(s => (s.open ? { ...s, cite, markerOrd } : s));
}

/** Re-render the active tab (e.g. after a filter change): a new object
 * reference is enough, renderFromStore recomputes content from app state.
 * No-op while the reader is closed. */
export function touchReader() {
  readerState.update(s => (s.open ? { ...s } : s));
}

/** Close the reader. Safe to call unconditionally (no-ops if already
 * closed). Returns whether it was actually open (so callers can skip
 * follow-up work like URL sync when nothing changed). */
export function closeReader() {
  let wasOpen = false;
  readerState.update(s => {
    wasOpen = s.open;
    return { open: false, cc: null, tabs: [], slug: null, cite: null, markerOrd: null };
  });
  return wasOpen;
}
