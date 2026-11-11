<script>
  // The tour and the contact route are the two things a confused reader
  // needs most, so they are explicit buttons above the prose rather than
  // inline links buried in it. The residual prose still comes from config
  // (common.texts.json footer.text) and can still carry {contact_link}/
  // {tour_link} placeholders for an older config string.
  //
  // `contact` and `repo` are the resolved {text, href} from lib/links.js,
  // or null when the untracked config file is absent: then that chip is
  // not rendered at all.
  let { text = '', contact = null, tourLabel = '', repo = null, onTour = null } = $props();

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[c]);
  }

  // Inline form for the legacy {contact_link} placeholder in footer.text.
  function contactInline() {
    if (!contact) return '';
    return `<a class="contact-link" href="${escapeHtml(contact.href)}" target="_blank" rel="noopener">${escapeHtml(contact.text)}</a>`;
  }

  function handleProseClick(e) {
    if (e.target.closest('.tour-link')) {
      e.preventDefault();
      if (onTour) onTour();
    }
  }
</script>

<div class="footer-actions">
  {#if onTour}
    <button type="button" class="footer-action" onclick={() => onTour()}>
      <svg class="footer-action-icon" viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v8M8 12h8" />
      </svg>
      <span>{tourLabel || 'Take the tour'}</span>
    </button>
  {/if}
  {#if contact}
    <a href={contact.href} target="_blank" rel="noopener" class="footer-action footer-contact">
      <svg class="footer-action-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
      <span>{contact.text}</span>
    </a>
  {/if}
  {#if repo}
    <a href={repo.href} target="_blank" rel="noopener" class="footer-action footer-repo">
      <svg class="footer-action-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M7 5C7 3.89543 7.89543 3 9 3H15C16.1046 3 17 3.89543 17 5V7H19C20.1046 7 21 7.89543 21 9V19C21 20.1046 20.1046 21 19 21H13M7 5V7H5C3.89543 7 3 7.89543 3 9V19C3 20.1046 3.89543 21 5 21H11M7 5H17M17 5V7" />
        <path d="M8 14l3 3 5-5" />
      </svg>
      <span>{repo.text}</span>
    </a>
  {/if}
</div>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<span class="footer-prose" onclick={handleProseClick}>
  {#if text}
    {@html text
      .replace('{contact_link}', contactInline())
      .replace('{tour_link}', onTour ? `<button type="button" class="tour-link">${tourLabel || 'Take the tour'}</button>` : '')
    }
  {/if}
</span>

<style>
  .footer-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 8px;
  }
  .footer-action {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    flex: 1 1 auto;
    min-width: 0;
    padding: 7px 10px;
    font-family: inherit;
    font-size: 12px;
    font-weight: 600;
    color: #cfe3f5;
    background: rgba(74, 138, 191, 0.14);
    border: 1px solid #3a6a95;
    border-radius: 4px;
    cursor: pointer;
    text-align: left;
    line-height: 1.25;
  }
  .footer-action:hover {
    background: rgba(74, 138, 191, 0.26);
    border-color: #5a9adb;
    color: #fff;
  }
  .footer-action:focus-visible {
    outline: 2px solid #6ab4f0;
    outline-offset: 2px;
  }
  .footer-action span {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  a.footer-action { text-decoration: none; }
  .footer-action-icon {
    width: 14px;
    height: 14px;
    flex-shrink: 0;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .footer-prose { display: block; }
  :global(.contact-link),
  :global(.tour-link) {
    display: inline;
    margin: 0;
    padding: 0;
    background: none;
    border: none;
    font: inherit;
    color: #6ab4f0;
    text-decoration: none;
    cursor: pointer;
  }
  :global(.contact-link:hover),
  :global(.tour-link:hover) { text-decoration: underline; }
</style>
