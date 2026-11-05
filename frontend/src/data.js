/** Data loading — manifest, role catalog, routes, flights lookup table. */
import { getDayRange, getRoles, passesAdvancedFilters } from "./filters.js";

/** Token separator in the `roles=` URL param (url.js). One of the
 * characters URLSearchParams leaves unencoded; "." is the group.member
 * sub-delimiter. */
export const ROLE_DELIMITER = "_";

let manifest = null;
let roleCatalog = null;
let focusConfig = null;
let flightsTable = null;
let flightsIndex = null;
let flightMasks = null;   // Int32Array, one bitmask per flight row (role id = bit index)
let hexRoleMask = null;   // Map<hex, bitmask> from hex_roles.json

export async function loadManifest() {
  const [mRes, catRes] = await Promise.all([
    fetch("/data/manifest.json"),
    fetch("/data/role_catalog.json"),
  ]);
  manifest = await mRes.json();
  roleCatalog = await catRes.json();
  try {
    const [modesRes, textsRes, refRes] = await Promise.all([
      fetch("/data/countries.modes.json"),
      fetch("/data/common.texts.json"),
      fetch("/data/country.reference.json"),
    ]);
    if (modesRes.ok && textsRes.ok && refRes.ok) {
      focusConfig = mergeFocusConfig(
        await modesRes.json(), await textsRes.json(), await refRes.json());
    }
  } catch { /* focus config unavailable — degrade gracefully */ }
  return manifest;
}

/** Merge the three config files into the focus-config object the app reads:
 * countries[cc] (name, modes, templated headline/closing, prose, plus a
 * `reference` of refusal_analysis + citations), `permissivenessLevels`
 * (legend text and map fill colour per level, keyed "0".."2") and the share
 * `hashtags`. Mirrors backend/util/focus_config.py; keep the two in sync.
 *
 * Every country, including the display-only UA/BY/RU, is one entry with a
 * `permissiveness` level (0-2) and an ordinal `interaction` (0-3; whatif
 * implies stats implies info). The two are orthogonal: Switzerland/Austria
 * are permissiveness 0 with interaction 2, Saudi/Kuwait permissiveness 2
 * with interaction 1. hasInfo/hasStats/hasWhatif derive the booleans, which
 * mergeFocusConfig also stamps on each entry as info/stats/whatif. */
export function hasInfo(entry) { return ((entry && entry.interaction) || 0) >= 1; }
export function hasStats(entry) { return ((entry && entry.interaction) || 0) >= 2; }
export function hasWhatif(entry) { return ((entry && entry.interaction) || 0) >= 3; }
function mergeFocusConfig(modes, texts, reference) {
  const templates = texts.templates || {};
  const hlIntro = templates.headline_intro || "";
  const hlOutro = templates.headline_outro || "";
  const closing = templates.closing || "";
  const out = {
    title: texts.title, description: texts.description, message: texts.message,
    footer: texts.footer, info: texts.info, hashtags: texts.hashtags,
    share: texts.share || null,
    permissivenessLevels: modes.permissiveness_levels || {},
    countries: {},
  };
  for (const [cc, attrs] of Object.entries(modes.countries)) {
    const entry = { ...attrs };
    // Static countries carry a full `headline`; templated ones carry
    // headline_name + headline_note, rendered into the shared intro/outro
    // template. `headline` is the full text (the map tooltip shows it);
    // `headlineIntro` is the stats sentence alone (the panel shows it, the
    // note goes on the Summary tab).
    if (!entry.headline && entry.headline_note != null && entry.headline_name) {
      const name = entry.headline_name;
      const outroResolved = hlOutro ? hlOutro.replaceAll("{name}", name) : "";
      entry.headlineIntro = hlIntro.replaceAll("{name}", name);
      entry.headline = entry.headlineIntro
        + (entry.headline_note ? " " + entry.headline_note : "")
        + (outroResolved ? "\n\n" + outroResolved : "");
      entry.closing = closing;
    } else if (entry.headline) {
      entry.headlineIntro = entry.headline;
    }
    const r = reference[cc] || {};
    if (r.prose) Object.assign(entry, r.prose);
    if (r.refusal_analysis != null) {
      entry.reference = { refusal_analysis: r.refusal_analysis, citations: r.citations || {} };
    }
    entry.info = hasInfo(entry);
    entry.stats = hasStats(entry);
    entry.whatif = hasWhatif(entry);
    out.countries[cc] = entry;
  }
  return out;
}

