/** Data lookups and HTML builders for the sidebar panel. Pure functions
 * except bindChartHover, which wires a rendered daily chart's tooltip. */

import { escapeHtml } from "./text.js";
import {
  getFocusConfig, fillHeadlineStats, countryEntry,
  computeWhatIf, currentDenialDay, dayLabel,
  getSightingIndex, loadSightingIndex,
  getRoleGroups, defaultOnRoleIds, roleLabel, ROLE_DELIMITER,
  hasInfo, hasStats, hasWhatif,
} from "./data.js";
import { computePlanes } from "./stats.js";
import { getDayRange, getRoles, getFilter } from "./filters.js";

// ---------------------------------------------------------------------------
// Data-refresh notification, fired (debounced) when sighting indices or
// headline stats finish loading. app.js's callback bumps panelData's
// refreshTick; further listeners (reference-view.js) register alongside it.
let refreshCb = null;
export function setDataRefreshCallback(cb) { refreshCb = cb; }

const refreshListeners = [];
export function addDataRefreshListener(cb) { refreshListeners.push(cb); }
let refreshTimer = null;
function fireDataRefresh() {
  if (refreshTimer) return;
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    refreshCb?.();
    for (const cb of refreshListeners) cb();
  }, 50);
}

// ---------------------------------------------------------------------------
// Country lookups
// ---------------------------------------------------------------------------

/** Name, permissiveness legend text and the interaction flags for a
 * country, or null. */
export function findCountryClassification(cc) {
  const fc = getFocusConfig();
  const info = countryEntry(cc, fc);
  if (!info) return null;
  const level = fc?.permissivenessLevels?.[String(info.permissiveness)];
  return {
    name: info.name || cc,
    permissiveness: info.permissiveness,
    permissivenessLabel: level?.label || "",
    stats: hasStats(info),
    whatif: hasWhatif(info),
    info: hasInfo(info),
  };
}

const COUNTRY_ARTICLE_RE =
  /\b(Kingdom|Republic|Netherlands|States|Emirates|Philippines|Islands|Isles|Union|Comoros|Bahamas|Gambia|Maldives|Coast)\b/;

export function withArticle(name) {
  if (!name) return "";
  return (COUNTRY_ARTICLE_RE.test(name) ? "the " : "") + name;
}

// ---------------------------------------------------------------------------
// Role URL notation
// ---------------------------------------------------------------------------

export function collectDefaultRoles() {
  return defaultOnRoleIds();
}

export function roleIndicesToGroupNotation(activeRoles) {
  const parts = [];
  getRoleGroups().forEach((group) => {
    const selected = group.roles.filter(id => activeRoles.has(id));
    if (selected.length === 0) return;
    if (selected.length === group.roles.length) {
      parts.push(String(group.id));
    } else {
      for (const id of selected) {
        const pos = group.roles.indexOf(id);
        parts.push(`${group.id}.${pos + 1}`);
      }
    }
  });
  return parts.join(ROLE_DELIMITER);
}

export function groupNotationToRoleIndices(notation) {
  const groups = getRoleGroups();
  const result = new Set();
  for (const part of notation.split(ROLE_DELIMITER)) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const dotIdx = trimmed.indexOf(".");
    const gid = parseInt(dotIdx === -1 ? trimmed : trimmed.slice(0, dotIdx), 10);
    const group = groups.find(g => g.id === gid);
    if (!group) continue;
    if (dotIdx === -1) {
      group.roles.forEach(id => result.add(id));
    } else {
      const ri = parseInt(trimmed.slice(dotIdx + 1), 10) - 1;
      if (ri >= 0 && ri < group.roles.length) result.add(group.roles[ri]);
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function textToParas(text) {
  return text.split(/\n\n+/).map(p => `<p>${escapeHtml(p.trim())}</p>`).join("");
}

export function domainFromUrl(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); }
  catch { return url; }
}

// ---------------------------------------------------------------------------
// Headline / info rendering
// ---------------------------------------------------------------------------

