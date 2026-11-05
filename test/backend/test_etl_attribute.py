"""etl attribute: attribution on a stub raster, sightings, endpoint fallback."""
import numpy as np
import pandas as pd

import backend.pipeline.etl.landing.attribute as countries_mod
from backend.pipeline.etl.landing.attribute import (
    _accumulate_sightings, run_attribute,
)


class StubRaster:
    """Country id by longitude band: [0,10) AA, [10,20) BB, >=20 CC.

    Southern hemisphere (lat < 0) is open ocean (id 0). Carries a
    minimal airport index (one far-away dummy field, SnapIndex needs a
    non-empty BallTree) so run_attribute' landing-evidence scan works;
    no test point ever snaps to it.
    """

    _id_to_iso2 = {1: "AA", 2: "BB", 3: "CC"}

    def __init__(self):
        from sklearn.neighbors import BallTree
        self.airports = [{"icao": "XXXX", "ident": "XXXX", "name": "far",
                          "country": "ZZ", "type": "medium_airport",
                          "lat": -80.0, "lon": 170.0, "radius_km": 3.0,
                          "elevation_ft": 0.0}]
        self._airport_tree = BallTree(
            np.radians([[-80.0, 170.0]]), metric="haversine")
        self._airport_radii_km = np.array([3.0])

    def country_id_at_many(self, lats, lons):
        lats = np.asarray(lats, dtype=float)
        lons = np.asarray(lons, dtype=float)
        ids = np.zeros(len(lats), dtype=np.int32)
        ids[(lons >= 0) & (lons < 10)] = 1
        ids[(lons >= 10) & (lons < 20)] = 2
        ids[lons >= 20] = 3
        ids[lats < 0] = 0
        return ids

    @classmethod
    def load(cls, data_dir):
        return cls()


# --- sightings accumulation -------------------------------------------------

def test_sightings_first_last_per_aircraft_day_country():
    sightings = {}
    _accumulate_sightings(
        sightings, "ae0001",
        np.array([100.0, 200.0, 90000.0]), np.array([1, 1, 2]))
    assert sightings[("ae0001", 0, 1)] == [100.0, 200.0]
    assert sightings[("ae0001", 1, 2)] == [90000.0, 90000.0]


def test_sightings_merge_across_calls():
    sightings = {}
    _accumulate_sightings(sightings, "ae0001",
                          np.array([100.0, 200.0]), np.array([1, 1]))
    _accumulate_sightings(sightings, "ae0001",
                          np.array([50.0, 300.0]), np.array([1, 1]))
    assert sightings[("ae0001", 0, 1)] == [50.0, 300.0]


def test_sightings_skip_no_country_points():
    sightings = {}
    _accumulate_sightings(sightings, "ae0001",
                          np.array([100.0]), np.array([0]))
    assert sightings == {}


# --- run_attribute end-to-end on synthetic data ------------------------------

