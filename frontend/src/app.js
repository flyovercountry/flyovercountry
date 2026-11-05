/** Main entry point — wire up map, data, controls, and filters. */
// Stylesheets as graph modules (see index.html): Vite extracts them into
// the page's single CSS asset; tailwind.css first so style.css's unlayered
// rules come after the utility layers.
import "./tailwind.css";
import "./style.css";
import { loadManifest, loadRoutesData, loadHexRoles, loadFlightsTable, getFlightsTable, getFlightMasks, rolesToMask, getFocusConfig, getManifest, dayToDateStr, ccContains } from "./data.js";
import { flightToRow, rowsToCsv } from "./csv.js";
import { initMap, getMap, applyFilter, refilterAll, loadCountryBoundaries, highlightCountry, highlightRoutes, showLandings, addRoutes, loadLandings, initTraceInteraction, setTraceInteraction, setCountryClickEnabled } from "./map.js";
import { buildFilter, getCountry, setCountry, stageCountry, getShowFlights, setShowFlights, getShowLandings, setShowLandings, getDayRange, getRoles, getFilter } from "./filters.js";
import { applyStaged, resetStaged, resetAll, registerAutoApply, cancelAutoApply, nonDefaultCategories } from "./lib/stores/filters.js";
import { init as initControls, setDateRange, setRolesByRoleIndex, selectCountryInGrid, setFilterUI, getSidebarFilterPanelProps } from "./controls.js";
import { panelData, openFilterView, toggleFilterView } from "./lib/stores/panel.js";
import { collectDefaultRoles, roleIndicesToGroupNotation, groupNotationToRoleIndices, setDataRefreshCallback } from "./panel-data.js";
import { computeStats, computeGlobalStats } from "./stats.js";
import { readParams, writeParams, checkAndRepair, repairedQueryString, hasParams, setRestoringFromPopstate } from "./url.js";
import { mountContentPanel, mountToolbar, mountMobileToolbar, mountReferenceView, mountInstagramModal, mountEmbedPopup, mountOnMapLegend, mountWhatIfPulldown, updateProps } from "./lib/mount.js";
import { SOCIAL_BUTTONS } from "./social-icons.js";
import { initRectExport, triggerRectExport } from "./rect-export.js";
import { initWhatIf, updateWhatIf, isWhatIfActive, getWhatIfDate, setWhatIfFromUrl, setWhatIfSyncUrl, setWhatIfRefreshPanel, setWhatIfMobileSnap, forceExitWhatIf, whatIfExpand, whatIfCollapse, whatIfSetDate } from "./whatif.js";
import { shareContext, updateShareTargets, siteTitle } from "./share-links.js";
import { initReferenceView, openReferenceView, closeReferenceView, refreshReferenceView, getReferenceViewState } from "./reference-view.js";
import { hasStats } from "./reference.js";
import { initBreadcrumbs } from "./breadcrumb.js";
import { GLOBAL_TABS } from "./global-content.js";
import { showTourOrWelcome } from "./tour.js";
import { isDesktopLayout, DESKTOP_BREAKPOINT } from "./lib/layout.js";
import { ALT_COLORS } from "./lib/alt-colors.js";
import { colorForLevel } from "./lib/permissiveness-colors.js";
import { escapeHtml } from "./text.js";
import { _ } from "svelte-i18n";
import { get } from "svelte/store";

let initialized = false;
let landingsLayerLoaded = false;
let exploreMode = false;
let contentPanel = null;
let mobileToolbarMount = null;
let toolbarMount = null;
let showToggleMount = null;
let embedPopupMount = null;
let instagramModalMount = null;
let embedOpen = false;
let pendingReferenceView = null;   // {view, cite} to open once the panel first renders
let shareHandlers = {};
let legendMount = null;
// Device-capability signal (touch + mobile UA), not a layout decision: it
// picks the lighter routes file and tunes map touch interaction. Layout,
// toolbar and tour steps are decided by isDesktopLayout() (lib/layout.js),
// otherwise a wide touch tablet would get the mobile toolbar and the
// desktop sidebar at once.
const isTouchMobileDevice = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent)
  && (window.innerWidth < DESKTOP_BREAKPOINT || "ontouchstart" in window);

function mobileUiActive() {
  return !isDesktopLayout();
}

// Shared by the toolbar's Explore toggle and map.js's onFlightOpen callback
// (deselect Explore once a flight link actually opens) so both paths flip
// the same set of dependents in lockstep instead of drifting out of sync.
function setExploreMode(active) {
  exploreMode = active;
  if (toolbarMount) updateProps(toolbarMount, { exploreActive: exploreMode });
  document.getElementById("sidebar").classList.toggle("explore-mode", exploreMode);
  // On desktop the Show toggle lives inside the toolbar's first group, so
  // the toolbar mount carries its disabled state; showToggleMount is only
  // non-null on layouts where the standalone island still mounts.
  if (showToggleMount) updateProps(showToggleMount, { disabled: exploreMode });
  if (toolbarMount) updateProps(toolbarMount, { showDisabled: exploreMode });
  if (mobileToolbarMount) updateProps(mobileToolbarMount, { showDisabled: exploreMode });
  setTraceInteraction(exploreMode);
  setCountryClickEnabled(!exploreMode);
  if (exploreMode) showExploreHint(); else hideExploreHint();
}

