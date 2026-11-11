<script>
  import { _ } from 'svelte-i18n';
  import { panelData } from '../stores/panel.js';
  import {
    buildCountryStatsHtml, buildGlobalStatsHtml, formatSummary, bindChartHover,
  } from '../../panel-data.js';
  import { toggleStatColDetail } from '../../stat-tap.js';
  import { MORE_STATS_SLUG, hasStats as hasStatsForCc } from '../../reference.js';
  import { readerState } from '../stores/reader.js';
  import { tick } from 'svelte';

  // onSectionSelect: the table's own "See the breakdown" hint opens the same
  // reader entry as the ToC's "See more statistics" item, through the same
  // callback ContentPanel threads into InfoSection.
  let { onHeaderClick = null, onSectionSelect = null, onSelectCountry = null } = $props();

  let statsContainer = $state(null);

  let cc = $derived($panelData.cc);
  let globalStats = $derived($panelData.globalStats);
  let countryStats = $derived($panelData.countryStats);
  let whatIfActive = $derived($panelData.whatIfActive);
  let refreshTick = $derived($panelData.refreshTick);
  let summary = $derived(formatSummary(globalStats));
  let showStatsToc = $derived(!whatIfActive && cc && hasStatsForCc(cc));
  let statsReaderActive = $derived($readerState.open && $readerState.cc === cc && $readerState.slug === MORE_STATS_SLUG);

  // Recomputed when the filtered data changes (a filter apply bumps
  // refreshTick); the table has no mode toggle of its own.
  let statsResult = $derived(buildStatsResult(cc, countryStats, globalStats, whatIfActive, refreshTick));

  function buildStatsResult(cc, countryStats, globalStats, whatIfActive, _tick) {
    if (whatIfActive) return null;
    // A selected country with countryStats === null means "stats not
    // eligible for this country" (app.js refreshPanel gates computeStats on
    // the `stats` flag), so it falls through to the empty-state message,
    // never to the global table.
    if (cc) return countryStats ? buildCountryStatsHtml(countryStats, cc) : null;
    if (globalStats) return buildGlobalStatsHtml(globalStats);
    return null;
  }

  let statsHtml = $derived(statsResult?.html || '');
  let chartIds = $derived(statsResult?.chartIds || []);

  $effect(() => {
    if (!statsHtml || !statsContainer) return;
    const container = statsContainer;
    const ids = chartIds;
    tick().then(() => {
      ids.forEach(id => bindChartHover(id));
      if (!container._statsDelegated) {
        container._statsDelegated = true;
        container.addEventListener("click", handleStatsClick);
        container.addEventListener("keydown", handleStatsKeydown);
      }
    });
  });

  // shared by both the click delegate below and the keydown
  // delegate — a country row now carries role="button" tabindex="0"
  // (panel-data.js's unifiedStatsRow ariaLabel param), so it needs the same
  // activation on Enter/Space that a native <button> gets for free.
  function activateStatsTarget(target) {
    if (toggleStatColDetail(target)) return;
    if (target.closest(".stats-more-hint")) {
      onSectionSelect?.(MORE_STATS_SLUG);
      return;
    }
    const rowEl = target.closest(".country-stat-row");
    if (rowEl && onSelectCountry) {
      onSelectCountry(rowEl.dataset.cc);
    }
  }

  function handleStatsClick(e) { activateStatsTarget(e.target); }

  function handleStatsKeydown(e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    // Only country rows are keyboard-focusable in here — the
    // stats-more-hint is a real <button> and handles its own Enter/Space
    // natively, so this only needs to intercept the delegated-role rows.
    if (!e.target.closest(".country-stat-row")) return;
    e.preventDefault();
    activateStatsTarget(e.target);
  }

  // Clicking the header bar opens/closes the filter view, so it is a
  // role="button" that answers Enter/Space as well as a click.
  function handleHeaderKeydown(e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    onHeaderClick?.();
  }
</script>

<div class="section-header" onclick={onHeaderClick} onkeydown={handleHeaderKeydown}
  role="button" tabindex="0" style="cursor:pointer">
  <span class="section-label">{$_('stats.title')}</span>
  <span class="stats-summary">{summary}</span>
</div>
<div class="stats-body" bind:this={statsContainer}>
  {#if statsHtml}
    {@html statsHtml}
  {:else}
    <div class="stat-row"><span class="stat-label">{$_('stats.no_data')}</span></div>
  {/if}
</div>
{#if showStatsToc}
  <button type="button" class="stats-toc-item" class:active={statsReaderActive}
    aria-current={statsReaderActive ? 'true' : undefined}
    onclick={() => onSectionSelect?.(MORE_STATS_SLUG)}>
    <span class="stats-toc-label">Statistics</span>
    <span class="stats-toc-chevron" aria-hidden="true">&rsaquo;</span>
  </button>
{/if}

<style>
  /* .section-header is styled globally in style.css — ContentPanel.svelte's
     whatif-section header also needs it, outside this component's scope. */
  .stats-summary {
    flex: 1;
    text-align: right;
    margin-right: 12px;
    font-size: 11px;
    /* was #777 (~4.1:1 on this section's #151515
     * background) — below WCAG AA's 4.5:1 floor for sub-12px text. */
    color: #9a9a9a;
    transition: opacity 0.15s;
  }
  .stats-body {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  /* .stat-row/.stat-col, .country-stat-row and .country-stats-table are
     styled globally in style.css, which also carries a
     .country-stat-row:focus-visible rule this component has no need to
     duplicate, and covers .stat-row for the still-separate What-If role
     table (panel-data.js's buildRoleTable) that renders outside .stats-body
     entirely. */
  /* the panel table's own hint pointing at the reader's "See
   * more statistics" entry — a plain-text-weight link, not a button, so it
   * reads as a footnote under the table rather than competing with the
   * Filters/mode toggles above it. */
  .stats-body :global(.stats-more-hint) {
    display: block;
    width: 100%;
    margin-top: 6px;
    padding: 4px 0 2px;
    background: none;
    border: none;
    font: inherit;
    font-size: 11px;
    color: #6ab4f0;
    text-align: left;
    cursor: pointer;
  }
  .stats-body :global(.stats-more-hint:hover) { color: #8fc7f5; text-decoration: underline; }
  .stats-body :global(.stats-more-hint:focus-visible) { outline: 2px solid #6ab4f0; outline-offset: 2px; }
  .stats-toc-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    width: 100%;
    padding: 8px 10px;
    margin-top: 8px;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid transparent;
    border-radius: 4px;
    color: #cfd3d8;
    font-size: 13px;
    font-family: inherit;
    text-align: left;
    cursor: pointer;
  }
  .stats-toc-item:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
  .stats-toc-item:focus-visible { outline: 2px solid #6ab4f0; outline-offset: -2px; }
  .stats-toc-item.active {
    background: rgba(74, 154, 219, 0.14);
    border-color: #3a6a95;
    color: #6ab4f0;
  }
  .stats-toc-label { flex: 1 1 auto; min-width: 0; }
  .stats-toc-chevron { flex-shrink: 0; color: #888; font-size: 15px; line-height: 1; }
  .stats-toc-item.active .stats-toc-chevron { color: #6ab4f0; }
</style>
