/** URL query parameter sync.
 *
 * Role URL scheme (see the roles taxonomy in data/in/config/roles.json): `roles=` is a
 * ROLE_DELIMITER ("_", from data.js)-delimited list of numeric tokens
 * against role_catalog.json's groups. Token "N" = all populated roles of
 * the group whose catalog id is N; token "N.M" = the Mth populated role
 * (1-based, catalog order) of that group. Positive union only, no
 * exclusion tokens. No param uses ";". "_" was chosen because it is one of
 * the few characters URLSearchParams.toString() leaves unencoded (with
 * `*-.`), and "." is already the group.member sub-delimiter.
 *
 * This file also owns the single-pass validate+repair path used before
 * any data computation: `checkAndRepair()` walks manifest+catalog only,
 * returns a table-driven list of issues (param, problem, plain-language
 * fix) plus a repaired param object. The repaired object is guaranteed to
 * itself validate clean (checked by the caller, app.js, before it builds
 * a repair link) — idempotence, so a shown "repaired" link never itself
 * shows the "this link has a problem" overlay a second time.
 */
import { getRoleGroups, ROLE_DELIMITER, countryEntry, hasInfo, hasStats, hasWhatif } from "./data.js";
import { MORE_STATS_SLUG } from "./reference.js";
import { GLOBAL_TABS } from "./global-content.js";

// Retired per-country view slugs from old deep links, resolved in readParams
// so nothing downstream sees them. "refusal-coop" must match reference.js's
// COMBINED_SLUG.
const LEGACY_VIEW_ALIASES = { history: "refusal-coop", coop: "refusal-coop" };

function resolveViewAlias(view) {
  return (view && LEGACY_VIEW_ALIASES[view]) || view;
}

export function readParams() {
  const p = new URLSearchParams(window.location.search);
  return {
    cc: p.get("cc") || null,
    start: p.get("start") || null,
    end: p.get("end") || null,
    roles: p.get("roles") || null,
    fm: p.get("fm") || null,
    wi: p.get("wi") || null,
    view: resolveViewAlias(p.get("view")) || null,   // reader tab slug
    cite: p.get("cite") || null,   // highlighted citation ordinal within a sourced-section view
    ex: p.get("ex") || null,   // "1" = Explore mode, desktop only
    // "0"/"1" layer toggles, present only when non-default (flights on, landings off).
    flights: p.get("flights") || null,
    landings: p.get("landings") || null,
  };
}

export function hasParams() {
  return window.location.search.length > 1;
}

const KNOWN_KEYS = new Set(["cc", "start", "end", "roles", "fm", "wi", "view", "cite", "ex", "flights", "landings"]);
// Per-country reader views (must match reference.js's slugs): "background"
// needs prose, the section slugs need cited analysis, MORE_STATS_SLUG needs
// stats. All require a selected country.
const VIEW_SLUGS = new Set(["background", "auth", "refusal-coop", "refuse", MORE_STATS_SLUG]);
const SECTION_VIEWS = new Set(["auth", "refusal-coop", "refuse"]);
// The reader's global entries, valid only without a selected country.
const GLOBAL_VIEW_SLUGS = new Set(GLOBAL_TABS.map(t => t.slug));
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const ROLE_PART_RE = /^\d+(\.\d+)?$/;
const FM_VALUES = ["none", "tasking", "-tasking", "ever", "-ever"];

/** True only for a real calendar date in YYYY-MM-DD form (rejects 2026-02-30). */
function isValidDate(s) {
  if (!DATE_RE.test(s)) return false;
  const [y, m, d] = s.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y
    && dt.getUTCMonth() === m - 1
    && dt.getUTCDate() === d;
}

function clampDate(s, lo, hi) {
  if (s < lo) return lo;
  if (s > hi) return hi;
  return s;
}

/** Group lookup by catalog id (spec: "Group N = catalog group with id N"),
 * not by array position — the catalog happens to keep ids contiguous and
 * 1-based today, but this stays correct if that ever changes. */
