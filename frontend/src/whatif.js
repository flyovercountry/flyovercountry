/** "What If?" counterfactual mode. */
import { escapeHtml } from "./text.js";
import {
  getFlightsTable, loadSightingIndex, getSightingIndex,
  computeWhatIf, whatifMinDay, setWhatIfState, dayToDateStr, dayOffset,
} from "./data.js";
import { getDayRange } from "./filters.js";
import { findCountryClassification, withArticle, buildRoleTable } from "./panel-data.js";
import { setWhatIfCorridor, clearWhatIfCorridor, setCountryClickEnabled, showLandings } from "./map.js";
import { mountWhatIfDetail, updateProps, unmount } from "./lib/mount.js";
import { whatif } from "./lib/stores/whatif.js";

let active = false;
let selectedCc = null;
let selectedName = null;
let denialDay = null;
let syncUrlCb = null;
let refreshPanelCb = null;
let mobileSnapCb = null;

let whatifBody = null;

// Shown as the title tooltip on the Export/Explore toolbar buttons while
// they're disabled-overlay'd for What-If (see enter/exitWhatIfMode below).
const WHATIF_DISABLED_TOOLTIP = "Not available in What-If mode - close What-If to use";

export function setWhatIfSyncUrl(cb) { syncUrlCb = cb; }
export function setWhatIfRefreshPanel(cb) { refreshPanelCb = cb; }
export function setWhatIfMobileSnap(cb) { mobileSnapCb = cb; }

export function isWhatIfActive() { return active; }
export function forceExitWhatIf() { exitWhatIfMode(); }

export function getWhatIfDate() {
  if (!active) return null;
  return dayToDateStr(denialDay);
}

export function setWhatIfFromUrl(dateStr) {
  pendingWi = dateStr;
}

let pendingWi = null;

// Set by initWhatIf, which app.js calls only once the map is ready — the
// URL-triggered auto-enter below draws a corridor on the map, so it must
// not run off an earlier refreshPanel (the flights table now loads before
// the map does).
let mapReady = false;

export function initWhatIf() {
  mapReady = true;
}

export function updateWhatIf(cc) {
  const section = document.getElementById("whatif-section");
  if (!section) return;

  if (active && cc !== selectedCc) {
    exitWhatIfMode();
  }

  const classification = cc ? findCountryClassification(cc) : null;
  const [dayMin, dayMax] = getDayRange();
  const effMin = Math.max(dayMin, whatifMinDay());
  const show = classification?.whatif === true && dayMax > effMin;

  if (!show) {
    section.classList.add("hidden");
    document.body.classList.remove("has-whatif");
    if (active) exitWhatIfMode();
    selectedCc = null;
    whatif.set({
      eligible: false, active: false, expanded: false,
      denialDay: null, minDay: 0, maxDay: 0, countryName: '',
    });
    return;
  }

  selectedCc = cc;
  selectedName = classification.name;
  ensureSightingIndex(cc);
  // #whatif-section stays hidden until enterWhatIfMode(); the entry point
  // is the "Refuse?" pill (WhatIfPulldown.svelte), driven by $whatif.eligible.
  document.body.classList.add("has-whatif");

  if (denialDay == null || denialDay < effMin || denialDay >= dayMax) {
    denialDay = Math.min(effMin, dayMax - 1);
  }

  whatif.update(s => ({
    ...s, eligible: true,
    minDay: effMin, maxDay: dayMax - 1,
    denialDay, countryName: selectedName,
  }));

  if (!active) {
    if (pendingWi && mapReady && getFlightsTable()) {
      denialDay = dayOffset(pendingWi);
      pendingWi = null;
      whatif.update(s => ({ ...s, denialDay }));
      mountResults();
      enterWhatIfMode();
    } else {
      mountResults();
    }
  }
}

function mountResults() {
  const body = document.getElementById("whatif-body");
  whatifBody = body;
  unmount(body);
  body.innerHTML = "";
  mountWhatIfDetail(body, {
    onResultsRendered: () => {
      const container = document.getElementById("whatif-results");
      if (container) bindInteractions(container);
    },
  });
}