export function getManifest() { return manifest; }

/** The merged config entry for `cc`, or null. interaction >= 1: a panel
 * (info); >= 2: stats; >= 3: What-If. Interaction 0 (UA/BY/RU) is drawn on
 * the map but never selectable. */
export function countryEntry(cc, fc = getFocusConfig()) {
  return (cc && fc?.countries?.[cc]) || null;
}

/** { id -> {id, slug, label, airframes, flights} } for O(1) lookup. */
let roleById = null;
function ensureRoleIndex() {
  if (roleById) return roleById;
  roleById = new Map();
  for (const r of roleCatalog.roles) roleById.set(r.id, r);
  return roleById;
}

/** Human-readable label for a catalog role id (e.g. "air refuelling"). */
export function roleLabel(id) {
  return ensureRoleIndex().get(id)?.label || `role ${id}`;
}

/** Catalog slug for a role id (e.g. "air_refuelling"). */
export function roleSlug(id) {
  return ensureRoleIndex().get(id)?.slug || `role_${id}`;
}

/** Catalog groups (each { id, slug, label, on, roles: [ids] }), in catalog order. */
export function getRoleGroups() { return roleCatalog.groups; }

// Per-group bitmasks (bit = role id). A flight belongs to a group iff its
// role mask intersects the group's, so it counts once per group however
// many of the group's roles it has.
let groupMasks = null;
function ensureGroupMasks() {
  if (groupMasks) return groupMasks;
  groupMasks = new Map();
  for (const g of roleCatalog.groups) groupMasks.set(g.id, rolesToMask(g.roles));
  return groupMasks;
}
export function groupMask(groupId) { return ensureGroupMasks().get(groupId) || 0; }

/** Union of role ids belonging to any catalog group with on:true. */
export function defaultOnRoleIds() {
  const ids = new Set();
  for (const g of roleCatalog.groups) {
    if (g.on) g.roles.forEach(id => ids.add(id));
  }
  return ids;
}

export function getFocusConfig() { return focusConfig; }

export function loadRoutesData(onProgress, mobile) {
  return new Promise((resolve, reject) => {
    const url = mobile ? "/data/routes-mobile.geojson" : "/data/routes.geojson";
    const xhr = new XMLHttpRequest();
    xhr.open("GET", url);
    xhr.responseType = "json";
    let total = 0;
    xhr.onreadystatechange = () => {
      if (xhr.readyState >= 2 && !total) {
        // serve.mjs sends this on gzipped responses; Cloudflare Pages does not.
        total = parseInt(xhr.getResponseHeader("X-Decompressed-Content-Length") || "0", 10);
      }
    };
    xhr.onprogress = (e) => {
      if (!onProgress) return;
      if (e.lengthComputable) { onProgress(e.loaded / e.total); return; }
      if (total > 0) onProgress(Math.min(1, e.loaded / total));
    };
    xhr.onload = () => {
      if (onProgress) onProgress(1);
      resolve(xhr.status === 200 ? xhr.response : null);
    };
    xhr.onerror = () => reject(new Error("routes fetch failed"));
    xhr.send();
  });
}

export async function loadHexRoles() {
  const res = await fetch("/data/hex_roles.json");
  const data = await res.json();
  hexRoleMask = new Map();
  for (const [hex, ids] of Object.entries(data)) {
    let mask = 0;
    for (const id of ids) mask |= (1 << id);
    hexRoleMask.set(hex, mask);
  }
}

export function getHexRoleMask() { return hexRoleMask; }

export async function loadFlightsTable() {
  const res = await fetch("/data/flights.json");
  flightsTable = await res.json();
  flightsIndex = new Map();
  flightMasks = new Int32Array(flightsTable.n);
  for (let i = 0; i < flightsTable.n; i++) {
    flightsIndex.set(flightsTable.id[i], i);
    flightMasks[i] = hexRoleMask?.get(flightsTable.hex[i]) || 0;
  }
  return flightsTable;
}

export function getFlightsTable() { return flightsTable; }

/** Int32Array of per-flight role bitmasks, index-aligned with getFlightsTable(). */
export function getFlightMasks() { return flightMasks; }

/** Build a bitmask from a Set/iterable of catalog role ids. */
export function rolesToMask(roleIds) {
  let mask = 0;
  for (const id of roleIds) mask |= (1 << id);
  return mask;
}

