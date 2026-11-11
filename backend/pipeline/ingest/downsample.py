"""Back up full-fidelity trace parquets and replace with downsampled versions.

Processes each parquet in per-aircraft chunks to avoid OOM on small
VPSes. Known limit: a single aircraft whose points exceed the batch
target is processed as one oversized batch — one aircraft's points are
never split across batches (the thinning needs them contiguous).

Crash safety: for each date, the original trace.parquet is only ever *read*
(never moved or truncated) while the thinned replacement is being computed.
The thinned frame is written to a `.tmp` file in the same date directory;
only once that write is durably complete do we preserve the original's
bytes under `backup_dir` (via a hardlink where possible, falling back to a
real copy across filesystems) and then atomically rename the tmp file onto
trace.parquet. At every instant, trace.parquet is either the untouched
original or the fully-written thinned file — never partial, never missing —
and the original's bytes always end up under backup_dir before the live
file is replaced. If a run is interrupted, the live trace.parquet for that
date is still either the intact original (nothing changed yet) or, if the
backup already exists, we refuse to guess and abort that date for a human
to inspect.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pandas as pd

from backend.util.paths import raw_dir, data_dir
from backend.util.log import log


def _log(msg: str):
    ts = time.strftime("%H:%M:%S")
    log(f"{ts} {msg}")


def _build_hex_batches(src: Path, target_size: int = 500_000) -> list[set[str]]:
    """Group icao24s into batches of roughly target_size points each."""
    tbl = pq.read_table(src, columns=["icao24"])
    counts = tbl.column("icao24").to_pandas().value_counts()
    del tbl
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


def _thin_mask(ts: np.ndarray, interval_sec: int) -> np.ndarray:
    """Greedy min-gap thinning over an ascending-sorted timestamp array:
    keep the first point, then each subsequent point that lands at least
    interval_sec after the last *kept* point (not merely the last point
    seen) — unlike fixed-grid bucketing, this never keeps two points closer
    together than interval_sec."""
    n = len(ts)
    keep = np.zeros(n, dtype=bool)
    if n == 0:
        return keep
    keep[0] = True
    last_kept = ts[0]
    for i in range(1, n):
        if ts[i] - last_kept >= interval_sec:
            keep[i] = True
            last_kept = ts[i]
    return keep


def _thin_batch(df: pd.DataFrame, interval_sec: int) -> pd.DataFrame:
    """Thin a per-aircraft (icao24) batch of trace rows. Traces have no
    segment_id — the thinning key is icao24 alone, sorted by ts_s.

    Deliberately not groupby(...).apply(...): when a batch happens to hold
    a single icao24, pandas' apply cannot tell a per-row Series result
    apart from a whole-group one and returns a garbled 2D frame instead
    (observed, not hypothetical). Plain iteration + concat has no such
    ambiguity."""
    df = df.sort_values(["icao24", "ts_s"], kind="mergesort")
    parts = [
        pd.Series(_thin_mask(sub["ts_s"].to_numpy(), interval_sec),
                  index=sub.index)
        for _, sub in df.groupby("icao24", sort=False)
    ]
    keep = pd.concat(parts).reindex(df.index)
    return df[keep.to_numpy()]


def _downsample_partition(src: Path, dst: Path, interval_sec: int) -> tuple[int, int]:
    """Downsample one trace parquet in point-count-balanced batches.

    `src` is only ever read here — the caller is responsible for not
    touching it until this whole call (and the resulting `dst`) is durably
    on disk."""
    batches = _build_hex_batches(src)
    n_batches = len(batches)
    _log(f"    {n_batches} batches")

    schema = pq.ParquetFile(src).schema_arrow
    total_before = 0
    total_after = 0
    writer = pq.ParquetWriter(dst, schema, compression="zstd")

    for i, hex_set in enumerate(batches, 1):
        filt = [("icao24", "in", sorted(hex_set))]
        tbl = pq.read_table(src, filters=filt)
        df = tbl.to_pandas()
        del tbl
        total_before += len(df)
        out = _thin_batch(df, interval_sec)
        total_after += len(out)
        writer.write_table(pa.Table.from_pandas(out, schema=schema,
                                                preserve_index=False))
        del df, out
        if i % 5 == 0 or i == n_batches:
            _log(f"    {i}/{n_batches} batches, "
                 f"{total_before:,} -> {total_after:,} pts")

    writer.close()
    return total_before, total_after


def _preserve_original(src: Path, dst: Path):
    """Get src's current bytes durably onto dst, as cheaply as possible.
    Hardlinking is a metadata-only operation (no data copy, instant,
    atomic) and is used whenever backup_dir shares src's filesystem;
    falls back to a real copy for a cross-device backup directory."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    if dst.stat().st_size != src.stat().st_size:
        raise IOError(
            f"backup size mismatch preserving {src} -> {dst}: "
            f"{dst.stat().st_size} != {src.stat().st_size}",
        )


