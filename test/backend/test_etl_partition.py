"""etl partition: ground/airborne interval split and blackout detection
on hand-built states."""
import numpy as np
import pandas as pd

from backend.pipeline.etl.timeline.partition import (
    OFF, GROUND, AIR,
    partition, interior_blackouts, _runs, profile_from_points,
)
from backend.pipeline.etl.preprocess.types import classify_excluded


def _state(seq):
    m = {"o": OFF, "g": GROUND, "a": AIR}
    return np.array([m[c] for c in seq], dtype=np.int8)


def test_ground_interval_seeded_by_ground_absorbs_adjacent_off():
    # off-ground-off in the middle, air on the ends.
    s = _state("aaoogoaa")  # idx 2,3 off ; 4 ground ; 5 off
    ground_ivs, air_ivs = partition(s)
    assert ground_ivs == [(2, 5)]              # the {off,ground,off} run, ground-seeded
    assert air_ivs == [(0, 1), (6, 7)]  # the two air ends


def test_pure_off_run_is_airborne_not_ground_interval():
    # off run with no ground -> stays in the airborne complement.
    s = _state("aaooaa")
    ground_ivs, air_ivs = partition(s)
    assert ground_ivs == []
    assert air_ivs == [(0, 5)]  # one airborne interval spanning air-off-air


def test_interior_blackout_flanked_by_air():
    s = _state("aaooaa")
    (air_iv,) = partition(s)[1]
    assert interior_blackouts(s, air_iv) == [(2, 3)]   # the off run, flanked by air


def test_leading_and_trailing_off_are_not_blackouts():
    # off at the start/end of an airborne interval is a coverage edge, not a split.
    s = _state("ooaaoo")   # all one airborne interval (no ground)
    (air_iv,) = partition(s)[1]
    assert interior_blackouts(s, air_iv) == []   # leading and trailing off excluded


def test_multi_airborne_interval_aircraft():
    # air ... ground(landing) ... air ... off-coverage ... air
    s = _state("aagaaooaa")
    ground_ivs, air_ivs = partition(s)
    assert ground_ivs == [(2, 2)]  # the single ground bin (its own ground interval)
    assert air_ivs == [(0, 1), (3, 8)]  # two flights' airspace, second has a blackout
    assert interior_blackouts(s, air_ivs[1]) == [(5, 6)]


def test_ground_with_trailing_off_extends_ground_interval():
    s = _state("aagooaa")               # ground then off then air
    ground_ivs, _ = partition(s)
    assert ground_ivs == [(2, 4)]             # ground+off floodfilled together


def test_runs_basic():
    assert _runs(np.array([0, 0, 2, 2, 1], dtype=np.int8)) == [
        (0, 0, 1), (2, 2, 3), (1, 4, 4)]


# --- profile_from_points ground-detection tests ---

def _make_profile(n_bins, points):
    """Helper: build profile from a list of (bin_idx, on_ground, gs) tuples.

    Returns the state array. lat/lon set to 0. gs is unused by
    profile_from_points (ground detection is on_ground-only) but kept in
    the tuple shape for test readability (see test_slow_airborne_is_air)."""
    ts = np.array([p[0] * 10.0 for p in points])
    lat = np.zeros(len(points))
    lon = np.zeros(len(points))
    on_ground = np.array([p[1] for p in points], dtype=bool)
    state, _, _, _ = profile_from_points(ts, lat, lon, on_ground,
                                         t0=0.0, bin_s=10, n_bins=n_bins)
    return state


def test_on_ground_is_ground():
    """on_ground flag -> GROUND regardless of speed."""
    state = _make_profile(10, [(5, True, 150.0)])
    assert state[5] == GROUND


def test_not_on_ground_is_air():
    """on_ground=False with any speed -> AIR."""
    state = _make_profile(10, [
        (3, False, 30.0),   # slow but airborne
        (7, False, 400.0),  # fast and airborne
    ])
    assert state[3] == AIR
    assert state[7] == AIR