function enterWhatIfMode() {
  active = true;
  setWhatIfState(true, denialDay);
  document.body.classList.add("whatif-active");
  document.getElementById("whatif-section")?.classList.remove("hidden");

  whatif.update(s => ({ ...s, active: true, expanded: true }));

  document.getElementById("stats-section").style.display = "none";
  document.getElementById("info-section").style.display = "none";

  document.getElementById("filters-section")?.classList.add("whatif-readonly");

  document.querySelectorAll("#map-toolbar button").forEach(b => {
    if (b.id === "share-menu-trigger" || document.getElementById("share-menu")?.contains(b)) return;
    // The pulldown's own collapse button lives in #map-toolbar too and must
    // stay usable.
    if (document.getElementById("toolbar-whatif-mount")?.contains(b)) return;
    b.classList.add("disabled-overlay");
    // Export's trigger and Explore's toggle specifically (not every
    // disabled-overlay button — e.g. the What-If pulldown's own pill lives
    // in #map-toolbar too and needs to stay usable) get an explanatory
    // tooltip. Stash the prior (i18n'd) title in a data-attr so exit can
    // restore it exactly rather than guessing/hardcoding it back.
    if (b.id === "export-menu-trigger" || b.id === "explore-flights-btn") {
      b.dataset.whatifPrevTitle = b.title;
      b.title = WHATIF_DISABLED_TOOLTIP;
    }
  });

  const showSub = document.getElementById("show-subsection");
  if (showSub) showSub.classList.add("disabled-overlay");
  document.getElementById("mobile-filter-btn")?.classList.add("disabled-overlay");

  showLandings(null);
  setCountryClickEnabled(false);
  mobileSnapCb?.(2);
  runAndRender();
  syncUrlCb?.();
  refreshPanelCb?.();
}

function exitWhatIfMode() {
  if (!active) return;
  active = false;
  setWhatIfState(false, null);
  document.body.classList.remove("whatif-active");
  // Re-hide the section on exit; see enterWhatIfMode above.
  document.getElementById("whatif-section")?.classList.add("hidden");

  whatif.update(s => ({ ...s, active: false, expanded: false }));

  document.getElementById("stats-section").style.display = "";
  document.getElementById("info-section").style.display = "";

  document.getElementById("filters-section")?.classList.remove("whatif-readonly");

  document.querySelectorAll("#map-toolbar button").forEach(b => {
    b.classList.remove("disabled-overlay");
    if (b.dataset.whatifPrevTitle !== undefined) {
      b.title = b.dataset.whatifPrevTitle;
      delete b.dataset.whatifPrevTitle;
    }
  });

  const showSub = document.getElementById("show-subsection");
  if (showSub) showSub.classList.remove("disabled-overlay");
  document.getElementById("mobile-filter-btn")?.classList.remove("disabled-overlay");

  setCountryClickEnabled(true);
  clearWhatIfCorridor();

  if (whatifBody) {
    updateProps(whatifBody, { resultsHtml: '' });
  }
  const detail = document.getElementById("whatif-detail");
  if (detail) detail.innerHTML = "";
  mobileSnapCb?.(1);
  syncUrlCb?.();
  refreshPanelCb?.();
}

function runAndRender() {
  const result = computeWhatIf(selectedCc, denialDay);

  setWhatIfCorridor(result.directIds, result.positioningIds, result.preDenialIds, denialDay);

  const postPct = result.postTotal > 0
    ? ((result.prevented / result.postTotal) * 100).toFixed(1) : "0";
  const dateStr = dayToDateStr(denialDay);
  const summaryHtml =
    `<div class="whatif-summary">${buildSummary(result, dateStr, postPct)}</div>`;
  const chartHtml = buildWhatIfChart(result);
  const name = withArticle(selectedName);
  const tableHtml = buildRoleTable(result,
    `Share of flights that flew over ${name} themselves on or after the refusal date — certain to be prevented.`,
    `Share of flights flown by planes that had already flown over ${name} at least once since the refusal date — an inference, not a certainty.`);
  // postPct's denominator is every flight on or after the refusal date,
  // not only those over this country; the caption says so next to the
  // number, which is what gets screenshotted.
  const bigPctHtml =
    `<div class="whatif-big-pct">${postPct}%</div>` +
    `<div class="whatif-big-pct-caption">of <strong>all</strong> flights ` +
    `tracked from ${escapeHtml(dateStr)} onward &mdash; not only those over ` +
    `${escapeHtml(withArticle(selectedName))}</div>`;
  const disclaimerHtml = `<div class="whatif-disclaimer">These results depend on your current filters.</div>`;

  if (whatifBody) {
    const resultsHtml = summaryHtml + chartHtml + tableHtml +
      bigPctHtml + disclaimerHtml;
    updateProps(whatifBody, { resultsHtml });
  }
  const detail = document.getElementById("whatif-detail");
  if (detail) detail.innerHTML = "";
}

