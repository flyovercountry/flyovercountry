<script>
  import { _ } from 'svelte-i18n';
  import { staged } from '../stores/filters.js';
  import { dayToDateStr } from '../../data.js';

  let { dateMin = '', dateMax = '', baseDate = '' } = $props();

  function dateToDay(dateStr) {
    const base = new Date(baseDate + 'T00:00:00Z');
    const target = new Date(dateStr + 'T00:00:00Z');
    return Math.round((target - base) / 86400000);
  }

  let startVal = $derived($staged.dayMin === 0 ? dateMin : dayToDateStr($staged.dayMin));
  let endVal = $derived($staged.dayMax === Infinity ? dateMax : dayToDateStr($staged.dayMax));

  // A native <input type="date"> only ever exposes `.value` as "" or a
  // real YYYY-MM-DD calendar date (the browser's own date-parsing rejects
  // anything else before it reaches `.value`/`change` at all) — but it
  // does NOT stop a cleared field (empty string) or a keyboard-typed date
  // outside [dateMin, dateMax] from firing `change` (min/max constrain the
  // picker UI, not typed text). Returns null for the former (caller
  // ignores it and restores the input), and clamps the latter into range,
  // matching the input's own min/max attributes — the same defense used
  // for the What If date input (whatif.js's whatIfSetDate).
  function clampedDay(value) {
    if (!value) return null;
    const clamped = value < dateMin ? dateMin : value > dateMax ? dateMax : value;
    const day = dateToDay(clamped);
    return Number.isNaN(day) ? null : day;
  }

  function onStartChange(e) {
    const day = clampedDay(e.target.value);
    if (day === null) { e.target.value = startVal; return; }
    staged.update(s => ({ ...s, dayMin: day }));
  }

  function onEndChange(e) {
    const day = clampedDay(e.target.value);
    if (day === null) { e.target.value = endVal; return; }
    staged.update(s => ({ ...s, dayMax: day }));
  }
</script>

<div class="filter-subsection">
  <div class="filter-section-title">{$_('filters.by_date')}</div>
  <div class="filter-dates-row">
    <input type="date" class="filter-date" value={startVal} min={dateMin} max={dateMax} onchange={onStartChange}>
    <span class="date-arrow">{$_('filters.date_arrow')}</span>
    <input type="date" class="filter-date" value={endVal} min={dateMin} max={dateMax} onchange={onEndChange}>
  </div>
</div>

<style>
  /* .filter-subsection and .filter-section-title are styled globally in
     style.css, shared with TheaterToggle.svelte and RoleGroupPicker.svelte. */
  .filter-dates-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .date-arrow {
    color: #556;
    font-size: 14px;
    flex-shrink: 0;
  }
  .filter-date {
    flex: 1;
    min-width: 0;
    background: #181c22;
    color: #bcc4cc;
    border: 1px solid #2a3040;
    border-radius: 4px;
    padding: 5px 8px;
    font-family: inherit;
    font-size: 13px;
  }
  .filter-date:focus {
    border-color: #4a8abb;
    outline: none;
  }
  .filter-date::-webkit-calendar-picker-indicator {
    filter: invert(0.7);
  }
</style>
