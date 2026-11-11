"""Regression tests for ingest-module review findings:

- _flush_day's manifest carries the keys run_ingest's post-flush log line
  reads (no more logging a KeyError-prone 'segments' that was never in the
  manifest), and sample_sec records the interval actually used.
- run_merge_batches tolerates date dirs that never had (or no longer have)
  segments.parquet.
- downsample.py's per-aircraft min-gap thinning keeps the expected subset.
- downsample.py's crash-safe backup-then-swap ordering: the live
  trace.parquet is only ever the untouched original or the fully-written
  thinned file, the original always survives byte-identical under the
  backup dir, and a pre-existing backup causes that date to be aborted
  untouched rather than silently skipped or overwritten.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import backend.pipeline.ingest.downsample as downsample_mod
import backend.pipeline.ingest.ingest as ingest_mod
from backend.util.paths import raw_dir


def _write_trace(path, rows):
    df = pd.DataFrame(rows)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), str(path))


# ---------------------------------------------------------------------------
# Manifest / log contract (bugs 1 + 2)
# ---------------------------------------------------------------------------

def test_flush_day_manifest_has_the_keys_run_ingest_logs(tmp_data_dir):
    """run_ingest logs manifest['aircraft'] and manifest['traces'] after a
    successful _flush_day. Both keys must exist, and there must be no
    'segments' key (that KeyError is exactly what shipped)."""
    date = "2026-01-01"
    batch_dir = raw_dir() / f"date={date}" / "_trace_batches"
    batch_dir.mkdir(parents=True)
    results = {
        "aircraft": [{"icao24": "ae1234"}, {"icao24": "ae5678"}],
        "trace_batch_dir": batch_dir,
        "trace_count": 0,
        "scanned": 2, "skipped": 0, "kept": 2, "errored": 0,
    }

    manifest = ingest_mod._flush_day(results, date, sample_sec=10)

    assert manifest["aircraft"] == 2
    assert manifest["traces"] == 0
    assert "segments" not in manifest
    # the exact log line format from run_ingest must not KeyError
    line = (f"  {date}: {manifest['aircraft']} aircraft, "
            f"{manifest['traces']} trace points")
    assert date in line


def test_flush_day_records_actual_sample_sec_not_a_constant(tmp_data_dir):
    """manifest['sample_sec'] must reflect the interval actually passed in,
    not the module's old (unrelated) DOWNSAMPLE_SEC=60 constant."""
    for day, sample_sec in enumerate((0, 10, 60), start=1):
        date = f"2026-02-{day:02d}"
        batch_dir = raw_dir() / f"date={date}" / "_trace_batches"
        batch_dir.mkdir(parents=True)
        results = {
            "aircraft": [], "trace_batch_dir": batch_dir, "trace_count": 0,
            "scanned": 0, "skipped": 0, "kept": 0, "errored": 0,
        }
        manifest = ingest_mod._flush_day(results, date, sample_sec=sample_sec)
        assert manifest["sample_sec"] == sample_sec


def test_flush_day_no_longer_writes_segments_parquet(tmp_data_dir):
    date = "2026-01-05"
    batch_dir = raw_dir() / f"date={date}" / "_trace_batches"
    batch_dir.mkdir(parents=True)
    results = {
        "aircraft": [{"icao24": "ae1234"}], "trace_batch_dir": batch_dir,
        "trace_count": 0, "scanned": 1, "skipped": 0, "kept": 1, "errored": 0,
    }
    ingest_mod._flush_day(results, date, sample_sec=10)
    assert not (raw_dir() / f"date={date}" / "segments.parquet").exists()
    assert (raw_dir() / f"date={date}" / "aircraft.parquet").exists()


# ---------------------------------------------------------------------------
# merge-batches tolerance (bug 3)
# ---------------------------------------------------------------------------

def test_merge_batches_handles_date_dir_without_segments_parquet(tmp_data_dir):
    date = "2026-01-02"
    dd = raw_dir() / f"date={date}"
    batch_dir = dd / "_trace_batches"
    batch_dir.mkdir(parents=True)
    _write_trace(batch_dir / "batch_0000.parquet",
                 {"icao24": ["ae0001"], "ts_s": [0]})
    _write_trace(dd / "aircraft.parquet", {"icao24": ["ae0001"]})
    # deliberately no segments.parquet

    ingest_mod.run_merge_batches()

    assert (dd / "trace.parquet").exists()
    assert not (dd / "segments.parquet").exists()
    done = json.loads(
        (raw_dir() / "ingest_state" / f"{date}.done").read_text())
    assert done["aircraft"] == 1
    assert done["traces"] == 1