export function lookupFlight(legId) {
  if (!flightsTable || !flightsIndex) return null;
  const i = flightsIndex.get(legId);
  if (i === undefined) return null;
  const mask = flightMasks[i];
  const rs = [];
  for (let b = 0; b < 31; b++) if (mask & (1 << b)) rs.push(b);
  return {
    id: flightsTable.id[i],
    hex: flightsTable.hex[i],
    ti: flightsTable.ti[i],
    rs,
    mask,
    day: flightsTable.day[i],
    cc: flightsTable.cc[i],
    kft: flightsTable.kft[i],
    path: flightsTable.path[i],
    thc: flightsTable.thc[i],
    the: flightsTable.the[i],
    oa: flightsTable.oa[i],
    da: flightsTable.da[i],
    occ: flightsTable.occ[i],
    dcc: flightsTable.dcc[i],
  };
}

// Per-country sighting index for What-If: { hex: { dayOffset(str): first_tod } }.
const sightingIndexCache = {};
const sightingIndexLoading = {};

export function loadSightingIndex(cc) {
  if (sightingIndexCache[cc]) return Promise.resolve(sightingIndexCache[cc]);
  if (sightingIndexLoading[cc]) return sightingIndexLoading[cc];
  const p = fetch(`/data/sightings/${cc}.json`)
    .then(r => (r.ok ? r.json() : {}))
    .then(j => { sightingIndexCache[cc] = j; return j; })
    .catch(() => { sightingIndexCache[cc] = {}; return {}; });
  sightingIndexLoading[cc] = p;
  return p;
}

export function getSightingIndex(cc) { return sightingIndexCache[cc] || null; }

/** Compute day offset from the build start date. */
export function dayOffset(dateStr) {
  const start = new Date(manifest.data_start + "T00:00:00Z");
  const d = new Date(dateStr + "T00:00:00Z");
  return Math.round((d - start) / 86400000);
}

/** Earliest selectable What-If refusal day offset (28 Feb, or 0 if unset). */
export function whatifMinDay() {
  if (!manifest?.whatif_start) return 0;
  return Math.max(0, dayOffset(manifest.whatif_start));
}

// What-If mode and its refusal day, pushed in by whatif.js (which imports
// this module; the reverse import would cycle).
let whatifActive = false;
let whatifDenialDay = null;

export function setWhatIfState(active, denialDay) {
  whatifActive = active;
  whatifDenialDay = active ? denialDay : null;
}

// The refusal day used by both the headline text and the default (non-slider)
// stats table. While What-If mode is active, this is the slider's date. Off,
// it's the later of 28 Feb (whatif_start) or the currently selected date
// filter's start — so narrowing the date filter also narrows what counts as
// "affected."
export function currentDenialDay() {
  if (whatifActive && whatifDenialDay != null) return whatifDenialDay;
  const [dayMinSel] = getDayRange();
  return Math.max(whatifMinDay(), dayMinSel);
}

/** True iff the comma-separated country list `ccStr` contains `cc`. */
export function ccContains(ccStr, cc) {
  return !!ccStr && ("," + ccStr + ",").includes("," + cc + ",");
}

// A flight ending on day `eday` at clock-time `etod` is affected by a refusal
// on day `denial` iff the same aircraft was sighted in the country at any time
// in [denial 00:00, flight end] — i.e. on some full day in [denial, eday-1],
// or on the end day itself at or before the flight's end clock-time.
function isAffected(idx, hex, eday, etod, denial) {
  if (eday < denial) return false;
  const byDay = idx[hex];
  if (!byDay) return false;
  for (let d = denial; d <= eday - 1; d++) {
    if (byDay[d] !== undefined) return true;
  }
  const firstTod = byDay[eday];
  return firstTod !== undefined && firstTod <= etod;
}

