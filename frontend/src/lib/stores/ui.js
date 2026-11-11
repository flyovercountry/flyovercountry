import { writable } from 'svelte/store';

// The app's one general-purpose UI-state slot.
export const ui = writable({});

/** Whether the on-map legend is collapsed to its pill. Shared because both
 * the legend's own pill and the toolbar's Legend button toggle it. Seeded
 * at mount (app.js initOnMapLegend). */
export const legendCollapsed = writable(false);

export function toggleLegend() {
  legendCollapsed.update(v => !v);
}

/** Whether the desktop panel is folded away. ContentPanel owns the control
 * and the body class; tour.js unfolds it before running, since two desktop
 * steps highlight #sidebar. */
export const sidebarCollapsed = writable(false);

export function setSidebarCollapsed(v) { sidebarCollapsed.set(!!v); }
export function toggleSidebarCollapsed() { sidebarCollapsed.update(v => !v); }

/** Bump to dismiss every transient surface (Share/Export dropdowns, the
 * expanded mobile sheet, breadcrumb mode), whose open state is local to its
 * component. A counter, so consecutive dismissals each notify. */
export const dismissTransients = writable(0);

export function dismissAllTransients() {
  dismissTransients.update(n => n + 1);
}
