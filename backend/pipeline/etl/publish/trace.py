"""Per-flight RDP-decimated polylines (trace stage).

Reads raw trace parquets, joins points to flights by (icao24, ts_s
within [t_start, t_end]), then decimates each polyline at the coarse
tolerance for frontend rendering.

Processes aircraft in flight-count-balanced batches to bound memory.

Reads data/build/etl/flights.parquet + data/build/ingest/date=*/trace.parquet.
Writes data/build/etl/traces.parquet:
    flight_id, pts_coarse
(npts_coarse and bbox were dropped in the column slim — export reads
only flight_id + pts_coarse.)
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from backend.pipeline.etl.loader import (
    dated_trace_partitions,
    index_flights as _index_flights, match_points_to_flights, read_trace_partition,
)
from backend.util.paths import etl_dir
from backend.util.log import log

_COARSE_TOL_M = 2000.0

# Flat-degree approximation: one degree treated as 111 km in BOTH axes.
# True only for latitude; at 73N a longitude degree is ~0.29x that, so the
# real east-west tolerance loosens toward the crop's north edge (rendering
# fidelity only — analysis never uses these polylines).
_DEG_PER_M = 1.0 / 111_000.0

OUT_SCHEMA = pa.schema([
    ("flight_id", pa.int64()),
    ("pts_coarse", pa.list_(pa.float64())),
])


def _rdp_mask(coords: np.ndarray, eps: float) -> np.ndarray:
    """Ramer-Douglas-Peucker: returns a bool mask of kept points.
    Coincident-endpoint fallback preserves closed loops (tanker racetracks,
    ISR orbits, holding patterns) instead of collapsing them."""
    n = len(coords)
    keep = np.zeros(n, dtype=bool)
    if n == 0:
        return keep
    keep[0] = True
    if n == 1:
        return keep
    keep[-1] = True
    if n == 2:
        return keep
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = coords[a:b + 1]
        d = seg[-1] - seg[0]
        denom2 = float((d * d).sum())
        if denom2 < 1e-18:
            # Coincident endpoints: radial distance fallback
            v = seg - seg[0]
            rad = np.sqrt((v * v).sum(axis=1))
            i = int(np.argmax(rad[1:-1])) + 1
            if rad[i] > eps:
                keep[a + i] = True
                stack.append((a, a + i))
                stack.append((a + i, b))
            continue
        v = seg - seg[0]
        cross = v[:, 0] * d[1] - v[:, 1] * d[0]
        dist = np.abs(cross) / np.sqrt(denom2)
        i = int(np.argmax(dist[1:-1])) + 1
        if dist[i] > eps:
            keep[a + i] = True
            stack.append((a, a + i))
            stack.append((a + i, b))
    return keep


def _match_partition(trace_path: Path, by_hex: dict):
    df = read_trace_partition(
        trace_path,
        columns=["icao24", "ts_s", "lat", "lon", "alt_ft"],
        keep=by_hex,
    )
    if len(df) == 0:
        return None
    out_fid, out_ts, out_lat, out_lon, out_alt = [], [], [], [], []
    for hex24, sub in (
        df.sort_values(["icao24", "ts_s"]).groupby("icao24", sort=False)
    ):
        ts = sub["ts_s"].values.astype(np.float64)
        matched_fids, ok = match_points_to_flights(by_hex, hex24, ts)
        if not ok.any():
            continue
        out_fid.append(matched_fids)
        out_ts.append(ts[ok].astype(np.float64))
        out_lat.append(sub["lat"].values[ok].astype(np.float32))
        out_lon.append(sub["lon"].values[ok].astype(np.float32))
        out_alt.append(sub["alt_ft"].values[ok].astype(np.float32))
    if not out_fid:
        return None
    return (
        np.concatenate(out_fid),
        np.concatenate(out_ts),
        np.concatenate(out_lat),
        np.concatenate(out_lon),
        np.concatenate(out_alt),
    )


def _pack_flat(ts: np.ndarray, lat: np.ndarray, lon: np.ndarray,
               alt: np.ndarray) -> list:
    n = len(lat)
    if n == 0:
        return []
    # NaN altitude stays NaN: "unknown" must remain distinguishable from
    # genuine ground level all the way to export's band computation
    kft = (alt / 1000.0).astype(np.float32)
    flat = np.empty(n * 4, dtype=np.float64)
    flat[0::4] = ts
    flat[1::4] = lat
    flat[2::4] = lon
    flat[3::4] = kft
    return flat.tolist()


def _empty_row(flight_id: int) -> dict:
    return {
        "flight_id": flight_id,
        "pts_coarse": [],
    }


def _decimate_batch(
    all_fid: np.ndarray, all_ts: np.ndarray,
    all_lat: np.ndarray, all_lon: np.ndarray, all_alt: np.ndarray,
    tolerance_m: float = _COARSE_TOL_M,
) -> list[dict]:
    order = np.argsort(all_fid, kind="stable")
    all_fid = all_fid[order]
    all_ts = all_ts[order]
    all_lat = all_lat[order]
    all_lon = all_lon[order]
    all_alt = all_alt[order]

    bounds = np.concatenate((
        [0],
        np.where(np.diff(all_fid) != 0)[0] + 1,
        [len(all_fid)],
    ))

    eps_coarse = tolerance_m * _DEG_PER_M
    rows = []

    for i in range(len(bounds) - 1):
        a, b = int(bounds[i]), int(bounds[i + 1])
        flight_id = int(all_fid[a])
        ts_s = all_ts[a:b]
        lat_s = all_lat[a:b]
        lon_s = all_lon[a:b]
        alt_s = all_alt[a:b]
        ts_order = np.argsort(ts_s, kind="stable")
        ts_s = ts_s[ts_order]
        lat_s = lat_s[ts_order]
        lon_s = lon_s[ts_order]
        alt_s = alt_s[ts_order]
        coords = np.column_stack([lat_s, lon_s])

        if len(coords) < 3:
            keep = np.ones(len(coords), dtype=bool)
        else:
            keep = _rdp_mask(coords, eps_coarse)

        rows.append({
            "flight_id": flight_id,
            "pts_coarse": _pack_flat(
                ts_s[keep], lat_s[keep], lon_s[keep], alt_s[keep]),
        })

    return rows


def _rows_to_table(rows: list[dict]) -> pa.Table:
    df = pd.DataFrame(rows)
    return pa.Table.from_pandas(df, schema=OUT_SCHEMA, preserve_index=False)


def _build_batches(flights: pd.DataFrame, target_size: int = 2000) -> list[set[str]]:
    """Group icao24s into batches of roughly target_size flights each."""
    counts = flights.groupby("icao24").size().sort_values(ascending=False)
    batches: list[set[str]] = []
    current: set[str] = set()
    current_size = 0
    for hex24, n in counts.items():
        if current_size > 0 and current_size + n > target_size:
            batches.append(current)
            current = set()
            current_size = 0
        current.add(hex24)
        current_size += n
    if current:
        batches.append(current)
    return batches


def run_trace(*, tolerance_m: float = _COARSE_TOL_M):
    t0 = time.time()
    etl = etl_dir()
    flights_path = etl / "flights.parquet"
    if not flights_path.exists():
        log("trace: flights.parquet not found — run flight first")
        return

    flights = pd.read_parquet(flights_path)
    log(f"trace: {len(flights):,} flights to trace "
         f"(tolerance {tolerance_m:.0f} m)")

    batches = _build_batches(flights, target_size=2000)
    log(f"trace: {len(batches)} batches (target ~2000 flights each)")

    parts = dated_trace_partitions()

    out_path = etl / "traces.parquet"
    # write to a tmp path and rename on success (matching export.py's
    # atomic pattern) — a killed run must not leave an existing-but-
    # truncated traces.parquet that satisfies the existence gate
    tmp_path = out_path.with_suffix(".parquet.tmp")
    writer = pq.ParquetWriter(str(tmp_path), OUT_SCHEMA, compression="zstd")
    total_points = 0
    total_flights = 0

    for bi, batch_hexes in enumerate(batches):
        batch_flights = flights[flights["icao24"].isin(batch_hexes)]
        if len(batch_flights) == 0:
            continue
        by_hex = _index_flights(batch_flights)

        chunks: list = []
        for p in parts:
            result = _match_partition(p / "trace.parquet", by_hex)
            if result is not None:
                chunks.append(result)

        if chunks:
            all_fid = np.concatenate([c[0] for c in chunks])
            all_ts = np.concatenate([c[1] for c in chunks])
            all_lat = np.concatenate([c[2] for c in chunks])
            all_lon = np.concatenate([c[3] for c in chunks])
            all_alt = np.concatenate([c[4] for c in chunks])
            del chunks

            total_points += len(all_fid)

            rows = _decimate_batch(all_fid, all_ts, all_lat, all_lon, all_alt,
                                   tolerance_m)
            del all_fid, all_ts, all_lat, all_lon, all_alt
        else:
            rows = []

        have_fids = {r["flight_id"] for r in rows}
        for fid in batch_flights["flight_id"].values:
            fid_int = int(fid)
            if fid_int not in have_fids:
                rows.append(_empty_row(fid_int))
                have_fids.add(fid_int)

        if rows:
            writer.write_table(_rows_to_table(rows))
            total_flights += len(rows)
            del rows

        if (bi + 1) % 5 == 0 or (bi + 1) == len(batches):
            log(f"trace: {bi + 1}/{len(batches)} batches, "
                 f"{total_points:,} points, {total_flights:,} flights")

    writer.close()
    os.replace(tmp_path, out_path)

    elapsed = time.time() - t0
    log(f"trace: wrote {out_path} ({total_flights:,} flights, "
         f"{total_points:,} points, {elapsed:.0f}s)")
