<script>
  import { _ } from 'svelte-i18n';
  import { untrack } from 'svelte';
  import { legendCollapsed, toggleLegend } from '../stores/ui.js';

  let { levels = [], altColors = [],
        altBands = 16, altBandKft = 2.5,
        showFlights = true, showLandings = false,
        startCollapsed = false } = $props();

  // One collapsible pill+body on every layout (app.js initOnMapLegend);
  // style.css moves it top-left on phones to clear the toolbar and sheet.
  // The collapsed flag is the shared `legendCollapsed` store because the
  // toolbar's Legend button toggles the same thing. `startCollapsed` seeds
  // it once at mount (untrack: the mount call never changes the prop, and
  // this silences Svelte's state_referenced_locally warning).
  untrack(() => legendCollapsed.set(startCollapsed));

  const toggle = toggleLegend;

  const LANDINGS_SCALE = [
    { px: 8, label: '1' },
    { px: 18, label: '50' },
    { px: 28, label: '500+' },
  ];

  function altGradient() {
    return `linear-gradient(to right, ${altColors.join(', ')})`;
  }

  function altTicks() {
    const maxKft = altBands * altBandKft;
    const step = 10;
    const result = [];
    for (let kft = 0; kft <= maxKft; kft += step) {
      result.push({ kft, left: `${(kft / maxKft) * 100}%` });
    }
    return result;
  }
</script>

{#if levels.length > 0}
<!-- Sheet layout: top-left (bottom-left would collide with the toolbar and
     bar; top-right/top-centre hold the zoom control and the What-If
     pulldown), z 30 above the collapsed bar. From md up: bottom-left of the
     map beside the panel. Starts collapsed on the sheet layout
     (app.js initOnMapLegend) since the open body is sized for desktop. -->
<div id="onmap-legend" class:collapsed={$legendCollapsed} aria-live="polite"
     class="absolute z-30 top-[max(12px,env(safe-area-inset-top,0px))] left-[max(12px,env(safe-area-inset-left,0px))]
            md:top-auto md:bottom-4 md:left-[calc(var(--sidebar-width)_+_16px)] md:z-10">
  <button id="onmap-legend-pill" type="button"
    aria-expanded={!$legendCollapsed} aria-controls="onmap-legend-body"
    title={$legendCollapsed ? $_('legend.show') : $_('legend.hide')}
    onclick={toggle}>
    <span class="onmap-legend-pill-label">{$_('legend.title')}</span>
    <span class="caret" aria-hidden="true"><span class="caret-glyph">&#9662;</span></span>
  </button>
  <div id="onmap-legend-body" role="group" aria-label={$_('legend.aria_label')}>
    <div id="onmap-legend-tiers">
      <div class="legend-label">{$_('legend.countries')}</div>
      {#each levels as level}
        <div class="legend-tier">
          <span class="legend-swatch" style="background: linear-gradient({level.color}42, {level.color}42), #0a0a0a"></span>{level.label}
        </div>
      {/each}
    </div>
    <div id="onmap-legend-altitude" class="legend-group"
      style:display={showFlights ? '' : 'none'}>
      <div class="legend-label">{$_('legend.altitude')}</div>
      <div class="legend-bar" id="onmap-legend-alt-bar"
        style:background={altGradient()}></div>
      <div id="onmap-legend-alt-ticks">
        {#each altTicks() as tick}
          <span class="alt-tick" style:left={tick.left}>{tick.kft}</span>
        {/each}
      </div>
    </div>
    <div id="onmap-legend-landings" class="legend-group"
      style:display={showLandings ? '' : 'none'}>
      <div class="legend-label">{$_('legend.landings')}</div>
      <div id="onmap-legend-ground-scale">
        {#each LANDINGS_SCALE as s}
          <div class="ground-scale-item">
            <span class="ground-scale-dot" style:width="{s.px}px" style:height="{s.px}px"></span>
            <span class="ground-scale-label">{s.label}</span>
          </div>
        {/each}
      </div>
      <div class="legend-note">{$_('legend.landings_note')}</div>
    </div>
  </div>
</div>
{/if}