function renderHeadline(text, footnote, closing) {
  let html = text.split("\n\n")
    .map(p => `<p>${escapeHtml(p).replace(
      /flights\*/g,
      'flights<a href="/methodology.html#theater-filter"><sup>*</sup></a>'
    )}</p>`)
    .join("");
  if (closing) html += `<p class="headline-closing">${escapeHtml(closing)}</p>`;
  if (footnote) html += `<p class="headline-footnote">${escapeHtml(footnote)}</p>`;
  return html;
}

function renderGlobalInfo(info) {
  let html = "";
  if (info.sections) {
    for (const sec of info.sections) {
      html += `<div class="info-section-title">${escapeHtml(sec.title)}</div>`;
      html += textToParas(sec.text);
    }
  } else if (info.text) {
    html += textToParas(info.text);
  }
  return html;
}

function renderFactSection(heading, section) {
  let html = "";
  if (heading) html += `<div class="info-heading">${escapeHtml(heading)}</div>`;
  html += textToParas(section.text);
  if (section.sources?.length) {
    html += `<div class="country-sources">` +
      section.sources.map(url =>
        `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${domainFromUrl(url)}</a>`
      ).join(" ") + `</div>`;
  }
  return html;
}

/** Interpolate headline stats ({{date}}/{{comb}} etc.) using live
 *  computeWhatIf + currentDenialDay. Returns { html, pending }
 *  where pending is a Promise that resolves when async sighting
 *  indices arrive (caller should re-render). */
export function interpolateHeadline(cc, text) {
  const { text: out, pending } = fillHeadlineStats(cc, text);
  if (pending) pending.then(fireDataRefresh);
  return { html: out, pending };
}

/** Build the info panel HTML for a given country (or global if cc is null).
 *  Returns { headlineHtml, previewHtml, fullHtml }. */
export function buildInfoHtml(cc, _tick) {
  const fc = getFocusConfig();
  let headlineHtml = "";
  let previewHtml = "";
  let fullHtml = "";

  if (!cc || !fc) {
    if (fc?.info) {
      if (fc.info.headline) {
        headlineHtml = renderHeadline(fc.info.headline, fc.info.footnote, fc.info.closing);
      }
      fullHtml = renderGlobalInfo(fc.info);
    } else {
      previewHtml = '<span style="color:#555">Select a country for details.</span>';
    }
  } else {
    const info = countryEntry(cc, fc);
    if (!info) {
      previewHtml = '<span style="color:#555">No details available.</span>';
    } else {
      // Only the stats-driven intro sentence; the editorial note is on the
      // Summary tab (reference.js backgroundData) and the map tooltip reads
      // the full headline.
      const introText = info.headlineIntro || info.headline;
      if (introText) {
        const interp = interpolateHeadline(cc, introText);
        headlineHtml = renderHeadline(interp.html, info.footnote);
      }
      if (info.counterfactual?.text) fullHtml += renderFactSection("If it denied overflight, then:", info.counterfactual);
      if (info.general?.text) fullHtml += renderFactSection("Background", info.general);
    }
  }

  return { headlineHtml, previewHtml, fullHtml };
}

// ---------------------------------------------------------------------------
// Daily chart
// ---------------------------------------------------------------------------

export function buildDailyChart(title, dailyTraverse, dates) {
  const max = Math.max(1, ...dailyTraverse);
  const chartId = "chart-" + Math.random().toString(36).slice(2, 8);
  const bars = dailyTraverse.map((t, i) => {
    const hPct = (t / max * 100).toFixed(1);
    const dt = dates[i] || "";
    return `<div class="daily-bar" data-chart="${chartId}" data-tip="${dt}: ${t}">
      <div class="daily-bar-stack" style="height:${hPct}%"></div>
    </div>`;
  }).join("");
  const heading = title ? `<div class="daily-chart-label">${title}</div>` : "";
  const tip = `<div class="daily-chart-tip" id="${chartId}-tip"></div>`;
  return { html: `<div class="daily-chart-wrap">${heading}${tip}<div class="daily-chart">${bars}</div></div>`, chartId };
}