def test_slow_airborne_is_air():
    """gs<40 with on_ground=False is AIR (hover, not ground)."""
    state = _make_profile(20, [
        (5, False, 10.0),
        (6, False, 5.0),
        (7, False, 0.0),
    ])
    assert state[5] == AIR
    assert state[6] == AIR
    assert state[7] == AIR


def test_no_data_is_off():
    """Bins with no points are OFF."""
    state = _make_profile(10, [(2, False, 300.0)])
    assert state[0] == OFF
    assert state[5] == OFF


def test_ground_wins_shared_bin():
    """If a bin has both on_ground and airborne points, ground wins."""
    # Two points in the same bin (same timestamp bucket)
    ts = np.array([50.0, 55.0])  # both map to bin 5
    lat = np.zeros(2)
    lon = np.zeros(2)
    on_ground = np.array([False, True], dtype=bool)
    state, _, _, _ = profile_from_points(ts, lat, lon, on_ground,
                                         t0=0.0, bin_s=10, n_bins=10)
    assert state[5] == GROUND


# --- exclusion gate: run_partition's keep = aircraft[~aircraft.excluded] ----

def test_run_partition_keep_gate_drops_rotary_and_non_aircraft():
    """The exact gate run_partition applies (keep = aircraft[~excluded]):
    rotary and non_aircraft aircraft drop, ordinary and untyped ones
    remain."""
    aircraft = pd.DataFrame({
        "icao24": ["rotary1", "nonair1", "ordinary1", "untyped1"],
        "type_code": ["H60", "TWR", "C17", None],
        "rotary": [True, False, False, False],
    })
    aircraft = classify_excluded(aircraft)
    keep = aircraft[~aircraft.excluded]
    assert set(keep.icao24) == {"ordinary1", "untyped1"}


def test_blackout_distance_uses_shared_haversine():
    """gc_km must come from the shared util implementation, not a local
    duplicate (the audit found an unimported arcsin copy feeding the one
    methodology-named threshold-critical distance)."""
    import backend.pipeline.etl.timeline.partition as profile
    from backend.util.geo.haversine import haversine_km
    assert profile.haversine_km is haversine_km
    assert not hasattr(profile, "_haversine")
    # one degree of latitude at constant longitude ~ 111.19 km
    assert abs(float(haversine_km(50.0, 5.0, 51.0, 5.0)) - 111.19) < 0.05


# --- run_partition: AIRCRAFT_BATCH must not change the result -------------

