"""Blackout boundary enrichment (edge stage).

For every blackout, extracts the flight state at its two boundaries from
the raw traces (nearest point within 120 s: altitude and smoothed
vertical rate) and derives the columns the verdict rule needs: implied
speed, type code, and the transatlantic flags.

Reads  data/build/etl/blackouts.parquet, aircraft.parquet,
       data/build/ingest/date=*/trace.parquet.
Writes data/build/etl/edges.parquet: the blackouts key +
       geometry columns, pre/post_alt_ft, pre/post_vert_rate_fpm,
       impl_kph, tc, transatlantic_split.
       (Research-only diagnostics — groundspeeds, on-ground flags,
       altitude extremes, airfield/ocean distances, cruise speeds,
       oceanic flags, Atlantic midpoints — were dropped in the column
       slim; the research repo derives its own poi_boundaries.parquet.)
"""
import numpy as np
import pandas as pd

from backend.pipeline.etl.constants import (
    AIRCRAFT_BATCH, TA_MERIDIAN, TA_MIN_GAP_S,
)
from backend.util.paths import etl_dir
from backend.util.log import log

VR_WINDOW = 5      # points considered for the smoothed vertical rate
VR_MIN_RAW = 3     # min raw vert_rate values before falling back to alt-delta
MAX_DT_S = 120     # boundary point must be within this of the boundary ts


def smooth_vr(alts, vr_raw, timestamps, idx, direction):
    """Smoothed vert rate from up to VR_WINDOW points near idx.

    direction=-1 for the pre boundary (look backward), +1 for the post
    boundary (look forward). Uses raw vert_rate_fpm if >= VR_MIN_RAW
    non-null values exist in the window; otherwise derives the rate from
    altitude deltas over the window span; otherwise the mean of whatever
    raw values exist; otherwise NaN.
    """
    n = len(alts)
    if direction == -1:
        lo, hi = max(0, idx - VR_WINDOW + 1), idx + 1
    else:
        lo, hi = idx, min(n, idx + VR_WINDOW)
    window_vr = vr_raw[lo:hi]
    valid_vr = window_vr[~np.isnan(window_vr)]
    if len(valid_vr) >= VR_MIN_RAW:
        return float(np.mean(valid_vr))
    window_alt = alts[lo:hi]
    window_ts = timestamps[lo:hi]
    valid = ~np.isnan(window_alt)
    if valid.sum() >= 2:
        good_alt = window_alt[valid]
        good_ts = window_ts[valid]
        dt = good_ts[-1] - good_ts[0]
        if dt > 0:
            return float((good_alt[-1] - good_alt[0]) / dt * 60.0)
    if len(valid_vr) >= 1:
        return float(np.mean(valid_vr))
    return np.nan


def boundary_props(T, blackouts):
    """Flight state at each blackout's two boundaries.

    T: trace frame [icao24, ts_s, alt_ft, vert_rate_fpm], sorted by
    (icao24, ts_s). Only points within MAX_DT_S of the boundary
    timestamp are used. Returns a dict of column arrays.
    """
    n = len(blackouts)
    pre_alt, post_alt = np.full(n, np.nan), np.full(n, np.nan)
    pre_vr, post_vr = np.full(n, np.nan), np.full(n, np.nan)
    pre_gs, post_gs = np.full(n, np.nan), np.full(n, np.nan)         # for the attribution model
    pre_trk, post_trk = np.full(n, np.nan), np.full(n, np.nan)       # heading, for the relative bearing to candidate fields
    pre_src = np.full(n, "", dtype=object)                           # edge-point receiver class
    post_src = np.full(n, "", dtype=object)                          # (MLAT edges get footprint widening)

    groups = {ic: g for ic, g in T.groupby("icao24", sort=False)}

    for i, row in enumerate(blackouts.itertuples()):
        g = groups.get(row.icao24)
        if g is None:
            continue
        ts = g.ts_s.values
        alts = g.alt_ft.values
        vrs = g.vert_rate_fpm.values
        gss = g["gs_kt"].values if "gs_kt" in g.columns else np.full(len(ts), np.nan)
        trk = g["track_deg"].values if "track_deg" in g.columns else np.full(len(ts), np.nan)
        src = (g["source"].astype(str).values if "source" in g.columns
               else np.full(len(ts), "", dtype=object))

        pre_idx = np.searchsorted(ts, row.pre_ts, side="right") - 1
        if 0 <= pre_idx < len(ts) and abs(ts[pre_idx] - row.pre_ts) <= MAX_DT_S:
            pre_alt[i] = alts[pre_idx]
            pre_vr[i] = smooth_vr(alts, vrs, ts, pre_idx, -1)
            pre_gs[i] = gss[pre_idx]
            pre_trk[i] = trk[pre_idx]
            pre_src[i] = src[pre_idx]

        post_idx = np.searchsorted(ts, row.post_ts, side="left")
        if 0 <= post_idx < len(ts) and abs(ts[post_idx] - row.post_ts) <= MAX_DT_S:
            post_alt[i] = alts[post_idx]
            post_vr[i] = smooth_vr(alts, vrs, ts, post_idx, +1)
            post_gs[i] = gss[post_idx]
            post_trk[i] = trk[post_idx]
            post_src[i] = src[post_idx]

    return {
        "pre_alt_ft": pre_alt, "pre_vert_rate_fpm": pre_vr,
        "post_alt_ft": post_alt, "post_vert_rate_fpm": post_vr,
        "pre_gs_kt": pre_gs, "post_gs_kt": post_gs,
        "pre_track_deg": pre_trk, "post_track_deg": post_trk,
        "pre_source": pre_src, "post_source": post_src,
    }


