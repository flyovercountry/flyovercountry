"""Flight assembly (flight stage).

A flight is a maximal stretch of one aircraft's airborne time that
contains no SPLIT-verdict blackout. Airborne intervals are the
containers: ground intervals and interval edges are hard flight
boundaries; within an airborne interval, each SPLIT blackout cuts at
its boundary timestamps (the last observed point before the blackout
ends one flight; the first observed point after it starts the next).
JOIN blackouts are simply not cut points.

Each flight keeps only observed trace points; a flight is dropped if
none of its points lie inside the pipeline crop box (the partition stage
only screens whole aircraft).

This stage computes NO landing or grounding logic. Each flight's
origin/destination airfield and country are written empty here and
back-filled by the attribute stage from the landing engine
(landings.parquet) — direct-evidence inheritance at observed stays and
engine attribution at silence edges. One source
of truth: the flight popup, the map circles and the stats tables can
never disagree about where an aircraft was on the ground.

Reads  data/build/etl/intervals.parquet, blackouts.parquet, verdicts.parquet,
       aircraft.parquet, data/build/ingest (traces).
Writes data/build/etl/flights.parquet, one row per flight, flight_id
       ordered by (icao24, t_start), minus flights with fewer than
       FLIGHT_MIN_PTS usable (denoise-surviving) trace points; their ids
       go to data/build/etl/flight_dropped.parquet (audit + the stage's
       staleness marker — downstream stages mutate flights.parquet in
       place, so the marker must be a file they never touch). Duration is
       computed transiently for the stage log only.
"""
import numpy as np
import pandas as pd

from backend.util import dimensions
from backend.util.geo.haversine import haversine_km
from backend.util.paths import etl_dir
from backend.pipeline.etl.constants import FLIGHT_MIN_PTS
from backend.util.log import log


def split_windows(iv_start, iv_end, splits):
    """Cut [iv_start, iv_end) at each SPLIT blackout's (pre_ts, post_ts).

    splits must be sorted by pre_ts and lie inside the interval.
    Returns [(w_start, w_end), ...]; interior edges are the blackout
    boundary timestamps (both are observed points, each belonging to
    its side's flight), the outer edges are the bin-aligned interval
    bounds.
    """
    edges = [iv_start]
    for pre_ts, post_ts in splits:
        edges += [pre_ts, post_ts]
    edges.append(iv_end)
    return [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2)]


# Computed per row for the stage log, dropped before the parquet write.
LOG_ONLY_COLS = ["duration_s"]


def _flight_row(icao24, pts, tc, roles):
    """One flight row from its observed points (already windowed).

    origin/dest fields are written EMPTY here: all grounding and airfield
    attribution lives in the landing engine (attribute stage), which
    back-fills these columns onto flights.parquet — single source of
    truth, no flight-aware landing logic anywhere.
    """
    lat, lon = pts.lat.values, pts.lon.values
    path_km = float(np.sum(haversine_km(lat[:-1], lon[:-1],
                                        lat[1:], lon[1:]))) if len(pts) > 1 else 0.0
    return {
        "icao24": icao24,
        "type_code": tc,
        "t_start": float(pts.ts_s.iloc[0]),
        "t_end": float(pts.ts_s.iloc[-1]),
        "duration_s": float(pts.ts_s.iloc[-1] - pts.ts_s.iloc[0]),
        "start_lat": float(lat[0]), "start_lon": float(lon[0]),
        "end_lat": float(lat[-1]), "end_lon": float(lon[-1]),
        "max_alt_ft": float(pts.alt_ft.max()) if pts.alt_ft.notna().any() else None,
        "total_path_km": path_km,
        "origin_icao": "",
        "origin_country": "",
        "origin_grounded": False,
        "dest_icao": "",
        "dest_country": "",
        "dest_grounded": False,
        "roles": roles,
    }


def assemble_aircraft(icao24, air_ivs, cuts, pts, crop, tc, roles):
    """All flights of one aircraft.

    air_ivs: this aircraft's airborne intervals (rows with interval_id,
    start_ts, end_ts). cuts: this aircraft's blackouts+verdicts frame.
    pts: its time-sorted trace points. tc: ICAO type code for the
    type_code column on each flight row. roles: pre-computed roles
    string from aircraft.parquet.
    """
    ts = pts.ts_s.values
    rows = []
    for iv in air_ivs.itertuples():
        b = cuts[cuts.interval_id == iv.interval_id].sort_values("pre_ts")
        splits = list(zip(b[b.verdict == "SPLIT"].pre_ts,
                          b[b.verdict == "SPLIT"].post_ts))
        for w_start, w_end in split_windows(iv.start_ts, iv.end_ts, splits):
            i0 = np.searchsorted(ts, w_start, side="left")
            i1 = np.searchsorted(ts, w_end, side="right")
            w = pts.iloc[i0:i1]
            w = w[w.ts_s < iv.end_ts]      # bin-aligned interval end is exclusive
            if len(w) == 0:
                # Was a hard assert: one pathological aircraft killed the
                # whole stage. This indicates something is wrong with this
                # aircraft's interval/blackout data specifically, so bail
                # out of assembling ITS remaining flights rather than the
                # whole run — other aircraft are unaffected.
                log(f"flight: WARNING empty flight window {icao24} "
                     f"[{w_start}, {w_end}] — skipping remaining flights "
                     f"for this aircraft")
                return rows
            in_crop = ((w.lat >= crop["lat_south"]) & (w.lat <= crop["lat_north"]) &
                       (w.lon >= crop["lon_west"]) & (w.lon <= crop["lon_east"]))
            if not in_crop.any():
                continue                    # never enters the crop box
            rows.append(_flight_row(icao24, w, tc, roles))
    return rows