// The Explore mode hint, created lazily on <body>. One element shows the
// instructions and, briefly after a miss-click, the "nothing there" note.
const EXPLORE_HINT_TEXT = "Hover a route to isolate it; click for flight details. Esc to exit.";
const EXPLORE_MISS_TEXT = "No flight under the cursor.";
let exploreMissTimer = null;

function getExploreHint() {
  let hint = document.getElementById("explore-hint");
  if (!hint) {
    hint = document.createElement("div");
    hint.id = "explore-hint";
    hint.className = "hidden";
    hint.setAttribute("role", "status");
    hint.setAttribute("aria-live", "polite");
    document.body.appendChild(hint);
  }
  return hint;
}

function showExploreHint() {
  clearTimeout(exploreMissTimer);
  const hint = getExploreHint();
  hint.textContent = EXPLORE_HINT_TEXT;
  hint.classList.remove("hidden", "explore-hint-flash");
}

function hideExploreHint() {
  clearTimeout(exploreMissTimer);
  const hint = document.getElementById("explore-hint");
  if (hint) hint.classList.add("hidden");
}

/** Transient "nothing under the cursor" note, reusing #explore-hint rather
 * than a second UI surface —
 * swaps the text + a flash tint in for a beat, then reverts to the
 * ordinary instructions text. No-ops if Explore was turned off in the
 * interim (e.g. Escape raced a stray click event). */
function flashExploreMissHint() {
  if (!exploreMode) return;
  const hint = getExploreHint();
  clearTimeout(exploreMissTimer);
  hint.textContent = EXPLORE_MISS_TEXT;
  hint.classList.remove("hidden");
  hint.classList.add("explore-hint-flash");
  exploreMissTimer = setTimeout(() => {
    hint.textContent = EXPLORE_HINT_TEXT;
    hint.classList.remove("explore-hint-flash");
  }, 1500);
}

async function handleShowToggle(key, value) {
  if (key === "flights") setShowFlights(value);
  else if (key === "landings") setShowLandings(value);
  const flights = getShowFlights();
  const landings = getShowLandings();
  if (showToggleMount) updateProps(showToggleMount, { showFlights: flights, showLandings: landings });
  if (toolbarMount) updateProps(toolbarMount, { showFlights: flights, showLandings: landings });
  if (mobileToolbarMount) updateProps(mobileToolbarMount, { showFlights: flights, showLandings: landings });
  syncUrl();
  showUpdating();
  await yieldToBrowser();
  await applyShowState();
  if (toolbarMount) updateProps(toolbarMount, { showExplore: flights });
  setTraceInteraction(exploreMode && flights);
  await new Promise(resolve => getMap().once("idle", resolve));
  hideUpdating();
}

let manifest = null;

// Filters commit themselves on a debounce, so two runs can overlap: a 3 s
// timer can fire while a previous recompute is still waiting on the map's
// idle event. `applyToken` makes the newest run the only one that owns the
// outcome; a superseded run bails at its next await boundary instead of
// hiding the overlay out from under the run still working.
let applyToken = 0;

async function applyFilters() {
  const token = ++applyToken;
  showUpdating();
  await yieldToBrowser();
  if (token !== applyToken) return;
  applyStaged();
  // A direct apply (map tap, URL restore) commits the staged edit that
  // also armed the auto-apply timer; without this it fires once more
  // and re-runs the whole recompute for nothing.
  cancelAutoApply();

  const cc = getCountry();
  highlightCountry(cc);
  highlightRoutes(cc);
  applyFilter(buildFilter());
  await applyShowState();
  if (token !== applyToken) return;

  refreshPanel();
  syncUrl();

  await new Promise(resolve => getMap().once("idle", resolve));
  if (token !== applyToken) return;
  hideUpdating();
}

function revertFilters() {
  // FilterActions.svelte's Revert button already calls resetStaged()
  // before invoking this callback; defensive for any other onRevert path.
  resetStaged();
}

async function resetFilters() {
  resetAll();
  await applyFilters();
}

function mountPanel() {
  contentPanel = mountContentPanel(document.getElementById("sidebar-mount"), {
    onSectionSelect: handleTocSelect,
    onLayoutChange: (mode, expanded, padding) => {
      const map = getMap();
      if (!map) return;
      if (expanded) return;
      if (padding) map.easeTo({ padding, duration: 300 });
    },
    // The map container's `left` comes from --sidebar-width, so folding the
    // panel changes its box. MapLibre only learns that from an explicit
    // resize(); called after the 250 ms CSS transition so the size it
    // measures is the settled one, plus once on transitionend in case the
    // transition was skipped (prefers-reduced-motion) or ran long.
    onSidebarFoldChange: () => {
      const map = getMap();
      if (!map) return;
      const el = document.getElementById("sidebar");
      const done = () => map.resize();
      el?.addEventListener("transitionend", done, { once: true });
      setTimeout(done, 280);
    },
    onSelectCountry: (cc) => {
      stageCountry(cc);
      selectCountryInGrid(cc);
      applyFilters();
    },
    onDeselectCountry: () => {
      stageCountry(null);
      selectCountryInGrid(null);
      applyFilters();
    },
    onEnterRefusal: () => whatIfExpand(),
    onExitRefusal: () => {
      whatIfCollapse();
      contentPanel?.expand();
    },
    getFilterPanelProps: () => getSidebarFilterPanelProps(),
  });
}