// What-If under the active role, date and theater filters. The slider
// (whatif.js), the stats panel and the headline all call this with
// currentDenialDay(), so their numbers agree.
export function computeWhatIf(cc, denial) {
  const ft = getFlightsTable();
  const m = getManifest();
  const masks = getFlightMasks();
  const idx = getSightingIndex(cc);
  const [dayMin, dayMax] = getDayRange();
  const nDays = Math.max(0, dayMax - dayMin + 1);
  if (!ft || !m || !masks || !idx) return emptyWhatIf(nDays, dayMin);

  const activeMask = rolesToMask(getRoles());
  const groups = getRoleGroups();
  const inRange = hexRangeTests(idx, dayMin, dayMax);

  // postTotal = flights still in the air at/after the refusal took effect
  // (ending on or after the refusal day, so flights straddling the refusal
  // midnight count); the denominator for every percentage in the summary
  // and role table. dailyAll tracks the whole start-end period for the
  // daily bar chart.
  const t = {
    prevented: 0, direct: 0, positioning: 0, postTotal: 0,
    preventedIds: new Set(), directIds: new Set(), positioningIds: new Set(), preDenialIds: new Set(),
    // Aircraft (hex) figures for the {{acft}} headline placeholder: the same
    // whole-range basis as computePlanes' Seen column in stats.js, not gated
    // by the denial day. Computed here rather than by importing computePlanes
    // (stats.js already imports this module) since this loop performs the
    // identical per-row test for the per-type breakdown.
    scopeHexes: new Set(), crossedHexes: new Set(), landedHexes: new Set(),
    dailyPrevented: new Array(nDays).fill(0), dailyDirect: new Array(nDays).fill(0),
    dailyPositioning: new Array(nDays).fill(0), dailyAll: new Array(nDays).fill(0),
  };
  // Union semantics: a flight counts once toward the totals, once toward
  // every per-role row of its active roles (rows legitimately overlap), and
  // once toward every per-group row whose mask it intersects (so a flight
  // with two Airlift roles counts once in Airlift's row). The per-type
  // breakdown rides along in the same pass; a separate scan would be a
  // second full pass over the flights table on every filter change.
  const roleAccum = new Map();
  const groupAccum = new Map();
  const typeAccum = new Map();

  for (let i = 0; i < ft.n; i++) {
    const day = ft.day[i];
    if (day < dayMin || day > dayMax) continue;
    const flightMask = masks[i] & activeMask;
    if (flightMask === 0) continue;
    if (!passesAdvancedFilters({ thc: ft.thc[i], the: ft.the[i] })) continue;
    const d = day - dayMin;
    t.dailyAll[d]++;

    const roleRows = [];
    for (let bit = 0; bit < 31; bit++) {
      if (flightMask & (1 << bit)) roleRows.push(getOrCreate(roleAccum, bit, () => newBreakdownRow(0)));
    }
    const groupRows = [];
    for (const g of groups) {
      if ((flightMask & groupMask(g.id)) !== 0) groupRows.push(getOrCreate(groupAccum, g.id, () => newBreakdownRow(nDays)));
    }
    const typeRow = getOrCreate(typeAccum, ft.ti[i], () => newTypeRow(nDays));
    const rows = [...roleRows, ...groupRows, typeRow];

    // Planes scope/crossed/landed: a standing "was this aircraft in the
    // fleet / ever seen / ever landed here" question over the whole range,
    // independent of the denial day.
    const hex = ft.hex[i];
    typeRow.scopeHexes.add(hex);
    t.scopeHexes.add(hex);
    if (inRange.crossed(hex)) { typeRow.crossedHexes.add(hex); t.crossedHexes.add(hex); }
    if (inRange.landed(hex)) { typeRow.landedHexes.add(hex); t.landedHexes.add(hex); }

    const eday = ft.eday[i];
    if (eday < denial) {
      if (ccContains(ft.cc[i], cc)) t.preDenialIds.add(ft.id[i]);
      continue;
    }
    t.postTotal++;
    for (const r of rows) r.postTotal++;

    const etod = ft.etod[i];
    // Direct: the flight's own trace crosses the country at/after refusal
    // 00:00. For a flight starting on/after the refusal day that follows
    // from cc membership alone; one already in progress at refusal midnight
    // additionally needs an observed sighting after 00:00.
    const isDirect = ccContains(ft.cc[i], cc) &&
      (day >= denial || isAffected(idx, hex, eday, etod, denial));
    // Positioning: the plane had flown over the country earlier (on or
    // after the refusal), so this flight could not have started where it
    // did. An inference, not a certainty.
    const isPositioning = !isDirect && isAffected(idx, hex, eday, etod, denial);
    if (!isDirect && !isPositioning) continue;

    const kind = isDirect ? "direct" : "positioning";
    t[kind]++;
    t[`${kind}Ids`].add(ft.id[i]);
    (isDirect ? t.dailyDirect : t.dailyPositioning)[d]++;
    for (const r of rows) r[kind]++;
    t.prevented++;
    t.preventedIds.add(ft.id[i]);
    t.dailyPrevented[d]++;
    // Daily histogram source (StatsBreakdownTab): one combined count per
    // day per group/type; nothing downstream needs the two halves per day.
    for (const r of groupRows) r.dailyPrevented[d]++;
    typeRow.dailyPrevented[d]++;
  }

  return {
    prevented: t.prevented, direct: t.direct, positioning: t.positioning, postTotal: t.postTotal,
    crossedAircraft: t.crossedHexes.size, scopeAircraft: t.scopeHexes.size, landedAircraft: t.landedHexes.size,
    preventedIds: t.preventedIds, directIds: t.directIds, positioningIds: t.positioningIds, preDenialIds: t.preDenialIds,
    dailyPrevented: t.dailyPrevented, dailyDirect: t.dailyDirect, dailyPositioning: t.dailyPositioning, dailyAll: t.dailyAll,
    byRole: finishByRole(roleAccum),
    byGroup: finishByGroup(groupAccum, groups),
    byType: finishByType(typeAccum, m.types || []),
    dates: dateLabels(m, dayMin, dayMax),
    dayMin,
  };
}