export function bindChartHover(chartId) {
  const tip = document.getElementById(chartId + "-tip");
  if (!tip) return;
  const bars = document.querySelectorAll(`.daily-bar[data-chart="${chartId}"]`);
  bars.forEach(bar => {
    bar.addEventListener("mouseenter", () => { tip.textContent = bar.dataset.tip; });
    bar.addEventListener("mouseleave", () => { tip.textContent = ""; });
  });
}

// ---------------------------------------------------------------------------
// Stats tables — per-country
// ---------------------------------------------------------------------------

/** Fetch any missing per-country sighting index (deduped in data.js) and
 * fire the data-refresh notification once loaded. */
export function ensureSightingIndices(ccList) {
  const missing = ccList.filter(cc => !getSightingIndex(cc));
  if (!missing.length) return;
  Promise.all(missing.map(loadSightingIndex)).then(fireDataRefresh);
}

/** Tooltip for the headline percentage, shared by the panel and the mobile bar. */
export function countryHeadlineTooltip() {
  const since = dayLabel(currentDenialDay());
  return `Share of flights that crossed the country, or were of planes that had crossed it, since ${since} — i.e. flights that could not have happened the way they did had the country refused overflight.`;
}

/** The headline percentage: the same computeWhatIf figure as the stats
 * table's Sum column, so the panel and the mobile bar agree. */
export function countryHeadlinePct(cc) {
  ensureSightingIndices([cc]);
  const wi = computeWhatIf(cc, currentDenialDay());
  const pct = wi.postTotal > 0 ? ((wi.direct + wi.positioning) / wi.postTotal) * 100 : 0;
  return { pct, direct: wi.direct, positioning: wi.positioning, postTotal: wi.postTotal };
}

// ---------------------------------------------------------------------------
// Unified stats table. One grid per row (header, subheader, data); the
// columns are a label, then Flights (Sum = Crosses + Previously crossed,
// with operator cells between), Planes (Sky / Ground) and Landings (Share).
// The same header/row builders serve the panel's Total row, the no-country
// per-country table and the reader's per-type/per-group breakdowns; only
// the label column's title differs.
//
// Cells carry stat-group-* classes (and stat-group-start on each group's
// first cell) because the header row spans a group in one node while the
// other rows have one cell per value, so nth-child cannot band them.
// "Previously crossed" is split over two lines with a real newline and
// white-space: pre-line, not a <br>, so textContent keeps the words apart.
// ---------------------------------------------------------------------------
export function unifiedStatsHeader(labelTitle = "Role", labelTooltip = "Aircraft role scope — scoped by the role filters, see Filters.") {
  return `<div class="unified-stats-grid stat-header">
    <span class="stat-label" title="${escapeHtml(labelTooltip)}">${escapeHtml(labelTitle)}</span>
    <span class="stat-col-group-header stat-group-flights stat-group-start">Flights</span>
    <span class="stat-col-group-header stat-group-planes stat-group-start">Planes</span>
    <span class="stat-col-group-header stat-group-landings stat-group-start">Landings</span>
  </div>
  <div class="unified-stats-grid stat-subheader">
    <span></span>
    <span class="stat-col-narrow stat-group-flights stat-group-start" title="Share of flights that flew over the country, or were of planes that had flown over it at some point — the union of Crosses and Previously crossed.">Sum</span>
    <span class="stat-op"></span>
    <span class="stat-col-narrow stat-group-flights" title="Share of flights that flew over the country themselves.">Crosses</span>
    <span class="stat-op"></span>
    <span class="stat-col-narrow stat-group-flights stat-subhead-2line" title="Share of flights flown by planes that had flown over the country at some point, without the flight itself crossing it.">Previously
crossed</span>
    <span class="stat-col-narrow stat-group-planes stat-group-start" title="Share of the currently in-scope fleet ever sighted flying over the country during the selected date range.">Sky</span>
    <span class="stat-col-narrow stat-group-planes" title="Share of the currently in-scope fleet that landed in the country at least once during the selected date range.">Ground</span>
    <span class="stat-col-narrow stat-group-landings stat-group-start" title="Share of all landings (distinct ground stays) that occurred in this country during the selected date range. Hover a row for absolute counts.">Share</span>
  </div>`;
}