function applyBranding(fc) {
  if (fc?.footer) {
    updateProps(document.getElementById("sidebar-mount"), {
      footerText: fc.footer.text,
      footerContactLabel: fc.footer.contact_label,
      footerTourLabel: fc.footer.tour_label,
    });
  }
  if (!fc?.title) return;
  document.title = fc.title;
  const h1 = document.querySelector("#sidebar-header h1");
  if (h1) {
    const logo = h1.querySelector(".site-logo");
    h1.textContent = "";
    if (logo) h1.prepend(logo);
    h1.append(fc.title);
  }
  const h2 = document.querySelector("#welcome-box h2");
  if (h2) h2.textContent = fc.title;
}

async function main() {
  mountPanel();
  manifest = await loadManifest();
  applyBranding(getFocusConfig());

  if (hasParams()) {
    const { issues } = checkAndRepair(manifest, getFocusConfig());
    if (issues.length > 0) {
      showParamErrors(issues, manifest, getFocusConfig());
      return;
    }
  }
  const urlParams = readParams();

  const map = initMap({
    mobile: isTouchMobileDevice,
    onCountryClick: (cc) => {
      const current = getCountry();
      // Tapping the selected country again, or anywhere with no selectable
      // country (cc === null), deselects. A null tap with nothing selected
      // is a no-op.
      if (cc === null && !current) return;
      const next = (cc === null || cc === current) ? null : cc;
      stageCountry(next);
      selectCountryInGrid(next);
      applyFilters();
      if (contentPanel && contentPanel.getMode() !== 'sidebar') {
        contentPanel.collapse();
        dismissTapHint();
      }
    },
    onFlightOpen: () => { setExploreMode(false); syncUrl(); },
    onExploreMiss: () => flashExploreMissHint(),
  });

  initControls({
    onApply: applyFilters,
    onRevert: revertFilters,
    onReset: resetFilters,
  });
  // What an auto-commit does when the debounce expires. Bare
  // applyFilters, deliberately without the closeFilterView() that the
  // explicit Apply-now path carries — an auto-apply must not yank the panel
  // away from a user who is still adjusting filters.
  registerAutoApply(applyFilters);
  // Keep the Filters trigger's C/D/R badge in step with the APPLIED state
  // on every commit, whatever caused it (auto, Apply now, Reset, a URL
  // restore, a back/forward step).
  nonDefaultCategories.subscribe(cats => {
    const badge = cats.join('');
    if (toolbarMount) updateProps(toolbarMount, { filterBadge: badge });
    if (mobileToolbarMount) updateProps(mobileToolbarMount, { filterBadge: badge });
  });
  initBreadcrumbs({
    infoLabel: GLOBAL_TABS.find((t) => t.slug === "methodology")?.label,
    onInfo: () => openReferenceView(getCountry(), { view: "methodology" }),
  });

  const urlShowOverride = applyInitialUrlState(urlParams);
  mountOverlays(urlParams);
  shareHandlers = buildShareHandlers();
  if (!mobileUiActive()) mountDesktopToolbar();
  updateShareTargets(syncUrl);
  if (window.Sharer) window.Sharer.init();

  applyStaged();
  applyFilter(buildFilter());
  initialized = true;
  // Register the panel re-render callbacks BEFORE the first refreshPanel:
  // its headline interpolation (panel-data.js interpolateHeadline) starts
  // the sighting-index fetch whose resolution re-renders through these.
  // The flights table the numbers need starts now too, in parallel with
  // the much larger routes download.
  setWhatIfSyncUrl(syncUrl);
  setWhatIfRefreshPanel(refreshPanel);
  setDataRefreshCallback(() => {
    panelData.update(d => ({ ...d, refreshTick: (d.refreshTick || 0) + 1 }));
  });
  const flightsTableReady = startFlightsTableLoad();
  refreshPanel();

  const progress = createLoadingProgress();
  const prefetch = startPrefetch(progress);
  installEscapeHandler();
  installPopstateHandler();

  await new Promise((resolve, reject) => {
    const run = () => onMapReady(map, { progress, prefetch, urlShowOverride, flightsTableReady }).then(resolve, reject);
    if (map.loaded()) run();
    else map.on("load", run);
  });
}

/** Seed filter, What-If, Explore and layer-toggle state from a validated
 * URL on first load. Returns whether a non-default flights/landings toggle
 * arrived, so onMapReady applies it to the layers once they exist. */