def test_run_attribute_trace_attribution(tmp_data_dir, monkeypatch):
    monkeypatch.setattr(countries_mod, "GeoRaster", StubRaster)
    (tmp_data_dir / "build" / "etl").mkdir()
    (tmp_data_dir / "generated").mkdir()

    flights = pd.DataFrame({
        "icao24": ["ae0001", "ae0002"],
        "flight_id": [0, 1],
        "t_start": [100.0, 100.0],
        "t_end": [200.0, 200.0],
        # attribution comes from the traces below, not the endpoints
        "start_lat": [50.0, 50.0], "start_lon": [5.0, 15.0],
        "end_lat": [50.0, 50.0], "end_lon": [5.0, 25.0],
        "origin_grounded": False, "dest_grounded": False,
    })
    flights_path = tmp_data_dir / "build" / "etl" / "flights.parquet"
    flights.to_parquet(flights_path, index=False)
    pd.DataFrame({
        "icao24": flights.icao24.unique(),
        "excluded": False, "in_bbox": True,
    }).to_parquet(tmp_data_dir / "build" / "etl" / "aircraft.parquet",
                  index=False)

    part = tmp_data_dir / "build" / "ingest" / "date=2026-02-26"
    part.mkdir()
    pd.DataFrame({
        "icao24": ["ae0001"] * 3 + ["ae0002"] * 2,
        # 500 is outside ae0001's [t_start, t_end]: its country (CC) must not appear
        "ts_s": [120.0, 150.0, 500.0, 120.0, 150.0],
        "lat": [50.0, 50.0, 50.0, 50.0, 50.0],
        "lon": [5.0, 15.0, 25.0, 15.0, 25.0],
        "on_ground": False,
        "gs_kt": 400.0,
        "alt_ft": 30000.0,
        "vert_rate_fpm": 0.0,
        "track_deg": 90.0,
        "source": "adsb_icao",
    }).to_parquet(part / "trace.parquet", index=False)

    run_attribute()

    out = pd.read_parquet(flights_path)
    assert list(out["traversed_countries"]) == ["AA,BB", "BB,CC"]

    st = pd.read_parquet(tmp_data_dir / "build" / "etl" / "sighting_times.parquet")
    st = st.sort_values(["icao24", "cc"]).reset_index(drop=True)
    # only points inside each flight's [t_start, t_end] window count, so
    # ae0001's third point (ts 500, lon 25 -> CC) is excluded
    assert list(st["icao24"]) == ["ae0001", "ae0001", "ae0002", "ae0002"]
    assert list(st["day"]) == [0, 0, 0, 0]
    assert list(st["cc"]) == ["AA", "BB", "BB", "CC"]
    assert list(st["first_ts"]) == [120.0, 150.0, 120.0, 150.0]
    assert list(st["last_ts"]) == [120.0, 150.0, 120.0, 150.0]
    # ae0002 alone (one flight) already exercises "a plane crossing several
    # countries": its trace touches BB and CC, both recorded — this is the
    # primitive the frontend's per-country `planes` numerator
    # reads via sightings/<cc>.json, one file per country, same icao24 key
    # appearing in each country's file it was sighted in.


def test_run_attribute_two_flights_same_aircraft_same_day_merge_to_one_row(
    tmp_data_dir, monkeypatch,
):
    """Two flights of the SAME aircraft, both crossing the same country on
    the same UTC day, must merge into exactly ONE sighting_times.parquet
    row for that (icao24, day, country) — not one row per flight.

    This is the ETL-layer guarantee that the frontend's `planes`
    computation (panel-data.js/stats.js) depends on: it counts distinct
    in-scope aircraft by building a Set/dict keyed on icao24 from
    sightings/<cc>.json. sighting_times.parquet already deduplicates by
    (icao24, day, country) — via _accumulate_sightings's per-hex merge —
    so no downstream JS aggregation step can be tricked into counting one
    aircraft twice just because it flew two separate missions the same day.
    """
    monkeypatch.setattr(countries_mod, "GeoRaster", StubRaster)
    (tmp_data_dir / "build" / "etl").mkdir()
    (tmp_data_dir / "generated").mkdir()

    flights = pd.DataFrame({
        "icao24": ["ae0001", "ae0001"],
        "flight_id": [0, 1],
        "t_start": [100.0, 300.0],
        "t_end": [200.0, 400.0],
        "start_lat": [50.0, 50.0], "start_lon": [5.0, 5.0],
        "end_lat": [50.0, 50.0], "end_lon": [5.0, 5.0],
        "origin_grounded": False, "dest_grounded": False,
    })
    flights_path = tmp_data_dir / "build" / "etl" / "flights.parquet"
    flights.to_parquet(flights_path, index=False)
    pd.DataFrame({
        "icao24": flights.icao24.unique(),
        "excluded": False, "in_bbox": True,
    }).to_parquet(tmp_data_dir / "build" / "etl" / "aircraft.parquet",
                  index=False)

    part = tmp_data_dir / "build" / "ingest" / "date=2026-02-26"
    part.mkdir()
    pd.DataFrame({
        "icao24": ["ae0001", "ae0001"],
        # one point per flight, both inside country AA (lon in [0,10))
        "ts_s": [120.0, 320.0],
        "lat": [50.0, 50.0],
        "lon": [5.0, 5.0],
        "on_ground": False,
        "gs_kt": 400.0,
        "alt_ft": 30000.0,
        "vert_rate_fpm": 0.0,
        "track_deg": 90.0,
        "source": "adsb_icao",
    }).to_parquet(part / "trace.parquet", index=False)

    run_attribute()

    st = pd.read_parquet(tmp_data_dir / "build" / "etl" / "sighting_times.parquet")
    assert len(st) == 1, "two same-day flights of one aircraft must merge, not double up"
    row = st.iloc[0]
    assert (row["icao24"], row["cc"], row["day"]) == ("ae0001", "AA", 0)
    assert row["first_ts"] == 120.0 and row["last_ts"] == 320.0


