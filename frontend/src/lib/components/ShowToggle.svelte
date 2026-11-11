<script>
  import { _ } from 'svelte-i18n';

  let { showFlights = true, showLandings = false, disabled = false,
        showLabel = true, onToggle = null } = $props();

  // The short version of the tour's flights-vs-landings
  // stage, for the majority who never run the tour. The asymmetry between
  // the two layers is the site's central argument and the toggle said
  // nothing about it.
  const FLIGHTS_CRUMB =
    'Show the flight paths. Flyover is spread across many countries \u2014 any one of '
    + 'them refusing could be substantially disruptive on its own.';
  const LANDINGS_CRUMB =
    'Show where aircraft touched down. Landings concentrate in a few hosting '
    + 'jurisdictions, which means runways, fuel and ground handling. Note the legal '
    + 'research on this site covers flyover permission only \u2014 landing and basing '
    + 'rights were not researched.';
</script>

<div id="show-subsection" class:disabled>
  {#if showLabel}
    <span class="show-label">{$_('show.label')}</span>
  {/if}
  <button class="toggle-btn" class:active={showFlights} class:disabled disabled={disabled}
    data-show="flights" data-breadcrumb={FLIGHTS_CRUMB}
    onclick={() => onToggle?.('flights', !showFlights)}>{$_('show.flights')}</button>
  <button class="toggle-btn" class:active={showLandings} class:disabled disabled={disabled}
    data-show="landings" data-breadcrumb={LANDINGS_CRUMB}
    onclick={() => onToggle?.('landings', !showLandings)}>{$_('show.ground')}</button>
</div>

<style>
</style>
