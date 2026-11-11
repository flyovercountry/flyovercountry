"""etl theater: per-flight mission-chain / aircraft-ever theater relevance."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import backend.util.dimensions as dimensions
from backend.pipeline.etl.landing.theater import (
    _balkans_event_times, _bbox_mask, _bearing_in_range, _chain_relevance,
    _smoothed_bearing, run_theater,
)

CROP = {"name": "crop", "lat_south": 20.0, "lat_north": 70.0,
        "lon_west": -100.0, "lon_east": 50.0}
ME = {"name": "me", "lat_south": 25.0, "lat_north": 35.0,
      "lon_west": 30.0, "lon_east": 40.0}
BALKANS = {"name": "balkans", "lat_south": 40.0, "lat_north": 46.0,
           "lon_west": 15.0, "lon_east": 25.0}


def _ts(day, hour, minute=0):
    return datetime(2024, 1, day, hour, minute, tzinfo=timezone.utc).timestamp()


# --- theater primitives ------------------------------------------------------

def test_bbox_mask():
    assert _bbox_mask(np.array([30.0]), np.array([35.0]), ME).any()
    assert not _bbox_mask(np.array([30.0]), np.array([45.0]), ME).any()


def test_bearing_in_range_wraps_at_360():
    assert _bearing_in_range(350.0, 0.0, 45.0)
    assert _bearing_in_range(170.0, 180.0, 45.0)
    assert not _bearing_in_range(90.0, 0.0, 45.0)


def test_smoothed_bearing_southward():
    b = _smoothed_bearing(np.array([45.0, 44.9, 44.8]),
                          np.array([20.0, 20.0, 20.0]))
    assert abs(b - 180.0) < 1.0


RANGE = (-1e6, 1e9)   # data range far from the synthetic points


def test_balkans_south_disappearance_yields_event():
    ts = np.array([0.0, 60.0, 120.0, 180.0, 2180.0])
    lats = np.array([45.0, 44.9, 44.8, 44.7, 44.6])
    lons = np.full(5, 20.0)
    events = _balkans_event_times(ts, lats, lons, BALKANS, *RANGE)
    assert events == [180.0]            # last point of the southbound run


def test_balkans_eastbound_yields_no_event():
    ts = np.array([0.0, 60.0, 120.0, 180.0, 2180.0])
    lats = np.full(5, 45.0)
    lons = np.array([20.0, 20.1, 20.2, 20.3, 20.4])
    assert _balkans_event_times(ts, lats, lons, BALKANS, *RANGE) == []


def test_balkans_north_appearance_yields_event():
    ts = np.array([0.0, 2000.0, 2060.0, 2120.0])
    lats = np.array([44.0, 44.0, 44.1, 44.2])
    lons = np.full(4, 20.0)
    events = _balkans_event_times(ts, lats, lons, BALKANS, *RANGE)
    assert events == [2000.0]           # first point of the northbound run


def test_balkans_range_edge_is_not_a_blackout():
    # run ends <30 min before the data-range end: blackout status is
    # unknowable, so no disappearance event
    ts = np.array([0.0, 60.0, 120.0, 180.0])
    lats = np.array([45.0, 44.9, 44.8, 44.7])
    lons = np.full(4, 20.0)
    assert _balkans_event_times(ts, lats, lons, BALKANS, -1e6, 180.0 + 60) == []


# --- chain relevance ---------------------------------------------------------

H = 3600.0


def test_chain_relevance_break_and_attribution():
    # flights 0-1 gap 47h (same chain), 1-2 gap 71h (break)
    starts = np.array([0.0, 49 * H, 122 * H])
    ends = np.array([2 * H, 51 * H, 124 * H])
    fs = [0, 1, 2]

    # evidence during flight 0: chain {0,1} relevant, chain {2} not
    rel = _chain_relevance(fs, starts, ends, np.array([1 * H]))
    assert rel == {0: True, 1: True, 2: False}

    # evidence 10h after flight 1 ends (gap midpoint is 35.5h): still
    # attributed to chain {0,1}
    rel = _chain_relevance(fs, starts, ends, np.array([61 * H]))
    assert rel == {0: True, 1: True, 2: False}

    # evidence past the gap midpoint: attributed to chain {2}
    rel = _chain_relevance(fs, starts, ends, np.array([100 * H]))
    assert rel == {0: False, 1: False, 2: True}

    # trailing evidence after the last flight attaches to the last chain
    rel = _chain_relevance(fs, starts, ends, np.array([500 * H]))
    assert rel == {0: False, 1: False, 2: True}

    # no evidence: nothing relevant
    rel = _chain_relevance(fs, starts, ends, np.empty(0))
    assert rel == {0: False, 1: False, 2: False}


def test_chain_relevance_single_flight():
    rel = _chain_relevance([0], np.array([0.0]), np.array([H]),
                           np.array([9999 * H]))
    assert rel == {0: True}             # lone chain spans the whole range


# --- run_theater on synthetic flights ------------------------------------------

def _write_traces(tmp_data_dir, date_str, rows):
    part = tmp_data_dir / "build" / "ingest" / f"date={date_str}"
    part.mkdir()
    pd.DataFrame(rows, columns=["icao24", "ts_s", "lat", "lon"]).to_parquet(
        part / "trace.parquet", index=False)


def test_run_theater_fields(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(dimensions, "start", lambda: "2024-01-08")
    monkeypatch.setattr(dimensions, "end", lambda: "2024-01-24")
    monkeypatch.setattr(dimensions, "bboxes", lambda: [CROP, ME, BALKANS])
    (tmp_data_dir / "build" / "etl").mkdir()

    # ae0001: flights on days 12 / 14 / 17. Gap 12->14 is 47h (same
    #         chain); gap 14->17 is 71h (chain break). ME-bbox trace
    #         point during the day-12 flight.
    # ae0003: single flight, ME evidence during it.
    # ae0004: single flight, no trace points at all.
    flights = pd.DataFrame({
        "icao24": ["ae0001", "ae0001", "ae0001", "ae0003", "ae0004"],
        "flight_id": [0, 1, 2, 3, 4],
        "t_start": [_ts(12, 10), _ts(14, 10), _ts(17, 10),
                    _ts(12, 10), _ts(12, 10)],
        "t_end": [_ts(12, 11), _ts(14, 11), _ts(17, 11),
                  _ts(12, 11), _ts(12, 11)],
        "traversed_countries": ["AA", "BB", "CC", "AA", "DD"],
        # retired columns from an older build: must be dropped
        "traversed_countries_7d": ["x", "x", "x", "x", "x"],
        "max_disp_km": [1.0, 2.0, 3.0, 4.0, 5.0],
    })
    flights_path = tmp_data_dir / "build" / "etl" / "flights.parquet"
    flights.to_parquet(flights_path, index=False)

    _write_traces(tmp_data_dir, "2024-01-12", [
        ("ae0001", _ts(12, 10) + 60, 50.0, 8.0),
        ("ae0001", _ts(12, 10) + 120, 50.0, 9.0),
        ("ae0001", _ts(12, 10, 30), 30.0, 35.0),     # ME point in flight 0
        ("ae0003", _ts(12, 10) + 60, 30.0, 35.0),
    ])
    _write_traces(tmp_data_dir, "2024-01-14", [
        ("ae0001", _ts(14, 10) + 60, 52.0, 8.0),
    ])
    _write_traces(tmp_data_dir, "2024-01-17", [
        ("ae0001", _ts(17, 10) + 60, 52.0, 9.0),
    ])

    run_theater()
    out = pd.read_parquet(flights_path)

    assert "traversed_countries_7d" not in out.columns
    assert "max_disp_7d_km" not in out.columns
    assert "in_theater_7d" not in out.columns
    assert "max_disp_km" not in out.columns

    # th_chain: ME evidence sits in ae0001's first chain (flights 0+1,
    # 47h gap) but not the second (flight 2, after the 71h break)
    assert list(out["th_chain"]) == [True, True, False, True, False]

    # th_ever: every flight of an aircraft with any theater evidence
    assert list(out["th_ever"]) == [True, True, True, True, False]