def test_run_partition_batch_size_does_not_change_output(tmp_data_dir, monkeypatch):
    """AIRCRAFT_BATCH only bounds peak memory (see its docstring) -- it
    must not change which points end up in which aircraft's intervals or
    blackouts. Runs the same fixture once batched one-aircraft-at-a-time
    and once as a single batch, and requires byte-identical output."""
    import backend.pipeline.etl.loader as loader
    import backend.pipeline.etl.timeline.partition as profile_mod
    from backend.util import dimensions as dims

    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir(parents=True)
    hexes = ["ae0001", "ae0002", "ae0003"]
    pd.DataFrame({
        "icao24": hexes,
        "type_code": ["C17"] * 3,
        "rotary": [False] * 3,
        "excluded": [False] * 3,
    }).to_parquet(etl / "aircraft.parquet", index=False)

    from datetime import datetime, timezone
    t0 = int(datetime.strptime(dims.start(), "%Y-%m-%d")
             .replace(tzinfo=timezone.utc).timestamp())
    # bin 0: on ground; bins 1-2: airborne (takeoff); bin 100: airborne
    # again -- the 97-bin (970s) silence in between is a manufactured,
    # interior, air-flanked blackout (> BLACKOUT_MIN_S = 900s).
    bin_idxs = [0, 1, 2, 100]
    on_ground = [True, False, False, False]

    frames = []
    for i, hx in enumerate(hexes):
        frames.append(pd.DataFrame({
            "icao24": hx,
            "ts_s": [t0 + b * 10 for b in bin_idxs],
            "lat": 45.0 + i, "lon": 10.0 + i,
            "alt_ft": [0.0, 5000.0, 35000.0, 35000.0],
            "on_ground": on_ground,
            "gs_kt": [0.0, 250.0, 480.0, 480.0],
            "vert_rate_fpm": [0.0, 1500.0, 0.0, 0.0],
        }))
    raw = tmp_data_dir / "build" / "ingest" / "date=2026-02-26"
    raw.mkdir(parents=True)
    pd.concat(frames, ignore_index=True).to_parquet(
        raw / "trace.parquet", index=False)

    monkeypatch.setattr(dims, "dates", lambda: ["2026-02-26"])
    try:
        monkeypatch.setattr(profile_mod, "AIRCRAFT_BATCH", 1)
        profile_mod.run_partition()
        iv_batch1 = pd.read_parquet(etl / "intervals.parquet")
        bo_batch1 = pd.read_parquet(etl / "blackouts.parquet")

        monkeypatch.setattr(profile_mod, "AIRCRAFT_BATCH", 300)
        profile_mod.run_partition()
        iv_batch300 = pd.read_parquet(etl / "intervals.parquet")
        bo_batch300 = pd.read_parquet(etl / "blackouts.parquet")
    finally:
        loader.reset_exclusion_cache()

    # sanity: the fixture actually produced the intended shape, so the
    # equivalence check below isn't trivially passing on two empty frames
    assert len(iv_batch1) == 3 * 2   # each aircraft: 1 ground + 1 airborne interval
    assert len(bo_batch1) == 3       # each aircraft: exactly the one manufactured gap

    def _norm(df):
        return df.sort_values(list(df.columns)).reset_index(drop=True)

    pd.testing.assert_frame_equal(_norm(iv_batch1), _norm(iv_batch300))
    pd.testing.assert_frame_equal(_norm(bo_batch1), _norm(bo_batch300))


def test_run_partition_preserves_census_mtime(tmp_data_dir, monkeypatch):
    """run_partition rewrites aircraft.parquet in place (in_bbox column) but
    must restore its mtime: the cli staleness chain reads that mtime as the
    types-lineage timestamp, and letting the rewrite bump it past
    exclusions.parquet would flag denoise stale after every full run."""
    import os
    import backend.pipeline.etl.loader as loader
    import backend.pipeline.etl.timeline.partition as profile_mod
    from backend.util import dimensions as dims

    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir(parents=True)
    aircraft_path = etl / "aircraft.parquet"
    pd.DataFrame({
        "icao24": ["ae0001"],
        "type_code": ["C17"],
        "rotary": [False],
        "excluded": [False],
    }).to_parquet(aircraft_path, index=False)
    orig_mtime = aircraft_path.stat().st_mtime - 100.0
    os.utime(aircraft_path, (orig_mtime, orig_mtime))

    from datetime import datetime, timezone
    t0 = int(datetime.strptime(dims.start(), "%Y-%m-%d")
             .replace(tzinfo=timezone.utc).timestamp())
    raw = tmp_data_dir / "build" / "ingest" / "date=2026-02-26"
    raw.mkdir(parents=True)
    pd.DataFrame({
        "icao24": "ae0001",
        "ts_s": [t0, t0 + 10],
        "lat": 45.0, "lon": 10.0,
        "alt_ft": [35000.0, 35000.0],
        "on_ground": False,
        "gs_kt": 480.0,
        "vert_rate_fpm": 0.0,
    }).to_parquet(raw / "trace.parquet", index=False)

    monkeypatch.setattr(dims, "dates", lambda: ["2026-02-26"])
    try:
        profile_mod.run_partition()
    finally:
        loader.reset_exclusion_cache()

    assert "in_bbox" in pd.read_parquet(aircraft_path).columns
    assert aircraft_path.stat().st_mtime == orig_mtime
