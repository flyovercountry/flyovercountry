<script>
  import { _ } from 'svelte-i18n';
  import DateRangePicker from './DateRangePicker.svelte';
  import TheaterToggle from './TheaterToggle.svelte';
  import RoleGroupPicker from './RoleGroupPicker.svelte';
  import FilterActions from './FilterActions.svelte';
  import { whatif } from '../stores/whatif.js';

  // `readonly` is a caller-supplied snapshot (see controls.js filterPanelProps);
  // it's OR'd with the live whatif store so the panel also locks/unlocks
  // reactively if What-If mode is toggled *while this panel stays mounted*
  // (e.g. filters opened, then What-If entered via the map's Refuse? control).
  let {
    dateMin = '', dateMax = '', baseDate = '',
    roleGroups = [], roleLabel = (id) => `Role ${id}`,
    readonly = false,
    onApply = null, onRevert = null, onReset = null, onTheaterHover = null,
  } = $props();

  let locked = $derived(readonly || $whatif.active);
</script>

<div class="refview-filters" id="filters-section" class:whatif-readonly-panel={locked}>
  <div class="refview-h">{$_('filters.title')}</div>
  <DateRangePicker {dateMin} {dateMax} {baseDate} />
  <TheaterToggle {onTheaterHover} />
  <RoleGroupPicker {roleGroups} {roleLabel} />
  <FilterActions {onApply} {onRevert} {onReset} />
</div>

<style>
  .refview-filters {
    margin-top: 32px;
    padding: 16px;
    background: rgba(18, 22, 28, 0.85);
    border: 1px solid #2a3040;
    border-radius: 8px;
  }
  .refview-h {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #6a7a8a;
    margin-bottom: 14px;
    padding-bottom: 8px;
    border-bottom: 1px solid #2a3040;
  }
  .whatif-readonly-panel {
    opacity: 0.35;
    pointer-events: none;
  }
</style>