def test_merge_batches_leaves_a_preexisting_segments_parquet_untouched(
        tmp_data_dir):
    """Days ingested before segments.parquet was retired still have one on
    disk; merge-batches must never read or rewrite it."""
    date = "2026-01-03"
    dd = raw_dir() / f"date={date}"
    batch_dir = dd / "_trace_batches"
    batch_dir.mkdir(parents=True)
    _write_trace(batch_dir / "batch_0000.parquet",
                 {"icao24": ["ae0001"], "ts_s": [0]})
    _write_trace(dd / "aircraft.parquet", {"icao24": ["ae0001"]})
    seg_path = dd / "segments.parquet"
    _write_trace(seg_path, {"icao24": ["ae0001"]})
    before = seg_path.read_bytes()
    before_mtime = seg_path.stat().st_mtime_ns

    ingest_mod.run_merge_batches()

    assert seg_path.read_bytes() == before
    assert seg_path.stat().st_mtime_ns == before_mtime


# ---------------------------------------------------------------------------
# Downsample thinning (bug 5, thinning semantics)
# ---------------------------------------------------------------------------

def test_thin_mask_keeps_first_point_and_min_gap_points():
    ts = np.array([0, 5, 12, 61, 140])
    mask = downsample_mod._thin_mask(ts, 60)
    assert ts[mask].tolist() == [0, 61, 140]


def test_downsample_partition_thins_per_aircraft(tmp_path):
    src = tmp_path / "trace.parquet"
    dst = tmp_path / "trace.thinned.parquet"
    ts = [0, 5, 12, 61, 140]
    rows = [{"icao24": hexid, "ts_s": t}
            for hexid in ("ae0001", "ae0002") for t in ts]
    _write_trace(src, rows)

    n_before, n_after = downsample_mod._downsample_partition(src, dst, 60)

    assert n_before == 10
    assert n_after == 6  # 3 kept per aircraft
    out = pq.read_table(str(dst)).to_pandas()
    for hexid in ("ae0001", "ae0002"):
        kept = sorted(out.loc[out["icao24"] == hexid, "ts_s"].tolist())
        assert kept == [0, 61, 140]


# ---------------------------------------------------------------------------
# Downsample crash-safety (bug 5, ordering)
# ---------------------------------------------------------------------------

def test_run_downsample_backs_up_original_and_thins_live_file(
        tmp_data_dir, capsys):
    date = "2026-03-01"
    dd = raw_dir() / f"date={date}"
    dd.mkdir(parents=True)
    trace_path = dd / "trace.parquet"
    ts = [0, 5, 12, 61, 140]
    _write_trace(trace_path, {"icao24": ["ae0001"] * len(ts), "ts_s": ts})
    original_bytes = trace_path.read_bytes()

    backup_dir = tmp_data_dir / "ingest_full_test"
    downsample_mod.run_downsample(
        interval_sec=60, backup_dir=str(backup_dir), start=date, end=date)

    backup_trace = backup_dir / f"date={date}" / "trace.parquet"
    assert backup_trace.exists()
    assert backup_trace.read_bytes() == original_bytes

    live = pq.read_table(str(trace_path)).to_pandas()
    assert sorted(live["ts_s"].tolist()) == [0, 61, 140]
    # no leftover tmp file
    assert not trace_path.with_suffix(".parquet.tmp").exists()


def test_run_downsample_aborts_date_when_backup_already_exists(
        tmp_data_dir, capsys):
    date = "2026-03-02"
    dd = raw_dir() / f"date={date}"
    dd.mkdir(parents=True)
    trace_path = dd / "trace.parquet"
    _write_trace(trace_path, {"icao24": ["ae0001"], "ts_s": [0]})
    live_before = trace_path.read_bytes()

    backup_dir = tmp_data_dir / "ingest_full_test2"
    backup_trace = backup_dir / f"date={date}" / "trace.parquet"
    backup_trace.parent.mkdir(parents=True)
    backup_trace.write_bytes(b"pre-existing backup content")
    backup_before = backup_trace.read_bytes()

    downsample_mod.run_downsample(
        interval_sec=60, backup_dir=str(backup_dir), start=date, end=date)

    # nothing touched: not the live file, not the pre-existing backup
    assert trace_path.read_bytes() == live_before
    assert backup_trace.read_bytes() == backup_before
    err = capsys.readouterr().err
    assert "ABORTING" in err
    assert date in err
