<script>
  import { _ } from 'svelte-i18n';
  import { panelData } from '../stores/panel.js';
  import { findCountryClassification } from '../../panel-data.js';
  import { generalTabsFor, GLOBAL_TAB_KINDS } from '../../global-content.js';
  import { readerState, readerTabsFor } from '../stores/reader.js';

  let { onSectionSelect = null } = $props();

  let cc = $derived($panelData.cc);
  let whatIfActive = $derived($panelData.whatIfActive);

  // The table of contents is the only way into the reader. No country: the
  // global entries. A country: its entries, then the general ones
  // (methodology, glossary). What-If active: the general ones only. The
  // list comes from readerTabsFor(), which openReader() resolves slugs
  // against, so the ToC cannot offer an entry the reader lacks.
  let toc = $derived(whatIfActive
    ? generalTabsFor()
    : readerTabsFor(cc).filter(t => t.kind !== 'stats-breakdown'));
  let readerOpen = $derived($readerState.open && $readerState.cc === cc);
  let activeSlug = $derived(readerOpen ? $readerState.slug : null);

  // The AI-research disclaimer sits above the first researched entry
  // ("section"/"combined-section"); the hand-written Summary, when
  // present, comes before it.
  let firstResearchedIdx = $derived(toc.findIndex(t => t.kind === 'section' || t.kind === 'combined-section'));

  // The "General (not specific to X)" heading marks where the general run
  // begins, and only when country entries precede it.
  let firstGeneralIdx = $derived(cc ? toc.findIndex(t => GLOBAL_TAB_KINDS.has(t.kind)) : -1);
  let countryName = $derived(cc ? (findCountryClassification(cc)?.name || cc) : '');
  let showGeneralHeading = $derived(firstGeneralIdx > 0 && !!countryName);

  function handleTocClick(slug) { onSectionSelect?.(slug); }
</script>

<div class="section-header">
  <span class="section-label">{$_('sidebar.info')}</span>
</div>
{#if toc.length > 0}
  <nav class="toc-list" aria-label={$_(cc ? 'refview.toc_aria' : 'refview.toc_aria_global')}>
    {#each toc as item, i (item.slug)}
      {#if i === firstResearchedIdx}
        <p class="toc-disclaimer">{$_('refview.ai_research_disclaimer')}</p>
      {/if}
      {#if showGeneralHeading && i === firstGeneralIdx}
        <p class="toc-general-heading">General (not specific to {countryName}):</p>
      {/if}
      <button type="button" class="toc-item" class:active={activeSlug === item.slug}
        data-slug={item.slug}
        aria-current={activeSlug === item.slug ? 'true' : undefined}
        onclick={() => handleTocClick(item.slug)}>
        <span class="toc-item-label">{item.label}</span>
        <span class="toc-item-chevron" aria-hidden="true">&rsaquo;</span>
      </button>
    {/each}
  </nav>
{/if}

<style>
  /* .section-header is styled globally in style.css — ContentPanel.svelte's
     whatif-section header also needs it, outside this component's scope. */
  .toc-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-top: 4px;
  }
  .toc-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    width: 100%;
    padding: 8px 10px;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid transparent;
    border-radius: 4px;
    color: #cfd3d8;
    font-size: 13px;
    font-family: inherit;
    text-align: left;
    cursor: pointer;
  }
  .toc-item:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
  .toc-item:focus-visible { outline: 2px solid #6ab4f0; outline-offset: -2px; }
  .toc-item.active {
    background: rgba(74, 154, 219, 0.14);
    border-color: #3a6a95;
    color: #6ab4f0;
  }
  .toc-item-label { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .toc-item-chevron { flex-shrink: 0; color: #888; font-size: 15px; line-height: 1; }
  .toc-item.active .toc-item-chevron { color: #6ab4f0; }
  /* subordinate to the ToC items it introduces — smaller, dimmer,
     no interactive affordance (not a button, no hover/focus state). */
  .toc-disclaimer {
    margin: 6px 2px 2px;
    font-size: 10px;
    line-height: 1.4;
    /* was #767676 (~4.0:1 on this section's #151515
     * background) — below WCAG AA's 4.5:1 floor for sub-12px text. */
    color: #9a9a9a;
    font-style: italic;
  }
  /* Same visual weight as .toc-disclaimer — it is a label for the run
     below it, not a control. Upright rather than italic so it does not
     read as a second disclaimer. */
  .toc-general-heading {
    margin: 10px 2px 2px;
    font-size: 10px;
    line-height: 1.4;
    color: #9a9a9a;
  }
</style>
