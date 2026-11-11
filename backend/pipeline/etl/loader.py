"""Shared raw-trace loading for etl stages.

Reads data/build/ingest/date=*/trace.parquet across the pipeline date range,
filtered to a set of aircraft, sorted by (icao24, ts_s).

Every raw-trace read in the pipeline routes through this module so the
denoise stage's exclusion list (data/build/etl/exclusions.parquet, the
(icao24, ts_s) reports it flagged as position outliers) is dropped in one
shared place — with a single sanctioned exception: denoise itself, which
PRODUCES the exclusion list (routing would be circular) and replicates the
placeholder guard inline. If the exclusions file is absent (denoise not
yet run) filtering is a silent no-op, so the pipeline still works without
the denoise stage.
"""
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from backend.util import dimensions
from backend.util.paths import etl_dir, raw_dir

BASE_COLS = ["icao24", "ts_s", "lat", "lon", "alt_ft", "on_ground", "gs_kt"]

_EXCL_CACHE = None   # frozenset of "icao24:ts_s" keys, or empty; loaded once


def _excluded_keys():
    """Cached set of "icao24:ts_s" keys from the denoise stage, or empty."""
    global _EXCL_CACHE
    if _EXCL_CACHE is None:
        p = etl_dir() / "exclusions.parquet"
        if p.exists():
            e = pd.read_parquet(p, columns=["icao24", "ts_s"])
            _EXCL_CACHE = frozenset(
                e["icao24"].astype(str) + ":" + e["ts_s"].astype("int64").astype(str)
            )
        else:
            _EXCL_CACHE = frozenset()
    return _EXCL_CACHE


def reset_exclusion_cache():
    """Force a re-read of exclusions.parquet (for tests / after a clean run)."""
    global _EXCL_CACHE
    _EXCL_CACHE = None


def drop_excluded(df):
    """Remove rows whose (icao24, ts_s) was flagged by the denoise stage."""
    keys = _excluded_keys()
    if not keys or df.empty:
        return df
    k = df["icao24"].astype(str) + ":" + df["ts_s"].astype("int64").astype(str)
    return df[~k.isin(keys)]


def _is_placeholder(path) -> bool:
    """True for ingest's zero-aircraft-day marker parquet: a single
    `__empty__` bool column, no rows (see ingest.py's _write_parquet /
    _merge_trace_batches). Reading one of these with an explicit
    `columns=` filter would raise, since the requested trace columns
    don't exist in that file's schema. Cheap: reads only the schema,
    never the (empty) data."""
    try:
        return pq.ParquetFile(path).schema_arrow.names == ["__empty__"]
    except (OSError, pa.ArrowException):
        return False


def dated_trace_partitions():
    """Existing date=YYYY-MM-DD partition dirs WITHIN the configured
    window, in date order — the pipeline's one partition-discovery idiom.
    Globbing whatever exists on disk would silently count stale partitions
    outside dimensions.json's window; types/denoise/export already iterate
    the window this way."""
    out = []
    for d in dimensions.dates():
        p = raw_dir() / f"date={d}"
        if (p / "trace.parquet").exists():
            out.append(p)
    return out


def read_trace_partition(path, columns, keep=None):
    """Read one date's trace parquet, optionally aircraft-filtered, with
    denoise-stage exclusions dropped. Used by the per-partition stages
    (trace, attribute) that do not go through load_traces()."""
    if _is_placeholder(path):
        return pd.DataFrame(columns=columns)
    df = pd.read_parquet(path, columns=columns)
    if keep is not None:
        df = df[df["icao24"].isin(keep)]
    return drop_excluded(df)


def load_traces(keep, extra_cols=None, columns=None, sort=True):
    """All raw trace points for the aircraft in `keep`.

    keep: set of icao24 hex strings. extra_cols: additional trace columns
    beyond BASE_COLS (deduplicated, order preserved); ignored if `columns`
    is given. columns: explicit column list, overriding BASE_COLS +
    extra_cols — for callers that want a narrower read than the default
    (e.g. theater, which only needs icao24/ts_s/lat/lon). sort: time-sort
    the concatenated result by (icao24, ts_s) (default); pass False to skip
    the final whole-frame mergesort when the caller only needs a per-hex
    time order it will establish itself (that sort is the memory peak on a
    multi-GB read — see theater.py).

    The icao24 filter is pushed down into the parquet read (pyarrow
    predicate pushdown) rather than applied after loading, so non-matching
    rows are never materialized into a pandas DataFrame at all — the
    difference matters when `keep` is a small subset (e.g. one batch of
    aircraft, or non-rotary-only) of everything a date's file contains.
    """
    cols = list(dict.fromkeys(columns if columns is not None
                              else BASE_COLS + (extra_cols or [])))
    keep_list = list(keep)
    if not keep_list:
        # an empty keep-set pushed into the pyarrow filter raises an
        # ArrowTypeError; an empty frame is the honest answer
        return pd.DataFrame(columns=cols)
    frames = []
    for d in dimensions.dates():
        p = raw_dir() / f"date={d}" / "trace.parquet"
        if not p.exists() or _is_placeholder(p):
            continue
        t = pd.read_parquet(p, columns=cols, filters=[("icao24", "in", keep_list)])
        frames.append(t)
    if not frames:
        return pd.DataFrame(columns=cols)
    T = pd.concat(frames, ignore_index=True)
    T = drop_excluded(T)
    if sort:
        T = T.sort_values(["icao24", "ts_s"], kind="mergesort")
    return T


def index_flights(flights: pd.DataFrame) -> dict:
    """hex24 -> (t_start array, t_end array, flight_id array), each sorted
    by t_start. Shared by flight/attribute/trace to match raw trace points
    to flight windows via match_points_to_flights()."""
    by_hex: dict = {}
    for hex24, sub in flights.groupby("icao24", sort=False):
        s = sub.sort_values("t_start")
        by_hex[hex24] = (
            s["t_start"].values.astype(np.float64),
            s["t_end"].values.astype(np.float64),
            s["flight_id"].values.astype(np.int64),
        )
    return by_hex


def match_points_to_flights(by_hex: dict, hex24, ts: np.ndarray):
    """Match one aircraft's trace timestamps to its flight windows.

    ts: that aircraft's trace point timestamps. by_hex: index_flights()'s
    output. Each ts is assigned to the flight whose [t_start, t_end]
    window contains it (via searchsorted against t_start, side="right",
    then bounds-checked against that flight's t_end).

    Returns (matched_fids, ok): ok is a boolean mask over `ts` selecting
    the points that fell inside some flight's window; matched_fids has
    the same length as ts[ok], giving each matched point's flight_id —
    index other per-point arrays (lat, lon, ...) with the same `ok` mask
    to keep them aligned with matched_fids.
    """
    t0a, t1a, ida = by_hex[hex24]
    idx = np.searchsorted(t0a, ts, side="right") - 1
    clipped = np.clip(idx, 0, len(t1a) - 1)
    ok = (idx >= 0) & (ts <= t1a[clipped])
    return ida[idx[ok]], ok
