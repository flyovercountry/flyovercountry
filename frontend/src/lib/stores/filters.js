/** Filter state. `applied` drives the map filter, stats, What-If and CSV
 * export; `staged` is the edit buffer the filter panel reads and writes;
 * `dirty` compares the two. Plain-function getters/setters at the bottom
 * serve modules outside the component tree.
 *
 * `appliedSnapshot` caches the store's current value through a permanent
 * subscription (store notifications are synchronous) so hot loops such as
 * stats.js's pass over the flights table do not pay get()'s
 * subscribe/unsubscribe per row. */
import { writable, derived, get } from 'svelte/store';

function emptyState() {
  return {
    roles: [],
    dayMin: 0,
    dayMax: Infinity,
    country: null,
    showFlights: true,
    showLandings: false,
    filterMode: 'tasking',
    filterInclude: true,
  };
}

function cloneState(s) {
  return { ...s, roles: [...s.roles] };
}

function toSortedArray(roles) {
  return [...roles].map(Number).sort((a, b) => a - b);
}

export const applied = writable(emptyState());
export const staged = writable(emptyState());

let appliedSnapshot = emptyState();
applied.subscribe(v => { appliedSnapshot = v; });

function rolesEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

/** Compared directly rather than through get(dirty): a raw `staged`
 * subscriber can run before the derived store recomputes and would read the
 * previous dirty value. */
function statesDiffer(a, b) {
  if (a.dayMin !== b.dayMin) return true;
  if (a.dayMax !== b.dayMax) return true;
  if (a.country !== b.country) return true;
  if (a.showFlights !== b.showFlights) return true;
  if (a.showLandings !== b.showLandings) return true;
  if (a.filterMode !== b.filterMode) return true;
  if (a.filterInclude !== b.filterInclude) return true;
  if (!rolesEqual(a.roles, b.roles)) return true;
  return false;
}

export const dirty = derived([applied, staged],
  ([$applied, $staged]) => statesDiffer($applied, $staged));

// --- Defaults (the Reset target) ---
let defaults = emptyState();
// Also a store, so the badge below recomputes when the real
// defaults arrive. initDefaults() runs after these stores are created (it
// needs the manifest), so a derived that closed over the plain `defaults`
// variable would be stuck comparing against emptyState() forever.
const defaultsStore = writable(emptyState());

/** Seed the Reset target and the initial applied+staged state in one
 * step — called once at startup with the manifest's full day range and
 * the catalog's default-on roles (see controls.js init()). */
export function initDefaults(roleIds, dayMin, dayMax) {
  defaults = {
    roles: toSortedArray(roleIds),
    dayMin, dayMax,
    country: null,
    showFlights: true,
    showLandings: false,
    filterMode: 'tasking',
    filterInclude: true,
  };
  defaultsStore.set(cloneState(defaults));
  applied.set(cloneState(defaults));
  staged.set(cloneState(defaults));
}

// Which filter categories differ from the seeded defaults: the C/D/R letters
// on the Filters trigger. Reads applied state, i.e. what the map shows.
function differingCategories(state, base) {
  const out = [];
  if (state.country !== base.country) out.push('C');
  if (state.dayMin !== base.dayMin || state.dayMax !== base.dayMax) out.push('D');
  if (!rolesEqual(state.roles, base.roles)) out.push('R');
  return out;
}

export const nonDefaultCategories = derived(
  [applied, defaultsStore],
  ([$applied, $defaults]) => differingCategories($applied, $defaults));

// --- Staged <-> applied lifecycle ---
export function applyStaged() { applied.set(cloneState(get(staged))); }
export function resetStaged() { staged.set(cloneState(appliedSnapshot)); }
export function resetAll() {
  applied.set(cloneState(defaults));
  staged.set(cloneState(defaults));
}

// --- Auto-apply, debounced ---
//
// Staged edits commit after a quiet period. Applying re-runs the map
// filter, stats, What-If and the export set over the full flights table, so
// a burst of edits must produce one recompute; the timer restarts on every
// edit and only the final state commits.

export const AUTO_APPLY_MS = 3000;

// True while an edit is staged and the timer is running. `pendingSeq` bumps
// on every (re)schedule so the UI can restart a CSS countdown animation
// without this module having to tick a timer itself — a 100ms interval just
// to move a progress bar would be its own small waste on a page that is
// already fighting for frames.
export const autoApplyPending = writable(false);
export const pendingSeq = writable(0);

