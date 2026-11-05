<script>
  import { _ } from 'svelte-i18n';
  import ShareMenu from './ShareMenu.svelte';
  import ShowToggle from './ShowToggle.svelte';

  let { socialButtons = [], showNative = false,
        onCopyLink = null, onEmbed = null, onNativeShare = null, onMastodon = null,
        onInstagram = null, onFilters = null, filterBadge = '',
        showFlights = true, showLandings = false, showDisabled = false,
        onShowToggle = null } = $props();

  // mobile controls need breadcrumbs at least as much as desktop
  // ones — this is the layout with no hover, so an unlabelled control has
  // no other way to explain itself.
  const CRUMB_BY_LETTER = { C: 'a country', D: 'the date range', R: 'aircraft roles' };
  let filterCrumb = $derived(
    filterBadge
      ? 'Narrow what the map shows. Currently filtered by: '
        + [...filterBadge].map(c => CRUMB_BY_LETTER[c]).filter(Boolean).join(', ')
        + '. Changes apply on their own a few seconds after you stop.'
      : 'Narrow what the map shows by date, aircraft role, or theater connection. '
        + 'Changes apply on their own a few seconds after you stop.');
</script>

<!-- There is no Info button here: mobile users reach the reader via the
     table of contents in #info-section, by expanding the panel; that ToC
     lets them pick a *specific* section directly. -->
<ShareMenu {socialButtons} {showNative}
  {onCopyLink} {onEmbed} {onNativeShare} {onMastodon} {onInstagram} />

<!-- the same C/D/R badge the desktop trigger carries — a mobile
     user is at least as likely to leave a filter narrowed and forget. -->
<button id="mobile-filter-btn" type="button"
  data-breadcrumb={filterCrumb}
  onclick={onFilters}>{$_('filters.title')}{#if filterBadge}<span
    class="filter-badge">{filterBadge}</span>{/if} <span class="caret" aria-hidden="true">&#9662;</span></button>

<style>
  .filter-badge {
    margin-left: 4px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
    color: #cfe3f5;
  }
</style>

<ShowToggle {showFlights} {showLandings} disabled={showDisabled}
  showLabel={false} onToggle={onShowToggle} />
