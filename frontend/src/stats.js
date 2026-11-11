/** Per-country stats over the active date/role/theater filters.
 *
 * Crossings count the per-flight `cc` (own-trace crossings), consistent
 * with the routes the map draws. Planes figures (Sky, Ground) are
 * distinct-aircraft counts over the whole date range, never gated by the
 * What-If denial day; Ground reads the sighting index's per-hex "g" lists,
 * the pipeline's corroborated landing days, and landingEvents counts runs
 * of consecutive "g" days. Role filtering uses the per-flight bitmask;
 * per-group rows use group-mask intersection, so they overlap and do not
 * sum to the totals. */
import {
  getManifest, getFlightsTable, getFlightMasks, rolesToMask,
  getRoleGroups, groupMask, getSightingIndex, ccContains,
} from "./data.js";
import { getDayRange, getRoles, getFilter, passesAdvancedFilters } from "./filters.js";

function hasAdvancedFilter() {
  return getFilter().mode !== "none";
}

/** Catalog groups whose mask intersects `flightMask` (group-level union
 * membership test — see the roles taxonomy in data/in/config/roles.json). */
function memberGroupsOf(flightMask) {
  return getRoleGroups().filter(g => (flightMask & groupMask(g.id)) !== 0);
}

export function computeStats(countryCode) {
  const ft = getFlightsTable();
  const masks = getFlightMasks();
  if (!ft || !masks) return null;

  const [dayMin, dayMax] = getDayRange();
  const activeMask = rolesToMask(getRoles());
  const nDays = Math.max(0, dayMax - dayMin + 1);
  const advanced = hasAdvancedFilter();

  // Daily traversal histogram only: every landings/ground figure the panel
  // shows comes from computePlanes' distinct-aircraft `landed` (the
  // sighting index's "g" lists) instead.
  const dailyTraverse = new Array(nDays).fill(0);

  for (let i = 0; i < ft.n; i++) {
    const day = ft.day[i];
    if (day < dayMin || day > dayMax) continue;
    if ((masks[i] & activeMask) === 0) continue;
    if (advanced && !passesAdvancedFilters({ thc: ft.thc[i], the: ft.the[i] })) continue;
    if (!ccContains(ft.cc[i] || "", countryCode)) continue;
    dailyTraverse[day - dayMin]++;
  }

  return { dailyTraverse, dates: manifestDates(dayMin, dayMax) };
}

/** Distinct airframes per aircraft type, against the whole dataset and
 * under the active filters, so the roles view can say "236 airframes of
 * this type, 4 pass your filters". The baseline is no filter at all (full
 * range, all roles, no theater mask), not the default filter state. One
 * pass over the flights table. */
export function computeTypeCounts() {
  const ft = getFlightsTable();
  const masks = getFlightMasks();
  if (!ft || !masks) return null;

  const [dayMin, dayMax] = getDayRange();
  const activeMask = rolesToMask(getRoles());
  const advanced = hasAdvancedFilter();

  const allByTi = new Map();       // ti -> Set(hex)   whole population
  const filteredByTi = new Map();  // ti -> Set(hex)   under active filters
  const allHexes = new Set();
  const filteredHexes = new Set();

  const add = (map, ti, hex) => {
    let set = map.get(ti);
    if (!set) { set = new Set(); map.set(ti, set); }
    set.add(hex);
  };

  for (let i = 0; i < ft.n; i++) {
    const ti = ft.ti[i];
    const hex = ft.hex[i];

    add(allByTi, ti, hex);
    allHexes.add(hex);

    if (ft.day[i] < dayMin || ft.day[i] > dayMax) continue;
    if ((masks[i] & activeMask) === 0) continue;
    if (advanced && !passesAdvancedFilters({ thc: ft.thc[i], the: ft.the[i] })) continue;

    add(filteredByTi, ti, hex);
    filteredHexes.add(hex);
  }

  // Keyed by type CODE, since that is what the roles taxonomy speaks in;
  // manifest.types[] is the ti -> code mapping (ti === -1 is "no type
  // resolved", which has no code and is reported separately).
  const types = getManifest()?.types || [];
  const byCode = {};
  for (const [ti, set] of allByTi) {
    if (ti < 0) continue;
    const code = types[ti]?.code;
    if (!code) continue;
    byCode[code] = {
      all: set.size,
      filtered: filteredByTi.get(ti)?.size || 0,
    };
  }

  return {
    byCode,
    totals: { all: allHexes.size, filtered: filteredHexes.size },
    untyped: {
      all: allByTi.get(-1)?.size || 0,
      filtered: filteredByTi.get(-1)?.size || 0,
    },
  };
}

