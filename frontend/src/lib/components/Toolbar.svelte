<script>
  import { _ } from 'svelte-i18n';
  import ShareMenu from './ShareMenu.svelte';
  import ExportMenu from './ExportMenu.svelte';
  import ShowToggle from './ShowToggle.svelte';
  import { legendCollapsed, toggleLegend } from '../stores/ui.js';

  // Two groups: what is on the map (Show Flights/Landings, Select
  // individual flight, Filters, the What-If pulldown) and what you take
  // away (Legend, Share, Export). onFilters opens the same filter view the
  // stats header does, in the sidebar or the expanded sheet.
  let { socialButtons = [], showNative = false, showExplore = false, exploreActive = false,
        onCopyLink = null, onEmbed = null, onNativeShare = null, onMastodon = null, onInstagram = null,
        onSaveImage = null, onExportCsv = null, onRectExport = null, onExploreToggle = null,
        onFilters = null, filtersActive = false, filterBadge = '',
        showFlights = true, showLandings = false, showDisabled = false,
        onShowToggle = null } = $props();

  // The Explore breadcrumb states up front that selecting a flight leaves
  // this site.
  const EXPLORE_CRUMB =
    'Pick a single flight out of the tangle. Hover a route to isolate it, then click for '
    + 'that aircraft\'s details \u2014 which OPENS A NEW TAB on adsbexchange.com, a '
    + 'different website.';

  // Names what is currently narrowed, rather than only that something is.
  const CRUMB_BY_LETTER = { C: 'a country', D: 'the date range', R: 'aircraft roles' };
  let filterCrumb = $derived(
    filterBadge
      ? 'Narrow what the map shows. Currently filtered by: '
        + [...filterBadge].map(c => CRUMB_BY_LETTER[c]).filter(Boolean).join(', ')
        + '. Changes apply on their own a few seconds after you stop.'
      : 'Narrow what the map shows by date, aircraft role, or theater connection. '
        + 'Changes apply on their own a few seconds after you stop.');
</script>

<!-- md-and-up control strip across the top of the map (the sheet layout has
     MobileToolbar instead). Wraps onto a second row, right-aligned, until lg
     so it clears MapLibre's zoom control. Hidden when the reader is open
     (body.refview-open in style.css). -->
<div id="map-toolbar"
     class="map-toolbar max-md:hidden md:absolute md:top-3 md:left-[calc(var(--sidebar-width)_+_20px)] md:right-[60px] md:z-[2050]
            md:flex md:flex-wrap md:justify-end md:gap-x-2 md:gap-y-1.5 lg:flex-nowrap lg:justify-normal lg:gap-y-0">
  <div class="toolbar-group" data-group="data">
    <ShowToggle {showFlights} {showLandings} disabled={showDisabled}
      showLabel={true} onToggle={onShowToggle} />
    {#if showExplore}
      <button id="explore-flights-btn" class:active={exploreActive}
        data-breadcrumb={EXPLORE_CRUMB}
        onclick={onExploreToggle}>{$_('nav.explore')}</button>
    {/if}
    <button id="toolbar-filter-btn" type="button"
      class:active={filtersActive} aria-expanded={filtersActive}
      data-breadcrumb={filterCrumb}
      onclick={onFilters}>{$_('filters.title')}{#if filterBadge}<span
        class="filter-badge">{filterBadge}</span>{/if}</button>
    <!-- the What-If "Refuse?" pulldown (a separate Svelte island,
         WhatIfPulldown.svelte) mounts into this slot from app.js so it reads
         as one group with the other what-is-on-the-map controls, instead of
         floating over the map on its own. Only present on desktop —
         Toolbar.svelte doesn't mount on the mobile layout at all (see
         app.js's mobileUiActive() gate), so mobile keeps the pulldown at
         its original standalone mount point. -->
    <div id="toolbar-whatif-mount"></div>
  </div>

  <div class="toolbar-group toolbar-group-end" data-group="output">
    <button id="toolbar-legend-btn" type="button"
      class:active={!$legendCollapsed} aria-expanded={!$legendCollapsed}
      data-breadcrumb="Show or hide the map key: what the country colours mean, and the altitude scale for the flight traces."
      aria-controls="onmap-legend-body"
      onclick={toggleLegend}>{$_('legend.title')}</button>
    <ShareMenu {socialButtons} {showNative}
      {onCopyLink} {onEmbed} {onNativeShare} {onMastodon} {onInstagram} />
    <ExportMenu {onSaveImage} {onExportCsv} {onRectExport} />
  </div>
</div>

<style>
  /* The two groups are separated by the gap on .toolbar-group-end plus its
     leading rule, so they read as two clusters rather than one strip. */
  :global(#map-toolbar) {
    align-items: flex-start;
  }
  .toolbar-group {
    display: flex;
    gap: 8px;
    align-items: flex-start;
    pointer-events: none;
  }
  .toolbar-group-end {
    margin-left: auto;
    padding-left: 14px;
    border-left: 1px solid rgba(255, 255, 255, 0.14);
  }
  /* The Show toggle keeps its pill styling from #show-subsection in
     style.css (translucent panel, border, blur, padding): that outline is
     what groups Flights and Landings into one control. Only the absolute
     positioning is neutralised so it flows inside this group. */
  .toolbar-group :global(#show-subsection) {
    position: static;
    bottom: auto;
    left: auto;
    z-index: auto;
    pointer-events: auto;
  }
  .filter-badge {
    margin-left: 5px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
    color: #cfe3f5;
  }
</style>
