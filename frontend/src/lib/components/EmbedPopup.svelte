<script>
  import { _ } from 'svelte-i18n';

  let { open = false, snippet = '', onClose = null } = $props();

  let popupEl = $state(null);
  let taEl = $state(null);
  let copied = $state(false);

  function handleCopy() {
    navigator.clipboard.writeText(snippet);
    copied = true;
  }

  function onDocClick(e) {
    const embedBtn = document.getElementById("embed-btn");
    if (popupEl?.contains(e.target)) return;
    if (e.target === embedBtn) return;
    onClose?.();
  }

  $effect(() => {
    if (!open) return;
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  });

  $effect(() => {
    if (!open || !taEl) return;
    taEl.focus();
    taEl.select();
  });

  $effect(() => {
    if (!copied) return;
    const t = setTimeout(() => { copied = false; }, 1500);
    return () => clearTimeout(t);
  });
</script>

{#if open}
  <div id="embed-popup" bind:this={popupEl}>
    <textarea readonly bind:this={taEl} value={snippet}></textarea>
    <div class="embed-actions">
      <span class="embed-hint">{$_('embed.hint')}</span>
      <button id="embed-copy" type="button" onclick={handleCopy}>{copied ? $_('embed.copied') : $_('embed.copy')}</button>
    </div>
  </div>
{/if}

<style>
</style>
