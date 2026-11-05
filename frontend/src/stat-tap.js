/** Tap-reveal for the stats table's percentage-cell tooltips: touch devices
 * have no hover, so a tap shows the cell's "N / M flights" text inline.
 * Shared by the panel's and the reader's delegated click handlers.
 *
 * breadcrumb.js moves `title` into `data-tip` on pointerenter (which a
 * touch tap also fires), so both attributes are checked. A tap can produce
 * a second synthetic click 400-500 ms later (reproducible in Chromium with
 * hasTouch), which would toggle the reveal straight back off; a per-cell
 * debounce of DEBOUNCE_MS absorbs it. */
const DEBOUNCE_MS = 600;
const lastToggleAt = new WeakMap();

export function toggleStatColDetail(target) {
  const cell = target.closest?.(".stat-col-narrow[title], .stat-col-narrow[data-tip]");
  if (!cell) return false;
  const text = cell.title || cell.dataset.tip;
  if (!text) return false;

  const now = Date.now();
  if (now - (lastToggleAt.get(cell) || 0) < DEBOUNCE_MS) return true;
  lastToggleAt.set(cell, now);

  const open = cell.querySelector(".stat-col-detail");
  if (open) {
    open.remove();
    cell.classList.remove("expanded");
    return true;
  }
  cell.classList.add("expanded");
  const detail = document.createElement("span");
  detail.className = "stat-col-detail";
  detail.textContent = text;
  cell.appendChild(detail);
  return true;
}