function applyInitialUrlState(p) {
  if (p.start || p.end) setDateRange(p.start, p.end);
  if (p.roles) setRolesByRoleIndex([...groupNotationToRoleIndices(p.roles)]);
  if (p.cc) {
    stageCountry(p.cc);
    setCountry(p.cc);
    selectCountryInGrid(p.cc);
  }
  applyTheaterParam(p.fm);
  if (p.wi) setWhatIfFromUrl(p.wi);
  // Explore has no mobile UI (no toggle, no touch way out), so ?ex=1 on a
  // phone is ignored. toolbarMount does not exist yet; mountDesktopToolbar
  // reads exploreMode into its initial render.
  if (p.ex === "1" && !mobileUiActive()) setExploreMode(true);
  let showOverride = false;
  if (p.flights !== null) {
    setShowFlights(p.flights === "1");
    showOverride = true;
  }
  if (p.landings !== null) {
    setShowLandings(p.landings === "1");
    showOverride = true;
  }
  return showOverride;
}

function applyTheaterParam(fm) {
  if (!fm) {
    setFilterUI("tasking", true);
    return;
  }
  const exclude = fm.startsWith("-");
  setFilterUI(exclude ? fm.slice(1) : fm, !exclude);
}

function mountOverlays(urlParams) {
  const refviewMount = document.getElementById("refview-mount");
  mountReferenceView(refviewMount);
  instagramModalMount = document.getElementById("instagram-modal-mount");
  mountInstagramModal(instagramModalMount, {
    onClose: () => updateProps(instagramModalMount, { open: false }),
  });
  embedPopupMount = document.getElementById("embed-popup-mount");
  mountEmbedPopup(embedPopupMount, {
    open: false,
    snippet: "",
    onClose: () => { embedOpen = false; updateProps(embedPopupMount, { open: false }); },
  });
  // The reader syncs the URL on every open/switch/close; a country change
  // while it is open closes it (refreshPanel), so nothing reopens here.
  initReferenceView(syncUrl, refviewMount);
  // checkAndRepair already guaranteed a surviving view is consistent with
  // cc (a country view with cc, or a global entry without), so no cc check.
  if (urlParams.view) {
    pendingReferenceView = { view: urlParams.view, cite: urlParams.cite };
  }
}

function buildShareHandlers() {
  return {
    onCopyLink: () => {
      syncUrl();
      const btn = document.getElementById("share-btn");
      const orig = btn.textContent;
      navigator.clipboard.writeText(window.location.href).then(() => {
        btn.textContent = "Link copied!";
        btn.classList.add("copied");
        setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 2000);
      }).catch(() => {
        // Clipboard permission denied or unavailable: show the URL in the
        // button itself, with the same .copied highlight, so the user can
        // select and copy it by hand. Longer timeout than the success case
        // because reading a URL takes longer than glancing at a confirmation.
        btn.textContent = window.location.href;
        btn.classList.add("copied");
        setTimeout(() => { btn.textContent = orig; btn.classList.remove("copied"); }, 4000);
      });
    },
    onEmbed: () => {
      if (embedOpen) {
        embedOpen = false;
        updateProps(embedPopupMount, { open: false });
        return;
      }
      syncUrl();
      const safeUrl = escapeHtml(window.location.href);
      const snippet = `<iframe src="${safeUrl}" width="100%" height="600" style="border:none" title="${escapeHtml(siteTitle())}: US military flights over Europe"></iframe>`;
      embedOpen = true;
      updateProps(embedPopupMount, { open: true, snippet });
    },
    onNativeShare: () => {
      const ctx = shareContext(syncUrl);
      let text = ctx.text + ctx.hashtags.map(h => " #" + h).join("");
      if (ctx.urlNote) text += "\n" + ctx.urlNote;
      navigator.share({ title: siteTitle(), text, url: ctx.url }).catch(() => {});
    },
    onMastodon: (host) => {
      const ctx = shareContext(syncUrl);
      const text = ctx.text + ctx.hashtags.map(h => " #" + h).join("");
      const urlPart = ctx.urlNote ? "\n" + ctx.urlNote + " " + ctx.url : " " + ctx.url;
      const shareUrl = "https://" + host + "/share?text=" + encodeURIComponent(text + urlPart);
      window.open(shareUrl, "_blank", "noopener");
    },
    onInstagram: () => {
      const caption = instagramCaption(syncUrl);
      updateProps(instagramModalMount, { open: true, caption });
    },
  };
}

function mountDesktopToolbar() {
  toolbarMount = document.getElementById("toolbar-mount");
  mountToolbar(toolbarMount, {
    socialButtons: SOCIAL_BUTTONS,
    showNative: typeof navigator.share === "function",
    showExplore: true,
    // ?ex=1 may have set exploreMode before this mount existed.
    exploreActive: exploreMode,
    onFilters: () => toggleFilterView(),
    showFlights: getShowFlights(),
    showLandings: getShowLandings(),
    showDisabled: exploreMode,
    onShowToggle: handleShowToggle,
    ...shareHandlers,
    onSaveImage: async () => {
      showUpdating();
      await yieldToBrowser();
      await saveMapImage();
      await hideAfterDownload();
    },
    onExportCsv: async () => {
      showUpdating();
      await yieldToBrowser();
      downloadCsv();
      await hideAfterDownload();
    },
    onRectExport: () => triggerRectExport(),
    onExploreToggle: () => { setExploreMode(!exploreMode); syncUrl(); },
  });
}