def enrich(pb, type_by_hex):
    """Add the verdict-rule inputs to a blackout+boundary-props frame.

    pb is modified in place and returned.
    """
    pb["impl_kph"] = pb.gc_km / (pb.gap_s / 3600)
    pb["tc"] = pb.icao24.map(type_by_hex).fillna("")

    # Transatlantic split: endpoints straddle the analysis bbox's western
    # meridian and the silence is long enough. |dlon| < 180 rejects
    # antimeridian-wrapping pairs (e.g. Japan<->Alaska).
    straddle = (pb.pre_lon < TA_MERIDIAN) != (pb.post_lon < TA_MERIDIAN)
    no_wrap = (pb.pre_lon - pb.post_lon).abs() < 180
    pb["transatlantic_split"] = (
        straddle & no_wrap & (pb.gap_s >= TA_MIN_GAP_S))
    return pb


def run_edge():
    """Enrich all blackouts; write data/build/etl/edges.parquet."""
    from backend.pipeline.etl.loader import load_traces

    blackouts_path = etl_dir() / "blackouts.parquet"
    aircraft_path = etl_dir() / "aircraft.parquet"
    if not blackouts_path.exists():
        log("edge: blackouts.parquet not found — run partition first")
        return
    if not aircraft_path.exists():
        log("edge: aircraft.parquet not found — run types first")
        return

    blackouts = pd.read_parquet(blackouts_path)
    aircraft = pd.read_parquet(aircraft_path)
    keep = aircraft[~aircraft.excluded]
    type_by_hex = dict(zip(keep.icao24, keep.type_code.fillna("")))

    # Bounded batches (AIRCRAFT_BATCH per load_traces call) to cap memory
    all_hexes = sorted(type_by_hex)
    parts = []
    for i in range(0, len(all_hexes), AIRCRAFT_BATCH):
        batch = set(all_hexes[i:i + AIRCRAFT_BATCH])
        bl_b = blackouts[blackouts.icao24.isin(batch)]
        if len(bl_b) == 0:
            continue
        T = load_traces(batch,
                        extra_cols=["vert_rate_fpm", "gs_kt", "track_deg",
                                    "source"])
        parts.append(pd.DataFrame(boundary_props(T, bl_b), index=bl_b.index))
    props = (pd.concat(parts).reindex(blackouts.index) if parts
             else pd.DataFrame(boundary_props(pd.DataFrame(
                 columns=["icao24", "ts_s", "alt_ft", "vert_rate_fpm"]),
                 blackouts), index=blackouts.index))
    # reindex fills rows whose aircraft had no trace with NaN; the two
    # source columns default to "" (matching boundary_props' own default).
    for col in ("pre_source", "post_source"):
        props[col] = props[col].fillna("")
    pb = blackouts.copy()
    for col in props.columns:
        pb[col] = props[col].to_numpy()

    pb = enrich(pb, type_by_hex)

    out_path = etl_dir() / "edges.parquet"
    pb.to_parquet(out_path, index=False)
    _characterize(pb)
    return pb


def _characterize(pb):
    n_ta = int(pb.transatlantic_split.sum())
    with_props = int(pb.pre_alt_ft.notna().sum())
    log(f"edge: blackout boundaries: {len(pb):,} blackouts, "
         f"{with_props:,} with pre-boundary state, "
         f"{n_ta} transatlantic")
