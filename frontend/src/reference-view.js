import { get } from "svelte/store";
import { computeTypeCounts } from "./stats.js";
import { sectionData, backgroundData, combinedSectionData } from "./reference.js";
import { getGlobalContent, ensureGlobalContent, getRolesData, ensureRolesData } from "./global-content.js";
import { buildStatsBreakdown } from "./stats-breakdown.js";
import { addDataRefreshListener, buildFullGlobalTableHTML } from "./panel-data.js";
import { updateProps } from "./lib/mount.js";
import { getFocusConfig } from "./data.js";
import {
  readerState, getReaderState, openReader, closeReader,
  setCiteState, touchReader,
} from "./lib/stores/reader.js";

// Fallback if the config's footer.contact_url is unavailable — same contact
// form the panel footer uses.
const FALLBACK_CONTACT_URL = "mailto:flyover@tuta.io";

function contactUrl() {
  return getFocusConfig()?.footer?.contact_url || FALLBACK_CONTACT_URL;
}

// Started at module load so the methodology/glossary content is normally
// there before anyone opens those entries.
ensureGlobalContent();

// The stats-breakdown tab depends on a per-country sighting index that may
// still be loading when it is first opened; re-render when data lands.
addDataRefreshListener(() => {
  if (get(readerState).open) renderFromStore(get(readerState));
});

let mountTarget = null;
let onChange = null;

// The last active tab, so closing can return focus to its ToC entry.
let lastOpenSlug = null;

/** The one subscriber of readerState: renders the reader (updateProps into
 * ReferenceViewModal.svelte), syncs the URL (onChange) and returns focus on
 * close, whoever changed the store. */
function renderFromStore(s) {
  if (!s.open) {
    updateProps(mountTarget, { open: false });
    if (lastOpenSlug != null) {
      document.querySelector(`.toc-item[data-slug="${CSS.escape(lastOpenSlug)}"]`)?.focus();
      lastOpenSlug = null;
    }
    onChange?.();
    return;
  }

  lastOpenSlug = s.slug;
  const tab = s.tabs.find(t => t.slug === s.slug);
  const props = {
    open: true,
    cc: s.cc,
    activeLabel: tab?.label || "",
    backgroundSections: null,
    sectionProps: null,
    globalHtml: null,
    glossaryTerms: null,
    globalLoaded: false,
    breakdownProps: null,
    tabKind: tab?.kind || null,
  };

  if (tab?.kind === "background") {
    props.backgroundSections = backgroundData(s.cc) || [];
  } else if (tab?.kind === "stats-breakdown") {
    // Recomputed on every readerState change while this tab is
    // active, same as every other tab kind here.
    const data = buildStatsBreakdown(s.cc);
    props.breakdownProps = { title: tab.label, ...data };
  } else if (tab?.kind === "section") {
    const data = sectionData(s.cc, tab.section);
    if (data) {
      props.sectionProps = {
        title: tab.label,
        paragraphs: data.paragraphs,
        citations: data.citations,
        idPrefix: data.idPrefix,
        activeCite: s.cite,
        activeMarkerOrd: s.markerOrd,
      };
    }
  } else if (tab?.kind === "combined-section") {
    // The merged "What happened: Refusal vs Cooperation" entry —
    // two sourced sections (tab.sections) rendered as one navigable ToC
    // item, with one continuously-numbered bibliography. See
    // reference.js's combinedSectionData for the numbering/dedup mechanics.
    const data = combinedSectionData(s.cc, tab.sections);
    if (data) {
      props.sectionProps = {
        title: tab.label,
        parts: data.parts,
        citations: data.citations,
        idPrefix: data.idPrefix,
        activeCite: s.cite,
        activeMarkerOrd: s.markerOrd,
      };
    }
  } else if (tab?.kind === "global-stats") {
    props.globalLoaded = true;
    props.globalHtml = buildFullGlobalTableHTML();
  } else if (tab?.kind === "roles") {
    // Taxonomy from the generated artifact, counts computed
    // live from the flights table on every render so the filtered column
    // tracks the user's current selection rather than a stale snapshot.
    const rolesData = getRolesData();
    if (!rolesData) {
      ensureRolesData().then(() => {
        const cur = get(readerState);
        if (cur.open && cur.slug === s.slug) renderFromStore(cur);
      });
    } else {
      props.rolesProps = {
        data: rolesData,
        counts: computeTypeCounts(),
        contactUrl: contactUrl(),
      };
    }
  } else if (tab?.kind === "methodology" || tab?.kind === "glossary") {
    // The reader's global (no-country) entries. Content is fetched
    // once (ensureGlobalContent, module load above) rather than bundled;
    // getGlobalContent() is null until that resolves, which — same-origin,
    // typically already cached — is normally well before anyone reaches
    // this branch at all. On the rare cold-cache race, re-render once it
    // lands, but only if this same tab is still the active one (a user who
    // has since closed the reader or switched tabs shouldn't get a stale
    // re-render).
    const content = getGlobalContent();
    if (!content) {
      ensureGlobalContent().then(() => {
        const cur = get(readerState);
        if (cur.open && cur.slug === s.slug) renderFromStore(cur);
      });
    } else {
      props.globalLoaded = true;
      if (tab.kind === "methodology") props.globalHtml = content.methodologyHtml;
      else props.glossaryTerms = content.glossaryTerms;
    }
  }

  updateProps(mountTarget, props);
  onChange?.();
}

readerState.subscribe(renderFromStore);

export function getReferenceViewState() {
  return getReaderState();
}

export function openReferenceView(cc, opts = {}) {
  const { opened, switched } = openReader(cc, opts);
  if (!opened) return;
  if (!switched && !opts.skipFocus) {
    requestAnimationFrame(() => document.getElementById("refview-close")?.focus());
  }
}

export function refreshReferenceView() {
  touchReader();
}

export function closeReferenceView() {
  closeReader();
}

export function initReferenceView(onStateChange, target) {
  onChange = onStateChange;
  mountTarget = target;

  updateProps(mountTarget, {
    onClose: closeReferenceView,
    onCiteMarkerClick: (m) => {
      const s = get(readerState);
      const tab = s.tabs.find(t => t.slug === s.slug);
      if (tab?.kind !== "section" && tab?.kind !== "combined-section") return;
      const targetId = m.dataset.target;
      const ord = targetId ? Number(targetId.split("-").pop()) : null;
      setCiteState(ord, ord);
    },
    onCiteEntryClick: () => {
      setCiteState(null, null);
    },
    onBackToText: () => {
      setCiteState(null, null);
    },
  });
}