function buildSummary(result, dateStr, postPct) {
  const name = escapeHtml(withArticle(selectedName));
  const plain = escapeHtml(selectedName);
  const endStr = dayToDateStr(getDayRange()[1]);
  const dirPct = result.postTotal > 0
    ? ((result.direct / result.postTotal) * 100).toFixed(1) : "0";
  const posPct = result.postTotal > 0
    ? ((result.positioning / result.postTotal) * 100).toFixed(1) : "0";

  // "of flights after that" would leave the denominator unmarked, and
  // the natural misreading is "of that country's flights". Spell out that
  // the base is every tracked flight in the period.
  const s1 = `If ${name} had denied overflight starting ${dateStr}, ` +
    `<strong>${postPct}%</strong> of <em>all</em> flights tracked after that date ` +
    `could not have happened the way they did &mdash; not ${postPct}% of the flights ` +
    `over ${plain}, but of the whole dataset for the period.`;

  const s2 = `<strong>${dirPct}%</strong> of flights between ${dateStr} and ${endStr} flew over ${plain}` +
    `, and another <strong>${posPct}%</strong> of flights were of planes ` +
    `that had flown over ${plain} at least once since ${dateStr}.`;

  return `<p>${s1}</p><p>${s2}</p>` +
    `<p class="headline-closing">Can you stop this war?</p>`;
}

function buildWhatIfChart(result) {
  const max = Math.max(1, ...result.dailyAll);
  const chartId = "whatif-chart-" + Math.random().toString(36).slice(2, 8);
  const denialIdx = denialDay - result.dayMin;

  const bars = result.dailyAll.map((t, i) => {
    const hPct = (t / max * 100).toFixed(1);
    const dt = result.dates[i] || "";
    const p = result.dailyPrevented[i];

    if (i < denialIdx && p === 0) {
      const tip = `${dt}: ${t} flights (before denial)`;
      return `<div class="daily-bar" data-chart="${chartId}" data-tip="${tip}">
        <div class="daily-bar-stack" style="height:${hPct}%">
          <div class="whatif-bar-ok"></div>
        </div>
      </div>`;
    }
    const d = result.dailyDirect[i];
    const pos = result.dailyPositioning[i];
    const ok = t - d - pos;
    const tip = `${dt}: ${d} crossing + ${pos} follow-on / ${t} total`;
    return `<div class="daily-bar" data-chart="${chartId}" data-tip="${tip}">
      <div class="daily-bar-stack" style="height:${hPct}%">
        <div class="whatif-bar-ok" style="flex:${ok}"></div>
        <div class="whatif-bar-positioning" style="flex:${pos}"></div>
        <div class="whatif-bar-prevented" style="flex:${d}"></div>
      </div>
    </div>`;
  }).join("");

  const tip = `<div class="daily-chart-tip" id="${chartId}-tip"></div>`;
  return `<div class="daily-chart-wrap">${tip}<div class="daily-chart">${bars}</div></div>`;
}

function bindInteractions(container) {
  container.querySelectorAll(".daily-bar[data-chart]").forEach(bar => {
    const tipId = bar.dataset.chart + "-tip";
    bar.addEventListener("mouseenter", () => {
      const tip = document.getElementById(tipId);
      if (tip) tip.textContent = bar.dataset.tip;
    });
    bar.addEventListener("mouseleave", () => {
      const tip = document.getElementById(tipId);
      if (tip) tip.textContent = "";
    });
  });
}

function ensureSightingIndex(cc) {
  if (getSightingIndex(cc)) return;
  loadSightingIndex(cc).then(() => {
    if (active && selectedCc === cc) runAndRender();
  });
}

export function whatIfExpand() {
  if (!selectedCc) return;
  enterWhatIfMode();
}

export function whatIfCollapse() {
  exitWhatIfMode();
}

export function whatIfSetDate(dateStr) {
  if (!active) return;
  let day = dayOffset(dateStr);
  // The date <input> (WhatIfPulldown) constrains its own min/max, but a
  // manually typed value can still slip through as out-of-range, and an
  // emptied/garbage value produces NaN here — guard both the same way
  // updateWhatIf's own denialDay clamp does, instead
  // of feeding NaN or an out-of-window day into setWhatIfState/computeWhatIf.
  if (Number.isNaN(day)) return;
  const [dayMin, dayMax] = getDayRange();
  const effMin = Math.max(dayMin, whatifMinDay());
  const effMax = dayMax - 1;
  day = Math.min(Math.max(day, effMin), effMax);

  denialDay = day;
  setWhatIfState(true, denialDay);
  whatif.update(s => ({ ...s, denialDay }));
  runAndRender();
  syncUrlCb?.();
}