def run_flight():
    """Assemble all flights, drop those with too few usable trace points,
    and write data/build/etl/flights.parquet (+ flight_dropped.parquet).

    The point-count drop (formerly its own `prune` stage) reuses the
    already-loaded, denoise-filtered traces: a flight with fewer than
    FLIGHT_MIN_PTS surviving points can't form a polyline or establish a
    direction of travel — typically a brief ping at the edge of receiver
    coverage. flight_dropped.parquet records the dropped flight ids and
    doubles as this stage's staleness marker: flights.parquet itself is
    mutated in place downstream (attribute/theater add columns), so the
    marker must be a file no later stage touches."""
    from backend.pipeline.etl.loader import load_traces

    paths = {
        "intervals": etl_dir() / "intervals.parquet",
        "blackouts": etl_dir() / "blackouts.parquet",
        "verdicts": etl_dir() / "verdicts.parquet",
        "aircraft": etl_dir() / "aircraft.parquet",
    }
    prereqs = {"intervals": "partition", "blackouts": "partition",
               "verdicts": "split", "aircraft": "types"}
    for name, p in paths.items():
        if not p.exists():
            log(f"flight: {p.name} not found — run {prereqs[name]} first")
            return

    intervals = pd.read_parquet(paths["intervals"])
    blackouts = pd.read_parquet(paths["blackouts"])
    verdicts = pd.read_parquet(paths["verdicts"])
    aircraft = pd.read_parquet(paths["aircraft"])
    tc_by_hex = dict(zip(aircraft.icao24, aircraft.type_code.fillna("")))
    roles_by_hex = dict(zip(aircraft.icao24, aircraft.roles))
    cuts_all = blackouts.merge(
        verdicts[["icao24", "interval_id", "pre_ts", "verdict"]],
        on=["icao24", "interval_id", "pre_ts"], how="left")
    assert cuts_all.verdict.notna().all(), "blackout without a verdict"

    crop = dimensions.crop()
    hexes = sorted(intervals.icao24.unique())
    # All traces resident at once (unlike partition.py's batched approach)
    T = load_traces(set(hexes))

    rows = []
    for icao24, pts in T.groupby("icao24", sort=False):
        ivs = intervals[intervals.icao24 == icao24]
        air = ivs[ivs.kind == "air"]
        rows.extend(assemble_aircraft(
            icao24, air,
            cuts_all[cuts_all.icao24 == icao24],
            pts, crop,
            tc_by_hex.get(icao24, ""),
            roles_by_hex.get(icao24, "typecode_unknown")))

    if not rows:
        log("flight: zero flights assembled — nothing to write "
             "(check crop bbox and upstream stages)")
        return None
    flights = pd.DataFrame(rows).sort_values(
        ["icao24", "t_start"]).reset_index(drop=True)
    flights["flight_id"] = flights.index.astype("int64")
    _characterize(flights, verdicts)
    published = flights.drop(columns=LOG_ONLY_COLS)

    kept, drop_fids = apply_min_points(published, T)
    log(f"flight: dropped {len(drop_fids)} flights with < "
         f"{FLIGHT_MIN_PTS} usable trace points "
         f"({len(published):,} -> {len(kept):,})")
    kept.to_parquet(etl_dir() / "flights.parquet", index=False)
    pd.DataFrame({"flight_id": drop_fids}).to_parquet(
        etl_dir() / "flight_dropped.parquet", index=False)
    return kept


def apply_min_points(published, T):
    """Drop flights with fewer than FLIGHT_MIN_PTS usable trace points.

    (Formerly the prune stage.) T must already be the denoise-filtered
    point set (load_traces applies drop_excluded), so counting per flight
    here is equivalent to the old prune partition scan. Returns
    (kept_frame, sorted dropped flight ids)."""
    from backend.pipeline.etl.loader import (
        index_flights, match_points_to_flights)
    by_hex = index_flights(published)
    totals: dict[int, int] = {}
    for hex24, sub in T.groupby("icao24", sort=False):
        if hex24 not in by_hex:
            continue
        ts = sub["ts_s"].values.astype("float64")
        matched_fids, ok = match_points_to_flights(by_hex, hex24, ts)
        if not ok.any():
            continue
        uniq, n = np.unique(matched_fids, return_counts=True)
        for fid, c in zip(uniq.tolist(), n.tolist()):
            totals[fid] = totals.get(fid, 0) + c
    drop_fids = sorted(
        int(fid) for fid in published["flight_id"].values
        if totals.get(int(fid), 0) < FLIGHT_MIN_PTS)
    kept = published[~published["flight_id"].isin(set(drop_fids))]
    return kept, drop_fids


def _characterize(flights, verdicts):
    n = len(flights)
    log(f"flight: flights assembled: {n:,} from {flights.icao24.nunique()} aircraft")
    n_split = int((verdicts.verdict == "SPLIT").sum())
    n_join = int((verdicts.verdict == "JOIN").sum())
    log(f"flight:   blackout cuts: {n_split:,} split, {n_join:,} joined over")
    d = flights.duration_s / 3600
    log(f"flight:   duration h p10/p50/p90: {np.percentile(d, 10):.2f}/"
         f"{np.percentile(d, 50):.2f}/{np.percentile(d, 90):.2f}")
    tiny = int((flights.duration_s < 60).sum())
    log(f"flight:   degenerate <60s flights: {tiny} ({100 * tiny / n:.2f}%)")