// `extraClass` and `dataAttrs` (pre-escaped, e.g. `data-key="7"`) let a
// clickable row carry a selection class and lookup key. `ariaLabel` makes
// the row a keyboard target (role="button" tabindex="0"); callers' delegated
// handlers answer Enter/Space as well as click. `denoms` overrides the
// per-column percentage denominators (sum/cross/prev/seen/ground/landings)
// instead of the row's own postTotal/scope; the breakdown type table passes
// the country's totals so a type reads as a share of the country.
export function unifiedStatsRow(label, wiRow, plRow, bold, extraClass = "", dataAttrs = "", ariaLabel = null, denoms = null) {
  const cls = bold ? " stat-total" : "";
  const { direct, positioning, postTotal } = wiRow;
  const sumDenom = denoms?.sum ?? postTotal;
  const crossDenom = denoms?.cross ?? postTotal;
  const prevDenom = denoms?.prev ?? postTotal;
  const seenDenom = denoms?.seen ?? plRow.scope;
  const groundDenom = denoms?.ground ?? plRow.scope;
  const flewPct = crossDenom > 0 ? ((direct / crossDenom) * 100).toFixed(1) : "0.0";
  const hadPct = prevDenom > 0 ? ((positioning / prevDenom) * 100).toFixed(1) : "0.0";
  const allPct = sumDenom > 0 ? (((direct + positioning) / sumDenom) * 100).toFixed(1) : "0.0";
  const skyPct = seenDenom > 0 ? ((plRow.crossed / seenDenom) * 100).toFixed(1) : "0.0";
  const landed = plRow.landed || 0;
  const groundPct = groundDenom > 0 ? ((landed / groundDenom) * 100).toFixed(1) : "0.0";

  const flewTip = escapeHtml(`${direct.toLocaleString()} / ${crossDenom.toLocaleString()} flights`);
  const hadTip = escapeHtml(`${positioning.toLocaleString()} / ${prevDenom.toLocaleString()} flights`);
  const allTip = escapeHtml(`${(direct + positioning).toLocaleString()} / ${sumDenom.toLocaleString()} flights`);
  const skyTip = escapeHtml(`${plRow.crossed.toLocaleString()} / ${seenDenom.toLocaleString()} aircraft`);
  const groundTip = escapeHtml(`${landed.toLocaleString()} / ${groundDenom.toLocaleString()} aircraft`);
  const eqTip = "Sum = Crosses + Previously crossed";

  const landingEvents = plRow.landingEvents || 0;
  const landingsDenom = denoms?.landings ?? plRow.landingEventsDenom ?? 0;
  const landingsPct = landingsDenom > 0 ? ((landingEvents / landingsDenom) * 100).toFixed(1) : "0.0";
  const landingsTip = escapeHtml(`${landingEvents.toLocaleString()} / ${landingsDenom.toLocaleString()} landings (${landed.toLocaleString()} distinct aircraft)`);

  const a11yAttrs = ariaLabel
    ? ` role="button" tabindex="0" aria-label="${escapeHtml(ariaLabel)}"`
    : "";

  return `<div class="unified-stats-grid${cls}${extraClass ? " " + extraClass : ""}"${dataAttrs ? " " + dataAttrs : ""}${a11yAttrs}>
    <span class="stat-label">${escapeHtml(label)}</span>
    <span class="stat-col-narrow stat-group-flights stat-group-start" title="${allTip}">${allPct}%</span>
    <span class="stat-op" aria-hidden="true" title="${eqTip}">=</span>
    <span class="stat-col-narrow stat-group-flights" title="${flewTip}">${flewPct}%</span>
    <span class="stat-op" aria-hidden="true" title="${eqTip}">+</span>
    <span class="stat-col-narrow stat-group-flights" title="${hadTip}">${hadPct}%</span>
    <span class="stat-col-narrow stat-group-planes stat-group-start" title="${skyTip}">${skyPct}%</span>
    <span class="stat-col-narrow stat-group-planes" title="${groundTip}">${groundPct}%</span>
    <span class="stat-col-narrow stat-group-landings stat-group-start" title="${landingsTip}">${landingsPct}%</span>
  </div>`;
}

