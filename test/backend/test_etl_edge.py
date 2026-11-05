"""etl boundaries: boundary-state extraction, VR smoothing, enrichment flags."""
import numpy as np
import pandas as pd

from backend.pipeline.etl.timeline.edge import (
    MAX_DT_S, boundary_props, enrich, smooth_vr,
)


# --- smooth_vr -----------------------------------------------------------

def test_smooth_vr_uses_raw_when_enough():
    alts = np.array([10000.0, 9900.0, 9800.0, 9700.0, 9600.0])
    vrs = np.array([-500.0, -600.0, -700.0, np.nan, np.nan])
    ts = np.arange(5) * 10.0
    # pre boundary at idx 4: window [0..4] has 3 raw values -> mean
    assert smooth_vr(alts, vrs, ts, 4, -1) == -600.0


def test_smooth_vr_altitude_fallback():
    # only 2 raw vr values -> derive from altitude delta over the window
    alts = np.array([10000.0, 9000.0, 8000.0])
    vrs = np.array([np.nan, np.nan, -500.0])
    ts = np.array([0.0, 60.0, 120.0])
    # pre at idx 2: (8000-10000)/120s * 60 = -1000 fpm
    assert np.isclose(smooth_vr(alts, vrs, ts, 2, -1), -1000.0)


def test_smooth_vr_single_raw_last_resort():
    alts = np.array([np.nan, np.nan])
    vrs = np.array([np.nan, -300.0])
    ts = np.array([0.0, 10.0])
    assert smooth_vr(alts, vrs, ts, 1, -1) == -300.0


def test_smooth_vr_nothing():
    alts = np.array([np.nan])
    vrs = np.array([np.nan])
    assert np.isnan(smooth_vr(alts, vrs, np.array([0.0]), 0, +1))


# --- boundary_props ------------------------------------------------------

def _trace(icao, ts, alt=30000.0, vr=0.0):
    n = len(ts)
    return pd.DataFrame({
        "icao24": [icao] * n, "ts_s": ts,
        "alt_ft": [alt] * n, "vert_rate_fpm": [vr] * n,
    })


def _blackout(icao="ae0001", pre_ts=1000.0, post_ts=5000.0):
    return pd.DataFrame({
        "icao24": [icao], "pre_ts": [pre_ts], "post_ts": [post_ts],
    })


def test_boundary_props_picks_flanking_points():
    # pre boundary: last point at/before pre_ts; post: first at/after post_ts
    t = pd.concat([
        _trace("ae0001", np.array([900.0, 1000.0]), alt=20000.0),
        _trace("ae0001", np.array([5000.0, 5100.0]), alt=10000.0),
    ], ignore_index=True)
    bp = boundary_props(t, _blackout())
    assert bp["pre_alt_ft"][0] == 20000.0
    assert bp["post_alt_ft"][0] == 10000.0


def test_boundary_props_extracts_gs_and_track():
    """The attribution model needs ground speed + heading at the edges;
    boundary_props samples them at the boundary index like alt/vr."""
    t = pd.DataFrame({
        "icao24": ["ae0001"] * 4,
        "ts_s": [900.0, 1000.0, 5000.0, 5100.0],
        "alt_ft": [20000.0, 20000.0, 10000.0, 10000.0],
        "vert_rate_fpm": [0.0, 0.0, 0.0, 0.0],
        "gs_kt": [410.0, 400.0, 200.0, 190.0],
        "track_deg": [90.0, 95.0, 270.0, 275.0],
    })
    bp = boundary_props(t, _blackout())
    assert bp["pre_gs_kt"][0] == 400.0 and bp["pre_track_deg"][0] == 95.0
    assert bp["post_gs_kt"][0] == 200.0 and bp["post_track_deg"][0] == 270.0


def test_boundary_props_gs_track_nan_when_columns_absent():
    """No gs_kt/track_deg columns (older trace read) -> NaN, not a crash."""
    bp = boundary_props(_trace("ae0001", np.array([1000.0, 5000.0])), _blackout())
    assert np.isnan(bp["pre_gs_kt"][0]) and np.isnan(bp["pre_track_deg"][0])


def test_boundary_props_respects_max_dt():
    # nearest points are 121s away on both sides -> no state extracted
    t = _trace("ae0001", np.array([1000.0 - MAX_DT_S - 1,
                                   5000.0 + MAX_DT_S + 1]))
    bp = boundary_props(t, _blackout())
    assert np.isnan(bp["pre_alt_ft"][0])
    assert np.isnan(bp["post_alt_ft"][0])


def test_boundary_props_unknown_aircraft():
    bp = boundary_props(_trace("ae0002", np.array([1000.0])), _blackout())
    assert np.isnan(bp["pre_alt_ft"][0])