let autoTimer = null;
let autoApplyCb = null;

/** Register what an auto-commit actually does (app.js's applyFilters).
 * Kept as a callback so this store stays free of map/stats imports. */
export function registerAutoApply(cb) { autoApplyCb = cb; }

export function cancelAutoApply() {
  if (autoTimer !== null) {
    clearTimeout(autoTimer);
    autoTimer = null;
  }
  autoApplyPending.set(false);
}

function scheduleAutoApply() {
  if (autoTimer !== null) clearTimeout(autoTimer);
  autoApplyPending.set(true);
  pendingSeq.update(n => n + 1);
  autoTimer = setTimeout(() => {
    autoTimer = null;
    autoApplyPending.set(false);
    // applyStaged() happens inside the callback (app.js's applyFilters
    // already calls it), so the commit and the recompute stay one step.
    autoApplyCb?.();
  }, AUTO_APPLY_MS);
}

// NOTE: there is deliberately no "commitNow that also applies" here. The
// Apply-now button goes through cancelAutoApply() + the caller's own onApply
// (controls.js's, which applies AND closes the filter view); routing it
// through the auto-apply callback as well would run the whole recompute
// twice for one click.

// Restart the countdown on every staged edit. Subscribing to `staged`
// rather than to `dirty` is deliberate: `dirty` is a boolean, so editing a
// second field while already dirty would not notify, and the timer would
// keep running against the first edit's deadline.
//
// The dirtiness test is statesDiffer() against the live appliedSnapshot,
// NOT get(dirty) — see the note on statesDiffer above for why reading the
// derived store from inside this callback is unsound.
//
// applyStaged() writes `applied`, not `staged`, so a commit does not
// re-trigger this. resetStaged()/resetAll()/initDefaults() do write staged,
// and each leaves the two equal, so they land on the cancel branch.
let seenFirstStaged = false;
staged.subscribe(s => {
  if (!seenFirstStaged) { seenFirstStaged = true; return; }
  if (statesDiffer(appliedSnapshot, s)) scheduleAutoApply();
  else cancelAutoApply();
});

// --- Field-level mutators/getters for callers outside the component
// tree (data.js, stats.js, whatif.js, share-links.js, app.js, controls.js
// — via the frontend/src/filters.js re-export). Each pair mutates exactly
// one field of exactly one store; applied and staged fields move
// independently of each other. ---
export function setRoles(roles) { applied.update(s => ({ ...s, roles: toSortedArray(roles) })); }
export function stageRoles(roles) { staged.update(s => ({ ...s, roles: toSortedArray(roles) })); }
export function getRoles() { return new Set(appliedSnapshot.roles); }

export function setDayRange(min, max) { applied.update(s => ({ ...s, dayMin: min, dayMax: max })); }
export function stageDayRange(min, max) { staged.update(s => ({ ...s, dayMin: min, dayMax: max })); }
export function getDayRange() { return [appliedSnapshot.dayMin, appliedSnapshot.dayMax]; }

export function setCountry(cc) { applied.update(s => ({ ...s, country: cc || null })); }
export function stageCountry(cc) { staged.update(s => ({ ...s, country: cc || null })); }
export function getCountry() { return appliedSnapshot.country; }

// showFlights/showLandings apply immediately (no staged/applied split —
// there's no Apply button for them), so both stores are written together.
export function setShowFlights(v) {
  applied.update(s => ({ ...s, showFlights: v }));
  staged.update(s => ({ ...s, showFlights: v }));
}
export function getShowFlights() { return appliedSnapshot.showFlights; }

export function setShowLandings(v) {
  applied.update(s => ({ ...s, showLandings: v }));
  staged.update(s => ({ ...s, showLandings: v }));
}
export function getShowLandings() { return appliedSnapshot.showLandings; }

export function setFilter(mode, include) { applied.update(s => ({ ...s, filterMode: mode, filterInclude: include })); }
export function stageFilter(mode, include) { staged.update(s => ({ ...s, filterMode: mode, filterInclude: include })); }
export function getFilter() { return { mode: appliedSnapshot.filterMode, include: appliedSnapshot.filterInclude }; }

/** Read-only accessor for buildFilter()/passesAdvancedFilters() (see
 * frontend/src/filters.js) — the applied snapshot itself, not a copy. */
export function getAppliedSnapshot() { return appliedSnapshot; }