/** The panel's table: header plus a single Total row. `wi` is
 * computeWhatIf's result, `pl` computePlanes'. The per-group and per-type
 * rows live in the reader's Statistics entry (stats-breakdown.js). */
function buildUnifiedCountryTable(wi, pl, globalLandingEvents) {
  let html = unifiedStatsHeader();
  const plRow = { ...pl, landingEventsDenom: globalLandingEvents || 0 };
  html += unifiedStatsRow(
    "Total (All Roles)",
    { direct: wi.direct, positioning: wi.positioning, postTotal: wi.postTotal },
    plRow,
    true);
  return html;
}

function buildCountryStatsHTML(stats, cc) {
  ensureSightingIndices([cc]);
  const wi = computeWhatIf(cc, currentDenialDay());
  const pl = computePlanes(cc) || { crossed: 0, scope: 0, landed: 0, landingEvents: 0, byGroup: [] };
  const data = getCountryStatsData();
  return buildUnifiedCountryTable(wi, pl, data?.globalLandingEvents);
}

// ---------------------------------------------------------------------------
// Stats table, no country selected: one row per country, most active first.
// The panel shows the top 8 (`limit`); the reader's Statistics entry shows
// them all.
// ---------------------------------------------------------------------------

let countryStatsCache = null;
let countryStatsCacheKey = "";

function statsFilterKey(countryCodes) {
  const [dayMin, dayMax] = getDayRange();
  const roles = [...getRoles()].sort().join(",");
  const f = getFilter();
  const denial = currentDenialDay();
  const idxLoaded = countryCodes.filter(cc => !!getSightingIndex(cc)).join(",");
  return `${dayMin}:${dayMax}:${roles}:${f.mode}:${f.include}:${denial}:${idxLoaded}`;
}

function getCountryStatsData() {
  const fc = getFocusConfig();
  if (!fc) return null;

  const countryCodes = [];
  const countryMeta = new Map();
  for (const [cc, info] of Object.entries(fc.countries)) {
    if (!hasStats(info)) continue;
    countryCodes.push(cc);
    countryMeta.set(cc, { name: info.name });
  }

  const key = statsFilterKey(countryCodes);
  if (countryStatsCache && countryStatsCacheKey === key) return countryStatsCache;

  ensureSightingIndices(countryCodes);
  const denial = currentDenialDay();
  const rows = countryCodes.map(cc => {
    const wi = computeWhatIf(cc, denial);
    const pl = computePlanes(cc) || { crossed: 0, scope: 0, landed: 0, landingEvents: 0, byGroup: [] };
    return { cc, wi, pl };
  });
  const sorted = rows.sort((a, b) =>
    (b.wi.direct + b.wi.positioning) - (a.wi.direct + a.wi.positioning));
  const globalLandingEvents = rows.reduce((s, r) => s + (r.pl.landingEvents || 0), 0);
  countryStatsCache = { sorted, countryMeta, globalLandingEvents };
  countryStatsCacheKey = key;
  return countryStatsCache;
}

function buildGlobalTableHTML(limit) {
  const data = getCountryStatsData();
  if (!data) return "";
  let html = unifiedStatsHeader("Country", "Focus countries currently in analysis scope — see the map legend for permissiveness.");
  const rows = limit ? data.sorted.slice(0, limit) : data.sorted;
  const gle = data.globalLandingEvents || 0;
  for (const row of rows) {
    const meta = data.countryMeta.get(row.cc);
    const name = meta?.name || row.cc;
    const pl = { ...row.pl, landingEventsDenom: gle };
    html += unifiedStatsRow(name, row.wi, pl, false,
      "country-stat-row", `data-cc="${row.cc}"`, `Select ${name}`);
  }
  return html;
}