# --- enrichment ----------------------------------------------------------

def _pb(pre_lon=-20.0, post_lon=-60.0, gc_km=3000.0, gap_s=6 * 3600.0,
        pre_lat=45.0, post_lat=45.0):
    """One blackout with boundary props already attached."""
    return pd.DataFrame({
        "icao24": ["ae0001"], "pre_lat": [pre_lat], "pre_lon": [pre_lon],
        "post_lat": [post_lat], "post_lon": [post_lon],
        "gc_km": [gc_km], "gap_s": [gap_s],
        "pre_alt_ft": [30000.0], "post_alt_ft": [20000.0],
    })


def _enrich(pb):
    return enrich(pb, {"ae0001": "C17"})


def test_enrich_derived_columns():
    pb = _enrich(_pb())
    assert np.isclose(pb.impl_kph[0], 3000.0 / 6.0)
    assert pb.tc[0] == "C17"


def test_transatlantic_split_straddles_meridian():
    # pre -20 (east of 31W), post -60 (west of it) -> straddle, 6h gap
    pb = _enrich(_pb())
    assert pb.transatlantic_split[0]


def test_not_transatlantic_when_both_east_of_meridian():
    pb = _enrich(_pb(pre_lon=10.0, post_lon=20.0))
    assert not pb.transatlantic_split[0]


def test_not_transatlantic_when_both_west_of_meridian():
    pb = _enrich(_pb(pre_lon=-50.0, post_lon=-70.0))
    assert not pb.transatlantic_split[0]


def test_transatlantic_requires_3h_gap():
    # straddles the meridian but only 2 h -> not split (short enough that the
    # aircraft likely did not leave the study area)
    pb = _enrich(_pb(gap_s=2 * 3600.0))
    assert not pb.transatlantic_split[0]


def test_transatlantic_wrap_guard_rejects_antimeridian_pair():
    # 140E and -150E straddle 31W numerically but wrap the antimeridian
    # (|Δlon| = 290 > 180) -> a Pacific pair, not a crossing
    pb = _enrich(_pb(pre_lon=140.0, post_lon=-150.0))
    assert not pb.transatlantic_split[0]


# --- run_edge batching equivalence ---------------------------------------

def test_run_edge_batched_matches_single_batch(tmp_data_dir, monkeypatch):
    """The AIRCRAFT_BATCH-bounded load must produce a byte-identical
    edges frame to one whole-set load: boundary_props is per-aircraft and
    the output keeps blackouts.parquet's own row order."""
    import backend.pipeline.etl.loader as loader
    import backend.pipeline.etl.timeline.edge as edge_mod
    from backend.pipeline.etl.timeline.edge import run_edge

    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir()
    pd.DataFrame({"icao24": ["ae0001", "ae0002"],
                  "excluded": [False, False],
                  "type_code": ["C17", "K35R"]}) \
        .to_parquet(etl / "aircraft.parquet", index=False)
    pd.DataFrame({
        "icao24": ["ae0001", "ae0002"],
        "pre_ts": [1000.0, 1000.0], "post_ts": [5000.0, 5000.0],
        "pre_lon": [-20.0, 10.0], "post_lon": [-60.0, 12.0],
        "gc_km": [3000.0, 200.0], "gap_s": [6 * 3600.0, 4000.0],
    }).to_parquet(etl / "blackouts.parquet", index=False)

    n = 20
    ts = np.linspace(0.0, 6000.0, n)
    raw = tmp_data_dir / "build" / "ingest" / "date=2026-02-26"
    raw.mkdir(parents=True)
    pd.concat([
        pd.DataFrame({
            "icao24": ic, "ts_s": ts.astype("int64"),
            "lat": 50.0 + off, "lon": 5.0 + np.arange(n) * 0.01,
            "alt_ft": 30000.0, "on_ground": False, "gs_kt": 400.0,
            "vert_rate_fpm": 0.0, "track_deg": 90.0, "source": "adsb_icao",
        }) for off, ic in ((0.0, "ae0001"), (2.0, "ae0002"))
    ]).to_parquet(raw / "trace.parquet", index=False)

    import backend.util.dimensions as dimensions
    monkeypatch.setattr(dimensions, "dates", lambda: ["2026-02-26"])
    try:
        run_edge()
        whole = pd.read_parquet(etl / "edges.parquet")
        monkeypatch.setattr(edge_mod, "AIRCRAFT_BATCH", 1)
        run_edge()
        one = pd.read_parquet(etl / "edges.parquet")
    finally:
        loader.reset_exclusion_cache()

    assert whole.pre_alt_ft.notna().any()   # fixture exercises the props
    pd.testing.assert_frame_equal(whole, one)