function startFlightsTableLoad() {
  const hasRefView = !!pendingReferenceView;
  return loadHexRoles().then(() => loadFlightsTable()).then(() => {
    refreshPanel();
    updateShareTargets(syncUrl);
    if (hasRefView) document.getElementById("loading-overlay")?.remove();
  });
}

/** The loading overlay's message and weighted progress bar. */
function createLoadingProgress() {
  const msg = document.getElementById("loading-message");
  msg.textContent = "Loading…";
  const bar = document.getElementById("loading-bar-fill");
  const pctEl = document.getElementById("loading-pct");
  const parts = { routes: 0, boundaries: 0, render: 0 };
  const weights = { routes: 0.70, boundaries: 0.10, render: 0.20 };
  return {
    message(text) { msg.textContent = text; },
    set(key, v) {
      parts[key] = v;
      const sum = parts.routes * weights.routes + parts.boundaries * weights.boundaries + parts.render * weights.render;
      const p = Math.min(100, Math.round(sum * 100));
      bar.style.width = p + "%";
      pctEl.textContent = p + "%";
    },
  };
}

// Start the routes/boundaries downloads now, concurrent with the external
// basemap tile wait: only adding them to the map (onMapReady) needs the map.
function startPrefetch(progress) {
  const routes = loadRoutesData((v) => progress.set("routes", v), isTouchMobileDevice);
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), 15000);
  const boundaries = fetch("/data/boundaries.geojson", { signal: abort.signal })
    .then(r => { clearTimeout(timer); return r.ok ? r.json() : null; })
    .catch(() => { clearTimeout(timer); return null; });
  return { routes, boundaries };
}

const nextTick = () => new Promise(r => setTimeout(r, 0));

async function addPrefetchedLayers(progress, prefetch) {
  if (isTouchMobileDevice) {
    const routesData = await prefetch.routes;
    if (routesData) {
      progress.message("Rendering…");
      progress.set("render", 0.3);
      await nextTick();
      addRoutes(routesData);
      progress.set("render", 0.8);
    }
    await nextTick();
    await loadCountryBoundaries(await prefetch.boundaries);
    progress.set("boundaries", 1);
    progress.set("render", 1);
    return;
  }
  const [boundariesData, routesData] = await Promise.all([prefetch.boundaries, prefetch.routes]);
  await loadCountryBoundaries(boundariesData);
  progress.set("boundaries", 1);
  if (routesData) {
    progress.message("Rendering…");
    progress.set("render", 0.3);
    await nextTick();
    addRoutes(routesData);
  }
  progress.set("render", 1);
}

async function onMapReady(map, { progress, prefetch, urlShowOverride, flightsTableReady }) {
  await addPrefetchedLayers(progress, prefetch);

  const cc = getCountry();
  if (cc) {
    highlightCountry(cc);
    highlightRoutes(cc);
  }
  applyFilter(buildFilter());
  // A deep link with a non-default flights/landings toggle: the layers
  // exist now, so apply the URL-driven state to them.
  if (urlShowOverride) await applyShowState();

  // Reveal the map once our own data is rendered rather than on full map
  // "idle", which blocks on every external basemap tile; prefer a clean
  // idle but cap the wait, and let the tiles stream in behind the UI.
  await Promise.race([
    new Promise(resolve => map.once("idle", resolve)),
    new Promise(resolve => setTimeout(resolve, 2500)),
  ]);
  document.getElementById("loading-overlay")?.remove();
  document.documentElement.dataset.appReady = "1";

  initTraceInteraction();
  await flightsTableReady;
  finishStartup(map);
}

function finishStartup(map) {
  initRectExport();
  initWhatIf();
  // On desktop the pulldown lives inside Toolbar.svelte next to Explore;
  // mobile never mounts that toolbar and keeps the map-overlay mount.
  const whatifPulldownTarget = toolbarMount
    ? document.getElementById('toolbar-whatif-mount')
    : document.getElementById('whatif-pulldown-mount');
  mountWhatIfPulldown(whatifPulldownTarget, {
    onExpand: () => whatIfExpand(),
    onCollapse: () => whatIfCollapse(),
    onDateChange: (dateStr) => whatIfSetDate(dateStr),
  });
  initOnMapLegend(getShowFlights(), getShowLandings());

  // Build the mobile sheet structure before the first panel render, so a
  // What-If view entered from the URL uses the same layout as one entered
  // via the button.
  initMobileToolbar(map);
  // sharer.min.js binds clicks only at init(); the mobile ShareMenu's
  // [data-sharer] buttons did not exist at the first call. Re-running it is
  // safe: it registers one stable listener reference per element, which
  // addEventListener de-duplicates.
  if (window.Sharer) window.Sharer.init();
  refreshPanel();
  showTourOrWelcome(maybeShowTapHint);
}

