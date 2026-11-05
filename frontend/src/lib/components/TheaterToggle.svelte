<script>
  import { _ } from 'svelte-i18n';
  import { staged } from '../stores/filters.js';

  const THEATER_OPTIONS = [
    { label: 'Tasking', value: 'tasking',
      tooltip: 'Flights on a mission chain that touches the Middle East theater.' },
    { label: 'Ever', value: 'ever',
      tooltip: 'Every flight of an aircraft seen in the theater at any point.' },
    { label: 'Off', value: 'off', tooltip: 'No theater filter.' },
  ];

  let { onTheaterHover = null } = $props();

  let mode = $derived($staged.filterMode === 'tasking' || $staged.filterMode === 'ever'
    ? $staged.filterMode : 'off');
  let excludedOnly = $derived(!$staged.filterInclude);

  function setMode(v) {
    if (v === 'off') {
      staged.update(s => ({ ...s, filterMode: 'none', filterInclude: true }));
    } else {
      staged.update(s => ({ ...s, filterMode: v }));
    }
  }

  function toggleExclude() {
    staged.update(s => ({ ...s, filterInclude: !s.filterInclude }));
  }
</script>

<div class="filter-subsection">
  <div class="filter-section-title" title='For more information, see the "Theater filter" section in the Methodology page.'>
    {$_('filters.by_theater')}
  </div>
  <div class="filter-row">
    <div class="filter-row-label">Theater</div>
    <div id="filter-theater-group" class="filter-toggle-group">
      {#each THEATER_OPTIONS as opt}
        <button
          class="toggle-btn"
          class:active={mode === opt.value}
          data-value={opt.value}
          title={opt.tooltip}
          onclick={() => setMode(opt.value)}
          onmouseenter={() => { if (opt.value === 'tasking' || opt.value === 'ever') onTheaterHover?.(true); }}
          onmouseleave={() => { if (opt.value === 'tasking' || opt.value === 'ever') onTheaterHover?.(false); }}
        >{opt.label}</button>
      {/each}
    </div>
  </div>
  {#if mode !== 'off'}
    <div class="filter-row">
      <div class="filter-row-label"></div>
      <div id="filter-inc-group" class="filter-toggle-group">
        <button
          class="toggle-btn"
          class:active={excludedOnly}
          aria-pressed={excludedOnly}
          title="Show only the flights this theater setting excludes."
          onclick={toggleExclude}
        >{$_('filters.show_excluded')}</button>
      </div>
    </div>
  {/if}
</div>

<style>
  /* .filter-subsection and .filter-section-title are styled globally in
     style.css, shared with DateRangePicker.svelte and RoleGroupPicker.svelte. */
  .filter-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 6px;
  }
  .filter-row-label {
    min-width: 60px;
    font-size: 12px;
    color: #8090a0;
  }
  .filter-toggle-group {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
  }
  /* Deliberately overrides the global .toggle-btn styling in style.css
     (used elsewhere by ShowToggle.svelte and whatif.js's window-group) with
     this component's own appearance. Kept as class="toggle-btn" rather than
     a distinct name because test/frontend selectors target it by that
     class within #filter-theater-group/#filter-inc-group. */
  .toggle-btn {
    padding: 4px 10px;
    background: #1a1e26;
    border: 1px solid #2a3040;
    border-radius: 4px;
    color: #7a8494;
    font-size: 12px;
    font-family: inherit;
    cursor: pointer;
    transition: all 0.15s;
  }
  .toggle-btn.active {
    background: #1a4a7b;
    border-color: #3a7abb;
    color: #e0eaf4;
  }
  .toggle-btn:hover:not(.active) {
    background: #222830;
    color: #b0b8c4;
  }
</style>
