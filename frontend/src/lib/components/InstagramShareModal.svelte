<script>
  import { _ } from 'svelte-i18n';

  let { open = false, caption = '', onClose = null } = $props();

  // Optimistic default (matches the original markup): clipboard writes are
  // effectively synchronous in practice, so start as "succeeded" and only
  // flip to the stale/error text if the promise actually rejects.
  let copyStatus = $state($_('instagram.copy_status'));
  let copyStale = $state(false);

  let boxEl;
  let closeBtnEl;
  let lastFocus = null;

  const FOCUSABLE = 'button, [href], textarea, input, select, [tabindex]:not([tabindex="-1"])';

  /** True if `el` can currently receive focus: attached to the document and not
   * inside a `hidden`/`display:none` ancestor (an offsetParent-based check,
   * same heuristic browsers use — cheaper than walking the tree by hand). */
  function isFocusable(el) {
    return !!(el && el.offsetParent !== null);
  }

  function copy() {
    navigator.clipboard.writeText(caption).then(() => {
      copyStatus = $_('instagram.copy_status');
      copyStale = false;
    }).catch(() => {
      copyStatus = "Couldn't auto-copy — select the text above and copy it manually";
      copyStale = true;
    });
  }

  function handleClose() {
    // The button that opened this (#share-instagram) sits inside the Share
    // dropdown list, which the dropdown's own click handler hides as part of
    // the same click — a hidden ancestor makes its descendants unfocusable,
    // so .focus() on it silently no-ops. Fall back to the Share trigger,
    // which stays visible/focusable regardless of the dropdown's state.
    const fallback = document.getElementById("share-menu-trigger");
    const target = (lastFocus?.isConnected && isFocusable(lastFocus)) ? lastFocus : fallback;
    onClose?.();
    target?.focus();
  }

  function handleBackdropClick(e) {
    if (e.target === e.currentTarget) handleClose();
  }

  function handleKeydown(e) {
    if (!open) return;
    if (e.key === "Escape") { handleClose(); return; }
    if (e.key !== "Tab" || !boxEl) return;
    const focusables = Array.from(boxEl.querySelectorAll(FOCUSABLE));
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  }

  // Android/iOS: try the native app deep link, fall back to the web app if it
  // doesn't take over the page in time (no app installed, or desktop browser
  // spoofing a mobile UA). Desktop: just open the web app directly.
  const isMobileOs = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);

  function openWithFallback(appUrl) {
    if (!isMobileOs) { window.open("https://www.instagram.com/", "_blank", "noopener"); return; }
    const fallbackTimer = setTimeout(() => {
      window.location.href = "https://www.instagram.com/";
    }, 1500);
    const onVisibilityChange = () => {
      if (document.hidden) clearTimeout(fallbackTimer);
    };
    document.addEventListener("visibilitychange", onVisibilityChange, { once: true });
    window.location.href = appUrl;
  }

  function openPost() { openWithFallback("instagram://app"); }
  function openStory() { openWithFallback("instagram://story-camera"); }

  // Auto-copy + focus management on open. Both `open` and `caption` are set
  // together by the caller (see app.js's onInstagram), so this fires once per
  // open — not on every keystroke or unrelated prop change.
  $effect(() => {
    if (!open) return;
    lastFocus = document.activeElement;
    copy();
    closeBtnEl?.focus();
  });
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div id="instagram-share-modal" class:hidden={!open} role="dialog" aria-modal="true" aria-labelledby="instagram-modal-title" onclick={handleBackdropClick}>
  <div id="instagram-modal-box" bind:this={boxEl}>
    <button id="instagram-modal-close" type="button" bind:this={closeBtnEl}
      title={$_('instagram.close_title')} aria-label={$_('instagram.close_aria')}
      onclick={handleClose}>{$_('instagram.close_symbol')}</button>
    <h2 id="instagram-modal-title">{$_('instagram.title')}</h2>
    <p class="instagram-modal-note">{$_('instagram.note')}</p>
    <label class="instagram-modal-label" for="instagram-caption">{$_('instagram.caption_label')}</label>
    <textarea id="instagram-caption" readonly value={caption}></textarea>
    <div class="instagram-copy-row">
      <span id="instagram-copy-status" class:stale={copyStale}>{copyStatus}</span>
      <button id="instagram-copy-again" type="button" onclick={copy}>{$_('instagram.copy_again')}</button>
    </div>
    <div class="instagram-modal-actions">
      <button id="instagram-open-post" type="button" onclick={openPost}>{$_('instagram.open_post')}</button>
      <button id="instagram-open-story" type="button" onclick={openStory}>{$_('instagram.open_story')}</button>
    </div>
  </div>
</div>

<style>
</style>
