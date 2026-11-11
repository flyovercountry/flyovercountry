<script>
  import { _ } from 'svelte-i18n';
  import { findCountryClassification } from '../../panel-data.js';
  import BackgroundTab from './BackgroundTab.svelte';
  import SectionTab from './SectionTab.svelte';
  import GlossaryTab from './GlossaryTab.svelte';
  import StatsBreakdownTab from './StatsBreakdownTab.svelte';
  import RolesTab from './RolesTab.svelte';

  // A display surface for the entry chosen in the panel's table of
  // contents; `activeLabel` names it in the header. `cc` is null for the
  // global entries, and the header then omits the country name.
  let { open = false, cc = null, activeLabel = '',
        tabKind = null,
        backgroundSections = null, sectionProps = null,
        globalHtml = null, glossaryTerms = null, globalLoaded = false,
        breakdownProps = null, rolesProps = null,
        onClose = null,
        onCiteMarkerClick = null, onCiteEntryClick = null,
        onBackToText = null } = $props();

  function handleClose() { onClose?.(); }

  function handleProseClick(e) {
    const a = e.target.closest('a[href^="#"]');
    if (!a) return;
    const id = a.getAttribute('href').slice(1);
    const target = document.getElementById(id);
    if (target) {
      e.preventDefault();
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function handleKeydown(e) {
    if (e.key === "Escape" && open) handleClose();
  }

  $effect(() => {
    if (open) document.body.classList.add("refview-open");
    else document.body.classList.remove("refview-open");
  });

  $effect(() => {
    if (!open || tabKind !== 'methodology') return;
    const prose = document.querySelector('.methodology-prose');
    if (!prose) return;
    const svg = prose.querySelector('.pipeline-diagram-wrap svg');
    if (!svg) return;
    const nodes = prose.querySelectorAll('.stage-link, .data-node');
    const cleanups = [];
    for (const el of nodes) {
      function onEnter() {
        const clone = el.cloneNode(true);
        clone.classList.add('hover-clone');
        clone.style.pointerEvents = 'none';
        clone.setAttribute('data-hover-clone', '');
        const rect = clone.querySelector('rect');
        if (rect) {
          rect.setAttribute('filter', 'brightness(2) saturate(1.5)');
          rect.setAttribute('stroke-width', '3');
        }
        svg.appendChild(clone);
        el._hoverClone = clone;
      }
      function onLeave() {
        if (el._hoverClone) {
          el._hoverClone.remove();
          el._hoverClone = null;
        }
      }
      el.addEventListener('mouseenter', onEnter);
      el.addEventListener('mouseleave', onLeave);
      cleanups.push(() => {
        el.removeEventListener('mouseenter', onEnter);
        el.removeEventListener('mouseleave', onLeave);
        if (el._hoverClone) { el._hoverClone.remove(); el._hoverClone = null; }
      });
    }
    return () => cleanups.forEach(fn => fn());
  });

  let countryName = $derived(cc ? (findCountryClassification(cc)?.name || cc) : '');
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- Full-viewport reader below md; from md up it sits beside the panel in
     #map's box (same left offset) so the ToC stays clickable while open. -->
<div id="reference-view" class:hidden={!open}
     class="fixed inset-y-0 right-0 left-0 z-[2000] flex flex-col md:left-[var(--sidebar-width)]
            max-md:pt-[var(--sai-top)] max-md:pb-[var(--sai-bottom)]"
     role="region" aria-label={activeLabel || $_('refview.dialog_aria')}>
  <div class="refview-header">
    <div class="refview-title" id="refview-title">
      <span class="refview-country">{countryName}</span>
      {#if activeLabel}<span class="refview-section-name">{activeLabel}</span>{/if}
    </div>
    <button id="refview-close" class="refview-close" type="button"
      title={$_('refview.close_title')} aria-label={$_('refview.close_aria')}
      onclick={handleClose}>{$_('refview.close_symbol')}</button>
  </div>
  <div class="refview-body" id="refview-body">
    <div class="refview-content">
      {#if tabKind === 'background' && backgroundSections}
        <BackgroundTab sections={backgroundSections} />
      {:else if (tabKind === 'section' || tabKind === 'combined-section') && sectionProps}
        <SectionTab
          title={sectionProps.title}
          paragraphs={sectionProps.paragraphs}
          parts={sectionProps.parts}
          citations={sectionProps.citations}
          idPrefix={sectionProps.idPrefix}
          activeCite={sectionProps.activeCite}
          activeMarkerOrd={sectionProps.activeMarkerOrd}
          onCiteMarkerClick={onCiteMarkerClick}
          onCiteEntryClick={onCiteEntryClick}
          onBackToText={onBackToText} />
      {:else if tabKind === 'global-stats'}
        <h3 class="refview-h">{activeLabel}</h3>
        {#if globalHtml}
          <div class="refview-prose global-stats-prose">{@html globalHtml}</div>
        {:else}
          <p class="ref-empty">No statistics available.</p>
        {/if}
      {:else if tabKind === 'methodology'}
        <h3 class="refview-h">{activeLabel}</h3>
        {#if globalHtml}
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <div class="refview-prose methodology-prose" role="document"
               onclick={handleProseClick}>{@html globalHtml}</div>
        {:else if globalLoaded}
          <p class="ref-empty">{$_('refview.no_methodology')}</p>
        {/if}
      {:else if tabKind === 'glossary' && globalLoaded}
        <GlossaryTab title={activeLabel} terms={glossaryTerms || []} />
      {:else if tabKind === 'stats-breakdown' && breakdownProps}
        <StatsBreakdownTab
          title={breakdownProps.title}
          types={breakdownProps.types}
          groups={breakdownProps.groups}
          dates={breakdownProps.dates}
          countryTotals={breakdownProps.countryTotals} />
      {:else if tabKind === 'roles'}
        <!-- rolesProps is null until the taxonomy fetch lands;
             the component renders its own loading line rather than the
             reader flashing an empty body. -->
        <RolesTab
          data={rolesProps?.data ?? null}
          counts={rolesProps?.counts ?? null}
          contactUrl={rolesProps?.contactUrl ?? ''} />
      {/if}
    </div>
  </div>
</div>

<style>
</style>