# --- observed ground intervals (owner-approved interval model) --------------

def _obs_rows(df, gi, presplit=None):
    """Accumulate one partition (or several, via presplit list of frames)
    and run merge+attribution; returns interval rows."""
    from backend.pipeline.etl.landing.attribute import (
        _accumulate_observed_intervals, _merge_and_attribute_intervals)
    from backend.util.geo.airport_snap import SnapIndex
    intervals: dict = {}
    obs_runs: dict = {}
    frames = presplit if presplit is not None else [df]
    for f in frames:
        if "source" not in f.columns:
            f = f.assign(source="adsb_icao")
        f = f.sort_values(["icao24", "ts_s"], ignore_index=True)
        _accumulate_observed_intervals(intervals, obs_runs, f)
    # merge reception runs across partition boundaries, as
    # _collect_trace_countries does before handing them on
    from backend.pipeline.etl.constants import BLACKOUT_MIN_S
    merged = {}
    for hx, runs in obs_runs.items():
        runs.sort()
        out = [list(runs[0])]
        for a, b in runs[1:]:
            if a - out[-1][1] < BLACKOUT_MIN_S:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        merged[hx] = out
    return _merge_and_attribute_intervals(intervals, merged, gi,
                                          SnapIndex(gi))


def _one_field_gi():
    """GeoRaster stub with one medium field AAAA/DE at (50, 8); every
    coordinate rasterizes to PL."""
    from sklearn.neighbors import BallTree
    from backend.util.geo.raster import GeoRaster
    airports = [{"icao": "AAAA", "ident": "AAAA", "name": "AAAA",
                 "country": "DE", "type": "medium_airport", "lat": 50.0,
                 "lon": 8.0, "radius_km": 3.0, "elevation_ft": 0.0}]
    ap = {"airports": airports,
          "tree": BallTree(np.radians([[50.0, 8.0]]), metric="haversine"),
          "radii_km": np.array([3.0])}

    class _Raster(GeoRaster):
        def __init__(self):
            super().__init__(np.zeros((1, 1), dtype=np.uint16), {},
                             {1: "PL"}, ap)

        def country_id_at_many(self, lats, lons):
            return np.ones(len(np.asarray(lats)), dtype=np.int64)

    return _Raster()


def _key(r):
    return (r["kind"], r["airport_icao"], r["cc"], r["evidence"])


def test_intervals_q1_is_field_free_and_severed_by_transits():
    """Q1 ground indication needs no field: slow-low reports anywhere
    become a country-level interval (fixed-MSL bound). A non-indicating
    report (fast or high) severs; approach+rollout at the field and
    slow-low far from it are separate intervals, each placed by its own
    lowest points in the second pass."""
    gi = _one_field_gi()
    day = 20500
    t0 = day * 86400.0
    df = pd.DataFrame({
        "icao24": ["ae0001"] * 6,
        "ts_s": [t0 + 10, t0 + 60, t0 + 120,     # approach + rollout at AAAA
                 t0 + 300,                       # cruise point: severs
                 t0 + 500, t0 + 560],            # slow-low far from any field
        "lat": [50.0, 50.0, 50.0, 52.0, 55.0, 55.0],
        "lon": [8.0, 8.0, 8.0, 12.0, 20.0, 20.0],
        "alt_ft": [1000.0, 500.0, 0.0, 20000.0, 800.0, 700.0],
        "gs_kt": [140.0, 120.0, 10.0, 400.0, 90.0, 85.0],
        "on_ground": [False, False, True, False, False, False],
    })
    rows = _obs_rows(df, gi)
    assert [_key(r) for r in rows] == [
        ("observed", "AAAA", "DE", "LO"),
        ("observed", "", "PL", "L"),
    ]
    # trimmed edges: exactly [first report, last report], never extended
    assert (rows[0]["t_start"], rows[0]["t_end"]) == (t0 + 10, t0 + 120)
    assert (rows[1]["t_start"], rows[1]["t_end"]) == (t0 + 500, t0 + 560)