def run_downsample(interval_sec: int = 10, backup_dir: str | None = None,
                   start: str | None = None, end: str | None = None):
    raw = raw_dir()
    backup = Path(backup_dir) if backup_dir else data_dir() / "build" / "ingest_full"

    date_dirs = sorted(raw.glob("date=*"))
    if start or end:
        def _in_range(dd: Path) -> bool:
            d = dd.name.split("=", 1)[1]
            if start and d < start:
                return False
            if end and d > end:
                return False
            return True
        date_dirs = [dd for dd in date_dirs if _in_range(dd)]
    if not date_dirs:
        _log("no date partitions found")
        return

    # disk preflight (mirrors ingest's MIN_FREE_GB pattern): the tmp write
    # plus a cross-filesystem backup copy can transiently need up to the
    # largest single trace file again — refuse upfront rather than OSError
    # hours into the run on exactly the disk-constrained hosts this tool
    # targets
    biggest = max((int((dd / "trace.parquet").stat().st_size)
                   for dd in date_dirs if (dd / "trace.parquet").exists()),
                  default=0)
    free = shutil.disk_usage(raw).free
    if free < biggest + (1 << 30):
        _log(f"insufficient disk: {free >> 20} MB free < largest trace "
             f"({biggest >> 20} MB) + 1 GB margin — aborting before any work")
        return

    _log(f"downsample: {len(date_dirs)} dates, interval={interval_sec}s, "
         f"backup={backup}")

    total_before = 0
    total_after = 0
    aborted = 0

    for i, dd in enumerate(date_dirs, 1):
        trace_path = dd / "trace.parquet"
        if not trace_path.exists():
            continue

        date_name = dd.name
        backup_date = backup / date_name
        backup_trace = backup_date / "trace.parquet"

        if backup_trace.exists():
            _log(f"  [{i}/{len(date_dirs)}] {date_name}: ABORTING — a backup "
                 f"already exists at {backup_trace}. Not assuming this date "
                 f"is done, not overwriting, not deleting anything. Inspect "
                 f"{trace_path} and {backup_trace} by hand before retrying.")
            aborted += 1
            continue

        before_bytes = trace_path.stat().st_size

        # trace_path is read-only input here — never moved or truncated
        # until the atomic rename below.
        thinned_tmp = trace_path.with_suffix(".parquet.tmp")
        n_before, n_after = _downsample_partition(trace_path, thinned_tmp,
                                                   interval_sec)

        # Original's bytes are durably preserved before the live file is
        # touched, so trace_path always remains one of {original, thinned}.
        _preserve_original(trace_path, backup_trace)
        thinned_tmp.rename(trace_path)

        total_before += before_bytes
        after_bytes = trace_path.stat().st_size
        total_after += after_bytes

        ratio = n_after / n_before * 100 if n_before else 0
        _log(f"  [{i}/{len(date_dirs)}] {date_name}: "
             f"{n_before:,} -> {n_after:,} pts ({ratio:.0f}%), "
             f"{before_bytes >> 20} -> {after_bytes >> 20} MB")

    pct = (total_after / total_before * 100) if total_before else 0
    _log(f"done: {total_before >> 20} MB -> {total_after >> 20} MB ({pct:.0f}%)")
    _log(f"originals backed up to {backup}")
    # the honest disk story: live went DOWN but the backups are still on
    # disk, so net usage went UP until they are deleted by hand
    _log(f"NET DISK: +{total_after >> 20} MB until you verify the thinned "
         f"data and delete {backup} yourself — nothing does this for you")
    if aborted:
        _log(f"WARNING: {aborted} date(s) aborted — pre-existing backups "
             f"need manual inspection, see ABORTING lines above")
