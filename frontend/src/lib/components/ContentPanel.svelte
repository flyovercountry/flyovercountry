<script>
  import { onMount, onDestroy } from 'svelte';
  import { _ } from 'svelte-i18n';
  import { panelData, filterViewOpen, closeFilterView, toggleFilterView } from '../stores/panel.js';
  import { readerState, closeReader } from '../stores/reader.js';
  import { findCountryClassification, withArticle, countryHeadlinePct, countryHeadlineTooltip } from '../../panel-data.js';
  import { isDesktopLayout } from '../layout.js';
  import { dismissTransients } from '../stores/ui.js';
  import { whatif } from '../stores/whatif.js';
  import { sidebarCollapsed as sidebarCollapsedStore,
           setSidebarCollapsed as setSidebarCollapsedStore,
           toggleSidebarCollapsed as toggleSidebarCollapsedStore } from '../stores/ui.js';
  import StatsSection from './StatsSection.svelte';
  import InfoSection from './InfoSection.svelte';
  import InfoProse from './InfoProse.svelte';
  import SidebarFooter from './SidebarFooter.svelte';
  import FilterPanel from './FilterPanel.svelte';

  // getFilterPanelProps supplies FilterPanel's props fresh each time the
  // filter view opens (app.js wires controls.getSidebarFilterPanelProps), so
  // this component stays decoupled from filter/manifest logic.
  // onSidebarFoldChange fires when the desktop panel folds/unfolds so the
  // map can resize to its new box.
  let {
    onSectionSelect = null, onLayoutChange = null,
    onSelectCountry = null, onDeselectCountry = null,
    onEnterRefusal = null, onExitRefusal = null,
    footerText = '', footerContact = null, footerTourLabel = '',
    footerRepo = null,
    getFilterPanelProps = null, onSidebarFoldChange = null,
  } = $props();

  // Clicking the Filters trigger while the filter view is open closes it.
  // The close path runs handleCloseFilters (which also dismisses the mobile
  // overlay); the open path goes through the shared toggleFilterView, so
  // the toolbar button and this trigger agree on what a second click does.
  function handleOpenFilters() {
    if (filterOpen) handleCloseFilters();
    else toggleFilterView();
  }

  function handleCloseFilters() {
    closeFilterView();
    // On the mobile sheet the filter view fills the expanded overlay; closing
    // it dismisses the whole overlay back to the map rather than swapping
    // to the stats content inside the same sheet. Desktop has no overlay.
    if (mode !== 'sidebar') collapse();
  }

  function handleFooterTour() { import('../../tour.js').then(m => m.startTour()); }

  let filterOpen = $state(false);
  let filterPanelProps = $state(null);
  const unsubFilterView = filterViewOpen.subscribe(v => {
    const wasOpen = filterOpen;
    filterOpen = v;
    if (filterOpen && !wasOpen) {
      filterPanelProps = getFilterPanelProps ? getFilterPanelProps() : {};
    } else if (!filterOpen && wasOpen) {
      filterPanelProps = null;
    }
  });

  // 'sidebar' (desktop: full-height panel) or 'sheet' (phones and tablets:
  // collapsible bottom sheet). Landscape phones are covered by
  // #rotate-overlay (style.css) and need no handling here.
  let mode = $state('sidebar');
  let expanded = $state(false);

  // The desktop fold, distinct from the sheet's `expanded`. The panel stays
  // mounted while folded (style.css slides it out), so its scroll position
  // and open views survive. The store lets tour.js unfold it; this
  // component owns the body class and the map-resize notification.
  let sidebarCollapsed = $state(false);
  const unsubFold = sidebarCollapsedStore.subscribe(v => applyFold(v));

  // The expanded sheet is local state; the tour closes it through this signal.
  let seenFirstDismiss = false;
  const unsubDismiss = dismissTransients.subscribe(() => {
    if (!seenFirstDismiss) { seenFirstDismiss = true; return; }
    closeFilterView();
    collapse();
  });
  const TOOLBAR_H = 48;
  const COLLAPSED_H = 52;

  export function getMode() { return mode; }
  export function isExpanded() { return expanded; }
  export function isSidebarCollapsed() { return sidebarCollapsed; }

  function applyFold(v) {
    const next = mode === 'sidebar' ? !!v : false;
    if (next === sidebarCollapsed) return;
    sidebarCollapsed = next;
    // On documentElement, not body: tour.js's sidebarWidth() reads the
    // custom property off getComputedStyle(document.documentElement),
    // which never sees a body-scoped override.
    document.documentElement.classList.toggle('sidebar-collapsed', sidebarCollapsed);
    // The map container's `left` is driven by --sidebar-width, so its box
    // changes size; MapLibre only learns about that from resize(). Fired
    // after the CSS transition so the final size is the one measured.
    onSidebarFoldChange?.(sidebarCollapsed);
  }

  export function setSidebarCollapsed(v) { setSidebarCollapsedStore(v); }
  function toggleFold() { toggleSidebarCollapsedStore(); }

  export function expand() {
    if (mode !== 'sheet') return;
    expanded = true;
    markExpandedOnce();
    document.body.classList.add('panel-expanded');
    notifyLayout();
  }

  export function collapse() {
    if (mode !== 'sheet') return;
    expanded = false;
    document.body.classList.remove('panel-expanded');
    notifyLayout();
  }

  export function getCollapsedPadding() {
    if (mode === 'sidebar') return null;
    return { top: 12, bottom: COLLAPSED_H + TOOLBAR_H + 12, left: 12, right: 12 };
  }

  function layoutMode() {
    return isDesktopLayout() ? 'sidebar' : 'sheet';
  }

  function notifyLayout() {
    onLayoutChange?.(mode, expanded, getCollapsedPadding());
  }

  function updateMode() {
    const newMode = layoutMode();
    const changed = newMode !== mode;
    mode = newMode;
    if (mode !== 'sheet' && expanded) {
      expanded = false;
      document.body.classList.remove('panel-expanded');
    }
    // the fold is desktop-only. Narrowing to the sheet layout
    // while folded would otherwise leave body.sidebar-collapsed set, and
    // --sidebar-width pinned at 0, for a layout that does not use it.
    if (mode !== 'sidebar' && sidebarCollapsed) {
      setSidebarCollapsedStore(false);
    }
    if (changed) notifyLayout();
  }

  let cc = $state(null);
  let globalStats = $state(null);
  let countryStats = $state(null);
  // One-shot cue on the collapsed bar when a country gets selected: the
  // bar's text alone does not draw the eye to the thing that expands.
  let barAttention = $state(false);
  let attentionTimer = null;
  let barPeek = $state(false);
  const PEEK_KEY = 'panelPeeked';
  const EXPANDED_KEY = 'panelExpandedOnce';
  let expandedOnce = $state(sessionStorage.getItem(EXPANDED_KEY) === '1');
  function markExpandedOnce() {
    expandedOnce = true;
    sessionStorage.setItem(EXPANDED_KEY, '1');
  }
  const PEEK_MS = 500;
  function peekBar() {
    if (sessionStorage.getItem(PEEK_KEY)) return 0;
    sessionStorage.setItem(PEEK_KEY, '1');
    barPeek = true;
    setTimeout(() => { barPeek = false; }, PEEK_MS);
    return PEEK_MS;
  }
  function flashBar() {
    clearTimeout(attentionTimer);
    barAttention = false;
    const delay = peekBar();
    // next frame so a back-to-back re-select restarts the animation
    requestAnimationFrame(() => {
      attentionTimer = setTimeout(() => {
        barAttention = true;
        attentionTimer = setTimeout(() => { barAttention = false; }, 1600);
      }, delay);
    });
  }
  const unsub = panelData.subscribe(d => {
    const prev = cc;
    cc = d.cc;
    globalStats = d.globalStats;
    countryStats = d.countryStats;
    if (cc && cc !== prev && mode === 'sheet' && !expanded) flashBar();
  });

  // The reader's state feeds the collapsed bar's subtitle and collapses the
  // sheet when a section opens (on the open transition only: collapse()
  // animates the map padding, and is a no-op outside sheet mode).
  let readerOpen = $state(false);
  let readerLabel = $state('');
  let wasReaderOpen = false;
  const unsubReader = readerState.subscribe(r => {
    readerOpen = r.open;
    readerLabel = r.open ? (r.tabs.find(t => t.slug === r.slug)?.label || '') : '';
    if (r.open && !wasReaderOpen) collapse();
    wasReaderOpen = r.open;
  });

  // A click anywhere in the panel other than a ToC entry closes the reader,
  // without stopping propagation, so the clicked control still acts. The
  // exempt selectors open the reader themselves and flip readerOpen earlier
  // in the same bubble.
  function handlePanelClick(e) {
    if (!readerOpen) return;
    if (e.target.closest('.toc-item, .toc-list, .stats-more-hint, .stats-toc-item')) return;
    closeReader();
  }

  let classification = $derived(cc ? findCountryClassification(cc) : null);
  let countryName = $derived(classification?.name || cc || '');
  let barTitle = $derived(countryName ? withArticle(countryName) : $_('app.title'));

  // Same computeWhatIf-based formula as the desktop panel's unified stats
  // table (panel-data.js countryHeadlinePct), so the mobile bar and the
  // desktop panel never show two different numbers for one country.
  // Recomputes on `cc` or `countryStats` (reassigned on every apply and on
  // sighting-index load).
  let barSubtitle = $derived.by(() => {
    // While a reader section is open the second line names the section.
    if (readerOpen && readerLabel) return readerLabel;
    if (cc && countryStats) {
      const { pct } = countryHeadlinePct(cc);
      return `could have affected ${pct.toFixed(1)}% of flights`;
    }
    if (globalStats) {
      return `${globalStats.total.toLocaleString()} flights | ${globalStats.aircraft.toLocaleString()} aircraft`;
    }
    return '';
  });

  let barHint = $derived(!cc && !expandedOnce && barSubtitle && !readerOpen
    ? `${barSubtitle} \u00b7 ${$_('panel.tap_for_details')}` : barSubtitle);

  let barSubtitleTitle = $derived((!readerOpen && cc && countryStats) ? countryHeadlineTooltip() : '');

  // Tapping the collapsed bar while a reader is open closes it and
  // expands the panel in the same action, rather than stacking two
  // full-screen overlays.
  function handleExpandClick() {
    if (readerOpen) closeReader();
    expand();
  }

  function handleExpandKeydown(e) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      expand();
    }
  }

  onMount(() => {
    updateMode();
    window.addEventListener('resize', updateMode);
    window.addEventListener('orientationchange', updateMode);
  });

  onDestroy(() => {
    unsub();
    unsubFilterView();
    unsubReader();
    unsubFold();
    unsubDismiss();
    window.removeEventListener('resize', updateMode);
    window.removeEventListener('orientationchange', updateMode);
    clearTimeout(attentionTimer);
    document.body.classList.remove('panel-expanded');
  });