def test_intervals_blackout_gap_severs_sub15_join_holds():
    """Consecutive indicating reports join across a sub-15-min silence
    into one interval; a >=15-min silence severs into two (each side
    multi-report, so the isolation veto is not in play)."""
    gi = _one_field_gi()
    t0 = 20500 * 86400.0
    df = pd.DataFrame({
        "icao24": ["ae0001"] * 4,
        # 800 s join, 900 s sever, 50 s join
        "ts_s": [t0, t0 + 800, t0 + 1700, t0 + 1750],
        "lat": 50.0, "lon": 8.0, "alt_ft": 0.0, "gs_kt": 5.0,
        "on_ground": True,
    })
    rows = _obs_rows(df, gi)
    assert [(r["t_start"], r["t_end"]) for r in rows] == [
        (t0, t0 + 800), (t0 + 1700, t0 + 1750)]


def test_intervals_merge_across_partition_boundary():
    """A run touching the partition's last report continues into the next
    partition's first report when the gap stays under 15 min (midnight is
    not a silence); a trailing non-indicating report blocks the merge."""
    gi = _one_field_gi()
    day = 20500
    mid = (day + 1) * 86400.0
    d1 = pd.DataFrame({
        "icao24": ["ae0001", "ae0002", "ae0002"],
        "ts_s": [mid - 100, mid - 200, mid - 100],
        "lat": 50.0, "lon": 8.0,
        "alt_ft": [0.0, 0.0, 20000.0],
        "gs_kt": [5.0, 5.0, 400.0],       # ae0002 ends on a cruise report
        "on_ground": [True, True, False],
    })
    d2 = pd.DataFrame({
        "icao24": ["ae0001", "ae0002"],
        "ts_s": [mid + 100, mid + 100],
        "lat": 50.0, "lon": 8.0, "alt_ft": 0.0, "gs_kt": 5.0,
        "on_ground": True,
    })
    rows = _obs_rows(None, gi, presplit=[d1, d2])
    ae1 = [(r["t_start"], r["t_end"]) for r in rows
           if r["icao24"] == "ae0001"]
    assert ae1 == [(mid - 100, mid + 100)]
    ae2 = sorted((r["t_start"], r["t_end"]) for r in rows
                 if r["icao24"] == "ae0002")
    assert ae2 == [(mid - 200, mid - 200), (mid + 100, mid + 100)]


def test_intervals_fast_low_point_is_not_ground():
    """A low point above LOW_APPROACH_GS_KT is never ground indication."""
    gi = _one_field_gi()
    t0 = 20500 * 86400.0
    df = pd.DataFrame({
        "icao24": ["ae0001"] * 2,
        "ts_s": [t0 + 10, t0 + 60],
        "lat": 50.0, "lon": 8.0,
        "alt_ft": [1000.0, 900.0],
        "gs_kt": [300.0, 280.0],       # fast low pass over the field
        "on_ground": False,
    })
    assert _obs_rows(df, gi) == []


def test_fast_on_ground_flag_is_rejected():
    """The on-ground bit misfires in bursts at cruise speed; a report with
    the flag set but gs > LOW_APPROACH_GS_KT is a false flag, not
    evidence. Unreported speed (NaN) stays acceptable."""
    gi = _one_field_gi()
    t0 = 20500 * 86400.0
    df = pd.DataFrame({
        "icao24": ["ae0001"] * 4,
        "ts_s": [t0 + 10, t0 + 60, t0 + 500, t0 + 550],
        "lat": 50.0, "lon": 8.0,
        "alt_ft": np.nan,
        # first pair: "on ground" at 455 kt (false flag burst) -> rejected;
        # second pair: on ground, speed unreported -> counts
        "gs_kt": [455.0, 450.0, np.nan, np.nan],
        "on_ground": True,
    })
    rows = _obs_rows(df, gi)
    assert [_key(r) for r in rows] == [("observed", "AAAA", "DE", "O")]
    assert (rows[0]["t_start"], rows[0]["t_end"]) == (t0 + 500, t0 + 550)


