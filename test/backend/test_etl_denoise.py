"""denoise stage: local-fit residual detector + exclusion filtering."""
from __future__ import annotations

import numpy as np
import pandas as pd

import backend.pipeline.etl.loader as loader
from backend.pipeline.etl.preprocess.denoise import (
    _fit_residuals, _fit_residuals_vec, _flag_aircraft, run_denoise,
)


def _straight_track(n=60, step_s=10, spd_deg=0.01):
    """A clean, evenly-sampled great-circle-ish track heading east."""
    ts = np.arange(n, dtype=float) * step_s
    lat = np.full(n, 50.0)
    lon = 5.0 + np.arange(n) * spd_deg
    return ts, lat, lon


def test_clean_track_flags_nothing():
    ts, lat, lon = _straight_track()
    assert not _flag_aircraft(ts, lat, lon).any()


def test_single_spike_is_flagged():
    ts, lat, lon = _straight_track()
    lon[30] += 1.5              # ~100 km sideways jump for one report
    mask = _flag_aircraft(ts, lat, lon)
    assert mask[30]
    assert mask.sum() == 1     # only the spike, not its neighbours


def test_gap_edge_report_is_testable():
    """A displaced report next to a long silence is still judged (the speed
    detector could not resolve these)."""
    ts, lat, lon = _straight_track(n=40)
    ts[20:] += 3600.0          # 1-hour gap before index 20
    lon[20] += 1.0             # displace the report at the gap edge
    mask = _flag_aircraft(ts, lat, lon)
    assert mask[20]


def test_fit_residual_excludes_self():
    """The report under test does not pull its own prediction."""
    ts, lat, lon = _straight_track()
    lon[30] += 2.0
    res = _fit_residuals(ts, lat, lon)
    assert res[30] > 100        # full deviation, not shrunk toward the outlier


def test_vectorised_matches_scalar():
    """The fast vectorised residual must equal the scalar oracle exactly."""
    rng = np.random.default_rng(7)
    for _ in range(8):
        n = int(rng.integers(15, 250))
        ts = np.cumsum(rng.integers(5, 40, n)).astype(float)
        lat = 50 + np.cumsum(rng.normal(0, 0.01, n))
        lon = 5 + np.cumsum(rng.normal(0, 0.01, n))
        if rng.random() < 0.7:                       # inject an outlier
            lon[int(rng.integers(5, n - 5))] += rng.uniform(0.5, 2.0)
        a = _fit_residuals(ts, lat, lon)
        b = _fit_residuals_vec(ts, lat, lon)
        assert np.allclose(a, b, atol=1e-6, equal_nan=True)


def test_exclusion_filter_roundtrip(tmp_data_dir):
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir()
    df = pd.DataFrame({
        "icao24": ["ae0001"] * 5,
        "ts_s": [10, 20, 30, 40, 50],
        "lat": [1.0, 2, 3, 4, 5], "lon": [1.0, 2, 3, 4, 5],
    })
    pd.DataFrame({"icao24": ["ae0001", "ae0001"], "ts_s": [20, 40]}) \
        .to_parquet(etl / "exclusions.parquet", index=False)
    loader.reset_exclusion_cache()
    out = loader.drop_excluded(df)
    assert list(out.ts_s) == [10, 30, 50]
    loader.reset_exclusion_cache()


def test_exclusion_absent_is_noop(tmp_data_dir):
    (tmp_data_dir / "build" / "etl").mkdir()
    loader.reset_exclusion_cache()
    df = pd.DataFrame({"icao24": ["x"], "ts_s": [1], "lat": [0.0], "lon": [0.0]})
    assert len(loader.drop_excluded(df)) == 1
    loader.reset_exclusion_cache()


def test_run_denoise_writes_exclusions(tmp_data_dir):
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir()
    pd.DataFrame({"icao24": ["ae0001"], "excluded": [False]}) \
        .to_parquet(etl / "aircraft.parquet", index=False)

    ts, lat, lon = _straight_track()
    lon[30] += 1.5
    raw = tmp_data_dir / "build" / "ingest" / "date=2026-02-26"
    raw.mkdir(parents=True)
    pd.DataFrame({"icao24": "ae0001", "ts_s": ts.astype("int64"),
                  "lat": lat, "lon": lon,
                  "source": "adsb_icao"}).to_parquet(
        raw / "trace.parquet", index=False)

    # dimensions.dates() reads dimensions.json; monkeypatch it to our single date
    import backend.util.dimensions as dimensions
    orig = dimensions.dates
    dimensions.dates = lambda: ["2026-02-26"]
    try:
        run_denoise()
    finally:
        dimensions.dates = orig
    excl = pd.read_parquet(etl / "exclusions.parquet")
    assert len(excl) == 1 and int(excl.ts_s.iloc[0]) == int(ts[30])
    loader.reset_exclusion_cache()


# ---------------------------------------------------------------------------
# AND-semantics regression: _flag_aircraft requires BOTH the K_SIGMA-times-
# local-scatter test AND the absolute FLOOR_KM test. A one-character
# `& -> |` regression would flag either of the two cases below.
# ---------------------------------------------------------------------------

def test_flag_requires_floor_km_not_just_k_sigma():
    """A displacement well beyond K_SIGMA * local scatter, but still under
    the absolute FLOOR_KM, must NOT be flagged."""
    from backend.pipeline.etl.preprocess.denoise import FLOOR_KM, K_SIGMA
    ts, lat, lon = _straight_track()
    lon[30] += 0.03   # ~2.1 km: >> K_SIGMA * local scatter (~0.3 km on a
                       # clean track) but < FLOOR_KM (3.0 km)
    mask = _flag_aircraft(ts, lat, lon)
    assert not mask[30]
    assert 2.1 < FLOOR_KM   # documents which side of the floor this sits on