function groupById(groups, id) {
  return groups.find(g => g.id === id) || null;
}

function isWiEligible(cc, focusConfig) {
  const info = countryEntry(cc, focusConfig);
  return !!info && hasWhatif(info);
}

const ccHasStats = (info) => !!info && hasStats(info);
const ccHasCited = (info) => !!info?.reference?.refusal_analysis;
const ccHasProse = (info) =>
  !!(info && (info.general?.text || info.counterfactual?.text));

/** A country is selectable by URL iff it has a panel at all — interaction
 * >= 1 (hasInfo), since whatif implies stats implies info. A country with
 * interaction 0 (UA/BY/RU today) has nothing to show and is never selectable. */
function isCcSelectable(cc, fc) {
  const info = countryEntry(cc, fc);
  return !!info && hasInfo(info);
}

/** Validate one roles= token against the catalog. Returns null if valid,
 * else a short reason string. */
function roleTokenProblem(token, groups) {
  if (!ROLE_PART_RE.test(token)) return "not in N or N.M form";
  const dotIdx = token.indexOf(".");
  const gid = parseInt(dotIdx === -1 ? token : token.slice(0, dotIdx), 10);
  const group = groupById(groups, gid);
  if (!group || group.roles.length === 0) return `group ${gid} does not exist`;
  if (dotIdx !== -1) {
    const m = parseInt(token.slice(dotIdx + 1), 10);
    if (m < 1 || m > group.roles.length) {
      return `group ${gid} has no member ${m} (it has ${group.roles.length})`;
    }
  }
  return null;
}

/**
 * Single-pass validate+repair over the raw query string, against manifest +
 * catalog only (no data computation). Returns:
 *   { issues: [{param, problem, fix}], repaired: {cc,start,end,roles,fm,wi,view,cite,ex,flights,landings} }
 * `repaired` always omits params that were dropped (value null), matching
 * writeParams' contract. `problem`/`fix` are plain-language, written for a
 * site visitor rather than a developer.
 */
export function checkAndRepair(manifest, focusConfig, search = window.location.search) {
  const raw = new URLSearchParams(search);
  const issues = [];
  const out = { cc: null, start: null, end: null, roles: null, fm: null, wi: null, view: null, cite: null, ex: null, flights: null, landings: null };
  const ctx = { raw, out, issues, manifest, focusConfig };

  // Order matters: wi and view validate against out.cc, wi against the
  // repaired start/end, cite against the repaired view.
  checkUnknownKeys(ctx);
  checkCc(ctx);
  checkDate(ctx, "start");
  checkDate(ctx, "end");
  checkDateOrder(ctx);
  checkRoles(ctx);
  for (const key of Object.keys(ENUM_PARAMS)) checkEnum(ctx, key);
  checkWi(ctx);
  checkView(ctx);
  checkCite(ctx);
  return { issues, repaired: out };
}

function checkUnknownKeys({ raw, issues }) {
  for (const key of raw.keys()) {
    if (!KNOWN_KEYS.has(key)) {
      issues.push({
        param: key,
        problem: "this is not a parameter this site uses",
        fix: "removed it from the link",
      });
    }
  }
}

function checkCc({ raw, out, issues, focusConfig }) {
  const cc = raw.get("cc");
  if (cc === null) return;
  if (cc && isCcSelectable(cc, focusConfig)) {
    out.cc = cc;
  } else {
    issues.push({
      param: "cc",
      problem: `"${cc}" is not a country this map covers`,
      fix: "removed the country filter",
    });
  }
}

/** start= / end=: a valid date is clamped into the data range, anything
 * else is dropped. */
function checkDate({ raw, out, issues, manifest }, key) {
  const value = raw.get(key);
  if (value === null) return;
  if (!value || !isValidDate(value)) {
    issues.push({
      param: key,
      problem: `"${value}" is not a valid date`,
      fix: `removed the ${key} date`,
    });
    return;
  }
  const clamped = clampDate(value, manifest.data_start, manifest.data_end);
  if (clamped !== value) {
    issues.push({
      param: key,
      problem: `${value} is outside the range of data this site has (${manifest.data_start} to ${manifest.data_end})`,
      fix: `moved the ${key} date to ${clamped}`,
    });
  }
  out[key] = clamped;
}

