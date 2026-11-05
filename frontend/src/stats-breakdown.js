/** Row data for the reader's Statistics entry: per-aircraft-type rows from
 * computeWhatIf's byType, per-role-group rows from its byGroup joined with
 * computePlanes' byGroup. Rendered with the panel table's own
 * unifiedStatsHeader/unifiedStatsRow. */
import { computeWhatIf, currentDenialDay } from "./data.js";
import { computePlanes } from "./stats.js";
import { ensureSightingIndices } from "./panel-data.js";

function byFlightsDesc(a, b) {
  const aFlights = a.wiRow.direct + a.wiRow.positioning;
  const bFlights = b.wiRow.direct + b.wiRow.positioning;
  return bFlights - aFlights || a.label.localeCompare(b.label);
}

function typeRows(wi) {
  return (wi.byType || [])
    .filter(t => (t.direct + t.positioning) > 0)
    .map(t => ({
      key: `t${t.ti}`,
      label: t.label,
      wiRow: { direct: t.direct, positioning: t.positioning, postTotal: t.postTotal },
      plRow: { crossed: t.crossed, scope: t.scope, landed: t.landed, landingEvents: t.landingEvents || 0 },
      crossed: t.crossed,
      dailyPrevented: t.dailyPrevented,
    }))
    .sort(byFlightsDesc);
}

function groupRows(wi, pl) {
  const plByGi = new Map((pl?.byGroup || []).map(g => [g.gi, g]));
  return (wi.byGroup || [])
    .filter(g => (g.direct + g.positioning) > 0)
    .map(g => {
      const pg = plByGi.get(g.gi) || { crossed: 0, scope: 0, landed: 0, landingEvents: 0 };
      return {
        key: `g${g.gi}`,
        label: g.label,
        wiRow: { direct: g.direct, positioning: g.positioning, postTotal: g.postTotal },
        plRow: { crossed: pg.crossed, scope: pg.scope, landed: pg.landed, landingEvents: pg.landingEvents },
        crossed: pg.crossed,
        dailyPrevented: g.dailyPrevented,
      };
    })
    .sort(byFlightsDesc);
}

/** { types, groups, dates, countryTotals } for country `cc`. `dates`
 * index-aligns with every row's dailyPrevented. `countryTotals` are the
 * country's own flight and aircraft counts, the denominators for the type
 * table's percentages; wi.postTotal and pl.scope count every in-scope
 * flight/aircraft regardless of country and must not be used for that. */
export function buildStatsBreakdown(cc) {
  ensureSightingIndices([cc]);
  const wi = computeWhatIf(cc, currentDenialDay());
  const pl = computePlanes(cc) || { crossed: 0, scope: 0, landed: 0, landingEvents: 0, byGroup: [] };
  return {
    types: typeRows(wi),
    groups: groupRows(wi, pl),
    dates: wi.dates,
    countryTotals: {
      direct: wi.direct,
      positioning: wi.positioning,
      prevented: wi.prevented,
      crossed: pl.crossed,
      landed: pl.landed,
      landingEvents: pl.landingEvents,
    },
  };
}