export function buildFullGlobalTableHTML() {
  return buildGlobalTableHTML();
}

// ---------------------------------------------------------------------------
// Top-level stats HTML builders (called by StatsSection)
// ---------------------------------------------------------------------------

/** Build stats HTML for a selected country: the daily histogram plus the
 * single "Total (All Roles)" row (see buildUnifiedCountryTable) — the
 * per-role-group breakdown lives in the reader's Statistics entry instead
 * (stats-breakdown.js). Returns { html, chartIds }. */
export function buildCountryStatsHtml(stats, cc) {
  const chartIds = [];
  let html = "";

  if (stats?.dailyTraverse) {
    const chart = buildDailyChart(null, stats.dailyTraverse, stats.dates);
    html += chart.html;
    chartIds.push(chart.chartId);
  }
  html += buildCountryStatsHTML(stats, cc);

  return { html, chartIds };
}

/** Build stats HTML for global view (no country selected): the daily
 * histogram, then one row per analyzed country in the same shape as the
 * selected-country table. Returns { html, chartIds }. */
export function buildGlobalStatsHtml(stats) {
  const chartIds = [];
  let html = "";
  if (stats?.dailyTotal) {
    const chart = buildDailyChart(null, stats.dailyTotal, stats.dates);
    html += chart.html;
    chartIds.push(chart.chartId);
  }
  html += buildGlobalTableHTML(8);
  return { html, chartIds };
}

/** Format summary line from global stats. */
export function formatSummary(globalStats) {
  if (!globalStats) return "";
  const ac = globalStats.aircraft > 0
    ? ` | ${globalStats.aircraft.toLocaleString()} aircraft` : "";
  return `${globalStats.total.toLocaleString()} flights${ac}`;
}

// ---------------------------------------------------------------------------
// What-If role table (used by whatif.js)
// ---------------------------------------------------------------------------

function roleRow(label, direct, positioning, postTotal, bold) {
  const dirPct = postTotal > 0 ? ((direct / postTotal) * 100).toFixed(1) : "0.0";
  const posPct = postTotal > 0 ? ((positioning / postTotal) * 100).toFixed(1) : "0.0";
  const cls = bold ? " stat-header stat-total" : "";
  const dirTip = escapeHtml(`${direct.toLocaleString()} / ${postTotal.toLocaleString()} flights`);
  const posTip = escapeHtml(`${positioning.toLocaleString()} / ${postTotal.toLocaleString()} flights`);
  return `<div class="stat-row whatif-role-row${cls}">
    <span class="stat-label">${escapeHtml(label)}</span>
    <span class="stat-col" title="${dirTip}">${dirPct}%</span>
    <span class="stat-col" title="${posTip}">${posPct}%</span>
  </div>`;
}

export function buildRoleTable(result, crossingTip, hadCrossedTip) {
  const groups = result.byGroup
    .filter(g => g.postTotal > 0)
    .map(g => ({ label: g.label, direct: g.direct, positioning: g.positioning, postTotal: g.postTotal }));
  groups.sort((a, b) => (b.direct + b.positioning) - (a.direct + a.positioning));

  let html = `<div class="stat-row stat-header whatif-role-header">
    <span class="stat-label" title="Aircraft mission role.">Aircraft Role</span>
    <span class="stat-col" title="${escapeHtml(crossingTip)}">Crosses</span>
    <span class="stat-col" title="${escapeHtml(hadCrossedTip)}">Previously crossed</span>
  </div>`;

  html += roleRow("Total (All Roles)", result.direct, result.positioning, result.postTotal, true);
  for (const g of groups) {
    html += roleRow(g.label, g.direct, g.positioning, g.postTotal, false);
  }
  return html;
}