// Minimal-change rule: do not swap an inverted range, drop the end date.
function checkDateOrder({ out, issues }) {
  if (out.start && out.end && out.start > out.end) {
    issues.push({
      param: "end",
      problem: `end date ${out.end} was before the start date ${out.start}`,
      fix: "removed the end date and kept the start date",
    });
    out.end = null;
  }
}

function checkRoles({ raw, out, issues }) {
  const roles = raw.get("roles");
  if (roles === null) return;
  const groups = getRoleGroups();
  const dropped = [];
  const kept = [];
  const seen = new Set();
  let duplicates = 0;
  for (const part of roles.split(ROLE_DELIMITER)) {
    const token = part.trim();
    if (!token) continue;
    const problem = roleTokenProblem(token, groups);
    if (problem) { dropped.push(`${token} (${problem})`); continue; }
    if (seen.has(token)) { duplicates++; continue; }
    seen.add(token);
    kept.push(token);
  }
  if (duplicates > 0) {
    issues.push({
      param: "roles",
      problem: "the same role selection appears more than once",
      fix: `removed ${duplicates} duplicate${duplicates > 1 ? "s" : ""}`,
    });
  }
  if (dropped.length > 0 && kept.length > 0) {
    issues.push({
      param: "roles",
      problem: `these role selections don't exist: ${dropped.join(", ")}`,
      fix: `kept the other valid role selections (${kept.join(", ")})`,
    });
  } else if (kept.length === 0) {
    // Malformed tokens only, or an empty roles=: either way no valid
    // selection, disclosed and dropped.
    issues.push({
      param: "roles",
      problem: roles ? `no valid role selections in "${roles}"` : "roles is empty",
      fix: "removed the role filter (default roles apply)",
    });
  }
  out.roles = kept.length > 0 ? kept.join(ROLE_DELIMITER) : null;
}

// Small closed enums with no data or cc dependency: the value is either
// one of the listed literals or it is dropped.
const ENUM_PARAMS = {
  fm: {
    values: FM_VALUES,
    problem: (v) => `"${v}" is not a recognized theater filter`,
    fix: "removed the theater filter (default applies)",
  },
  ex: {
    values: ["1"],
    problem: (v) => `"${v}" is not a valid explore-mode value`,
    fix: "removed the explore-mode flag",
  },
  flights: {
    values: ["0", "1"],
    problem: (v) => `"${v}" is not a valid flights toggle value`,
    fix: "removed the flights toggle (default applies)",
  },
  landings: {
    values: ["0", "1"],
    problem: (v) => `"${v}" is not a valid landings toggle value`,
    fix: "removed the landings toggle (default applies)",
  },
};

function checkEnum({ raw, out, issues }, key) {
  const value = raw.get(key);
  if (value === null) return;
  const spec = ENUM_PARAMS[key];
  if (spec.values.includes(value)) out[key] = value;
  else issues.push({ param: key, problem: spec.problem(value), fix: spec.fix });
}

/** wi= needs a What-If-eligible selected country and a day inside
 * [max(whatif_start, start), end) — the end day itself is out of range. */
function checkWi({ raw, out, issues, manifest, focusConfig }) {
  const wi = raw.get("wi");
  if (wi === null) return;
  const drop = (problem) => issues.push({ param: "wi", problem, fix: "removed the What If date" });
  if (!isValidDate(wi)) return drop(`"${wi}" is not a valid date`);
  if (!out.cc) return drop("What If needs a selected country, and none was set");
  if (!isWiEligible(out.cc, focusConfig)) return drop(`What If is not available for "${out.cc}"`);

  const wiFloor = manifest.whatif_start || manifest.data_start;
  const effStart = out.start || manifest.data_start;
  const effEnd = out.end || manifest.data_end;
  const lo = wiFloor > effStart ? wiFloor : effStart;
  if (wi >= lo && wi < effEnd) {
    out.wi = wi;
    return;
  }
  const clamped = wi < lo ? lo : clampDate(wi, lo, effEnd);
  const problem = `${wi} is outside the selected date range (${effStart} to ${effEnd})`;
  // Clamping onto the exclusive end leaves no valid day in the window.
  if (clamped >= effEnd) return drop(problem);
  issues.push({ param: "wi", problem, fix: `moved the What If date to ${clamped}` });
  out.wi = clamped;
}

