<script>
  import { tick } from 'svelte';

  let { resultsHtml = '', onResultsRendered = null } = $props();

  // Notify once per distinct results HTML, after it is in the DOM.
  let prevResultsHtml = '';
  $effect(() => {
    if (resultsHtml === prevResultsHtml) return;
    prevResultsHtml = resultsHtml;
    if (resultsHtml) tick().then(() => onResultsRendered?.());
  });
</script>

{#if resultsHtml}
  <div id="whatif-results">{@html resultsHtml}</div>
{/if}

<style>
  :global(.whatif-summary) {
    font-size: 13px;
    color: #ccc;
    margin: 12px 0 8px;
    line-height: 1.5;
  }
  :global(.whatif-summary p) {
    margin: 0 0 0.6em;
  }
  :global(.whatif-summary p:last-child) {
    margin-bottom: 0;
  }

  :global(#whatif-results .daily-bar-stack) {
    background: transparent;
    display: flex;
    flex-direction: column;
  }

  :global(.whatif-bar-prevented) {
    background: #e53935;
    flex: none;
  }

  :global(.whatif-bar-positioning) {
    background: #42a5f5;
    flex: none;
  }

  :global(.whatif-bar-ok) {
    background: #4caf50;
    flex: 1;
  }

  :global(.whatif-collapse-header) {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-top: 14px;
    margin-bottom: 4px;
  }

  :global(.whatif-collapse-toggle) {
    font-size: 11px;
    font-weight: 600;
    color: #4a8abf;
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
  }

  :global(.whatif-collapse-toggle:hover) {
    color: #6ab4f0;
  }

  :global(.whatif-collapse-label) {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #aaa;
  }

  :global(.whatif-collapsed) {
    display: none;
  }

  :global(.whatif-big-pct) {
    font-size: 36px;
    font-weight: 700;
    color: #fff;
    text-align: center;
    /* the caption below is part of this number's statement, so
       the gap under the digits closes and the pair's spacing moves to the
       caption's own bottom margin. */
    margin: 16px 0 2px;
    letter-spacing: 1px;
  }

  /* names the denominator at the number itself. This is the
     element that travels — the big percentage is what gets screenshotted —
     so it cannot rely on the summary paragraph above it for scope. */
  :global(.whatif-big-pct-caption) {
    font-size: 11px;
    color: #b0b0b0;
    text-align: center;
    margin: 0 0 10px;
    line-height: 1.4;
    padding: 0 8px;
  }
  :global(.whatif-big-pct-caption strong) {
    color: #e0e0e0;
    font-weight: 700;
  }

  :global(.whatif-role-row .stat-col),
  :global(.whatif-role-header .stat-col) {
    white-space: nowrap;
    font-size: 11px;
    min-width: 0;
  }

  :global(.whatif-role-row .stat-label) {
    min-width: 0;
    overflow-wrap: break-word;
  }

  :global(.whatif-disclaimer) {
    font-size: 11px;
    /* was #666 (~3.2:1 on the sidebar's near-black
     * background) — below WCAG AA's 4.5:1 floor for sub-12px text. */
    color: #9a9a9a;
    text-align: center;
    margin: 8px 0 12px;
    line-height: 1.4;
  }
</style>
