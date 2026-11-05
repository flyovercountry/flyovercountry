<script>
  import { _ } from 'svelte-i18n';
  import { dismissTransients } from '../stores/ui.js';

  let { onSaveImage = null, onExportCsv = null, onRectExport = null } = $props();

  let open = $state(false);
  let triggerEl = $state(null);
  let listEl = $state(null);

  function getItems() {
    if (!listEl) return [];
    return Array.from(listEl.querySelectorAll('[role="menuitem"]'));
  }

  function doOpen() { open = true; }
  // this dropdown's open state is local, so an outside
  // party (the tour) has no other way to shut it. Skips the store's
  // initial synchronous notification on subscribe.
  let seenFirstDismiss = false;
  dismissTransients.subscribe(() => {
    if (!seenFirstDismiss) { seenFirstDismiss = true; return; }
    if (open) close(false);
  });
  function close(refocus) {
    open = false;
    if (refocus && triggerEl) triggerEl.focus();
  }
  function toggle() { open ? close(false) : doOpen(); }

  function onDocClick(e) {
    if (triggerEl?.contains(e.target)) return;
    if (listEl?.contains(e.target)) return;
    close(false);
  }

  function onTriggerKeydown(e) {
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (!open) doOpen();
      setTimeout(() => getItems()[0]?.focus(), 0);
    } else if (e.key === "Escape") { close(true); }
  }

  function onItemKeydown(e, i) {
    const items = getItems();
    if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
    else if (e.key === "Escape") { e.preventDefault(); close(true); }
    else if (e.key === "Tab") { close(false); }
  }

  $effect(() => {
    if (!open) return;
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  });
</script>

<div id="export-menu" class="toolbar-dropdown export-menu">
  <button id="export-menu-trigger" type="button"
    aria-haspopup="true" aria-expanded={open}
    aria-controls="export-menu-list"
    title={$_('export.trigger_tooltip')}
    bind:this={triggerEl}
    onclick={() => toggle()}
    onkeydown={onTriggerKeydown}>
    {$_('nav.export')} <span class="caret" aria-hidden="true">&#9662;</span>
  </button>
  <div id="export-menu-list" role="menu" aria-label={$_('export.menu_aria')}
    hidden={!open} bind:this={listEl}>
    <button id="save-image-btn" role="menuitem" title={$_('export.save_image_tooltip')}
      onclick={() => { onSaveImage?.(); close(false); }}
      onkeydown={(e) => onItemKeydown(e, 0)}>{$_('nav.save_image')}</button>
    <button id="download-csv-btn" role="menuitem" title={$_('export.csv_tooltip')}
      onclick={() => { onExportCsv?.(); close(false); }}
      onkeydown={(e) => onItemKeydown(e, 1)}>{$_('nav.export_csv')}</button>
    <button id="rect-export-btn" role="menuitem"
      title={$_('export.rect_tooltip')}
      onclick={() => { onRectExport?.(); close(false); }}
      onkeydown={(e) => onItemKeydown(e, 2)}>{$_('nav.select_area')}</button>
  </div>
</div>

<style>
</style>
