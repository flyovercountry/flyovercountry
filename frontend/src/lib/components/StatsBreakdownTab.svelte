<script>
  /** The reader's Statistics entry: breakdown tables by aircraft type and
   * by role group, each with a daily histogram for the selected row. Row
   * data comes computed from stats-breakdown.js; this component renders,
   * selects rows and wires the histogram. The table markup comes from
   * panel-data.js via {@html}, so its classes are styled in style.css, not
   * in this component's scoped <style>. */
  import { tick } from 'svelte';
  import { unifiedStatsHeader, unifiedStatsRow, buildDailyChart, bindChartHover } from '../../panel-data.js';
  import { toggleStatColDetail } from '../../stat-tap.js';

  let { title = '', types = [], groups = [], dates = [], countryTotals = {} } = $props();

  let selectedTypeKey = $state(null);
  let selectedGroupKey = $state(null);

  // Keep the selection across a filter-driven refresh while the row still
  // exists; otherwise fall back to the first (highest-ranked) row.
  $effect(() => {
    if (!types.some(t => t.key === selectedTypeKey)) selectedTypeKey = types[0]?.key ?? null;
  });
  $effect(() => {
    if (!groups.some(g => g.key === selectedGroupKey)) selectedGroupKey = groups[0]?.key ?? null;
  });

  let selectedType = $derived(types.find(t => t.key === selectedTypeKey) || null);
  let selectedGroup = $derived(groups.find(g => g.key === selectedGroupKey) || null);

  // `denoms` makes a row's percentages read against the country's totals
  // (unifiedStatsRow). The type table passes it; the role table keeps
  // per-row denominators because roles overlap.
  function buildTableHtml(rows, labelTitle, labelTooltip, selectedKey, denoms = null) {
    let html = unifiedStatsHeader(labelTitle, labelTooltip);
    for (const row of rows) {
      const extraClass = row.key === selectedKey ? "breakdown-row selected" : "breakdown-row";
      html += unifiedStatsRow(row.label, row.wiRow, row.plRow, false, extraClass, `data-key="${row.key}"`, `Select ${row.label}`, denoms);
    }
    return html;
  }

  let typeDenoms = $derived({
    sum: countryTotals.prevented,
    cross: countryTotals.direct,
    prev: countryTotals.positioning,
    seen: countryTotals.crossed,
    ground: countryTotals.landed,
    landings: countryTotals.landingEvents,
  });

  let typeTableHtml = $derived(buildTableHtml(types, "Model", "Aircraft type — the disambiguated model name.", selectedTypeKey, typeDenoms));
  let groupTableHtml = $derived(buildTableHtml(groups, "Role group", "Aircraft mission role group — scoped by the role filters, see Filters.", selectedGroupKey));

  let typeChart = $derived(selectedType ? buildDailyChart(selectedType.label, selectedType.dailyPrevented, dates) : null);
  let groupChart = $derived(selectedGroup ? buildDailyChart(selectedGroup.label, selectedGroup.dailyPrevented, dates) : null);

  // Each buildDailyChart call gets a fresh chartId; bind its hover tooltip
  // once the new nodes exist.
  $effect(() => {
    const id = typeChart?.chartId;
    if (id) tick().then(() => bindChartHover(id));
  });
  $effect(() => {
    const id = groupChart?.chartId;
    if (id) tick().then(() => bindChartHover(id));
  });

  // Mobile tap-reveal (stat-tap.js) on a percentage cell takes priority over
  // row selection, same order StatsSection.svelte's own delegated handler
  // uses for the identical `.stat-col-narrow[title]` cells — a touch tap
  // reveals that cell's tooltip text instead of swapping the histogram.
  function activateType(target) {
    if (toggleStatColDetail(target)) return;
    const el = target.closest('[data-key]');
    if (el) selectedTypeKey = el.dataset.key;
  }
  function activateGroup(target) {
    if (toggleStatColDetail(target)) return;
    const el = target.closest('[data-key]');
    if (el) selectedGroupKey = el.dataset.key;
  }
  function handleTypeClick(e) { activateType(e.target); }
  function handleGroupClick(e) { activateGroup(e.target); }

  // rows carry role="button" tabindex="0" (unifiedStatsRow's
  // ariaLabel param, wired in buildTableHtml above) — Enter/Space here
  // mirrors the click handler exactly, gated to keys/targets that
  // originate from an actual row so a stray keydown bubbling from
  // elsewhere in the wrap isn't misinterpreted as a row activation.
  function handleTypeKeydown(e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    if (!e.target.closest('.breakdown-row')) return;
    e.preventDefault();
    activateType(e.target);
  }
  function handleGroupKeydown(e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    if (!e.target.closest('.breakdown-row')) return;
    e.preventDefault();
    activateGroup(e.target);
  }
</script>

<h3 class="refview-h">{title}</h3>

<section class="breakdown-block">
  <div class="breakdown-heading">Aircraft types</div>
  <p class="breakdown-desc">Breakdown of this country's traffic by aircraft type. Percentages are relative to the country's totals and sum to 100%.</p>
  {#if selectedType}
    {@html typeChart.html}
  {/if}
  {#if types.length > 0}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="breakdown-table-wrap" onclick={handleTypeClick} onkeydown={handleTypeKeydown}>
      {@html typeTableHtml}
    </div>
  {:else}
    <p class="ref-empty">No aircraft types in the current filters.</p>
  {/if}
</section>

<section class="breakdown-block">
  <div class="breakdown-heading">Role groups</div>
  <p class="breakdown-desc">How important this country is to each role. Percentages are relative to each role's fleet-wide total, not the country's &mdash; a high number means this country is critical to that role's global operations. Roles overlap, so columns do not sum to 100%.</p>
  {#if selectedGroup}
    {@html groupChart.html}
  {/if}
  {#if groups.length > 0}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="breakdown-table-wrap" onclick={handleGroupClick} onkeydown={handleGroupKeydown}>
      {@html groupTableHtml}
    </div>
  {:else}
    <p class="ref-empty">No role groups in the current filters.</p>
  {/if}
</section>

<style>
  .breakdown-block { margin-bottom: 30px; }
  .breakdown-block:last-child { margin-bottom: 0; }
  .breakdown-heading {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #888;
    margin-bottom: 8px;
  }
  .breakdown-desc {
    font-size: 11px;
    color: #888;
    margin: 0 0 8px;
  }
</style>
