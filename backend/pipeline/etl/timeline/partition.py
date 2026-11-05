"""Ternary profile + interval partition for one aircraft's crop-interval trace.

Each bin is OFF (no data) / GROUND (on_ground flag) / AIR (else).
- Ground interval: a maximal non-air run containing >=1 ground bin
  (floodfill from ground through ground/off, stop at air).
- Airborne interval: the complement runs (purely air/off, no ground).
- Blackout: an interior off run flanked by air on both sides inside an
  airborne interval -> a flight-split candidate.

The stage runner (run_partition) reads data/build/ingest traces for relevant aircraft
and writes data/build/etl/intervals.parquet (kind: ground|air, per-aircraft
interval_id among same-kind intervals), and blackouts.parquet (blackouts
>15 min; shorter off runs are definite joins and never emitted).
No flight assembly, no split/join verdicts.
"""
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backend.util import dimensions
from backend.pipeline.etl.constants import AIRCRAFT_BATCH, BIN_S, BLACKOUT_MIN_S
from backend.util.geo.haversine import haversine_km
from backend.util.paths import etl_dir
from backend.util.log import log

OFF, GROUND, AIR = 0, 1, 2


def _runs(arr):
    """[(value, start, end_inclusive), ...] of maximal equal-value runs."""
    if len(arr) == 0:
        return []
    cut = np.flatnonzero(np.diff(arr)) + 1
    edges = np.concatenate(([0], cut, [len(arr)]))
    return [(int(arr[edges[i]]), int(edges[i]), int(edges[i + 1] - 1))
            for i in range(len(edges) - 1)]


