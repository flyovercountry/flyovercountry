<script>
  import { _ } from 'svelte-i18n';
  import { domainFromUrl } from '../../panel-data.js';

  // A single section passes `paragraphs`; the merged Refusal-vs-Cooperation
  // entry passes `parts` ({ heading, paragraphs } each) sharing one
  // citations list. reference-view.js sets exactly one of them.
  let { title = '', paragraphs = [], parts = null, citations = [], idPrefix = '',
        activeCite = null, activeMarkerOrd = null,
        onCiteMarkerClick = null, onCiteEntryClick = null,
        onBackToText = null } = $props();

  const reduceMotion = () =>
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let proseEl = $state(null);
  let flashOrd = $state(null);

  $effect(() => {
    if (activeCite != null && activeCite !== flashOrd) {
      flashOrd = activeCite;
      if (!reduceMotion()) {
        requestAnimationFrame(() => {
          const el = proseEl?.querySelector(`#${CSS.escape(`${idPrefix}-${activeCite}`)}`);
          if (el) {
            el.classList.add("cite-flash");
            el.addEventListener("animationend", () => el.classList.remove("cite-flash"), { once: true });
            const bodyEl = document.getElementById("refview-body");
            if (bodyEl) {
              const r = el.getBoundingClientRect();
              const br = bodyEl.getBoundingClientRect();
              if (r.top < br.top || r.bottom > br.bottom) {
                el.scrollIntoView({ block: "center", behavior: "smooth" });
              }
            }
            el.focus({ preventScroll: true });
          }
        });
      }
    } else if (activeCite == null) {
      flashOrd = null;
    }
  });

  function handleClick(e) {
    const marker = e.target.closest('.cite-marker');
    if (marker) {
      e.preventDefault();
      onCiteMarkerClick?.(marker);
      return;
    }
    const entry = e.target.closest('.cite-entry');
    if (entry && entry.classList.contains("cite-selected")) {
      onCiteEntryClick?.();
    }
  }

  function handleBackToText() {
    if (activeMarkerOrd != null) {
      const marker = proseEl?.querySelector(`.cite-marker[data-target="${idPrefix}-${activeMarkerOrd}"]`);
      if (marker) {
        marker.scrollIntoView({ block: "center", behavior: reduceMotion() ? "auto" : "smooth" });
        marker.focus?.({ preventScroll: true });
      }
    }
    onBackToText?.();
  }
</script>

<h3 class="refview-h">{title}</h3>
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="refview-prose" bind:this={proseEl} onclick={handleClick} onkeydown={null}>
  {#if parts}
    {#each parts as part}
      <h4 class="refview-subh">{part.heading}</h4>
      {#each part.paragraphs as para}
        <p>{@html para.html}</p>
      {/each}
    {/each}
  {:else}
    {#each paragraphs as para}
      <p>{@html para.html}</p>
    {/each}
  {/if}
  {#if citations.length > 0}
    <div class="ref-citations">
      <h4 class="ref-citations-title">{$_('refview.sources')}</h4>
      <ol class="cite-list">
        {#each citations as cite}
          <li class="cite-entry" id={cite.id} data-ord={cite.ord} tabindex="-1"
              class:cite-selected={activeCite === cite.ord}>
            {#if cite.url}
              <a class="cite-row cite-row-link" href={cite.url} target="_blank" rel="noopener">
                <span class="cite-badge">{cite.ord}</span>
                <span class="cite-title">{cite.title}</span>
                <span class="cite-domain">{domainFromUrl(cite.url)}</span>
              </a>
            {:else}
              <div class="cite-row">
                <span class="cite-badge">{cite.ord}</span>
                <span class="cite-title">{cite.title}</span>
              </div>
            {/if}
            {#if activeCite === cite.ord && activeMarkerOrd != null}
              <button type="button" class="cite-back" onclick={(e) => { e.stopPropagation(); handleBackToText(); }}>
                {$_('refview.back_to_text')}
              </button>
            {/if}
          </li>
        {/each}
      </ol>
    </div>
  {/if}
</div>

<style>
  /* Citation list: each entry is a full-width clickable block when it has
     a URL — the <a> is the outermost interactive element so the browser's
     native "Copy Link" context menu works. Entries without a URL fall back
     to a plain, non-interactive row. .cite-entry/.cite-selected/.cite-flash
     class names and the flash keyframe below intentionally match the
     former global rules (removed from style.css) — handleClick and the
     $effect flash logic in <script> target these classes directly. */
  .cite-list { list-style: none; padding: 0; margin: 0; }
  .cite-entry {
    padding: 0;
    border-bottom: 1px solid #232323;
    border-radius: 4px;
    overflow: hidden;
  }

  .cite-row {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    column-gap: 10px;
    row-gap: 2px;
    padding: 10px 10px;
    color: #cfd3d8;
    text-decoration: none;
    border-radius: 4px;
    transition: background 0.12s ease;
  }

  .cite-row-link:hover,
  .cite-row-link:focus-visible {
    background: rgba(106, 180, 240, 0.08);
  }

  .cite-row-link:focus-visible {
    outline: 2px solid #6ab4f0;
    outline-offset: -2px;
  }

  .cite-badge {
    display: inline-block;
    min-width: 20px;
    height: 20px;
    line-height: 20px;
    text-align: center;
    background: #24405c;
    color: #7ec0ff;
    border-radius: 3px;
    font-size: 12px;
    font-weight: 700;
    flex: 0 0 auto;
  }

  .cite-title { color: #d6dade; flex: 1 1 200px; min-width: 0; }

  .cite-domain {
    color: #7c8792;
    font-size: 11px;
    white-space: nowrap;
    flex: 0 0 auto;
    margin-left: auto;
  }

  /* Rendered as a sibling of .cite-row, not nested inside the <a>, so a
     <button> never ends up inside an <a> (invalid nesting, and it would
     make right-click "Copy Link" ambiguous). */
  .cite-back {
    display: block;
    margin: 0 10px 10px 38px;
    padding: 2px 8px;
    background: transparent;
    color: #7ec0ff;
    border: 1px solid #3a5878;
    border-radius: 3px;
    font-size: 12px;
    cursor: pointer;
  }

  .cite-entry.cite-selected { background: #14283c; outline: 2px solid #4a9adb; outline-offset: -2px; }
  /* cite-flash is added/removed via classList in <script>, not the class:
     directive, so Svelte's unused-CSS-selector pruning can't see it's used —
     :global() opts this rule out of that analysis. */
  .cite-entry:global(.cite-flash) { animation: cite-flash 1s ease-out; }
  @keyframes cite-flash { from { background: #2a5a8c; } to { background: #14283c; } }

  .ref-citations { margin-top: 30px; border-top: 1px solid #282828; padding-top: 16px; }
  .ref-citations-title {
    color: #9aa0a8;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .05em;
    margin: 0 0 10px;
  }

  @media (prefers-reduced-motion: reduce) {
    .cite-entry:global(.cite-flash) { animation: none; }
  }
</style>