def test_second_pass_names_field_from_lowest_points():
    """WHERE is decided by the interval's lowest points: approach pings
    near field A followed by an even lower rollout inside field B's
    radius name B, not A."""
    from sklearn.neighbors import BallTree
    from backend.util.geo.raster import GeoRaster
    fields = [(50.0, 8.0, 3.0, "medium_airport", "AAAA"),
              (50.0, 8.3, 3.0, "medium_airport", "BBBB")]
    airports = [{"icao": ic, "ident": ic, "name": ic, "country": "DE",
                 "type": t, "lat": la, "lon": lo, "radius_km": r,
                 "elevation_ft": 0.0}
                for la, lo, r, t, ic in fields]
    ap = {"airports": airports,
          "tree": BallTree(np.radians([[f[0], f[1]] for f in fields]),
                           metric="haversine"),
          "radii_km": np.array([float(f[2]) for f in fields])}
    gi = GeoRaster(np.zeros((1, 1), dtype=np.uint16), {}, {}, ap)
    t0 = 20500 * 86400.0
    df = pd.DataFrame({
        "icao24": ["ae0001"] * 4,
        "ts_s": [t0, t0 + 30, t0 + 60, t0 + 90],
        "lat": 50.0,
        "lon": [8.0, 8.0, 8.3, 8.3],
        "alt_ft": [2000.0, 1500.0, 100.0, 50.0],
        "gs_kt": [150.0, 140.0, 60.0, 20.0],
        "on_ground": False,
    })
    rows = _obs_rows(df, gi)
    assert [_key(r) for r in rows] == [("observed", "BBBB", "DE", "L")]


def test_isolated_single_report_interval_is_dropped():
    """An interval resting solely on ONE report with no companion of the
    aircraft within BLACKOUT_MIN_S on either side is dropped; the same
    report with any nearby companion (even a non-indicating cruise point)
    stands."""
    gi = _one_field_gi()
    t0 = 20500 * 86400.0
    # ae0001: lone ground report, nothing within 15 min -> dropped
    # ae0002: same lone report but a cruise point 5 min later -> stands
    df = pd.DataFrame({
        "icao24": ["ae0001", "ae0002", "ae0002"],
        "ts_s": [t0 + 10, t0 + 40000, t0 + 40300],
        "lat": [50.0, 50.0, 51.0],
        "lon": [8.0, 8.0, 9.0],
        "alt_ft": [np.nan, np.nan, 30000.0],
        "gs_kt": [np.nan, np.nan, 400.0],
        "on_ground": [True, True, False],
    })
    rows = _obs_rows(df, gi)
    assert [(r["icao24"],) + _key(r) for r in rows] == [
        ("ae0002", "observed", "AAAA", "DE", "O")]


# --- flight origin/dest inheritance ----------------------------------------


def test_inherit_only_within_continuous_observation():
    """The project's premise: any blackout-length silence may conceal a
    flight, so a claim is NEVER carried across one. Within a continuously
    observed span, elapsed time is irrelevant (10 h of parked pings still
    licenses the link); one severing gap kills it at any distance."""
    from backend.pipeline.etl.landing.attribute import _inherit_flight_fields
    t = 20500 * 86400.0
    flights = pd.DataFrame({
        "icao24": ["ae0001", "ae0001", "ae0002"],
        "flight_id": [0, 1, 2],
        # flight 0: evidence 10 h earlier but SAME observed span ->
        #   inherits origin; ends at an attributed blackout edge -> dest
        # flight 1: evidence exists 30 min before, but a blackout severs
        #   the span between -> no origin despite tiny elapsed time
        "t_start": [t + 10 * 3600.0, t + 200000.0, t],
        "t_end": [t + 11 * 3600.0, t + 201000.0, t + 100.0],
    })
    ev_times = {"ae0001": [(t, "BASE", "DE"), (t + 198200.0, "BASE", "DE")]}
    edge_map = {("ae0001", "pre", t + 11 * 3600.0): ("FWD", "PL")}
    obs_runs = {"ae0001": [
        [t - 100.0, t + 12 * 3600.0],          # continuous span, 12 h
        [t + 198000.0, t + 198300.0],          # evidence's own short span
        [t + 199900.0, t + 202000.0],          # flight 1's span (severed
                                               # from the evidence span)
    ]}
    out = _inherit_flight_fields(flights, ev_times, edge_map, obs_runs)
    assert list(out.origin_icao) == ["BASE", "", ""]
    assert list(out.dest_icao) == ["FWD", "", ""]
    assert list(out.origin_grounded) == [True, False, False]
    assert list(out.dest_grounded) == [True, False, False]
