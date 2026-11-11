<script>
  import { _ } from 'svelte-i18n';
  import {
    dirty, resetStaged, resetAll, cancelAutoApply,
    autoApplyPending, pendingSeq, AUTO_APPLY_MS,
  } from '../stores/filters.js';

  // Filters commit themselves after a quiet period; this component states
  // what is about to happen and offers a way to skip the wait, without the
  // pressure of a pulsing Apply button.
  //
  // Three states the user can be in:
  //   settled  — applied === staged, nothing shown
  //   pending  — an edit is staged, countdown running (this component)
  //   applying — recompute in flight, covered by #updating-overlay
  let { onApply = null, onRevert = null, onReset = null } = $props();

  // Cancel the timer, then let the caller's own handler do the applying —
  // controls.js's onApply both applies and closes the filter view, which is
  // the right outcome for an explicit "I am done" click. Auto-apply, by
  // contrast, deliberately leaves the panel open: the user is still working.
  function applyNow() {
    cancelAutoApply();
    onApply?.();
  }

  function revert() {
    cancelAutoApply();
    resetStaged();
    onRevert?.();
  }

  function reset() {
    cancelAutoApply();
    resetAll();
    onReset?.();
  }
</script>

{#if $autoApplyPending}
  <div class="auto-apply-note" role="status" aria-live="polite">
    <div class="auto-apply-row">
      <span class="auto-apply-text">Applying your changes automatically…</span>
      <button type="button" class="btn-apply-now" onclick={applyNow}>Apply now</button>
    </div>
    <!-- Keyed on pendingSeq so each reschedule remounts the bar and
         restarts its animation from zero; a CSS animation costs nothing per
         frame here, where a JS countdown tick would not be free on a page
         already competing for frames. -->
    {#key $pendingSeq}
      <div class="auto-apply-bar" style="animation-duration:{AUTO_APPLY_MS}ms"></div>
    {/key}
  </div>
{/if}

<div class="filter-panel-actions">
  <button class="btn-revert-clone" disabled={!$dirty} onclick={revert}>{$_('filters.revert')}</button>
  <button class="btn-reset-clone" onclick={reset}>{$_('filters.reset')}</button>
</div>

<style>
  /* Informational, not an alarm: no pulse, no saturated fill. */
  .auto-apply-note {
    margin-top: 14px;
    padding: 8px 10px 9px;
    background: rgba(74, 138, 191, 0.10);
    border: 1px solid #2c4460;
    border-radius: 4px;
  }
  .auto-apply-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }
  .auto-apply-text {
    font-size: 11px;
    color: #b8cfe4;
    line-height: 1.3;
  }
  .btn-apply-now {
    flex-shrink: 0;
    padding: 3px 9px;
    font-family: inherit;
    font-size: 11px;
    font-weight: 600;
    color: #cfe3f5;
    background: rgba(74, 138, 191, 0.22);
    border: 1px solid #3a6a95;
    border-radius: 3px;
    cursor: pointer;
  }
  .btn-apply-now:hover { background: rgba(74, 138, 191, 0.36); color: #fff; }
  .btn-apply-now:focus-visible { outline: 2px solid #6ab4f0; outline-offset: 1px; }

  .auto-apply-bar {
    height: 2px;
    margin-top: 7px;
    background: #4a8abf;
    border-radius: 1px;
    transform-origin: left center;
    animation-name: auto-apply-countdown;
    animation-timing-function: linear;
    animation-fill-mode: forwards;
  }
  @keyframes auto-apply-countdown {
    from { transform: scaleX(1); }
    to   { transform: scaleX(0); }
  }
  @media (prefers-reduced-motion: reduce) {
    .auto-apply-bar { animation: none; transform: scaleX(1); opacity: 0.5; }
  }

  .filter-panel-actions {
    display: flex;
    gap: 8px;
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px solid #2a3040;
  }
  .filter-panel-actions button {
    flex: 1;
    padding: 5px 14px;
    font-size: 12px;
    font-family: inherit;
    border-radius: 4px;
    cursor: pointer;
    transition: all 0.12s;
    border: 1px solid #2a3040;
    background: #1a1e26;
    color: #8a94a0;
  }
  .filter-panel-actions button:hover:not(:disabled) {
    background: #222830;
    color: #c0c8d0;
    border-color: #3a4a5a;
  }
  .filter-panel-actions button:disabled {
    opacity: 0.35;
    cursor: default;
  }
</style>