def test_flag_requires_k_sigma_not_just_floor_km():
    """A displacement beyond FLOOR_KM, but not beyond K_SIGMA times a
    (deliberately noisy) local scatter, must NOT be flagged."""
    ts, lat, lon = _straight_track()
    rng = np.random.default_rng(2)
    noise = rng.normal(0, 0.02, size=len(lon))
    noise[30] = 0.0          # the point under test stays on the point 30
    lon = lon + noise        # jitter every OTHER point to raise local scatter
    lon[30] += 0.05          # ~4.5 km displacement: > FLOOR_KM (3.0 km) but
                              # well under K_SIGMA * (now-noisy) local scatter
    mask = _flag_aircraft(ts, lat, lon)
    assert not mask[30]


def test_run_denoise_does_not_rewrite_census(tmp_data_dir):
    """denoise reads aircraft.parquet for the keep set and leaves it alone:
    the staleness gate uses its mtime as the types-stage marker."""
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir()
    census_path = etl / "aircraft.parquet"
    pd.DataFrame({"icao24": ["ae0001"], "excluded": [False]}) \
        .to_parquet(census_path, index=False)
    before = census_path.stat().st_mtime_ns
    ts, lat, lon = _straight_track()
    raw = tmp_data_dir / "build" / "ingest" / "date=2026-02-26"
    raw.mkdir(parents=True)
    pd.DataFrame({"icao24": "ae0001", "ts_s": ts.astype("int64"),
                  "lat": lat, "lon": lon, "source": "adsb_icao"}) \
        .to_parquet(raw / "trace.parquet", index=False)
    import backend.util.dimensions as dimensions
    orig = dimensions.dates
    dimensions.dates = lambda: ["2026-02-26"]
    try:
        run_denoise()
    finally:
        dimensions.dates = orig
    assert census_path.stat().st_mtime_ns == before
    assert (etl / "exclusions.parquet").exists()
    loader.reset_exclusion_cache()


def test_teleport_mask_flags_isolated_impossible_report():
    """A report whose every near neighbor implies >TELEPORT_KT travel is
    excluded; a conflicting pair flags both members (no adjudication);
    plausible sparse points survive."""
    import numpy as np
    from backend.pipeline.etl.preprocess.denoise import _teleport_mask
    # fast cruise with jetstream (~600 kt ground): plausible, nothing
    # flagged — the bound must sit far above any real ground speed
    ts = np.array([0.0, 600.0, 1200.0, 1800.0])
    lat = np.array([50.0, 51.66, 53.32, 54.98])
    lon = np.full(4, 8.0)
    assert not _teleport_mask(ts, lat, lon).any()
    # same track with one point displaced 700 km: the stray is flagged,
    # and so is the trailing edge point whose ONLY near neighbor is the
    # stray — one neighbor, one vote, no adjudication, both drop (the
    # same stance as the two-point conflict below). Interior neighbors
    # of the stray survive on the strength of their other side.
    lat2 = lat.copy(); lat2[2] = 70.0
    m = _teleport_mask(ts, lat2, lon)
    assert m.tolist() == [False, False, True, True]
    # two-point conflict: both flagged
    m2 = _teleport_mask(np.array([0.0, 300.0]),
                        np.array([50.0, 58.0]), np.array([8.0, 8.0]))
    assert m2.tolist() == [True, True]


# ---------------------------------------------------------------------------
# batching equivalence: the AIRCRAFT_BATCH-bounded scan must produce the
# same exclusions and census verdicts as one whole-set pass (the canonical
# (icao24, ts_s) output sort makes the comparison order-independent).
# ---------------------------------------------------------------------------
def test_run_denoise_batched_matches_single_batch(tmp_data_dir, monkeypatch):
    import backend.pipeline.etl.preprocess.denoise as dn

    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir()
    pd.DataFrame({"icao24": ["ae0001", "ae0002"],
                  "excluded": [False, False]}) \
        .to_parquet(etl / "aircraft.parquet", index=False)

    ts, lat, lon = _straight_track()
    lon2 = lon.copy()
    lon2[30] += 1.5                      # ae0002: one flagged spike
    raw = tmp_data_dir / "build" / "ingest" / "date=2026-02-26"
    raw.mkdir(parents=True)
    pd.concat([
        pd.DataFrame({"icao24": "ae0001", "ts_s": ts.astype("int64"),
                      "lat": lat, "lon": lon, "source": "adsb_icao"}),
        pd.DataFrame({"icao24": "ae0002", "ts_s": ts.astype("int64"),
                      "lat": lat, "lon": lon2, "source": "adsb_icao"}),
    ]).to_parquet(raw / "trace.parquet", index=False)

    import backend.util.dimensions as dimensions
    monkeypatch.setattr(dimensions, "dates", lambda: ["2026-02-26"])
    try:
        run_denoise()
        excl_whole = pd.read_parquet(etl / "exclusions.parquet")

        # re-run with one aircraft per batch
        monkeypatch.setattr(dn, "AIRCRAFT_BATCH", 1)
        run_denoise()
        excl_one = pd.read_parquet(etl / "exclusions.parquet")
    finally:
        loader.reset_exclusion_cache()

    assert len(excl_whole) > 0           # the fixture must exercise the loop
    pd.testing.assert_frame_equal(excl_whole, excl_one)