function emptyWhatIf(nDays, dayMin) {
  return {
    prevented: 0, direct: 0, positioning: 0,
    postTotal: 0, crossedAircraft: 0, scopeAircraft: 0, landedAircraft: 0,
    preventedIds: new Set(), directIds: new Set(), positioningIds: new Set(), preDenialIds: new Set(),
    dailyPrevented: new Array(nDays).fill(0),
    dailyDirect: new Array(nDays).fill(0),
    dailyPositioning: new Array(nDays).fill(0),
    dailyAll: new Array(nDays).fill(0),
    byRole: [], byGroup: [], byType: [], dates: [], dayMin,
  };
}

/** Per-hex, whole-range sighting tests against the country's sighting
 * index, memoised for one computeWhatIf call (the same hex recurs across
 * every flight of that aircraft). The index's per-hex "g" list is the
 * pipeline's corroborated landing days for the country. */
function hexRangeTests(idx, dayMin, dayMax) {
  const crossedMemo = new Map();
  const landedMemo = new Map();
  return {
    crossed(hex) {
      if (!crossedMemo.has(hex)) {
        const byDay = idx[hex];
        crossedMemo.set(hex, !!byDay && Object.keys(byDay).some(d => +d >= dayMin && +d <= dayMax));
      }
      return crossedMemo.get(hex);
    },
    landed(hex) {
      if (!landedMemo.has(hex)) {
        const byDay = idx[hex];
        landedMemo.set(hex, !!byDay?.g && byDay.g.some(d => d >= dayMin && d <= dayMax));
      }
      return landedMemo.get(hex);
    },
  };
}

function getOrCreate(map, key, make) {
  let v = map.get(key);
  if (v === undefined) { v = make(); map.set(key, v); }
  return v;
}

// Role rows carry no daily series (nDays 0); group rows do.
function newBreakdownRow(nDays) {
  const row = { direct: 0, positioning: 0, postTotal: 0 };
  if (nDays > 0) row.dailyPrevented = new Array(nDays).fill(0);
  return row;
}

function newTypeRow(nDays) {
  return {
    direct: 0, positioning: 0, postTotal: 0,
    scopeHexes: new Set(), crossedHexes: new Set(), landedHexes: new Set(),
    dailyPrevented: new Array(nDays).fill(0),
  };
}

const byPreventedDesc = (a, b) => (b.direct + b.positioning) - (a.direct + a.positioning);

function finishByRole(roleAccum) {
  return [...roleAccum.entries()]
    .map(([rid, counts]) => ({ ri: rid, label: roleLabel(rid), ...counts }))
    .sort(byPreventedDesc);
}

function finishByGroup(groupAccum, groups) {
  return [...groupAccum.entries()]
    .map(([gid, counts]) => {
      const g = groups.find(gr => gr.id === gid);
      return { gi: gid, label: g?.label || `group ${gid}`, ...counts };
    })
    .sort(byPreventedDesc);
}