/** view=: a global entry is valid only without a country, a country view
 * only with one, and then only if that country has the content. */
function checkView({ raw, out, issues, focusConfig }) {
  const rawView = raw.get("view");
  if (rawView === null) return;
  const drop = (problem, fix = "removed the view") => issues.push({ param: "view", problem, fix });
  // A retired slug resolves to its merged successor before validation, so
  // an old deep link is simply valid (LEGACY_VIEW_ALIASES).
  const view = resolveViewAlias(rawView);
  if (GLOBAL_VIEW_SLUGS.has(view)) {
    if (out.cc) drop(`the "${view}" view isn't available while a country is selected`);
    else out.view = view;
    return;
  }
  if (!out.cc) return drop("a view needs a selected country, and none was set");
  if (!VIEW_SLUGS.has(view)) return drop(`"${rawView}" is not a view this site has`);
  const info = countryEntry(out.cc, focusConfig);
  const ok = view === "background" ? ccHasProse(info)
    : view === MORE_STATS_SLUG ? ccHasStats(info)
    : ccHasCited(info);
  if (ok) out.view = view;
  else drop(`the "${view}" view isn't available for this country`, "showed the country's default view instead");
}

// A citation highlight only applies to a sourced section view.
function checkCite({ raw, out, issues }) {
  const cite = raw.get("cite");
  if (cite === null) return;
  const n = Number(cite);
  if (out.view && SECTION_VIEWS.has(out.view) && Number.isInteger(n) && n > 0) {
    out.cite = String(n);
  } else {
    issues.push({ param: "cite", problem: `"${cite}" is not a valid citation reference`, fix: "removed the citation highlight" });
  }
}

function buildSearchParams({ cc, start, end, roles, fm, wi, view, cite, ex, flights, landings }) {
  const p = new URLSearchParams();
  if (cc) p.set("cc", cc);
  if (start) p.set("start", start);
  if (end) p.set("end", end);
  if (roles) p.set("roles", roles);
  if (fm) p.set("fm", fm);
  if (wi) p.set("wi", wi);
  if (view) p.set("view", view);
  if (cite) p.set("cite", cite);
  if (ex) p.set("ex", ex);
  if (flights) p.set("flights", flights);
  if (landings) p.set("landings", landings);
  return p;
}

/** Query string (no leading "?") for a repaired param set — shares
 * serialization with writeParams so the two never drift apart. */
export function repairedQueryString(repaired) {
  return buildSearchParams(repaired).toString();
}

let restoringFromPopstate = false;

export function setRestoringFromPopstate(val) { restoringFromPopstate = val; }

// ex included: entering/exiting Explore should be a back/forward-able
// navigation step, same as selecting a country or entering What-If — see
// the "back button can't exit [Explore]" defect this closes.
const NAVIGATION_KEYS = ["cc", "wi", "view", "ex"];

export function writeParams(params) {
  const qs = buildSearchParams(params).toString();
  const url = qs ? `?${qs}` : window.location.pathname;

  if (restoringFromPopstate) {
    history.replaceState({ flyover: true }, "", url);
    return;
  }

  const current = readParams();
  const shouldPush = NAVIGATION_KEYS.some(
    k => (params[k] || null) !== (current[k] || null)
  );

  if (shouldPush) {
    history.pushState({ flyover: true }, "", url);
  } else {
    history.replaceState({ flyover: true }, "", url);
  }
}
