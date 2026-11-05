/** Breadcrumbs: the short explanatory readout attached to a control (a
 * tooltip, not a navigation trail). One document-wide delegate on
 * `[data-breadcrumb]`/`[title]` gives every control the same hover readout
 * on desktop and tap readout on touch, where native title tooltips do not
 * exist. map.js's `#map-tooltip` (country hover on the canvas) is separate.
 * Every control whose purpose is not evident from its label gets one. */

import { dismissTransients } from "./lib/stores/ui.js";
import { escapeHtmlWithBreaks } from "./text.js";

const SHOW_DELAY = 400;
const HIDE_DELAY = 100;

let tip = null;
let showTimer = null;
let hideTimer = null;
let currentTarget = null;
let delegated = false;

let infoBtn = null;

/** Read a breadcrumb off an element, whichever way it carries one.
 * `data-breadcrumb` wins over `title` so an element can keep a native
 * `title` for assistive tech while showing richer text here. */
function breadcrumbTextFor(el) {
  if (!el) return null;
  return el.getAttribute("data-breadcrumb")
    || el.getAttribute("data-tip")
    || el.getAttribute("title")
    || null;
}

function ensureTip() {
  if (tip) return tip;
  tip = document.createElement("div");
  tip.id = "sidebar-tip";
  tip.className = "breadcrumb-tip";
  document.body.appendChild(tip);
  return tip;
}

function position(el) {
  const t = ensureTip();
  const rect = el.getBoundingClientRect();
  const tipRect = t.getBoundingClientRect();

  let left = rect.left + rect.width / 2 - tipRect.width / 2;
  let top = rect.bottom + 6;

  if (left < 4) left = 4;
  if (left + tipRect.width > window.innerWidth - 4) {
    left = window.innerWidth - tipRect.width - 4;
  }
  if (top + tipRect.height > window.innerHeight - 4) {
    top = rect.top - tipRect.height - 6;
  }
  t.style.left = left + "px";
  t.style.top = top + "px";
}

/** Show a breadcrumb anchored to `el`.
 * `html` may contain markup (the country breadcrumb uses a paragraph
 * break); callers pass already-escaped content.
 */
function showBreadcrumb(el, html) {
  const t = ensureTip();
  t.innerHTML = "";

  const body = document.createElement("div");
  body.className = "breadcrumb-body";
  body.innerHTML = html;
  t.appendChild(body);

  t.style.display = "block";
  position(el);
}

function hideBreadcrumb() {
  if (tip) tip.style.display = "none";
  currentTarget = null;
}

// --- desktop: hover ---------------------------------------------------

/** Capture-phase document listeners receive events whose target may be the
 * document or another non-Element node, which has no .closest(). Guard once
 * here rather than at each call site. */
function closestFrom(target, selector) {
  return target && typeof target.closest === "function"
    ? target.closest(selector) : null;
}

function onPointerEnter(e) {
  const el = closestFrom(e.target, "[data-breadcrumb], [title]");
  if (!el) return;
  const text = breadcrumbTextFor(el);
  if (!text) return;

  // Stash and strip `title` so the browser's own tooltip does not race
  // this one; restored on leave.
  if (el.hasAttribute("title")) {
    el.dataset.tip = el.getAttribute("title");
    el.removeAttribute("title");
  }

  clearTimeout(hideTimer);
  clearTimeout(showTimer);
  currentTarget = el;
  showTimer = setTimeout(() => {
    if (currentTarget === el) showBreadcrumb(el, escapeHtmlWithBreaks(text));
  }, SHOW_DELAY);
}

function onPointerLeave(e) {
  const el = closestFrom(e.target, "[data-tip], [data-breadcrumb]");
  if (!el) return;
  if (el.dataset.tip !== undefined) {
    el.setAttribute("title", el.dataset.tip);
    delete el.dataset.tip;
  }
  clearTimeout(showTimer);
  hideTimer = setTimeout(hideBreadcrumb, HIDE_DELAY);
  currentTarget = null;
}

/** The round info button, present in every view (see #breadcrumb-mode-btn
 * in style.css for its placement); it opens the Methodology reader. */
function ensureInfoButton(label, onInfo) {
  if (infoBtn) return infoBtn;
  infoBtn = document.createElement("button");
  infoBtn.id = "breadcrumb-mode-btn";
  infoBtn.type = "button";
  infoBtn.setAttribute("aria-label", label);
  infoBtn.title = label;
  infoBtn.innerHTML =
    '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor"' +
    ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/>' +
    '<circle cx="12" cy="7.6" r="0.9" fill="currentColor" stroke="none"/></svg>';
  infoBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    hideBreadcrumb();
    onInfo?.();
  });
  document.body.appendChild(infoBtn);
  return infoBtn;
}

/** Wire the whole mechanism once. Safe to call repeatedly. */
export function initBreadcrumbs({ infoLabel = "Methodology", onInfo = null } = {}) {
  if (delegated) return;
  delegated = true;

  // Document-wide, so toolbar and on-map controls are covered too.
  document.addEventListener("pointerenter", onPointerEnter, true);
  document.addEventListener("pointerleave", onPointerLeave, true);

  // Any scroll invalidates the anchor position.
  document.addEventListener("scroll", () => {
    clearTimeout(showTimer);
    hideBreadcrumb();
  }, { passive: true, capture: true });

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideBreadcrumb();
  });

  ensureInfoButton(infoLabel, onInfo);

  // Dismiss the readout when everything else is dismissed.
  let seenFirstDismiss = false;
  dismissTransients.subscribe(() => {
    if (!seenFirstDismiss) { seenFirstDismiss = true; return; }
    hideBreadcrumb();
  });
}
