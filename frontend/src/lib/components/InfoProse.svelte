<script>
  // The country headline + prose preview, a separate component from
  // InfoSection (the ToC) so ContentPanel can place it below the stats, the
  // table of contents and the footer buttons: the prose is unbounded, and
  // the controls that navigate it must not be pushed off the panel by it.
  // buildInfoHtml() is called once per render, here only.
  import { panelData } from '../stores/panel.js';
  import { buildInfoHtml } from '../../panel-data.js';

  let cc = $derived($panelData.cc);
  let whatIfActive = $derived($panelData.whatIfActive);
  let refreshTick = $derived($panelData.refreshTick);

  let infoResult = $derived(whatIfActive ? null : buildInfoHtml(cc, refreshTick));
  let headline = $derived(infoResult?.headlineHtml || '');
  let preview = $derived(infoResult?.previewHtml || '');
</script>

{#if headline || preview}
  <div class="info-prose">
    {#if headline}
      <div class="country-headline">{@html headline}</div>
    {/if}
    {#if preview}
      <div class="info-preview">{@html preview}</div>
    {/if}
  </div>
{/if}

<style>
  /* A quiet rule above the prose marks it as the end of the panel's
     interactive content rather than a continuation of the footer. */
  .info-prose {
    border-top: 1px solid #262626;
    padding-top: 12px;
    margin-top: 4px;
  }
  .country-headline {
    font-size: 13px;
    line-height: 1.45;
    color: #e0e0e0;
    background: rgba(255, 255, 255, 0.06);
    border-left: 3px solid #4a8abf;
    padding: 8px 10px;
    margin-bottom: 10px;
  }
  .country-headline :global(p) { margin: 0 0 0.6em; }
  .country-headline :global(p:last-child) { margin-bottom: 0; }
  .country-headline :global(.headline-footnote) { font-size: 11px; color: #9a9a9a; }
  .country-headline :global(a) { color: #6ab4f0; text-decoration: none; }
  .country-headline :global(a:hover) { text-decoration: underline; }
  .info-preview {
    font-size: 12px;
    line-height: 1.5;
    color: #aaa;
  }
</style>