/** The unified stats table's "planes" columns (Sky/Ground) and the
 * "landings" column (event count).
 *
 * Sky/Ground are distinct-aircraft (icao24) counts over the in-scope
 * fleet. landingEvents counts distinct ground stays (runs of consecutive
 * "g" days per hex). All are standing questions (not gated by
 * currentDenialDay), gated by date-range, role, and theater filters. */
function countLandingEvents(gDays, dayMin, dayMax) {
  let events = 0, prev = -2;
  for (const d of gDays) {
    if (d < dayMin || d > dayMax) continue;
    if (d !== prev + 1) events++;
    prev = d;
  }
  return events;
}

export function computePlanes(countryCode) {
  const ft = getFlightsTable();
  const masks = getFlightMasks();
  const idx = getSightingIndex(countryCode);
  if (!ft || !masks) return null;

  const [dayMin, dayMax] = getDayRange();
  const activeMask = rolesToMask(getRoles());
  const advanced = hasAdvancedFilter();

  const scopeHexes = new Set();
  const crossedHexes = new Set();
  const landedHexes = new Set();
  const groupScope = new Map();
  const groupCrossed = new Map();
  const groupLanded = new Map();

  for (let i = 0; i < ft.n; i++) {
    const day = ft.day[i];
    if (day < dayMin || day > dayMax) continue;
    const flightMask = masks[i] & activeMask;
    if (flightMask === 0) continue;
    if (advanced && !passesAdvancedFilters({ thc: ft.thc[i], the: ft.the[i] })) continue;

    const hex = ft.hex[i];
    scopeHexes.add(hex);
    const byDay = idx ? idx[hex] : null;
    const crossed = !!byDay && Object.keys(byDay).some(d => +d >= dayMin && +d <= dayMax);
    if (crossed) crossedHexes.add(hex);
    const landed = !!byDay?.g && byDay.g.some(d => d >= dayMin && d <= dayMax);
    if (landed) landedHexes.add(hex);

    // Group-level union: one hex per member group,
    // regardless of how many of the flight's roles fall in that group.
    for (const g of memberGroupsOf(flightMask)) {
      if (!groupScope.has(g.id)) {
        groupScope.set(g.id, new Set());
        groupCrossed.set(g.id, new Set());
        groupLanded.set(g.id, new Set());
      }
      groupScope.get(g.id).add(hex);
      if (crossed) groupCrossed.get(g.id).add(hex);
      if (landed) groupLanded.get(g.id).add(hex);
    }
  }

  let totalLandingEvents = 0;
  for (const hex of landedHexes) {
    const gDays = idx[hex]?.g;
    if (gDays) totalLandingEvents += countLandingEvents(gDays, dayMin, dayMax);
  }

  const byGroup = [...groupScope.entries()].map(([gid, scopeSet]) => {
    const g = getRoleGroups().find(gr => gr.id === gid);
    let groupEvents = 0;
    for (const hex of groupLanded.get(gid)) {
      const gDays = idx?.[hex]?.g;
      if (gDays) groupEvents += countLandingEvents(gDays, dayMin, dayMax);
    }
    return {
      gi: gid, label: g?.label || `group ${gid}`,
      crossed: groupCrossed.get(gid).size, scope: scopeSet.size,
      landed: groupLanded.get(gid).size, landingEvents: groupEvents,
    };
  });

  return {
    crossed: crossedHexes.size, scope: scopeHexes.size,
    landed: landedHexes.size, landingEvents: totalLandingEvents, byGroup,
  };
}

export function computeGlobalStats() {
  const ft = getFlightsTable();
  const m = getManifest();
  const masks = getFlightMasks();
  if (!ft || !m || !masks) return null;

  const [dayMin, dayMax] = getDayRange();
  const activeMask = rolesToMask(getRoles());
  const nDays = Math.max(0, dayMax - dayMin + 1);
  const advanced = hasAdvancedFilter();

  let total = 0;
  const dailyTotal = new Array(nDays).fill(0);
  const aircraftSet = new Set();

  for (let i = 0; i < ft.n; i++) {
    const day = ft.day[i];
    if (day < dayMin || day > dayMax) continue;
    if ((masks[i] & activeMask) === 0) continue;
    if (advanced && !passesAdvancedFilters({ thc: ft.thc[i], the: ft.the[i] })) continue;

    total++;
    dailyTotal[day - dayMin]++;
    aircraftSet.add(ft.hex[i]);
  }

  return {
    total, aircraft: aircraftSet.size, dailyTotal,
    dates: manifestDates(dayMin, dayMax),
  };
}


function manifestDates(dayMin, dayMax) {
  const m = getManifest();
  const dates = [];
  for (let d = dayMin; d <= dayMax; d++) {
    dates.push(m?.dates?.[d] || "");
  }
  return dates;
}