// Labels key off manifest.types[] by ti ("Unknown type" for -1): the
// resolved type_code's label, never a per-aircraft `desc`, which can name a
// different family than the resolved type_code.
function finishByType(typeAccum, types) {
  return [...typeAccum.entries()]
    .map(([tid, counts]) => {
      const t = tid >= 0 ? types[tid] : null;
      const { scopeHexes, crossedHexes, landedHexes, ...rest } = counts;
      return {
        ti: tid,
        code: t?.code ?? null,
        label: t?.label ?? t?.code ?? "Unknown type",
        crossed: crossedHexes.size, scope: scopeHexes.size, landed: landedHexes.size,
        ...rest,
      };
    })
    .sort(byPreventedDesc);
}

function dateLabels(m, dayMin, dayMax) {
  const dates = [];
  for (let d = dayMin; d <= dayMax; d++) dates.push(m.dates?.[d] || "");
  return dates;
}

function formatPct(n) {
  const s = n.toFixed(1);
  return s.endsWith(".0") ? s.slice(0, -2) : s;
}

/** Date object for a day offset from data_start — the one place that
 * actually does the offset arithmetic; dayToDateStr/dayLabel below and
 * every other call site across the frontend format this same Date rather
 * than each re-deriving it. */
function dayToDateObj(day) {
  const d = new Date(manifest.data_start + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + day);
  return d;
}

/** ISO date string (YYYY-MM-DD) for a day offset from data_start. */
export function dayToDateStr(day) {
  return dayToDateObj(day).toISOString().slice(0, 10);
}

/** "28 Feb 2026"-style label for a day offset (matches headline prose). */
export function dayLabel(day) {
  return dayToDateObj(day).toLocaleDateString("en-GB",
    { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

// Fill the {{acft}}/{{comb}}/{{dir}}/{{pos}} placeholders in a country headline
// template from computeWhatIf, using currentDenialDay() — the same call the
// dynamic stats table makes, so the headline always matches what the table
// shows. Shared by the sidebar info panel (panel-data.js) and the map hover
// tooltip (map.js) so both stay in sync. If the sighting index for `cc`
// isn't loaded yet, kicks off the (cached) load and returns the text with
// placeholders shown as an ellipsis; `pending` resolves once the real
// numbers are available, so the caller can re-render then.
export function fillHeadlineStats(cc, text) {
  if (!text.includes("{{")) return { text, pending: null };
  // Date tokens need no sighting index — resolve them first so even the
  // ellipsis-placeholder state shows the real dates. {{date}} is the denial
  // anchor the percentages are computed from; {{end}} is the effective end
  // of the selected range.
  const [, dayMaxSel] = getDayRange();
  const endDay = Math.min(dayMaxSel, dayOffset(manifest.data_end));
  text = text
    .replace(/\{\{date\}\}/g, dayLabel(currentDenialDay()))
    .replace(/\{\{end\}\}/g, dayLabel(endDay));
  if (!getSightingIndex(cc)) {
    return {
      text: text
        .replace(/\{\{(acft|comb|dir|pos)\}\}/g, "…")
        .replace(/\{\{flights_and_landings\}\}/g, " and ~…% of individual flights"),
      pending: loadSightingIndex(cc),
    };
  }
  const result = computeWhatIf(cc, currentDenialDay());
  const denom = result.postTotal;
  const combPct = denom > 0 ? formatPct(100 * result.prevented / denom) : "0";
  const dirPct = denom > 0 ? formatPct(100 * result.direct / denom) : "0";
  const posPct = denom > 0 ? formatPct(100 * result.positioning / denom) : "0";
  const acftDenom = result.scopeAircraft;
  const acftPct = acftDenom > 0 ? formatPct(100 * result.crossedAircraft / acftDenom) : "0";
  const landPct = acftDenom > 0 ? (100 * result.landedAircraft / acftDenom) : 0;
  const landFloor = Math.floor(landPct);
  const flightsAndLandings = landFloor >= 5
    ? `, ~${combPct}% of individual flights and more than ~${landFloor}% of landings`
    : ` and ~${combPct}% of individual flights`;
  const resolved = text
    .replace(/\{\{flights_and_landings\}\}/g, flightsAndLandings)
    .replace(/\{\{acft\}\}/g, acftPct)
    .replace(/\{\{comb\}\}/g, combPct)
    .replace(/\{\{dir\}\}/g, dirPct)
    .replace(/\{\{pos\}\}/g, posPct);
  return { text: resolved, pending: null };
}