</script>

{#if mode !== 'sidebar' && !expanded}
  <div class="collapsed-bar collapsed-sheet"
       class:has-country={!!cc}
       class:attention={barAttention}
       class:peek={barPeek}
       onclick={handleExpandClick}
       onkeydown={handleExpandKeydown}
       role="button"
       tabindex="0"
       aria-label="Expand panel">
    <span class="collapsed-grabber" aria-hidden="true"></span>
    <div class="collapsed-text">
      <span class="collapsed-title">{barTitle}</span>
      {#if barSubtitle}
        <span class="collapsed-subtitle" title={barSubtitleTitle}>{barHint}</span>
      {/if}
    </div>
    <svg class="collapsed-chevron" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 15l5-5 5 5"/>
    </svg>
  </div>
{/if}

<!-- the expand affordance cannot live inside the panel, because
     while collapsed the panel is off-screen. Pinned to the viewport edge
     instead, and only rendered on the desktop layout. -->
{#if mode === 'sidebar' && sidebarCollapsed}
  <button id="sidebar-expand-tab" type="button"
          aria-expanded="false" aria-controls="sidebar"
          title="Show the panel"
          data-breadcrumb="Bring back the statistics, country details and filters panel."
          onclick={() => setSidebarCollapsedStore(false)}>
    <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true"
         fill="none" stroke="currentColor" stroke-width="2.5"
         stroke-linecap="round" stroke-linejoin="round">
      <path d="M9 6l6 6-6 6"/>
    </svg>
    <span class="expand-tab-label">Show panel</span>
  </button>
{/if}

<!-- Mobile-first geometry: below md the panel is either off (collapsed
     sheet — the 52px bar above stands in for it) or a full-screen overlay;
     from md up it is the fixed-width left column. `.expanded` stays as a
     state hook for tests and the inert/fold rules in style.css. -->
<div id="sidebar" data-mode={mode} class:expanded={mode !== 'sidebar' && expanded}
     class="{mode !== 'sidebar' && expanded ? 'fixed inset-0 z-[200] flex flex-col overflow-hidden pt-[var(--sai-top)] pb-[var(--sai-bottom)]' : 'max-md:hidden'}
            md:absolute md:inset-auto md:top-0 md:left-0 md:z-auto md:flex md:flex-col
            md:w-[var(--sidebar-width-expanded)] md:h-screen md:overflow-y-auto
            md:border-r md:border-[#333]"
     inert={mode === 'sidebar' && sidebarCollapsed ? true : undefined}>
  {#if mode === 'sidebar'}
    <button id="sidebar-collapse-btn" type="button"
            aria-expanded={!sidebarCollapsed} aria-controls="sidebar"
            title="Hide this panel" aria-label="Hide this panel"
            data-breadcrumb="Hide this panel to give the map the full window. A tab
 on the left edge brings it back exactly as you left it."
            onclick={toggleFold}>
      <svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true"
           fill="none" stroke="currentColor" stroke-width="2.5"
           stroke-linecap="round" stroke-linejoin="round">
        <path d="M15 6l-6 6 6 6"/>
      </svg>
      <span class="fold-tab-label">Hide</span>
    </button>
  {/if}
  {#if mode !== 'sidebar' && expanded && !filterOpen}
    <div class="panel-close-row">
      <span class="panel-title">{barTitle}</span>
      <button class="panel-close-btn" onclick={() => collapse()}
              aria-label="Close panel">&times;</button>
    </div>
  {/if}

  <!-- This click is a dismiss-on-interaction convenience, not an action;
       keyboard users close the reader with its close button or Escape. -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="panel-scroll" class:panel-scroll-hidden={mode !== 'sidebar' && !expanded}
       onclick={handlePanelClick}>
    <div class:hidden={filterOpen}>
      <header id="sidebar-header">
        <h1><img src="logo.png" alt="" class="site-logo">{$_('app.title')}</h1>
        <div class="country-selection-line">
          {#if cc}
            <span class="country-sel-text">{$_('sidebar.country_selection', { values: { name: countryName, cc } })}</span>
            <button type="button" class="deselect-country-btn"
                    aria-label={$_('sidebar.deselect_country', { values: { name: countryName } })}
                    title={$_('sidebar.deselect_country', { values: { name: countryName } })}
                    onclick={() => onDeselectCountry?.()}>&times;</button>
            {#if classification?.permissivenessLabel}
              <span class="country-permissiveness-label">{classification.permissivenessLabel}</span>
            {/if}
          {:else}
            <span class="country-sel-text">{$_('sidebar.country_selection_none')}</span>
          {/if}
        </div>
        {#if mode === 'sheet' && cc}
          <div class="filter-toggle-group mode-toggle" role="group"
               aria-label={$_('panel.mode_aria')}>
            <button type="button" class="toggle-btn" class:active={!$whatif.active}
                    aria-pressed={!$whatif.active}
                    onclick={() => { if ($whatif.active) onExitRefusal?.(); }}>{$_('panel.mode_regular')}</button>
            <button type="button" class="toggle-btn" class:active={$whatif.active}
                    aria-pressed={$whatif.active}
                    onclick={() => { if (!$whatif.active) onEnterRefusal?.(); }}>{$_('panel.mode_refusal')}</button>
          </div>
        {/if}
      </header>

      <section id="whatif-section" class="hidden">
        <div class="section-header">
          <span class="section-label">{$_('whatif.title')}</span>
        </div>
        <div id="whatif-body"></div>
      </section>

      <div id="whatif-detail"></div>

      <!-- Order: statistics, table of contents, the long-form prose, and
           the footer actions last, at the bottom of the panel. -->
      <section id="stats-section">
        <StatsSection
          onHeaderClick={handleOpenFilters}
          {onSelectCountry}
          {onSectionSelect} />
      </section>
      <section id="info-section">
        <InfoSection {onSectionSelect} />
      </section>

      <section id="info-prose-section">
        <InfoProse />
      </section>

      <!-- Rendered unconditionally: the tour button must not depend on
           footer.text being configured. The contact and repo chips appear
           only when their untracked configs resolve (non-null). -->
      <footer id="sidebar-footer">
        <SidebarFooter
          text={footerText}
          contact={footerContact}
          tourLabel={footerTourLabel}
          repo={footerRepo}
          onTour={handleFooterTour} />
      </footer>
    </div>

    {#if filterOpen}
      <div class="filter-view">
        <button class="filter-close-x" type="button" title="Close filters"
                aria-label="Close filters" onclick={handleCloseFilters}>
          <span aria-hidden="true">&times;</span>
        </button>
        {#if filterPanelProps}
          <FilterPanel {...filterPanelProps} />
        {/if}
      </div>
    {/if}
  </div>
</div>

<style>
  /* ---- Collapsed bar ---- */
  .collapsed-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    cursor: pointer;
    user-select: none;
    background: #1a1a1a;
    color: #e0e0e0;
    /* The resting-state element at the bottom of the mobile layout, so it
       sits at the bottom of the mobile z-index stack: under #onmap-legend
       (30) and .mobile-toolbar (55). The toolbar is its own stacking
       context, so everything mounted in it (Share/Export sheets, the
       Refuse pulldown) paints above this bar as a unit.
       Exception: while a reader section is open (#reference-view, 2000,
       full-viewport on mobile) this bar is the country + section readout
       (barSubtitle), so body.refview-open (set by ReferenceViewModal.svelte)
       raises it above the reader; the toolbar is display:none there. */
    z-index: 20;
  }
  :global(body.refview-open) .collapsed-bar {
    z-index: 2001;
  }
  .collapsed-bar:hover { background: #242424; }
  .collapsed-bar:focus-visible {
    outline: 2px solid #4a9adb;
    outline-offset: -2px;
  }

  .collapsed-sheet {
    position: fixed;
    /* Flush at the very bottom, with the bar's own background extending
       through the home-indicator inset (padding-bottom) rather than
       floating above a see-through strip of map. Its 52px of content
       therefore starts at 52px + inset from the bottom edge — the same
       edge .mobile-toolbar, the MapLibre bottom controls and the
       breadcrumb button position against (style.css). */
    bottom: 0;
    left: 0;
    right: 0;
    height: calc(52px + var(--sai-bottom));
    padding: 6px 16px var(--sai-bottom);
    border-top: 1px solid #333;
    box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.3);
  }

  .collapsed-grabber {
    position: absolute;
    top: 6px;
    left: 50%;
    width: 36px;
    height: 4px;
    margin-left: -18px;
    border-radius: 2px;
    background: #555;
    pointer-events: none;
  }
  .collapsed-sheet.has-country .collapsed-grabber {
    background: #4a9adb;
  }
  @keyframes bar-peek {
    0%, 100% { transform: translateY(0); }
    50%      { transform: translateY(-10px); }
  }
  @media (prefers-reduced-motion: no-preference) {
    .collapsed-sheet.peek { animation: bar-peek 0.5s ease-out; }
  }

  .mode-toggle {
    display: flex;
    gap: 4px;
    margin-top: 8px;
  }

  .collapsed-text {
    display: flex;
    flex-direction: column;
    min-width: 0;
    flex: 1;
  }

  .collapsed-title {
    font-size: 14px;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .collapsed-subtitle {
    font-size: 11px;
    color: #999;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-top: 1px;
  }

  .collapsed-chevron {
    width: 20px;
    height: 20px;
    flex-shrink: 0;
    fill: none;
    stroke: #888;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: stroke 0.3s ease;
  }

  /* ---- "there is more here" cue once a country is selected ---- */
  .collapsed-sheet.has-country {
    border-top-color: #4a9adb;
  }
  .collapsed-sheet.has-country .collapsed-chevron {
    stroke: #4a9adb;
    stroke-width: 2.5;
  }
  @keyframes bar-flash {
    0%   { background: #1a1a1a; box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.3); }
    25%  { background: #24405c; box-shadow: 0 -2px 18px rgba(74, 154, 219, 0.55); }
    100% { background: #1a1a1a; box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.3); }
  }
  @keyframes chevron-nudge {
    0%, 100% { transform: translateY(0); }
    20%      { transform: translateY(-6px); }
    40%      { transform: translateY(0); }
    60%      { transform: translateY(-4px); }
    80%      { transform: translateY(0); }
  }
  .collapsed-sheet.attention {
    animation: bar-flash 1.6s ease-out;
  }
  .collapsed-sheet.attention .collapsed-chevron {
    animation: chevron-nudge 1.2s ease-out;
  }
  @media (prefers-reduced-motion: reduce) {
    .collapsed-sheet.attention,
    .collapsed-sheet.attention .collapsed-chevron {
      animation: none;
    }
  }

  /* ---- Expanded overlay: geometry is on #sidebar's class list above ---- */

  .panel-close-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid #333;
    flex-shrink: 0;
    background: #151515;
  }

  .panel-title {
    font-size: 15px;
    font-weight: 600;
    color: #fff;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
  }

  .panel-close-btn {
    background: none;
    border: 1px solid #444;
    color: #ccc;
    font-size: 22px;
    width: 36px;
    height: 36px;
    border-radius: 50%;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    margin-left: 12px;
  }
  .panel-close-btn:hover { background: #2a2a2a; color: #fff; }

  /* ---- Scrollable content area ---- */
  .panel-scroll {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }

  .panel-scroll-hidden {
    display: none;
  }

  .panel-scroll::-webkit-scrollbar { width: 6px; }
  .panel-scroll::-webkit-scrollbar-track { background: #111; }
  .panel-scroll::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }

  /* ---- Filter view: replaces the normal sidebar content in place ---- */
  .filter-view {
    position: relative;
    min-height: 100%;
    padding: 12px 16px 16px;
  }

  /* A real, obvious button rather than an unbordered glyph that reads as
     decoration: bordered, filled, large enough to hit, with a hover and
     focus state. */
  .filter-close-x {
    position: absolute;
    top: 8px;
    right: 8px;
    width: 30px;
    height: 30px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;
    line-height: 1;
    color: #c8ced4;
    cursor: pointer;
    background: #1d2129;
    border: 1px solid #3a4250;
    border-radius: 4px;
    padding: 0;
    user-select: none;
    z-index: 2;
  }
  .filter-close-x:hover {
    color: #fff;
    background: #2a3038;
    border-color: #5a6470;
  }
  .filter-close-x:focus-visible {
    outline: 2px solid #6ab4f0;
    outline-offset: 2px;
  }

  /* ---- Country selection line (always visible, under the h1 title) ---- */
  .country-selection-line {
    display: flex;
    align-items: center;
    gap: 4px;
    margin-top: 4px;
    font-size: 13px;
    color: #ccc;
  }
  .country-sel-text { font-weight: 500; }
  .country-permissiveness-label {
    margin-left: auto;
    font-size: 11px;
    font-weight: 600;
  }
  .deselect-country-btn {
    border: none;
    background: transparent;
    color: #888;
    font-size: 15px;
    line-height: 1;
    padding: 0 2px;
    cursor: pointer;
  }
  .deselect-country-btn:hover { color: #e53935; }
</style>
