<script>
  import { _ } from 'svelte-i18n';

  // `terms` is global-content.js's [{term, definitionHtml}], parsed from
  // methodology.html's glossary section.
  let { title = '', terms = [] } = $props();
</script>

<h3 class="refview-h">{title}</h3>
<dl class="glossary-list">
  {#each terms as t}
    <div class="glossary-entry">
      <dt class="glossary-dt">{t.term}</dt>
      <dd class="glossary-dd">{@html t.definitionHtml}</dd>
    </div>
  {/each}
</dl>
{#if terms.length === 0}
  <p class="ref-empty">{$_('refview.no_glossary')}</p>
{/if}

<style>
</style>