// Escape closes exactly one layer per press, topmost first: an open
// Share/Export dropdown (which handles its own Escape while focused, so it
// is skipped here), then What-If (a page-wide takeover that sits above
// Explore), then Explore. rect-export.js's selection-mode Escape is its own
// listener, live only during a drag-select.
function installEscapeHandler() {
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (document.querySelector("#share-menu-list:not([hidden]), #export-menu-list:not([hidden])")) return;
    if (isWhatIfActive()) { whatIfCollapse(); return; }
    if (exploreMode) { setExploreMode(false); syncUrl(); }
  });
}

function installPopstateHandler() {
  window.addEventListener("popstate", async () => {
    if (!initialized) return;
    setRestoringFromPopstate(true);
    try {
      // Back/forward can land on a malformed or stale URL as easily as a
      // fresh navigation, so it goes through the same validate+repair pass.
      const { repaired } = checkAndRepair(manifest, getFocusConfig());
      applyPopstateUrlState(repaired);
      await applyFilters();
    } finally {
      setRestoringFromPopstate(false);
    }
  });
}

/** Land on whatever state the URL now says, defaults for absent params. */
function applyPopstateUrlState(p) {
  stageCountry(p.cc || null);
  selectCountryInGrid(p.cc || null);

  if (p.start || p.end) setDateRange(p.start, p.end);
  else setDateRange(getManifest().data_start, getManifest().data_end);

  if (p.roles) setRolesByRoleIndex([...groupNotationToRoleIndices(p.roles)]);
  else setRolesByRoleIndex([...collectDefaultRoles()]);

  applyTheaterParam(p.fm);

  if (p.wi) setWhatIfFromUrl(p.wi);
  else if (isWhatIfActive()) forceExitWhatIf();

  // applyFilters() does the map-layer work through applyShowState(); the
  // toggle UIs are updated here so they do not go stale.
  setShowFlights(p.flights !== null ? p.flights === "1" : true);
  setShowLandings(p.landings !== null ? p.landings === "1" : false);
  const show = { showFlights: getShowFlights(), showLandings: getShowLandings() };
  if (showToggleMount) updateProps(showToggleMount, show);
  if (toolbarMount) updateProps(toolbarMount, show);
  if (mobileToolbarMount) updateProps(mobileToolbarMount, show);

  // Mobile has no Explore UI, so ex=1 is ignored there as on first load.
  if (p.ex === "1" && !mobileUiActive()) {
    if (!exploreMode) setExploreMode(true);
  } else if (exploreMode) {
    setExploreMode(false);
  }

  if (p.view) pendingReferenceView = { view: p.view, cite: p.cite };
  else closeReferenceView();
}

/** Blocking overlay for invalid URL parameters, rendered inside the loading
 * box: each problem in plain language plus one link to a repaired URL. */
function showParamErrors(issues, manifest, focusConfig) {
  const box = document.getElementById("loading-box");
  const path = window.location.pathname;

  // Re-check the repaired query until it validates clean, dropping any
  // param that still fails (inter-param dependencies such as wi needing
  // cc). Each pass removes at least one param, so this terminates.
  const { repaired } = checkAndRepair(manifest, focusConfig);
  let qs = repairedQueryString(repaired);
  for (let pass = 0; pass < 8; pass++) {
    const recheck = checkAndRepair(manifest, focusConfig, qs ? `?${qs}` : "");
    if (recheck.issues.length === 0) break;
    for (const issue of recheck.issues) delete repaired[issue.param];
    qs = repairedQueryString(repaired);
  }

  const repairedHref = qs ? `${path}?${qs}` : path;

  box.innerHTML =
    `<div class="param-error">` +
    `<h2>This link has a problem</h2>` +
    `<p>Some parts of the link you followed aren't valid, so we fixed them:</p>` +
    `<ul>${issues.map(i =>
      `<li><strong>${escapeHtml(i.param)}</strong>: ${escapeHtml(i.problem)} &mdash; ${escapeHtml(i.fix)}.</li>`
    ).join("")}</ul>` +
    `<p><a href="${escapeHtml(repairedHref)}">Continue with the fixed link</a></p>` +
    `</div>`;
}

function applyFlightsVisibility(visible) {
  const m = getMap();
  if (m.getLayer("routes-line")) {
    m.setLayoutProperty("routes-line", "visibility", visible ? "visible" : "none");
  }
}

async function applyShowState() {
  const flights = getShowFlights();
  const landings = getShowLandings();
  let freshLoad = false;

  applyFlightsVisibility(flights);

  if (landings && !landingsLayerLoaded) {
    landingsLayerLoaded = true;
    freshLoad = true;
    await loadLandings();
    refilterAll();
  }
  showLandings(landings ? (getCountry() || true) : null);

  updateLegendVisibility();
  return freshLoad;
}