def profile_from_points(ts, lat, lon, on_ground, t0, bin_s, n_bins):
    """Build the ternary state array plus per-bin coords for boundary facts.

    Ground detection uses the on_ground flag exclusively. It is never null in
    this dataset (structurally encoded in ADS-B message type: surface TC 5-8
    vs airborne TC 9-22) and hardware-sourced (weight-on-wheels squat switch,
    mandated for all aircraft >6000 lbs MTOW).

    A gs<40 fallback was evaluated and rejected: it misclassifies helicopter
    hover as ground and solves a problem (missing on_ground) that doesn't exist
    in this data (0% null rate across 55M points).
    """
    bins = ((ts - t0) // bin_s).astype(np.int64)
    keep = (bins >= 0) & (bins < n_bins)
    bins = bins[keep]
    og = on_ground[keep]

    state = np.zeros(n_bins, dtype=np.int8)
    state[bins] = AIR
    state[bins[og]] = GROUND

    blat = np.full(n_bins, np.nan)
    blon = np.full(n_bins, np.nan)
    bts = np.full(n_bins, np.nan)
    blat[bins] = lat[keep]
    blon[bins] = lon[keep]
    bts[bins] = ts[keep]
    return state, blat, blon, bts


def partition(state):
    """Partition a ternary state array into (ground_intervals,
    airborne_intervals), each a list of (start, end_inclusive) bin spans."""
    non_air = (state != AIR).astype(np.int8)
    is_ground = state == GROUND
    grounded = np.zeros(len(state), dtype=np.int8)
    for val, a, b in _runs(non_air):
        if val and is_ground[a:b + 1].any():
            grounded[a:b + 1] = 1
    ground_intervals = [(a, b) for v, a, b in _runs(grounded) if v]
    airborne_intervals = [(a, b) for v, a, b in _runs(grounded) if not v]
    return ground_intervals, airborne_intervals


def interior_blackouts(state, airborne_interval):
    """Blackouts (off runs flanked by air) inside an airborne interval
    (start, end_inclusive)."""
    a, b = airborne_interval
    out = []
    for val, sa, sb in _runs(state[a:b + 1]):
        if val == OFF and sa > 0 and sb < (b - a):
            out.append((a + sa, a + sb))
    return out


# ---------------------------------------------------------------------------
# Stage runner
# ---------------------------------------------------------------------------

def _crop_interval():
    t0 = int(datetime.strptime(dimensions.start(), "%Y-%m-%d")
             .replace(tzinfo=timezone.utc).timestamp())
    t1 = int(datetime.strptime(dimensions.end(), "%Y-%m-%d")
             .replace(tzinfo=timezone.utc).timestamp()) + 86400
    return t0, t1, (t1 - t0) // BIN_S


def _partition_aircraft(ic, g, t0, n_bins, crop):
    """One aircraft -> (interval rows, blackout rows)."""
    in_box = ((g.lat >= crop["lat_south"]) & (g.lat <= crop["lat_north"]) &
              (g.lon >= crop["lon_west"]) & (g.lon <= crop["lon_east"]))
    if not in_box.any():
        return [], []           # never touches the crop box -> not relevant
    state, blat, blon, bts = profile_from_points(
        g.ts_s.values, g.lat.values, g.lon.values,
        g.on_ground.values, t0, BIN_S, n_bins)
    ground_ivs, air_ivs = partition(state)
    ivs = []
    for kind, group in (("ground", ground_ivs), ("air", air_ivs)):
        for iv_id, (a, b) in enumerate(group):
            seg = state[a:b + 1]
            ivs.append((ic, kind, iv_id, t0 + a * BIN_S, t0 + (b + 1) * BIN_S,
                        int((seg == GROUND).sum()), int((seg == AIR).sum()),
                        int((seg == OFF).sum())))
    blackouts = []
    for interval_id, air_iv in enumerate(air_ivs):
        for ps, pe in interior_blackouts(state, air_iv):
            i, j = ps - 1, pe + 1   # flanking air bins
            gap = bts[j] - bts[i]
            if gap <= BLACKOUT_MIN_S:   # first-pass join: not a blackout
                continue
            blackouts.append((ic, interval_id, float(bts[i]), float(blat[i]),
                              float(blon[i]), float(bts[j]), float(blat[j]),
                              float(blon[j]), float(gap),
                              float(haversine_km(blat[i], blon[i], blat[j], blon[j]))))
    return ivs, blackouts


def run_partition():
    """Partition every relevant aircraft's crop-interval trace, in bounded
    batches so the full crop-window trace is never resident in memory at
    once (see constants.AIRCRAFT_BATCH)."""
    from backend.pipeline.etl.loader import load_traces

    aircraft_path = etl_dir() / "aircraft.parquet"
    if not aircraft_path.exists():
        log("partition: aircraft.parquet not found — run types first")
        return

    t0, _, n_bins = _crop_interval()
    crop = dimensions.crop()
    aircraft = pd.read_parquet(aircraft_path)
    keep = aircraft[~aircraft.excluded]
    n_rotary = int((aircraft.excluded & aircraft.rotary).sum())
    n_non_aircraft = int((aircraft.excluded & ~aircraft.rotary).sum())
    log(f"partition: excluded {len(aircraft) - len(keep)} aircraft "
         f"(rotary={n_rotary}, non_aircraft={n_non_aircraft}); "
         f"{len(keep)} remain")
    type_by_hex = dict(zip(keep.icao24, keep.type_code.fillna("")))
    all_hexes = sorted(type_by_hex)

    iv_rows, blackout_rows = [], []
    in_bbox_hexes = set()
    for i in range(0, len(all_hexes), AIRCRAFT_BATCH):
        batch = set(all_hexes[i:i + AIRCRAFT_BATCH])
        T = load_traces(batch)
        for ic, g in T.groupby("icao24", sort=False):
            ivs, blackouts = _partition_aircraft(ic, g, t0, n_bins, crop)
            if ivs:
                in_bbox_hexes.add(ic)
            iv_rows.extend(ivs)
            blackout_rows.extend(blackouts)

    aircraft["in_bbox"] = aircraft["icao24"].isin(in_bbox_hexes)
    # Restore aircraft.parquet's mtime: the staleness chain reads it as
    # the types-lineage timestamp; bumping it would falsely flag denoise
    # stale after every full run.
    census_stat = aircraft_path.stat()
    aircraft.to_parquet(aircraft_path, index=False)
    os.utime(aircraft_path, (census_stat.st_atime, census_stat.st_mtime))
    n_bbox = int(aircraft["in_bbox"].sum())
    log(f"partition: {n_bbox} aircraft have trace points in crop bbox "
         f"({len(keep) - n_bbox} excluded-but-not-rotary had none)")

    intervals = pd.DataFrame(iv_rows, columns=[
        "icao24", "kind", "interval_id", "start_ts", "end_ts",
        "n_ground", "n_air", "n_off"])
    blackouts = pd.DataFrame(blackout_rows, columns=[
        "icao24", "interval_id", "pre_ts", "pre_lat", "pre_lon",
        "post_ts", "post_lat", "post_lon", "gap_s", "gc_km"])
    out = etl_dir()
    out.mkdir(parents=True, exist_ok=True)
    intervals.to_parquet(out / "intervals.parquet", index=False)
    blackouts.to_parquet(out / "blackouts.parquet", index=False)
    _characterize(intervals, blackouts)
    return intervals, blackouts


def _characterize(intervals, blackouts):
    n_af = intervals.icao24.nunique()
    air = intervals[intervals.kind == "air"]
    ground = intervals[intervals.kind == "ground"]
    log(f"partition: timeseries partitioned: {n_af} aircraft")
    log(f"partition:   ground intervals: {len(ground):,}   airborne intervals: {len(air):,}   "
         f"blackouts >15min: {len(blackouts):,}")
    if len(blackouts):
        g = blackouts.gap_s / 60.0
        for q in (10, 50, 90, 99):
            log(f"partition:   blackout gap p{q}: {np.percentile(g, q):.0f} min   "
                 f"gc_km p{q}: {np.percentile(blackouts.gc_km, q):.0f}")
        impl = blackouts.gc_km / (blackouts.gap_s / 3600)
        log(f"partition:   implied speed: <300 slow/loiter={int((impl<300).sum())}  "
             f"300-950 cruise-plausible={int(((impl>=300)&(impl<=950)).sum())}  "
             f">950 impossible-direct={int((impl>950).sum())}")


