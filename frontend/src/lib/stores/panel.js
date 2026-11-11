import { writable } from 'svelte/store';
import { closeReader } from './reader.js';

export const panelData = writable({
  cc: null,
  globalStats: null,
  countryStats: null,
  whatIfActive: false,
  refreshTick: 0,
});

// Whether the in-sidebar filter view is showing (replacing the normal
// stats/info/footer content in the same column) — desktop and mobile both
// drive this one flag; mobile additionally expands the panel full-screen
// (see ContentPanel.expand()) so the swapped-in content covers the viewport.
export const filterViewOpen = writable(false);

export function openFilterView() {
  // Opening filters dismisses the reader: filters exist to change what
  // the MAP shows, and the reader is a full-screen overlay covering it.
  // Editing filters behind a reading pane means not seeing the result.
  closeReader();
  filterViewOpen.set(true);
}

export function closeFilterView() {
  filterViewOpen.set(false);
}

/** Clicking the Filters trigger again while the view is already
 * open closes it. Both triggers (the toolbar button and ContentPanel's own
 * stats-header/Filters trigger) go through this, so the two cannot disagree
 * about what a second click does. */
export function toggleFilterView() {
  let opening = false;
  filterViewOpen.update(v => { opening = !v; return !v; });
  if (opening) closeReader();
}