function syncUrl() {
  const m = getManifest();
  const [dayMin, dayMax] = getDayRange();
  const startDate = dayToDateStr(dayMin);
  const endDate = dayToDateStr(dayMax);
  const activeRoles = getRoles();
  const defaultRoles = collectDefaultRoles();
  const isDefault = activeRoles.size === defaultRoles.size &&
    [...defaultRoles].every(r => activeRoles.has(r));
  const f = getFilter();
  const rv = getReferenceViewState();
  writeParams({
    cc: getCountry(),
    start: startDate !== m.data_start ? startDate : null,
    end: endDate !== m.data_end ? endDate : null,
    roles: isDefault ? null : roleIndicesToGroupNotation(activeRoles),
    fm: f.mode === "tasking" && f.include ? null
      : f.mode !== "none" ? (f.include ? f.mode : `-${f.mode}`)
      : "none",
    wi: getWhatIfDate(),
    ex: exploreMode ? "1" : null,
    view: rv.open ? rv.view : null,
    cite: rv.open && rv.cite != null ? String(rv.cite) : null,
    // Default is (flights on, landings off) — see filters.js's emptyState/
    // initDefaults — so only the non-default direction of each toggle needs
    // a param, same "null when default" contract as every field above.
    flights: getShowFlights() ? null : "0",
    landings: getShowLandings() ? "1" : null,
  });
}

function frameMobileMap(map) {
  if (!contentPanel || contentPanel.getMode() === 'sidebar') return;
  const padding = contentPanel.getCollapsedPadding() ||
    { top: 12, bottom: 112, left: 12, right: 12 };
  const bounds = [[-11, 34], [30, 56]];
  map.setMinZoom(2.3);
  try {
    map.fitBounds(bounds, { padding, animate: false, maxZoom: 5 });
  } catch (e) {
    map.setCenter([13, 48]);
    map.setZoom(2.7);
  }
}

const updatingEl = document.getElementById("updating-overlay");

function showUpdating() {
  updatingEl.classList.remove("hidden");
}

function hideUpdating() {
  updatingEl.classList.add("hidden");
}

function hideAfterDownload() {
  return new Promise(r => setTimeout(() => { hideUpdating(); r(); }, 1500));
}

function yieldToBrowser() {
  return new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));
}

// A ToC click opens, switches or (on the active entry) closes the reader.
// openReferenceView handles the switch; cc may be null for a global entry.
function handleTocSelect(slug) {
  const cc = getCountry();
  const rv = getReferenceViewState();
  if (rv.open && rv.cc === cc && rv.view === slug) {
    closeReferenceView();
  } else {
    openReferenceView(cc, { view: slug });
  }
}

function refreshPanel() {
  if (!initialized) return;
  const cc = getCountry();
  updateWhatIf(cc);

  const active = isWhatIfActive();
  const globalStats = active ? null : computeGlobalStats();
  // Selectable does not imply stats-eligible (CH/AT/SA/KW have a panel but
  // no stats); computeStats itself does not check the flag.
  const stats = (!active && cc && hasStats(cc)) ? computeStats(cc) : null;

  panelData.set({
    cc,
    globalStats,
    countryStats: stats,
    whatIfActive: active,
    refreshTick: 0,
  });

  if (active) return;

  // Close the reader only when its country no longer matches the selection;
  // a global entry (rv.cc null) survives a no-country refresh.
  const rv = getReferenceViewState();
  if (pendingReferenceView) {
    openReferenceView(cc, pendingReferenceView);
    pendingReferenceView = null;
  } else if (rv.open && rv.cc !== cc) {
    // A country change while the reader is open closes it — there is no
    // "stay open, reopen on the new country" path.
    closeReferenceView();
  } else {
    refreshReferenceView();
  }
}


/** Caption for the Instagram modal: same text + hashtags used elsewhere,
 * with the URL last (Instagram captions are plain text — the link won't be
 * clickable, but it stays copy-pasteable into the post/story or bio). */
function instagramCaption(syncUrl) {
  const ctx = shareContext(syncUrl);
  const text = ctx.text + ctx.hashtags.map(h => " #" + h).join("");
  return ctx.urlNote
    ? text + "\n" + ctx.urlNote + " " + ctx.url
    : text + "\n" + ctx.url;
}

function initMobileToolbar(map) {
  if (!mobileUiActive()) return;

  const target = document.getElementById("mobile-toolbar");
  target.classList.remove("hidden");
  mobileToolbarMount = target;
  mountMobileToolbar(target, {
    socialButtons: SOCIAL_BUTTONS,
    showNative: typeof navigator.share === "function",
    onCopyLink: shareHandlers.onCopyLink,
    onEmbed: shareHandlers.onEmbed,
    onNativeShare: shareHandlers.onNativeShare,
    onMastodon: shareHandlers.onMastodon,
    onInstagram: shareHandlers.onInstagram,
    onFilters: () => {
      openFilterView();
      contentPanel.expand();
    },
    showFlights: getShowFlights(),
    showLandings: getShowLandings(),
    onShowToggle: handleShowToggle,
  });

  setWhatIfMobileSnap((idx) => {
    if (!contentPanel || contentPanel.getMode() === 'sidebar') return;
    if (idx >= 2) contentPanel.expand();
    else contentPanel.collapse();
  });

  map.resize();
  frameMobileMap(map);
}


