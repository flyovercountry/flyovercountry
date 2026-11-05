<script>
  import { _ } from 'svelte-i18n';
  import { whatif } from '../stores/whatif.js';
  import { dayToDateStr } from '../../data.js';

  let { onExpand = null, onCollapse = null, onDateChange = null } = $props();
</script>

{#if $whatif.eligible}
  <div id="whatif-pulldown" class:expanded={$whatif.expanded}>
    {#if !$whatif.expanded}
      <button type="button" id="whatif-pulldown-pill"
        onclick={() => onExpand?.()}>
        Refuse? <span class="caret">&#9662;</span>
      </button>
    {:else}
      <div id="whatif-pulldown-body">
        <label id="whatif-pulldown-label" for="whatif-date-input">
          Refuse overflight from:
        </label>
        <input
          type="date"
          id="whatif-date-input"
          min={dayToDateStr($whatif.minDay)}
          max={dayToDateStr($whatif.maxDay)}
          value={dayToDateStr($whatif.denialDay)}
          onchange={(e) => onDateChange?.(e.target.value)}
        />
        <button type="button" id="whatif-pulldown-collapse"
          aria-label="Return to normal mode"
          onclick={() => onCollapse?.()}>{$_('sidebar.whatif_close_symbol')}</button>
      </div>
    {/if}
  </div>
{/if}
