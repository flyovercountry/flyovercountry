<script>
  import { _ } from 'svelte-i18n';
  import { dismissTransients } from '../stores/ui.js';
  import { sanitizeInstance, updateShareTargets } from '../../share-links.js';

  let { onCopyLink = null, onEmbed = null, onNativeShare = null, onMastodon = null,
        onInstagram = null, showNative = false, socialButtons = [] } = $props();

  let open = $state(false);
  let triggerEl = $state(null);
  let listEl = $state(null);
  let mastodonFormOpen = $state(false);
  let mastodonHost = $state(
    (typeof localStorage !== 'undefined' && localStorage.getItem('mastodon-instance')) || ''
  );
  let mastodonInputEl = $state(null);

  function getItems() {
    if (!listEl) return [];
    return Array.from(listEl.querySelectorAll('[role="menuitem"]')).filter(el => !el.hidden);
  }

  function doOpen() {
    // Refresh the [data-sharer] attributes on open so every country, view
    // and What-If change is covered here rather than at each call site. No
    // syncUrl flush: every applied state change already synced the URL,
    // and a staged filter edit is correctly not in it yet.
    updateShareTargets();
    open = true;
  }

  // The open state is local; the tour closes it through this signal. The
  // store's initial notification on subscribe is skipped.
  let seenFirstDismiss = false;
  dismissTransients.subscribe(() => {
    if (!seenFirstDismiss) { seenFirstDismiss = true; return; }
    if (open) close(false);
  });
  function close(refocus) {
    open = false;
    mastodonFormOpen = false;
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

  function toggleMastodonForm() {
    mastodonFormOpen = !mastodonFormOpen;
    if (mastodonFormOpen) {
      setTimeout(() => mastodonInputEl?.focus(), 0);
    }
  }

  function submitMastodon() {
    const host = sanitizeInstance(mastodonHost);
    if (!host) return;
    mastodonHost = host;
    try { localStorage.setItem('mastodon-instance', host); } catch {}
    onMastodon?.(host);
    mastodonFormOpen = false;
    close(false);
  }

  function onMastodonInputKeydown(e) {
    if (e.key === "Enter") { e.preventDefault(); submitMastodon(); }
    else if (e.key === "Escape") { e.preventDefault(); mastodonFormOpen = false; }
  }

  $effect(() => {
    if (!open) return;
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  });
</script>

<div id="share-menu" class="toolbar-dropdown share-menu" class:has-native-share={showNative}>
  <button id="share-menu-trigger" class="toolbar-primary" type="button"
    aria-haspopup="true" aria-expanded={open}
    aria-controls="share-menu-list"
    title={$_('share.trigger_tooltip')}
    bind:this={triggerEl}
    onclick={() => toggle()}
    onkeydown={onTriggerKeydown}>
    {$_('nav.share')} <span class="caret" aria-hidden="true">&#9662;</span>
  </button>
  <div id="share-menu-list" role="menu" aria-label={$_('share.menu_aria')}
    hidden={!open} bind:this={listEl}>
    <div class="share-sheet-handle" aria-hidden="true"></div>
    <div class="share-sheet-title" aria-hidden="true">{$_('nav.share')}</div>
    <!-- Found while wiring up item 3's clipboard-failure feedback: every
         other menuitem below calls close(false) right after its handler,
         but that closes (`hidden`s) this whole list synchronously, before
         onCopyLink's async navigator.clipboard.writeText().then()/.catch()
         ever resolves -- so the "Link copied!"/failure text it sets on
         this button was already being rendered into a hidden, display:none
         element and was never actually visible. Left open here so the
         feedback can be seen; it still closes normally via outside click,
         Escape, or Cancel. -->
    <button id="share-btn" role="menuitem" title={$_('share.copy_link_tooltip')}
      onclick={() => onCopyLink?.()}
      onkeydown={(e) => onItemKeydown(e, 0)}>
      <svg class="share-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M16 1H4a2 2 0 0 0-2 2v14h2V3h12V1zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H8V7h11v14z"/></svg>
      {$_('share.copy_link')}
    </button>
    <button id="embed-btn" role="menuitem" title={$_('share.embed_tooltip')}
      onclick={() => { onEmbed?.(); close(false); }}
      onkeydown={(e) => onItemKeydown(e, 1)}>
      <svg class="share-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M9.4 16.6 4.8 12l4.6-4.6L8 6l-6 6 6 6 1.4-1.4zm5.2 0L19.2 12l-4.6-4.6L16 6l6 6-6 6-1.4-1.4z"/></svg>
      {$_('share.embed')}
    </button>
    <button id="native-share-btn" role="menuitem" title={$_('share.native_tooltip')}
      hidden={!showNative}
      onclick={() => { onNativeShare?.(); close(false); }}
      onkeydown={(e) => onItemKeydown(e, 2)}>
      <svg class="share-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M18 16c-.8 0-1.4.3-2 .8l-7.1-4.2c.1-.2.1-.4.1-.6s0-.4-.1-.6L16 7.2c.5.5 1.2.8 2 .8 1.7 0 3-1.3 3-3s-1.3-3-3-3-3 1.3-3 3c0 .2 0 .4.1.6L8 9.8C7.5 9.3 6.8 9 6 9c-1.7 0-3 1.3-3 3s1.3 3 3 3c.8 0 1.5-.3 2-.8l7.1 4.2c-.1.2-.1.3-.1.6 0 1.6 1.3 2.9 2.9 2.9s2.9-1.3 2.9-2.9-1.2-3-2.8-3z"/></svg>
      {$_('share.native')}
    </button>
    <div class="share-menu-sep" role="separator"></div>
    <div class="share-menu-social">
      {#each socialButtons as btn, i}
        <button id={btn.id} role="menuitem" data-sharer={btn.sharer || null}
          aria-label={btn.label} title={btn.label}
          onclick={() => {
            if (!btn.sharer) {
              if (btn.id === "share-mastodon") { toggleMastodonForm(); return; }
              else if (btn.id === "share-instagram") onInstagram?.();
            }
            close(false);
          }}
          onkeydown={(e) => onItemKeydown(e, 3 + i)}>
          {@html btn.svg}
        </button>
      {/each}
    </div>
    {#if mastodonFormOpen}
      <div class="mastodon-form">
        <label class="mastodon-form-label" for="mastodon-instance">{$_('mastodon.instance_label')}</label>
        <div class="mastodon-input-row">
          <input id="mastodon-instance" type="text"
            placeholder={$_('mastodon.instance_placeholder')}
            bind:value={mastodonHost}
            bind:this={mastodonInputEl}
            onkeydown={onMastodonInputKeydown} />
          <button type="button" class="mastodon-share-btn"
            onclick={submitMastodon}>{$_('mastodon.share_btn')}</button>
        </div>
      </div>
    {/if}
    <button class="share-sheet-cancel" role="menuitem"
      onclick={() => close(false)}>{$_('share.cancel')}</button>
  </div>
</div>

<style>
</style>