/** One-time pulsing hint that countries are tappable (mobile only). */
function maybeShowTapHint() {
  if (!mobileUiActive()) return;
  if (getCountry()) return;
  let hint = document.getElementById("tap-hint");
  if (!hint) {
    hint = document.createElement("div");
    hint.id = "tap-hint";
    hint.textContent = "Tap a country to see its role";
    document.body.appendChild(hint);
  }
  requestAnimationFrame(() => hint.classList.add("show"));
  clearTimeout(hint._t);
  hint._t = setTimeout(dismissTapHint, 6000);
}

function dismissTapHint() {
  const hint = document.getElementById("tap-hint");
  if (!hint) return;
  clearTimeout(hint._t);
  hint.classList.remove("show");
  setTimeout(() => hint.remove(), 400);
}

function downloadCsv() {
  const m = getManifest();
  const ft = getFlightsTable();
  const masks = getFlightMasks();
  if (!ft || !masks) return;
  const [dayMin, dayMax] = getDayRange();
  const activeMask = rolesToMask(getRoles());
  const cc = getCountry();

  const rows = [];
  for (let i = 0; i < ft.n; i++) {
    const day = ft.day[i];
    if (day < dayMin || day > dayMax) continue;
    if ((masks[i] & activeMask) === 0) continue;
    const countries = ft.cc[i] || "";
    if (cc && !ccContains(countries, cc)) continue;

    rows.push(flightToRow({
      hex: ft.hex[i], ti: ft.ti[i], day, rs: ft.rs[i], cc: countries,
      oa: ft.oa[i], da: ft.da[i], occ: ft.occ[i], dcc: ft.dcc[i],
      path: ft.path[i], kft: ft.kft[i], thc: ft.thc[i], the: ft.the[i],
    }, m.types));
  }

  const csv = rowsToCsv(rows);
  const blob = new Blob([csv], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = cc ? `flights-${cc}.csv` : "flights-all.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

function saveMapImage() {
  return new Promise((resolve) => {
    const m = getMap();
    const capture = () => {
      const mapCanvas = m.getCanvas();
      const footer = 40;
      const w = mapCanvas.width;
      const h = mapCanvas.height + footer;
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");

      ctx.drawImage(mapCanvas, 0, 0);

      ctx.fillStyle = "#111";
      ctx.fillRect(0, mapCanvas.height, w, footer);

      ctx.fillStyle = "#888";
      ctx.font = `${Math.round(footer * 0.4)}px -apple-system, BlinkMacSystemFont, sans-serif`;
      ctx.textBaseline = "middle";
      const manifest = getManifest();
      const dateRange = manifest ? `${manifest.data_start} to ${manifest.data_end}` : "";
      const left = `${siteTitle()}  |  ${dateRange}`;
      ctx.fillText(left, 12, mapCanvas.height + footer / 2);

      const url = window.location.href;
      const urlW = ctx.measureText(url).width;
      ctx.fillText(url, w - urlW - 12, mapCanvas.height + footer / 2);

      canvas.toBlob((blob) => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "airspace.png";
        a.click();
        URL.revokeObjectURL(a.href);
        resolve();
      }, "image/png");
    };
    m.once("render", capture);
    m.triggerRepaint();
  });
}

function initOnMapLegend(showFlights, showLandings) {
  const target = document.getElementById("onmap-legend-mount");
  if (!target) return;
  const fc = getFocusConfig();
  // The legend's country section is the 3 permissiveness levels (0-2). Both
  // halves of each row — the label and the swatch colour — come from the
  // same permissiveness_levels entry the map fill uses, so the legend can't
  // describe a colour the map isn't drawing. Sorted numerically since
  // permissivenessLevels is a plain object keyed "0".."2".
  const levels = fc?.permissivenessLevels
    ? Object.entries(fc.permissivenessLevels)
        .sort(([a], [b]) => Number(a) - Number(b))
        .map(([level, v]) => ({
          level: Number(level),
          label: v.label,
          color: colorForLevel(Number(level), fc.permissivenessLevels),
        }))
    : [];
  if (!levels.length) return;
  levels.push({ label: get(_)("legend.not_analyzed"), color: "#0a0a0a" });
  const m = getManifest();
  legendMount = target;
  mountOnMapLegend(target, {
    levels,
    altColors: ALT_COLORS,
    altBands: m?.alt_bands || 16,
    altBandKft: m?.alt_band_kft || 2.5,
    showFlights,
    showLandings,
    // Desktop starts expanded: the colour key is the first thing a new
    // visitor needs. Phones start collapsed because the open legend
    // otherwise covers the Refuse pulldown.
    startCollapsed: !toolbarMount,
  });
}

function updateLegendVisibility() {
  if (legendMount) {
    updateProps(legendMount, {
      showFlights: getShowFlights(),
      showLandings: getShowLandings(),
    });
  }
}

main().catch(err => {
  console.error(err);
  const box = document.getElementById("loading-box");
  if (box) {
    const home = window.location.pathname;
    box.innerHTML =
      `<div class="param-error">` +
      `<h2>Failed to load</h2>` +
      `<p style="color:#ccc;font-size:13px">${err.message || err}</p>` +
      `<p><a href="${home}">Go to homepage</a></p>` +
      `</div>`;
  }
});
