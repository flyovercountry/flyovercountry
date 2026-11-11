"""etl traces: RDP mask, point-to-flight matching, output schema."""
import numpy as np
import pandas as pd

from backend.pipeline.etl.publish.trace import (
    OUT_SCHEMA, _decimate_batch, _index_flights, _match_partition,
    _rdp_mask, _rows_to_table,
)


# --- RDP mask --------------------------------------------------------------

def test_rdp_straight_line_collapses_to_endpoints():
    coords = np.column_stack([np.linspace(0.0, 10.0, 20),
                              np.zeros(20)])
    keep = _rdp_mask(coords, eps=0.01)
    assert keep[0] and keep[-1]
    assert keep.sum() == 2


def test_rdp_spike_is_kept():
    lats = np.zeros(11)
    lats[5] = 1.0                       # 1-degree spike off the baseline
    coords = np.column_stack([lats, np.linspace(0.0, 10.0, 11)])
    keep = _rdp_mask(coords, eps=0.5)
    assert keep[5]


def test_rdp_tolerance_scaling_drops_small_spike():
    lats = np.zeros(11)
    lats[5] = 1.0
    coords = np.column_stack([lats, np.linspace(0.0, 10.0, 11)])
    keep = _rdp_mask(coords, eps=2.0)   # tolerance above spike deviation
    assert not keep[5]
    assert keep.sum() == 2


def test_rdp_degenerate_sizes():
    assert _rdp_mask(np.empty((0, 2)), eps=0.1).sum() == 0
    assert _rdp_mask(np.array([[1.0, 2.0]]), eps=0.1).sum() == 1
    assert _rdp_mask(np.array([[1.0, 2.0], [3.0, 4.0]]), eps=0.1).sum() == 2


# --- point-to-flight matching ---------------------------------------------

def _flights_frame():
    return pd.DataFrame({
        "icao24": ["ae0001", "ae0001"],
        "t_start": [100.0, 300.0],
        "t_end": [200.0, 400.0],
        "flight_id": [7, 8],
    })


def _write_trace(tmp_path, ts_list):
    n = len(ts_list)
    path = tmp_path / "trace.parquet"
    pd.DataFrame({
        "icao24": ["ae0001"] * n,
        "ts_s": np.asarray(ts_list, dtype=float),
        "lat": np.linspace(50.0, 51.0, n),
        "lon": np.full(n, 8.0),
        "alt_ft": np.full(n, 30000.0),
    }).to_parquet(path, index=False)
    return path


def test_match_boundaries_inclusive(tmp_path):
    # exactly t_start and t_end match; before/after/between excluded
    path = _write_trace(tmp_path, [99.0, 100.0, 200.0, 250.0, 300.0,
                                   400.0, 401.0])
    by_hex = _index_flights(_flights_frame())
    fid, ts, lat, lon, alt = _match_partition(path, by_hex)
    assert list(ts) == [100.0, 200.0, 300.0, 400.0]
    assert list(fid) == [7, 7, 8, 8]


def test_match_no_points_in_any_flight(tmp_path):
    path = _write_trace(tmp_path, [10.0, 250.0, 500.0])
    by_hex = _index_flights(_flights_frame())
    assert _match_partition(path, by_hex) is None


def test_match_unknown_aircraft_ignored(tmp_path):
    path = _write_trace(tmp_path, [150.0])
    by_hex = _index_flights(pd.DataFrame({
        "icao24": ["ae0002"], "t_start": [0.0], "t_end": [1000.0],
        "flight_id": [0],
    }))
    assert _match_partition(path, by_hex) is None


# --- decimation output -----------------------------------------------------

def _decimate_one(lats, lons, ts=None, alts=None, tolerance_m=2000.0):
    n = len(lats)
    return _decimate_batch(
        np.zeros(n, dtype=np.int64),
        np.asarray(ts if ts is not None else np.arange(n), dtype=float),
        np.asarray(lats, dtype=float), np.asarray(lons, dtype=float),
        np.asarray(alts if alts is not None else np.full(n, 30000.0),
                   dtype=float),
        tolerance_m,
    )


def test_pts_coarse_packs_four_values_per_point():
    rows = _decimate_one([50.0, 51.0], [8.0, 8.0], ts=[10.0, 20.0],
                         alts=[30000.0, np.nan])
    r = rows[0]
    assert len(r["pts_coarse"]) == 4 * 2
    # flat layout: ts, lat, lon, altitude in kft. NaN altitude is
    # PRESERVED (unknown != ground level); export's band computation
    # fills it from neighbouring known bands
    assert r["pts_coarse"][0] == 10.0
    assert r["pts_coarse"][1] == 50.0
    assert r["pts_coarse"][2] == 8.0
    assert r["pts_coarse"][3] == 30.0
    assert np.isnan(r["pts_coarse"][7])


def test_points_resorted_by_timestamp():
    rows = _decimate_one([51.0, 50.0], [8.0, 8.0], ts=[20.0, 10.0])
    assert rows[0]["pts_coarse"][:2] == [10.0, 50.0]


def test_output_schema_fields():
    rows = _decimate_one([50.0, 51.0], [8.0, 8.0])
    assert set(rows[0]) == {"flight_id", "pts_coarse"}
    table = _rows_to_table(rows)
    assert table.schema == OUT_SCHEMA
    assert OUT_SCHEMA.names == ["flight_id", "pts_coarse"]


def test_rdp_preserves_closed_loop():
    """Coincident endpoints previously discarded every interior point —
    erasing orbits/racetracks from rendered routes. The radial fallback
    must keep the far side of the loop."""
    import numpy as np
    from backend.pipeline.etl.publish.trace import _rdp_mask

    loop = np.array([[50.0, 5.0], [50.5, 5.5], [51.0, 5.0],
                     [50.5, 4.5], [50.0, 5.0]])
    keep = _rdp_mask(loop, eps=0.01)
    assert keep[0] and keep[-1]
    assert keep[1:-1].any(), "interior of a closed loop must survive"
    # the farthest point from the shared endpoint is certainly kept
    assert keep[2]
