/** MapLibre filter expression builder + advanced-filter predicate, built
 * from the single filter-state store (lib/stores/filters.js).
 *
 * filterMode values:
 *   "none"     no advanced filter
 *   "tasking"  theater: the flight's mission chain touches the theater
 *              (default view)
 *   "ever"     theater: the aircraft was in the theater at any point
 *
 * `roles` is a set of role_catalog.json role ids — the same ids used as
 * bit positions in the per-flight bitmask (data.js). Role filtering is
 * positive union inclusion: a flight passes if ANY of its roles is in
 * the active set (see buildFilter's `rs`-array membership test).
 *
 * This module also re-exports the plain-function getter/setter API that
 * non-Svelte modules (data.js, stats.js, whatif.js, share-links.js,
 * app.js, controls.js) use to read/write the store without importing
 * Svelte primitives themselves.
 */
import { getAppliedSnapshot } from "./lib/stores/filters.js";

export {
  initDefaults,
  setRoles, getRoles, stageRoles,
  setDayRange, getDayRange, stageDayRange,
  setCountry, getCountry, stageCountry,
  setShowFlights, getShowFlights,
  setShowLandings, getShowLandings,
  setFilter, getFilter, stageFilter,
} from "./lib/stores/filters.js";

/** Build a MapLibre filter expression from the applied state. */
export function buildFilter() {
  const state = getAppliedSnapshot();
  const parts = ["all"];
  parts.push([">=", ["get", "day"], state.dayMin]);
  parts.push(["<=", ["get", "day"], state.dayMax]);
  if (state.roles.length === 0) {
    parts.push(false);
  } else {
    // The flight's `rs` role-id array must contain an active role id;
    // MapLibre's `in` takes an array-valued property as the haystack.
    parts.push(["any", ...state.roles.map(id => ["in", id, ["get", "rs"]])]);
  }

  if (state.filterMode === "tasking") {
    parts.push(["==", ["get", "thc"], state.filterInclude ? 1 : 0]);
  } else if (state.filterMode === "ever") {
    parts.push(["==", ["get", "the"], state.filterInclude ? 1 : 0]);
  }

  return parts;
}

/** Test whether a single flight record passes the advanced filter. */
export function passesAdvancedFilters(flight) {
  const state = getAppliedSnapshot();
  if (state.filterMode === "tasking") {
    return state.filterInclude ? !!flight.thc : !flight.thc;
  }
  if (state.filterMode === "ever") {
    return state.filterInclude ? !!flight.the : !flight.the;
  }
  return true;
}
