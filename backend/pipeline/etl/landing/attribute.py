"""Country traversal and landing attribution.

Three jobs in one pass over the raw traces:

  1. Per-flight country sets: sample each flight's full-fidelity trace
     against the georaster to determine which countries the trajectory
     crosses.  Written as the 'traversed_countries' column on
     flights.parquet.

  2. Sighting times: per (icao24, UTC-day, country) earliest and latest
     timestamps.  Written to sighting_times.parquet.

  3. Landing evidence collection: observed ground intervals (the
     field-free Q1 indication -- speed-gated transponder ground flag or
     low-slow airborne points; maximal runs severed by any non-indicating
     report or blackout-length silence) and per-aircraft window-edge
     states.  These, together with edges.parquet and verdicts.parquet,
     feed the landing engine (engine.py), which writes landings.parquet
     and timeline.parquet.

Flight origin/destination is inherited from the engine's landings table
(single source of truth) via two rules documented on
_inherit_flight_fields.

Country codes are ISO 3166-1 alpha-2; the georaster canonicalizes
UK -> GB.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from backend.pipeline.etl.constants import (
    BLACKOUT_MIN_S, LOW_APPROACH_GS_KT,
    LOWEST_BAND_FT, LOW_SLOW_MSL_FT,
    AIRPORT_TYPE_WEIGHT,
)
from backend.pipeline.etl.loader import (
    dated_trace_partitions,
    index_flights as _index_flights, match_points_to_flights, read_trace_partition,
)
from backend.util.geo.airport_snap import SnapIndex, snap_points
from backend.util.geo.raster import GeoRaster
from backend.util.paths import etl_dir, lookup_dir
from backend.util.log import log


def _accumulate_sightings(sightings: dict, hex24: str, ts_arr, country_ids) -> None:
    """Update the sightings dict from one aircraft's matched trace points.

    Key: (icao24, day_abs, country_id).  Value: [first_ts, last_ts].
    day_abs = floor(ts / 86400) -- the absolute UTC calendar day."""
    valid = country_ids != 0
    if not valid.any():
        return
    vts = ts_arr[valid]
    vcid = country_ids[valid].astype(np.int64)
    vday = np.floor(vts / 86400.0).astype(np.int64)
    agg = (
        pd.DataFrame({"day": vday, "cid": vcid, "ts": vts})
        .groupby(["day", "cid"])["ts"].agg(["min", "max"])
    )
    for (day, cid), row in agg.iterrows():
        key = (hex24, int(day), int(cid))
        cur = sightings.get(key)
        if cur is None:
            sightings[key] = [float(row["min"]), float(row["max"])]
        else:
            cur[0] = min(cur[0], float(row["min"]))
            cur[1] = max(cur[1], float(row["max"]))


def _airport_key(ap: dict) -> str:
    return ap.get("icao") or ap.get("ident") or ""


def _accumulate_observed_intervals(intervals: dict,
                                   obs_runs: dict,
                                   df: pd.DataFrame) -> None:
    """Fold one date partition into per-aircraft observed ground intervals.

    An "indicating" report is either a transponder ground flag (O) or a
    low-slow airborne point (L).  Maximal runs of stream-consecutive
    indicating reports are collected, severed by any non-indicating
    report or by a silence >= BLACKOUT_MIN_S.

    Each interval record carries:
      t_start, t_end   bounding timestamps
      classes           evidence classes seen (O and/or L)
      n                 report count
      open_start/end    whether the run touches the partition edge
                        (licence for cross-partition merging)
      min_alt           lowest altitude in the run
      low[]             bounded sample of the lowest reports (up to 40,
                        sorted by altitude) for the second-pass naming

    Also extends obs_runs: continuous-reception spans (no gap >=
    BLACKOUT_MIN_S), used by the isolation veto and flight origin/dest
    projection."""
    og = df["on_ground"].values.astype(bool)
    gs = df["gs_kt"].values.astype(np.float64)
    alt = df["alt_ft"].values.astype(np.float64)
    with np.errstate(invalid="ignore"):
        og_ok = og & ~(gs > LOW_APPROACH_GS_KT)
        low = (~og & (gs <= LOW_APPROACH_GS_KT) & (alt <= LOW_SLOW_MSL_FT))
    ev = og_ok | low
    srcs = pd.Series(df["source"].fillna("").astype(str).values)
    src_cls = np.where(srcs.str.startswith("mlat"), "M",
              np.where(srcs.str.startswith("tisb"), "T", "A"))
    lat_all = df["lat"].values.astype(np.float64)
    lon_all = df["lon"].values.astype(np.float64)

    for hx, idx in df.groupby("icao24", sort=False).indices.items():
        ts = df["ts_s"].values[idx].astype(np.float64)
        br = np.ones(len(ts), dtype=bool)
        br[1:] = np.diff(ts) >= BLACKOUT_MIN_S
        obs_runs.setdefault(hx, []).extend(
            zip(ts[br].tolist(), ts[np.roll(br, -1)].tolist()))

        e = ev[idx]
        if not e.any():
            continue
        al = alt[idx]
        oo = og_ok[idx]
        n = len(ts)

        runs = []
        start = None
        for j in range(n):
            if not e[j]:
                if start is not None:
                    runs.append((start, j - 1))
                    start = None
            elif start is None:
                start = j
            elif ts[j] - ts[j - 1] >= BLACKOUT_MIN_S:
                runs.append((start, j - 1))
                start = j
        if start is not None:
            runs.append((start, n - 1))

        for a, b in runs:
            seg_alt = al[a:b + 1]
            seg_flag = oo[a:b + 1]
            finite = np.isfinite(seg_alt)
            min_alt = float(seg_alt[finite].min()) if finite.any() else np.nan
            # Sample the lowest reports for the second-pass naming: all
            # on-ground-flagged points, plus any within LOWEST_BAND_FT of
            # the minimum altitude, capped at 40 sorted by altitude.
            keep = seg_flag.copy()
            if np.isfinite(min_alt):
                keep |= finite & (seg_alt <= min_alt + LOWEST_BAND_FT)
            kidx = np.where(keep)[0]
            if len(kidx) > 40:
                order = np.argsort(np.where(np.isfinite(seg_alt[kidx]),
                                            seg_alt[kidx], -1.0))
                kidx = kidx[order[:40]]
            sample = [(float(seg_alt[k]) if np.isfinite(seg_alt[k])
                       else np.nan,
                       float(lat_all[idx[a + k]]), float(lon_all[idx[a + k]]),
                       bool(seg_flag[k])) for k in kidx]
            intervals.setdefault(hx, []).append({
                "t_start": float(ts[a]), "t_end": float(ts[b]),
                "classes": {"O"} if seg_flag.any() else set(),
                "n": int(b - a + 1),
                "open_start": a == 0, "open_end": b == n - 1,
                "min_alt": min_alt, "low": sample,
                "sources": set(np.unique(src_cls[idx[a:b + 1]]).tolist()),
                "has_l": bool((~seg_flag).any()),
            })


def _merge_and_attribute_intervals(intervals: dict, obs_runs: dict,
                                   gi: GeoRaster,
                                   sindex: SnapIndex) -> list:
    """Merge partition-boundary continuations, apply the isolation veto,
    and run the second pass (WHERE): name the airfield from the lowest
    points via weighted snap, else the unanimous raster country, else
    no place.

    Isolation veto: a single-report interval whose continuous-observation
    run is degenerate (no other report of the aircraft within
    BLACKOUT_MIN_S on either side) is dropped -- the lone-ping shape is
    indistinguishable from a stray."""
    iso_lut = gi._id_to_iso2
    out = []
    for hx in sorted(intervals):
        recs = sorted(intervals[hx], key=lambda r: r["t_start"])
        merged = []
        for r in recs:
            m = merged[-1] if merged else None
            if (m and m["open_end"] and r["open_start"]
                    and r["t_start"] - m["t_end"] < BLACKOUT_MIN_S):
                m["t_end"] = r["t_end"]
                m["classes"] |= r["classes"]
                m["sources"] |= r["sources"]
                m["n"] += r["n"]
                m["open_end"] = r["open_end"]
                m["has_l"] |= r["has_l"]
                m["low"].extend(r["low"])
                if not (m["min_alt"] <= r["min_alt"]):   # NaN-aware min
                    m["min_alt"] = r["min_alt"]
            else:
                merged.append(dict(r))
        runs = obs_runs.get(hx, [])
        for m in merged:
            if m["n"] == 1:
                t = m["t_start"]
                if any(a <= t <= b and a == b for a, b in runs):
                    continue                           # isolation veto
            lowpts = [x for x in m["low"]
                      if x[3] or (np.isfinite(m["min_alt"])
                                  and np.isfinite(x[0])
                                  and x[0] <= m["min_alt"] + LOWEST_BAND_FT)]
            # Second-pass naming: weighted vote among snapped airfields,
            # breaking ties by airport-type weight then lexicographic key.
            # Fallback: unanimous raster country of the low points.
            apt = cc = ""
            if lowpts:
                lats = np.array([x[1] for x in lowpts])
                lons = np.array([x[2] for x in lowpts])
                alts = np.array([x[0] for x in lowpts])
                votes: dict = {}
                for ap in snap_points(sindex, lats, lons, alts):
                    if ap is not None:
                        k = _airport_key(ap)
                        v = votes.setdefault(
                            k, [0, AIRPORT_TYPE_WEIGHT.get(
                                ap.get("type", ""), 1.0), ap])
                        v[0] += 1
                if votes:
                    key, v = max(votes.items(),
                                 key=lambda kv: (kv[1][0], kv[1][1], kv[0]))
                    apt, cc = key, v[2].get("country") or ""
                if not apt:
                    u = set(int(c) for c in
                            gi.country_id_at_many(lats, lons))
                    if len(u) == 1 and 0 not in u:
                        cc = iso_lut.get(u.pop()) or ""
            cls = set(m["classes"])
            if m["has_l"]:
                cls.add("L")
            # Representative ground fix for the engine's dark-gap merge:
            # first on-ground-flagged point, else first low point.
            gpt = next((x for x in m["low"] if x[3]),
                       m["low"][0] if m["low"] else None)
            out.append({
                "icao24": hx, "t_start": m["t_start"], "t_end": m["t_end"],
                "kind": "observed", "airport_icao": apt, "cc": cc,
                "evidence": "".join(sorted(cls)),
                "sources": "".join(sorted(m["sources"])),
                "glat": float(gpt[1]) if gpt else np.nan,
                "glon": float(gpt[2]) if gpt else np.nan,
            })
    return out


def _collect_trace_countries(
    flights: pd.DataFrame, by_hex: dict, census_keep: set, gi: GeoRaster,
) -> tuple[dict[int, set[str]], dict, dict, dict]:
    """Walk every raw trace partition; accumulate per-flight country sets,
    sighting timestamps, and observed ground intervals.

    Landing evidence collection is census-scoped (census_keep: every
    non-excluded in-bbox aircraft, with or without flights).
    Sightings and country attribution remain flight-scoped (by_hex)."""
    iso_lut = gi._id_to_iso2
    parts = dated_trace_partitions()
    log(f"attribute: scanning {len(parts)} partitions for full-fidelity traces")

    flight_countries: dict[int, set[str]] = {}
    sightings: dict[tuple, list[float]] = {}
    intervals: dict = {}
    obs_runs: dict = {}

    wstates: dict = {}
    for i, p in enumerate(parts):
        df = read_trace_partition(
            p / "trace.parquet",
            columns=["icao24", "ts_s", "lat", "lon", "alt_ft", "gs_kt",
                     "on_ground", "source", "vert_rate_fpm", "track_deg"],
            keep=census_keep,
        )
        if len(df) == 0:
            continue

        df = df.sort_values(["icao24", "ts_s"],
                            ignore_index=True)
        _accumulate_observed_intervals(intervals, obs_runs, df)
        _accumulate_window_states(wstates, df)

        for hex24, sub in df.groupby("icao24", sort=False):
            if hex24 not in by_hex:
                continue
            ts = sub["ts_s"].values.astype(np.float64)
            matched_fids, ok = match_points_to_flights(by_hex, hex24, ts)
            if not ok.any():
                continue

            matched_ts = ts[ok]
            lats = sub["lat"].values[ok]
            lons = sub["lon"].values[ok]

            country_ids = gi.country_id_at_many(
                lats, lons,
            )

            valid = country_ids != 0
            if valid.any():
                pairs = np.unique(np.stack(
                    [matched_fids[valid].astype(np.int64),
                     country_ids[valid].astype(np.int64)], axis=1), axis=0)
                for fid_int, cid_int in pairs.tolist():
                    iso = iso_lut.get(cid_int)
                    if not iso:
                        continue
                    flight_countries.setdefault(fid_int, set()).add(iso)

            _accumulate_sightings(sightings, hex24, matched_ts, country_ids)

        if (i + 1) % 25 == 0:
            log(f"  ...partition {i + 1}/{len(parts)}")

    # Merge observation runs across partition boundaries: consecutive
    # runs closer than BLACKOUT_MIN_S are one continuously observed span.
    merged: dict = {}
    for hx, runs in obs_runs.items():
        runs.sort()
        out = [list(runs[0])]
        for a, b in runs[1:]:
            if a - out[-1][1] < BLACKOUT_MIN_S:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        merged[hx] = out
    return flight_countries, sightings, intervals, merged, wstates


def _accumulate_window_states(wstates: dict, df) -> None:
    """Track each aircraft's first and last observed AIRBORNE point.

    Used by the engine for W (window-boundary) silences.  Partitions
    arrive in date order and df is time-sorted, so first-seen wins
    and last overwrites."""
    air = (~df.on_ground.astype(bool)
           & df.alt_ft.notna() & df.gs_kt.notna()
           & df.vert_rate_fpm.notna() & df.track_deg.notna())
    for hex24, sub in df[air].groupby("icao24", sort=False):
        first = sub.iloc[0]
        last = sub.iloc[-1]
        st = wstates.setdefault(hex24, {})
        if "first" not in st:
            st["first"] = dict(
                ts=float(first.ts_s), lat=float(first.lat),
                lon=float(first.lon), gs=float(first.gs_kt),
                vr=float(first.vert_rate_fpm), alt=float(first.alt_ft),
                track=float(first.track_deg), source=str(first.source))
        st["last"] = dict(
            ts=float(last.ts_s), lat=float(last.lat), lon=float(last.lon),
            gs=float(last.gs_kt), vr=float(last.vert_rate_fpm),
            alt=float(last.alt_ft), track=float(last.track_deg),
            source=str(last.source))


def _same_observation_run(runs: list, t_a: float, t_b: float) -> bool:
    """True iff both timestamps fall inside ONE continuously observed span.

    This is the ONLY licence for linking claims across time: within
    continuous observation an unobserved flight is impossible by the
    pipeline's own standard; a single blackout-length silence severs the
    link regardless of how close the timestamps are."""
    lo, hi = min(t_a, t_b), max(t_a, t_b)
    for a, b in runs:
        if a - 60.0 <= lo and hi <= b + 60.0:
            return True
        if a > lo:
            break
    return False


def _inherit_flight_fields(flights: pd.DataFrame, ev_times: dict,
                           edge_map: dict, obs_runs: dict) -> pd.DataFrame:
    """Back-fill each flight's origin/destination from landing evidence.

    Two rules, in order of strength:

    1. Direct-evidence inheritance: the nearest landing-evidence timestamp
       before the flight's start (after its end, for destination) --
       accepted ONLY when evidence and flight edge lie in the same
       continuously observed span AND no other observed flight lies
       between them.

    2. Blackout-edge attribution: the flight boundary IS a split
       blackout's boundary point, and that blackout was attributed by
       the silence-edge rule (a claim made AT the edge, not carried
       across the silence)."""
    ev_sorted = {hx: sorted(v) for hx, v in ev_times.items()}
    # Intervening-flight guard: inherited evidence must postdate the
    # aircraft's PREVIOUS observed flight (and predate its NEXT one for
    # destinations) -- evidence separated by another flight says where
    # the plane was before THAT flight, not where this one began.
    fl_sorted = flights.sort_values(["icao24", "t_start"])
    prev_end: dict = {}
    next_start: dict = {}
    last_by_hex: dict = {}
    for r in fl_sorted.itertuples(index=False):
        if r.icao24 in last_by_hex:
            lr = last_by_hex[r.icao24]
            prev_end[(r.icao24, float(r.t_start))] = float(lr.t_end)
            next_start[(r.icao24, float(lr.t_end))] = float(r.t_start)
        last_by_hex[r.icao24] = r

    o_icao, o_cc, o_g, d_icao, d_cc, d_g = [], [], [], [], [], []
    for r in flights.itertuples(index=False):
        ev = ev_sorted.get(r.icao24, [])
        ts_arr = [e[0] for e in ev]
        origin = dest = None
        t_start, t_end = float(r.t_start), float(r.t_end)
        runs = obs_runs.get(r.icao24, [])
        floor_ts = prev_end.get((r.icao24, t_start), -np.inf)
        k = np.searchsorted(ts_arr, t_start + 60.0)
        if (k > 0 and ev[k - 1][0] >= floor_ts - 60.0
                and _same_observation_run(runs, ev[k - 1][0], t_start)):
            origin = (ev[k - 1][1], ev[k - 1][2])
        if origin is None:
            origin = edge_map.get((r.icao24, "post", t_start))
        ceil_ts = next_start.get((r.icao24, t_end), np.inf)
        k = np.searchsorted(ts_arr, t_end - 60.0)
        if (k < len(ev) and ev[k][0] <= ceil_ts + 60.0
                and _same_observation_run(runs, t_end, ev[k][0])):
            dest = (ev[k][1], ev[k][2])
        if dest is None:
            dest = edge_map.get((r.icao24, "pre", t_end))
        o_icao.append(origin[0] if origin else "")
        o_cc.append(origin[1] if origin else "")
        o_g.append(origin is not None)
        d_icao.append(dest[0] if dest else "")
        d_cc.append(dest[1] if dest else "")
        d_g.append(dest is not None)
    flights = flights.copy()
    flights["origin_icao"] = o_icao
    flights["origin_country"] = o_cc
    flights["origin_grounded"] = o_g
    flights["dest_icao"] = d_icao
    flights["dest_country"] = d_cc
    flights["dest_grounded"] = d_g
    return flights


def _run_engine(gi, etl, inbox, obs_rows, obs_runs, wstates=None):
    """Bridge to the landing engine: loads model + edges + verdicts,
    calls run_engine, writes landings.parquet + timeline.parquet.
    Returns the landings frame for flight origin/dest inheritance."""
    import pickle
    from datetime import datetime, timezone
    mpath = etl / "attribution_model.pkl"
    epath = etl / "edges.parquet"
    vpath = etl / "verdicts.parquet"
    if not (mpath.exists() and epath.exists() and vpath.exists()):
        log("attribute: landing engine skipped (model/edges/verdicts missing)")
        return None
    with open(mpath, "rb") as f:
        model = pickle.load(f)
    if "merge" not in model:
        log("attribute: landing engine skipped (model has no merge "
             "calibration — re-run the train stage)")
        return None
    from backend.pipeline.etl.landing.engine import run_engine
    from backend.util import dimensions
    edges_df = pd.read_parquet(epath)
    verdicts = pd.read_parquet(vpath)
    ivp = etl / "intervals.parquet"
    air = None
    if ivp.exists():
        iv = pd.read_parquet(ivp)
        air = iv[iv.kind == "air"][["icao24", "start_ts", "end_ts"]]

    def _epoch(iso):
        return datetime.strptime(iso, "%Y-%m-%d").replace(
            tzinfo=timezone.utc).timestamp()
    window = (_epoch(dimensions.start()), _epoch(dimensions.end()) + 86400.0)
    landings, timeline = run_engine(obs_rows, obs_runs, edges_df, verdicts,
                                    model, gi, inbox, air_intervals=air,
                                    wstates=wstates, window=window)
    landings.to_parquet(etl / "landings.parquet", index=False)
    timeline.to_parquet(etl / "timeline.parquet", index=False)
    n_named = int((landings.field != "").sum())
    n_cc = int((landings.cc != "").sum())
    log(f"attribute: landings.parquet — {len(landings):,} events "
         f"({n_named:,} field-named, {n_cc:,} with country) + "
         f"timeline.parquet {len(timeline):,} rows")
    return landings


def _write_sighting_times(sightings: dict, gi: GeoRaster, etl: Path) -> None:
    """Write per-(icao24, UTC-day, country) earliest/latest sighting times."""
    iso_lut = gi._id_to_iso2
    rows = []
    for (hex24, day_abs, cid), (first_ts, last_ts) in sightings.items():
        iso = iso_lut.get(cid)
        if not iso:
            continue
        rows.append((hex24, int(day_abs), iso, float(first_ts), float(last_ts)))
    df = pd.DataFrame(
        rows, columns=["icao24", "day", "cc", "first_ts", "last_ts"]
    )
    out = etl / "sighting_times.parquet"
    df.to_parquet(out, index=False)
    log(f"attribute: sighting_times.parquet — {len(df):,} (aircraft,day,country) rows")


def run_attribute():
    t0 = time.time()
    etl = etl_dir()
    flights_path = etl / "flights.parquet"
    if not flights_path.exists():
        log("attribute: flights.parquet not found — run flight first")
        return

    flights = pd.read_parquet(flights_path)
    log(f"attribute: {len(flights):,} flights")

    gi = GeoRaster.load(lookup_dir())
    by_hex = _index_flights(flights)
    census = pd.read_parquet(etl / "aircraft.parquet")
    inbox = set(census[~census.excluded
                       & census.get("in_bbox", True)].icao24)   # attribution scope
    census_keep = set(inbox) | set(by_hex)

    (flight_countries, sightings, intervals,
     obs_runs, wstates) = _collect_trace_countries(flights, by_hex,
                                                   census_keep, gi)
    log(f"attribute: {len(flight_countries):,} flights attributed from traces")
    obs_rows = _merge_and_attribute_intervals(intervals, obs_runs, gi,
                                              SnapIndex(gi))
    _write_sighting_times(sightings, gi, etl)

    landings = _run_engine(gi, etl, inbox, obs_rows, obs_runs, wstates)
    if landings is None:
        log("attribute: WARNING no landing artifacts produced — flights "
             "get no origin/dest (run the train stage, then re-run)")

    # Build two lookup structures from the engine's landings for flight
    # origin/dest inheritance:
    #   ev_times: {icao24: [(ts, field, cc), ...]} from observed landings
    #   edge_map: {(icao24, "pre"/"post", ts): (field, cc)} from blackout edges
    ev_times: dict = {}
    edge_map: dict = {}
    if landings is not None:
        for r in landings.itertuples(index=False):
            if not r.field:
                continue
            if r.origin == "observed":
                ev_times.setdefault(r.icao24, []).append(
                    (r.t_arrival, r.field, r.cc))
                if r.t_departure != r.t_arrival:
                    ev_times[r.icao24].append(
                        (r.t_departure, r.field, r.cc))
            else:
                if pd.notna(r.t_arrival):
                    edge_map[(r.icao24, "pre", float(r.t_arrival))] = \
                        (r.field, r.cc)
                if pd.notna(r.t_departure):
                    edge_map[(r.icao24, "post", float(r.t_departure))] = \
                        (r.field, r.cc)

    flights = _inherit_flight_fields(flights, ev_times, edge_map, obs_runs)
    n_og = int(flights.origin_grounded.sum())
    n_dg = int(flights.dest_grounded.sum())
    log(f"attribute: flight fields inherited — origin {n_og:,}, "
         f"dest {n_dg:,} of {len(flights):,}")

    trav_col = []
    for fid in flights["flight_id"].values:
        fid_int = int(fid)
        countries = flight_countries.get(fid_int, set())
        trav_col.append(",".join(sorted(countries)))

    flights["traversed_countries"] = trav_col
    flights.to_parquet(flights_path, index=False)

    elapsed = time.time() - t0
    n_with = sum(1 for v in trav_col if v)
    log(f"attribute: updated {flights_path} "
         f"({n_with:,}/{len(flights):,} flights have country data, {elapsed:.0f}s)")
