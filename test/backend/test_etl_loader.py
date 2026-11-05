"""etl loader: shared raw-trace read (load_traces/read_trace_partition),
the placeholder-file skip, and the index_flights/match_points_to_flights
pair shared by flight/attribute/trace.
"""
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import backend.util.dimensions as dimensions
from backend.pipeline.etl.loader import (
    index_flights, load_traces, match_points_to_flights, read_trace_partition,
)


def _write_trace(tmp_data_dir, date_str, rows, extra_cols=None):
    """rows: list of (icao24, ts_s, lat, lon, alt_ft, on_ground, gs_kt, ...)."""
    part = tmp_data_dir / "build" / "ingest" / f"date={date_str}"
    part.mkdir(parents=True, exist_ok=True)
    cols = ["icao24", "ts_s", "lat", "lon", "alt_ft", "on_ground", "gs_kt"] \
        + (extra_cols or [])
    pd.DataFrame(rows, columns=cols).to_parquet(part / "trace.parquet", index=False)
    return part / "trace.parquet"


def _write_placeholder(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({"__empty__": pa.array([], type=pa.bool_())})
    pq.write_table(table, str(path))


# --- index_flights / match_points_to_flights -----------------------------------

def _flights_frame():
    return pd.DataFrame({
        "icao24": ["ae0001", "ae0001"],
        "t_start": [100.0, 300.0],
        "t_end": [200.0, 400.0],
        "flight_id": [7, 8],
    })


def test_match_points_to_flights_boundaries_inclusive():
    by_hex = index_flights(_flights_frame())
    ts = np.array([99.0, 100.0, 200.0, 250.0, 300.0, 400.0, 401.0])
    fids, ok = match_points_to_flights(by_hex, "ae0001", ts)
    assert list(ts[ok]) == [100.0, 200.0, 300.0, 400.0]
    assert list(fids) == [7, 7, 8, 8]


def test_match_points_to_flights_none_in_any_window():
    by_hex = index_flights(_flights_frame())
    ts = np.array([10.0, 250.0, 500.0])
    fids, ok = match_points_to_flights(by_hex, "ae0001", ts)
    assert not ok.any()
    assert len(fids) == 0


def test_index_flights_sorts_by_t_start_per_hex():
    # rows given out of start-time order; index_flights must sort them so
    # match_points_to_flights' searchsorted is valid.
    flights = pd.DataFrame({
        "icao24": ["ae0001", "ae0001"],
        "t_start": [300.0, 100.0],
        "t_end": [400.0, 200.0],
        "flight_id": [8, 7],
    })
    by_hex = index_flights(flights)
    t0a, t1a, ida = by_hex["ae0001"]
    assert list(t0a) == [100.0, 300.0]
    assert list(ida) == [7, 8]


# --- placeholder-file skip -------------------------------------------------

def test_read_trace_partition_skips_placeholder(tmp_data_dir):
    path = tmp_data_dir / "build" / "ingest" / "date=2024-01-01" / "trace.parquet"
    _write_placeholder(path)
    df = read_trace_partition(path, columns=["icao24", "ts_s"])
    assert len(df) == 0
    assert list(df.columns) == ["icao24", "ts_s"]


def test_load_traces_skips_placeholder_date(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(dimensions, "dates", lambda: ["2024-01-01", "2024-01-02"])
    _write_placeholder(
        tmp_data_dir / "build" / "ingest" / "date=2024-01-01" / "trace.parquet")
    _write_trace(tmp_data_dir, "2024-01-02", [
        ("ae0001", 100.0, 50.0, 8.0, 30000.0, False, 400.0),
    ])

    T = load_traces({"ae0001"})
    assert len(T) == 1
    assert T.iloc[0]["ts_s"] == 100.0


def test_load_traces_all_placeholders_returns_empty_frame(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(dimensions, "dates", lambda: ["2024-01-01"])
    _write_placeholder(
        tmp_data_dir / "build" / "ingest" / "date=2024-01-01" / "trace.parquet")
    T = load_traces({"ae0001"})
    assert len(T) == 0


# --- columns / sort params ---------------------------------------------------

def test_load_traces_default_columns_include_base_and_extra(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(dimensions, "dates", lambda: ["2024-01-01"])
    _write_trace(tmp_data_dir, "2024-01-01", [
        ("ae0001", 100.0, 50.0, 8.0, 30000.0, False, 400.0, 0.0),
    ], extra_cols=["vert_rate_fpm"])

    T = load_traces({"ae0001"}, extra_cols=["vert_rate_fpm"])
    assert set(T.columns) == {
        "icao24", "ts_s", "lat", "lon", "alt_ft", "on_ground", "gs_kt",
        "vert_rate_fpm",
    }


def test_load_traces_explicit_columns_overrides_base(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(dimensions, "dates", lambda: ["2024-01-01"])
    _write_trace(tmp_data_dir, "2024-01-01", [
        ("ae0001", 100.0, 50.0, 8.0, 30000.0, False, 400.0),
    ])

    T = load_traces({"ae0001"}, columns=["icao24", "ts_s", "lat", "lon"])
    assert list(T.columns) == ["icao24", "ts_s", "lat", "lon"]


def test_load_traces_sort_true_orders_by_hex_then_ts(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(dimensions, "dates", lambda: ["2024-01-01"])
    _write_trace(tmp_data_dir, "2024-01-01", [
        ("ae0002", 200.0, 50.0, 8.0, 30000.0, False, 400.0),
        ("ae0001", 300.0, 50.0, 8.0, 30000.0, False, 400.0),
        ("ae0001", 100.0, 50.0, 8.0, 30000.0, False, 400.0),
    ])

    T = load_traces({"ae0001", "ae0002"}, columns=["icao24", "ts_s"])
    assert list(zip(T["icao24"], T["ts_s"])) == [
        ("ae0001", 100.0), ("ae0001", 300.0), ("ae0002", 200.0),
    ]


def test_load_traces_sort_false_preserves_read_order(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(dimensions, "dates", lambda: ["2024-01-01"])
    _write_trace(tmp_data_dir, "2024-01-01", [
        ("ae0002", 200.0, 50.0, 8.0, 30000.0, False, 400.0),
        ("ae0001", 300.0, 50.0, 8.0, 30000.0, False, 400.0),
        ("ae0001", 100.0, 50.0, 8.0, 30000.0, False, 400.0),
    ])

    T = load_traces({"ae0001", "ae0002"}, columns=["icao24", "ts_s"], sort=False)
    assert list(zip(T["icao24"], T["ts_s"])) == [
        ("ae0002", 200.0), ("ae0001", 300.0), ("ae0001", 100.0),
    ]
