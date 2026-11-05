"""Landing engine: turns edge evidence into landing events and a timeline.

This is the orchestration layer.  It consumes the pure modules (footprint,
merge, naming) and the trained model artifact to produce the two landing
artifacts that everything downstream reads:

  landings.parquet   one row per landing event (field, country, tier, odds)
  timeline.parquet   tiled intervals (ground / silence / air) per aircraft

Per aircraft, events come from three sources:

  Observed stays
      Direct ground evidence, tier "observed".  Consecutive stays separated
      by a PURE-DARK gap (no observed points between them) are merged when
      their ground fixes agree -- the G-silence-G rule (97-99% of dark
      ground periods involve no movement).

  Blackouts (air-flanked silences)
      Edge-typed L/C/X by the split stage.  Dispatch:
        L x L  calibrated merge rule (odds table + floor margin), plus a
               confident-disagreement override (split when each edge
               independently names a distinct field).
        L x C / C x L  one-sided event from the L edge.
        C x C  flight (JOIN verdict) or unknown (SPLIT).
        X (transatlantic)  resolves AWAY; any L-side events kept one-sided.

  W (window-boundary) silences
      A trailing descent into silence at the observation window's end is a
      landing (arrival attested, departure open).  A leading climb out of
      pre-window silence records presence but no landing event.

Naming never changes counts: merged events are named jointly; failures
degrade to country (footprint mass) then unplaced.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from backend.pipeline.etl.constants import (
    BLACKOUT_MIN_S, LAND_ALT_FT, LAND_VR_FPM, MERGE_FLOOR_MARGIN,
    SAME_PLACE_KM,
)
from backend.pipeline.etl.grounding import landing_edges_directed
from backend.pipeline.etl.landing import footprint as fp
from backend.pipeline.etl.landing import merge as mg
from backend.pipeline.etl.landing import naming as nm
from backend.util.geo.haversine import haversine_km
from backend.util.log import log

# A stay counts as "at the edge" of an observation run if it's within
# this many seconds of the run boundary.
RUN_SLACK_S = 60.0


def _event(icao24, t_arr, t_dep, tier, origin, **kw):
    """Row factory for a landing event."""
    row = {"icao24": icao24, "t_arrival": t_arr, "t_departure": t_dep,
           "field": "", "cc": "", "tier": tier, "origin": origin,
           "log_odds": np.nan, "override": False, "contradictory": False,
           "merged_n": 1, "field_log": "", "country_log": ""}
    row.update(kw)
    return row


def merge_dark_gap_stays(stays, runs):
    """Group consecutive observed stays separated by pure-dark gaps at the
    same place.

    A gap is "pure dark" when both the preceding and following stays sit
    at the edges of adjacent observation runs with nothing between them.
    Same-place is determined by field snap (if both snap) or by distance
    within SAME_PLACE_KM.

    stays: dicts with t_start, t_end, apt, cc, glat, glon.
    runs: [[a, b], ...] continuous-observation spans, sorted.
    Returns a list of groups (each a list of stay dicts)."""
    if not stays:
        return []
    starts = np.array([r[0] for r in runs]) if runs else np.array([])

    def run_of(t):
        if len(starts) == 0:
            return None
        i = int(np.searchsorted(starts, t, side="right")) - 1
        if i >= 0 and runs[i][0] - RUN_SLACK_S <= t <= runs[i][1] + RUN_SLACK_S:
            return i
        return None

    groups = [[stays[0]]]
    for prev, cur in zip(stays[:-1], stays[1:]):
        gap = cur["t_start"] - prev["t_end"]
        merged = False
        if gap >= BLACKOUT_MIN_S:
            rp, rc = run_of(prev["t_end"]), run_of(cur["t_start"])
            pure_dark = (
                rp is not None and rc is not None and rc == rp + 1
                and abs(runs[rp][1] - prev["t_end"]) <= RUN_SLACK_S
                and abs(runs[rc][0] - cur["t_start"]) <= RUN_SLACK_S)
            if pure_dark:
                if prev["apt"] and cur["apt"]:
                    same = prev["apt"] == cur["apt"]
                elif (np.isfinite(prev.get("glat", np.nan))
                        and np.isfinite(cur.get("glat", np.nan))):
                    same = haversine_km(
                        prev["glat"], prev["glon"], cur["glat"],
                        cur["glon"]) <= SAME_PLACE_KM
                else:
                    same = False
                merged = same
        if merged:
            groups[-1].append(cur)
        else:
            groups.append([cur])
    return groups


class Engine:
    """Processes one aircraft at a time, holding the trained model and
    lookup structures.  Separated from run_engine so the decision logic
    is unit-testable."""

    def __init__(self, model, gi):
        self.model = model
        self.gi = gi
        self.iso = gi._id_to_iso2
        self.flat = model["elig_lat"]
        self.flon = model["elig_lon"]
        self.idents = model["elig_idents"]
        self.lp = model["log_prior"]
        self.tree = BallTree(
            np.radians(np.column_stack([self.flat, self.flon])),
            metric="haversine")
        self.fcid = gi.country_id_at_many(self.flat, self.flon)
        m = model.get("merge")
        self.table = mg.OddsTable(m["odds_log10s"], m["odds_log10gap_h"],
                                  m["odds_log_odds"]) if m else None

    # -- naming helpers ---------------------------------------------------
    def _cands(self, edge):
        return self.tree.query_radius(
            np.radians([[edge["lat"], edge["lon"]]]),
            r=fp.REACH_KM / fp.R_EARTH_KM)[0]

    def _edge_name(self, edge, dirn):
        ci = self._cands(edge)
        post = nm.field_posterior(edge, ci, self.flat, self.flon,
                                  self.model[dirn], self.lp)
        name, log = nm.name_from_posterior(
            post, ci, self.idents, self.model[dirn]["field_R"])
        return name, log, post, ci

    def _field_cc(self, name):
        if not name:
            return ""
        try:
            k = self.idents.index(name)
        except ValueError:
            return ""
        return self.iso.get(int(self.fcid[k])) or ""

    def _country(self, grid, mass):
        cm = fp.country_mass(grid, mass, self.gi)
        cc, clog = nm.name_country(cm, self.iso, self.model["country_R"])
        return cc or "", ";".join(f"{c}:{p:.3f}" for c, p in clog)

    # -- blackout dispatch ------------------------------------------------
    def blackout(self, hex24, row, verdict, named):
        """Process one blackout.  Returns (events, resolution).

        row: edges.parquet row (namedtuple).
        named: whether this aircraft is in naming scope (in-bbox)."""
        sp, sq = landing_edges_directed(
            np.array([row.pre_alt_ft]), np.array([row.pre_vert_rate_fpm]),
            np.array([row.post_alt_ft]), np.array([row.post_vert_rate_fpm]))
        pre_l = bool(sp[0]) and np.isfinite(row.pre_gs_kt) \
            and np.isfinite(row.pre_track_deg)
        post_l = bool(sq[0]) and np.isfinite(row.post_gs_kt) \
            and np.isfinite(row.post_track_deg)
        ea = dict(ts=float(row.pre_ts), lat=float(row.pre_lat),
                  lon=float(row.pre_lon), gs=float(row.pre_gs_kt),
                  vr=float(row.pre_vert_rate_fpm), alt=float(row.pre_alt_ft),
                  track=float(row.pre_track_deg),
                  source=str(getattr(row, "pre_source", ""))) if pre_l else None
        ed = dict(ts=float(row.post_ts), lat=float(row.post_lat),
                  lon=float(row.post_lon), gs=float(row.post_gs_kt),
                  vr=float(row.post_vert_rate_fpm), alt=float(row.post_alt_ft),
                  track=float(row.post_track_deg),
                  source=str(getattr(row, "post_source", ""))) if post_l else None

        if bool(getattr(row, "transatlantic_split", False)):
            ev = []
            if ea:
                ev.append(self._one_sided(hex24, ea, "arr", named))
            if ed:
                ev.append(self._one_sided(hex24, ed, "dep", named))
            return ev, "away"

        if ea and ed:
            return self._two_edged(hex24, ea, ed, named)
        if ea:
            return [self._one_sided(hex24, ea, "arr", named)], "one-sided"
        if ed:
            return [self._one_sided(hex24, ed, "dep", named)], "one-sided"
        return [], ("flight" if verdict == "JOIN" else "unknown")

    def _one_sided(self, hex24, edge, dirn_key, named):
        """Single-edge event: only arrival or departure is attested."""
        dirn = "arr" if dirn_key == "arr" else "dep"
        t_arr = edge["ts"] if dirn == "arr" else np.nan
        t_dep = edge["ts"] if dirn == "dep" else np.nan
        ev = _event(hex24, t_arr, t_dep, "one-edge", "blackout")
        if named:
            name, log, _, _ = self._edge_name(edge, dirn)
            grid = fp.Grid([edge["lat"]], [edge["lon"]])
            mass = fp.l_footprint(grid, edge, self.model[dirn])
            ccc, clog = self._country(grid, mass)
            ev["field"] = name or ""
            ev["cc"] = self._field_cc(name) or ccc
            ev["field_log"] = ";".join(f"{i}:{p:.3f}" for i, p in log)
            ev["country_log"] = clog
        return ev

    def _two_edged(self, hex24, ea, ed, named):
        """Both edges are landing-shaped: run the calibrated merge rule."""
        grid = fp.Grid([ea["lat"], ed["lat"]], [ea["lon"], ed["lon"]])
        ma = fp.l_footprint(grid, ea, self.model["arr"])
        md = fp.l_footprint(grid, ed, self.model["dep"])
        score = fp.overlap(ma, md)
        ca, cd = fp.mass_centre(grid, ma), fp.mass_centre(grid, md)
        sep = haversine_km(ca[0], ca[1], cd[0], cd[1])
        gap = ed["ts"] - ea["ts"]
        if self.table is None:
            verdict, lo = mg.SAME, np.nan
        else:
            verdict, lo = mg.decide(score, gap, sep, self.table,
                                    MERGE_FLOOR_MARGIN)

        # Confident-disagreement override: if the odds say SAME but each
        # edge independently names a different field, trust the naming.
        override = False
        na = nb = None
        if named:
            na, la_, pa, cia = self._edge_name(ea, "arr")
            nb, lb_, pb, cib = self._edge_name(ed, "dep")
            if verdict == mg.SAME and na and nb and na != nb:
                verdict, override = mg.DIFFERENT, True

        if verdict == mg.SAME:
            ev = _event(hex24, ea["ts"], ed["ts"], "two-edge", "blackout",
                        log_odds=lo)
            if named:
                name, log = nm.name_merged(pa, pb, cia, cib, self.lp,
                                           self.idents,
                                           self.model["arr"]["field_R"])
                joint = fp._normalize(ma * md)
                ccc, clog = self._country(grid, joint)
                ev["field"] = name or ""
                ev["cc"] = self._field_cc(name) or ccc
                ev["field_log"] = ";".join(f"{i}:{p:.3f}" for i, p in log)
                ev["country_log"] = clog
            return [ev], "merged"

        if verdict == mg.CONTRADICTORY:
            ev = _event(hex24, ea["ts"], ed["ts"], "two-edge", "blackout",
                        log_odds=lo, contradictory=True)
            return [ev], "contradictory"

        e1 = self._one_sided(hex24, ea, "arr", named)
        e2 = self._one_sided(hex24, ed, "dep", named)
        for e in (e1, e2):
            e["log_odds"] = lo
            e["override"] = override
        return [e1, e2], "multi"


def run_engine(obs_rows, obs_runs, edges_df, verdicts, model, gi, inbox,
               air_intervals=None, wstates=None, window=None):
    """Full pass over all evidence.  Returns (landings_df, timeline_df).

    air_intervals: airborne intervals (icao24, start_ts, end_ts) for
    the timeline tiling.
    wstates: {icao24: {"first": edge-state, "last": edge-state}} for
    window-boundary silences.
    window: (start_ts, end_ts) of the observation crop window."""
    eng = Engine(model, gi)
    events, tl = [], []

    # --- observed stays, grouped across pure-dark gaps ---
    by_hex: dict = {}
    for r in obs_rows:
        by_hex.setdefault(r["icao24"], []).append(dict(
            t_start=r["t_start"], t_end=r["t_end"], apt=r["airport_icao"],
            cc=r["cc"], glat=r.get("glat", np.nan),
            glon=r.get("glon", np.nan)))
    n_darkmerged = 0
    for hex24, stays in by_hex.items():
        stays.sort(key=lambda s: s["t_start"])
        for grp in merge_dark_gap_stays(stays, obs_runs.get(hex24, [])):
            apt = next((s["apt"] for s in grp if s["apt"]), "")
            cc = next((s["cc"] for s in grp if s["cc"]), "")
            events.append(_event(
                hex24, grp[0]["t_start"], grp[-1]["t_end"], "observed",
                "observed", field=apt, cc=cc, merged_n=len(grp)))
            n_darkmerged += len(grp) - 1
            for s in grp:
                tl.append({"icao24": hex24, "t_start": s["t_start"],
                           "t_end": s["t_end"], "state": "ground",
                           "resolution": ""})
            for a, b in zip(grp[:-1], grp[1:]):
                tl.append({"icao24": hex24, "t_start": a["t_end"],
                           "t_end": b["t_start"], "state": "silence",
                           "resolution": "merged-ground"})

    # --- blackouts ---
    vmap = dict(zip(zip(verdicts.icao24, verdicts.interval_id,
                        verdicts.pre_ts), verdicts.verdict))
    n_res: dict = {}
    for row in edges_df.itertuples(index=False):
        v = vmap.get((row.icao24, row.interval_id, row.pre_ts), "SPLIT")
        named = row.icao24 in inbox
        evs, res = eng.blackout(row.icao24, row, v, named)
        n_res[res] = n_res.get(res, 0) + 1
        events.extend(evs)
        tl.append({"icao24": row.icao24, "t_start": float(row.pre_ts),
                   "t_end": float(row.post_ts), "state": "silence",
                   "resolution": res})

    # --- W (window-boundary) silences ---
    n_w = 0
    if wstates and window:
        w0, w1 = window
        for hex24, st in wstates.items():
            # Trailing descent into silence at window end -> landing
            last = st.get("last")
            if (last and w1 - last["ts"] >= BLACKOUT_MIN_S
                    and last["alt"] < LAND_ALT_FT
                    and last["vr"] < -LAND_VR_FPM
                    and np.isfinite(last["gs"]) and np.isfinite(last["track"])):
                ev = eng._one_sided(hex24, last, "arr", hex24 in inbox)
                ev["origin"] = "window"
                events.append(ev)
                n_w += 1
                tl.append({"icao24": hex24, "t_start": last["ts"],
                           "t_end": w1, "state": "silence",
                           "resolution": "window"})
            # Leading climb out of pre-window silence -> presence, not landing
            first = st.get("first")
            if (first and first["ts"] - w0 >= BLACKOUT_MIN_S
                    and first["alt"] < LAND_ALT_FT
                    and first["vr"] > LAND_VR_FPM):
                tl.append({"icao24": hex24, "t_start": w0,
                           "t_end": first["ts"], "state": "silence",
                           "resolution": "window"})

    if air_intervals is not None:
        for r in air_intervals.itertuples(index=False):
            tl.append({"icao24": r.icao24, "t_start": float(r.start_ts),
                       "t_end": float(r.end_ts), "state": "air",
                       "resolution": ""})

    log(f"engine: {len(events):,} landing events "
         f"({n_darkmerged:,} dark-gap stays merged, {n_w:,} window-edge); "
         "blackout resolutions: "
         + ", ".join(f"{k}={v:,}" for k, v in sorted(n_res.items())))
    landings = pd.DataFrame(events, columns=[
        "icao24", "t_arrival", "t_departure", "field", "cc", "tier",
        "origin", "log_odds", "override", "contradictory", "merged_n",
        "field_log", "country_log"])
    timeline = pd.DataFrame(tl, columns=[
        "icao24", "t_start", "t_end", "state", "resolution"])
    return (landings.sort_values(["icao24", "t_arrival", "t_departure"],
                                 ignore_index=True),
            timeline.sort_values(["icao24", "t_start"], ignore_index=True))
