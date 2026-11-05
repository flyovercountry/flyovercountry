<script>
  import { _ } from 'svelte-i18n';
  import { domainFromUrl } from '../../panel-data.js';

  let { sections = [] } = $props();

  function splitParagraphs(text) {
    return String(text).split(/\n\n+/).filter(p => p.trim());
  }
</script>

{#if sections.length === 0}
  <p class="ref-empty">{$_('refview.no_background')}</p>
{:else}
  {#each sections as section}
    {#if section.heading}
      <div class="info-heading">{section.heading}</div>
    {/if}
    {#each splitParagraphs(section.text) as para}
      <p>{para}</p>
    {/each}
    {#if section.sources?.length}
      <div class="country-sources">
        {#each section.sources as src}
          {@const domain = domainFromUrl(src.url)}
          <a class="source-block" href={src.url} target="_blank" rel="noopener">
            <span class="source-title">{src.title || src.url}</span>
            {#if src.title && src.title !== domain}
              <span class="source-domain">{domain}</span>
            {/if}
          </a>
        {/each}
      </div>
    {/if}
  {/each}
{/if}

<style>
  /* Shares its class name with panel-data.js's renderFactSection, which
     builds an unrelated, unscoped .country-sources block of bare <a> tags
     (styled by style.css's own global .country-sources rule) for a
     different reader entry — a naming collision between two independently
     live consumers, not a stale duplicate of this rule. */
  .country-sources {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: 4px 0 14px;
  }

  .source-block {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    padding: 9px 12px;
    border: 1px solid #282828;
    border-radius: 6px;
    text-decoration: none;
    background: transparent;
    transition: background 0.12s ease, border-color 0.12s ease;
  }

  .source-block:hover,
  .source-block:focus-visible {
    background: rgba(106, 180, 240, 0.08);
    border-color: #3a5878;
  }

  .source-block:focus-visible {
    outline: 2px solid #6ab4f0;
    outline-offset: -2px;
  }

  .source-title {
    color: #cfd3d8;
    font-size: 13px;
    line-height: 1.4;
  }

  .source-domain {
    color: #7c8792;
    font-size: 11px;
    white-space: nowrap;
    flex: 0 0 auto;
  }
</style>
