/** Sidebar UI: startup wiring + FilterPanel mount point.
 *
 *  Filter state lives in lib/stores/filters.js (the applied/staged store
 *  pair). This module seeds the initial state at startup, translates URL-
 *  and map-driven filter changes into store writes, and builds the props
 *  FilterPanel.svelte needs. Rendering is panel-data.js + the Svelte
 *  components under ContentPanel.svelte, which swaps in FilterPanel.svelte
 *  when the filterViewOpen store (lib/stores/panel.js) is true. */
import { getManifest, dayOffset, getRoleGroups, roleLabel } from "./data.js";
import {
  stageRoles, stageDayRange, stageCountry,
  initDefaults, setFilter, stageFilter, setDayRange,
} from "./filters.js";
import { collectDefaultRoles } from "./panel-data.js";
import { closeFilterView } from "./lib/stores/panel.js";
import { isWhatIfActive } from "./whatif.js";

let onApply = () => {};
let onRevert = () => {};
let onReset = () => {};

export function init(opts) {
  onApply = opts.onApply || onApply;
  onRevert = opts.onRevert || onRevert;
  onReset = opts.onReset || onReset;

  const m = getManifest();
  initDefaults(collectDefaultRoles(), dayOffset(m.data_start), dayOffset(m.data_end));
}

export function setRolesByRoleIndex(roleIds) {
  const activeSet = new Set(roleIds.map(Number));
  const validIds = new Set();
  getRoleGroups().forEach(group => {
    group.roles.forEach(id => { if (activeSet.has(id)) validIds.add(id); });
  });
  stageRoles(validIds);
}

export function setDateRange(start, end) {
  const m = getManifest();
  const dMin = dayOffset(start || m.data_start);
  const dMax = dayOffset(end || m.data_end);
  stageDayRange(dMin, dMax);
  setDayRange(dMin, dMax);
}

export function selectCountryInGrid(cc) {
  stageCountry(cc);
}

export function setFilterUI(mode, include) {
  setFilter(mode, include);
  stageFilter(mode, include);
}

/** Shared prop set for FilterPanel.svelte, mounted declaratively as a child
 * component of ContentPanel. `overrides` lets callers replace
 * onApply/onRevert/onReset with variants that also handle closing whatever
 * container they're in. */
function filterPanelProps(overrides = {}) {
  const m = getManifest();
  const isReadonly = isWhatIfActive();

  return {
    dateMin: m.data_start,
    dateMax: m.data_end,
    baseDate: m.data_start,
    roleGroups: getRoleGroups(),
    roleLabel,
    readonly: isReadonly,
    onApply: overrides.onApply || (() => onApply()),
    onRevert: overrides.onRevert || (() => onRevert()),
    onReset: overrides.onReset || (() => onReset()),
    onTheaterHover: (hovering) => showTheaterBboxOverlay(hovering),
  };
}

// --- Sidebar / mobile in-panel filter view ---
// FilterPanel.svelte is mounted declaratively there (as a normal child
// component of ContentPanel.svelte), reading the live applied/staged
// stores directly — no separate lifecycle hook is needed to seed or tear
// down state around it.

export function getSidebarFilterPanelProps() {
  return filterPanelProps({
    onApply: () => { onApply(); closeFilterView(); },
    onRevert: () => { onRevert(); closeFilterView(); },
    onReset: () => { onReset(); closeFilterView(); },
  });
}

let theaterOverlayActive = false;

function showTheaterBboxOverlay(show) {
  const map = window.__map;
  if (!map) return;
  const layers = ["theater-me-overlay", "theater-balkans-overlay"];

  if (!show) {
    for (const id of layers) {
      if (map.getLayer(id)) map.removeLayer(id);
      if (map.getSource(id)) map.removeSource(id);
    }
    theaterOverlayActive = false;
    return;
  }

  if (theaterOverlayActive) return;
  const bboxes = getManifest()?.bboxes;
  if (!bboxes) return;

  for (let i = 1; i < bboxes.length; i++) {
    const b = bboxes[i];
    if (b.name !== "me" && b.name !== "balkans") continue;
    const id = `theater-${b.name}-overlay`;
    const color = b.name === "me" ? "#ff6600" : "#00ccff";
    const ring = [[b.lon_west, b.lat_south], [b.lon_east, b.lat_south],
                  [b.lon_east, b.lat_north], [b.lon_west, b.lat_north],
                  [b.lon_west, b.lat_south]];
    map.addSource(id, {
      type: "geojson",
      data: { type: "Feature", geometry: { type: "Polygon", coordinates: [ring] } },
    });
    map.addLayer({
      id, type: "line", source: id,
      paint: { "line-color": color, "line-width": 2, "line-dasharray": [4, 3] },
    });
  }
  theaterOverlayActive = true;
}
