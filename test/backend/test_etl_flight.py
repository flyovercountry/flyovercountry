"""etl flights: window cutting, assembly, crop gate, airport snap, roles."""
import numpy as np
import pandas as pd

from backend.pipeline.etl.timeline.flight import (
    assemble_aircraft, split_windows,
)
from backend.util.roles import roles_string
from backend.util.geo.raster import GeoRaster

CROP = {"lat_south": 20.0, "lat_north": 70.0,
        "lon_west": -100.0, "lon_east": 50.0}


def _pts(ts, lat=50.0, lon=8.0, alt=30000.0, og=False, vr=0.0):
    n = len(ts)
    return pd.DataFrame({
        "icao24": ["ae0001"] * n, "ts_s": np.asarray(ts, dtype=float),
        "lat": np.full(n, lat), "lon": np.full(n, lon),
        "alt_ft": np.full(n, alt), "on_ground": [og] * n,
        "gs_kt": np.full(n, 400.0),
        "vert_rate_fpm": np.full(n, float(vr)),
    })


def _air_iv(start, end, interval_id=0):
    return pd.DataFrame({"interval_id": [interval_id],
                         "start_ts": [float(start)], "end_ts": [float(end)]})


def _cuts(*rows):
    cols = ["icao24", "interval_id", "pre_ts", "post_ts", "verdict"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(list(rows), columns=cols)


def _assemble(air, cuts, pts, tc="C17", roles="strategic_airlift"):
    return assemble_aircraft("ae0001", air, cuts, pts, CROP, tc, roles)


# --- split_windows -------------------------------------------------------

def test_split_windows_no_splits():
    assert split_windows(0.0, 100.0, []) == [(0.0, 100.0)]


def test_split_windows_two_splits():
    assert split_windows(0.0, 100.0, [(20.0, 30.0), (60.0, 70.0)]) == [
        (0.0, 20.0), (30.0, 60.0), (70.0, 100.0)]


# --- assembly ------------------------------------------------------------

def test_join_blackout_spans_one_flight():
    # points before and after a JOIN blackout -> one flight
    pts = _pts([100.0, 1100.0, 3000.0, 3100.0])   # blackout 1100..3000
    cuts = _cuts(("ae0001", 0, 1100.0, 3000.0, "JOIN"))
    rows = _assemble(_air_iv(100.0, 3110.0), cuts, pts)
    assert len(rows) == 1
    r = rows[0]
    assert (r["t_start"], r["t_end"]) == (100.0, 3100.0)


def test_split_blackout_cuts_two_flights():
    pts = _pts([100.0, 1100.0, 3000.0, 3100.0])
    cuts = _cuts(("ae0001", 0, 1100.0, 3000.0, "SPLIT"))
    rows = _assemble(_air_iv(100.0, 3110.0), cuts, pts)
    assert len(rows) == 2
    assert (rows[0]["t_start"], rows[0]["t_end"]) == (100.0, 1100.0)
    assert (rows[1]["t_start"], rows[1]["t_end"]) == (3000.0, 3100.0)


def test_two_airborne_intervals_are_separate_flights():
    pts = _pts([100.0, 200.0, 5000.0, 5100.0])
    air = pd.concat([_air_iv(100.0, 210.0, 0), _air_iv(5000.0, 5110.0, 1)],
                    ignore_index=True)
    rows = _assemble(air, _cuts(), pts)
    assert len(rows) == 2


def test_crop_gate_drops_out_of_box_flight():
    pts = _pts([100.0, 200.0], lat=-10.0)      # south of the crop box
    rows = _assemble(_air_iv(100.0, 210.0), _cuts(), pts)
    assert rows == []


def test_origin_dest_written_empty_here():
    """The flight stage computes no grounding: origin/dest fields are
    empty placeholders, back-filled later by the attribute stage from
    the landing evidence table (single source of truth)."""
    pts = _pts([100.0, 210.0], alt=200.0, og=True)
    r = _assemble(_air_iv(100.0, 210.0), _cuts(), pts)[0]
    assert r["origin_icao"] == "" and r["dest_icao"] == ""
    assert not r["origin_grounded"] and not r["dest_grounded"]


def test_metadata_aggregation():
    pts = _pts([100.0, 200.0, 300.0])
    pts.loc[1, "alt_ft"] = 35000.0
    pts.loc[1, "lat"] = 51.0
    r = _assemble(_air_iv(100.0, 310.0), _cuts(), pts)[0]
    assert r["max_alt_ft"] == 35000.0
    assert r["total_path_km"] > 200.0    # 50->51->50 lat = ~222 km
    assert r["type_code"] == "C17"
    assert r["roles"] == "strategic_airlift"


# --- descent/climb signal gate (R2) ---------------------------------------

def test_signal_needs_low_altitude():
    # descending hard but at 20,000 ft: above the 15,000 ft signal floor
    pts = _pts([100.0, 200.0, 300.0, 400.0, 500.0], alt=20000.0, vr=-600.0)
    r = _assemble(_air_iv(100.0, 510.0), _cuts(), pts)[0]
    assert not r["origin_grounded"] and not r["dest_grounded"]


def test_signal_needs_min_rate():
    # drifting at -50 fpm: below the 100 fpm signal threshold
    pts = _pts([100.0, 200.0, 300.0, 400.0, 500.0], alt=10000.0, vr=-50.0)
    r = _assemble(_air_iv(100.0, 510.0), _cuts(), pts)[0]
    assert not r["origin_grounded"] and not r["dest_grounded"]


# --- snap-confidence columns ----------------------------------------------


# --- shared military-weighted snap (backend/util/geo/airport_snap) ---------

def _snap_index(fields):
    """SnapIndex over synthetic airfields: (lat, lon, radius_km, type, id[, elev])."""
    from sklearn.neighbors import BallTree
    from backend.util.geo.airport_snap import SnapIndex
    airports = [{"icao": f[4], "ident": f[4], "name": f[4], "country": "DE",
                 "type": f[3], "lat": f[0], "lon": f[1], "radius_km": f[2],
                 "elevation_ft": (f[5] if len(f) > 5 else 0.0)}
                for f in fields]
    ap = {"airports": airports,
          "tree": BallTree(np.radians([[f[0], f[1]] for f in fields]),
                           metric="haversine"),
          "radii_km": np.array([float(f[2]) for f in fields])}
    return SnapIndex(GeoRaster(np.zeros((1, 1), dtype=np.uint16), {}, {}, ap))


def test_snap_shadow_proof_and_minor_fields_ineligible():
    # nearest field is a heliport: under the shared rule it can never win
    # (not in AIRPORT_SNAP_RADIUS_KM); the base 5 km out contains the
    # point and takes it. A k=1 nearest-neighbour lookup would have found
    # the heliport and refused — the shadowing failure the radius query
    # exists to beat.
    from backend.util.geo.airport_snap import snap_point
    idx = _snap_index([(50.018, 8.0, 1.5, "heliport", "HELI"),      # ~2.0 km
                       (50.045, 8.0, 8.0, "military_base", "BASE")])  # ~5.0 km
    ap = snap_point(idx, 50.0, 8.0, 0.0)
    assert ap is not None and ap["icao"] == "BASE"


def test_snap_ground_point_is_pure_distance():
    # at ground level the type weight is fully blended out: the nearer
    # containing medium airport beats the farther military base despite
    # the base's 10x weight
    from backend.util.geo.airport_snap import snap_point
    # MED ~1.0 km / radius 3 (score 0.33) vs BASE ~3.3 km / radius 4
    # (score 0.83): at ground level weights blend out and MED wins
    idx = _snap_index([(50.009, 8.0, 3.0, "medium_airport", "MED"),
                       (50.030, 8.0, 4.0, "military_base", "BASE")])
    ap = snap_point(idx, 50.0, 8.0, 0.0)
    assert ap["icao"] == "MED"


def test_snap_altitude_gives_military_priority():
    # same geometry at 2,500 ft AGL: full type weight, BASE scores
    # 3.3/(4*10)=0.08 vs MED 1.0/(3*1)=0.33 — the base wins
    from backend.util.geo.airport_snap import snap_point
    idx = _snap_index([(50.009, 8.0, 3.0, "medium_airport", "MED"),
                       (50.030, 8.0, 4.0, "military_base", "BASE")])
    ap = snap_point(idx, 50.0, 8.0, 2500.0)
    assert ap["icao"] == "BASE"


def test_snap_agl_gate_uses_field_elevation():
    # point at 3,000 ft MSL: rejected next to a sea-level field
    # (3,000 ft AGL > 2,500) but accepted at a 1,000 ft-elevation field
    # (2,000 ft AGL); endpoints (gate off) accept both
    from backend.util.geo.airport_snap import snap_point
    low = _snap_index([(50.009, 8.0, 3.0, "medium_airport", "SEA", 0.0)])
    high = _snap_index([(50.009, 8.0, 3.0, "medium_airport", "HIGH", 1000.0)])
    assert snap_point(low, 50.0, 8.0, 3000.0) is None
    assert snap_point(high, 50.0, 8.0, 3000.0)["icao"] == "HIGH"
    assert snap_point(low, 50.0, 8.0, 3000.0,
                      enforce_agl_gate=False)["icao"] == "SEA"

# --- roles column (see the roles taxonomy in data/in/config/roles.json) --------------------

def test_roles_string_resolved_single_role():
    assert roles_string("C17", "resolved") == "strategic_airlift"


def test_roles_string_resolved_multi_role_sorted_comma_joined():
    # C130's roleset has no precedence: every role is present, sorted.
    from backend.util.roles import roles_of
    expected = ",".join(sorted(roles_of("C130")))
    assert roles_string("C130", "resolved") == expected
    assert len(roles_of("C130")) > 1   # sanity: this is genuinely multi-role


def test_roles_string_resolved_empty_roleset_is_role_unknown():
    # A resolved type_code that maps to no role (unmapped designator).
    assert roles_string("ZZZZ", "resolved") == "role_unknown"


def test_roles_string_type_unknown_bucket():
    assert roles_string(None, "type_unknown") == "type_unknown"


def test_roles_string_typecode_unknown_bucket():
    assert roles_string(None, "typecode_unknown") == "typecode_unknown"


def test_roles_string_rejects_unrecognized_status():
    import pytest
    with pytest.raises(ValueError):
        roles_string("C17", "bogus")


def test_assemble_aircraft_passes_precomputed_roles():
    pts = _pts([100.0, 200.0])
    expected = roles_string("C130", "resolved")
    r = _assemble(_air_iv(100.0, 210.0), _cuts(), pts, tc="C130",
                 roles=expected)[0]
    assert r["roles"] == expected


def test_assemble_aircraft_type_unknown_roles():
    pts = _pts([100.0, 200.0])
    r = _assemble(_air_iv(100.0, 210.0), _cuts(), pts, tc="",
                 roles="type_unknown")[0]
    assert r["roles"] == "type_unknown"


def test_assemble_aircraft_typecode_unknown_roles():
    pts = _pts([100.0, 200.0])
    r = _assemble(_air_iv(100.0, 210.0), _cuts(), pts, tc="",
                 roles="typecode_unknown")[0]
    assert r["roles"] == "typecode_unknown"


# --- desc-based role narrowing -------------------------------------------

def test_roles_string_desc_narrows_c130j():
    assert roles_string("C30J", "resolved", "Lockheed Martin KC-130J Hercules") \
        == "air_refuelling"
    assert roles_string("C30J", "resolved", "Lockheed Martin MC-130J Commando II") \
        == "special_ops_lift"
    assert roles_string("C30J", "resolved", "Lockheed Martin HC-130J Combat King II") \
        == "csar"
    assert roles_string("C30J", "resolved", "Lockheed Martin AC-130J Ghostrider") \
        == "gunship"
    assert roles_string("C30J", "resolved", "Lockheed Martin EC-130J Commando Solo") \
        == "sigint_elint"
    assert roles_string("C30J", "resolved", "Lockheed Martin C-130J-30 Hercules") \
        == "tactical_airlift"


def test_roles_string_desc_narrows_h60():
    assert roles_string("H60", "resolved", "Sikorsky HH-60M Black Hawk") == "csar"
    assert roles_string("H60", "resolved", "Sikorsky MH-60R Seahawk") \
        == "special_ops_lift"
    assert roles_string("H60", "resolved", "SIKORSKY UH-60 Black Hawk") \
        == "rotary_lift"


def test_roles_string_desc_narrows_b762():
    assert roles_string("B762", "resolved", "Boeing KC-46A Pegasus") \
        == "air_refuelling"
    assert roles_string("B762", "resolved", "BOEING 767-200") == "civil_cargo"


def test_roles_string_no_desc_returns_all_roles():
    from backend.util.roles import roles_of
    expected = ",".join(sorted(roles_of("C30J")))
    assert roles_string("C30J", "resolved") == expected
    assert roles_string("C30J", "resolved", None) == expected
    assert roles_string("C30J", "resolved", "") == expected


def test_roles_string_desc_no_match_returns_all_roles():
    from backend.util.roles import roles_of
    expected = ",".join(sorted(roles_of("H60")))
    assert roles_string("H60", "resolved", "Sikorsky EH-60A Black Hawk") == expected


def test_landing_attribution_edges_shared_verbatim():
    """The split/landings consistency guarantee IS this identity: the verdict
    rule's LANDING_SPLIT gate and the landing engine's edge typing must call
    the same landing_edges_directed function object. The verdict ORs its two
    edges (arrival | departure); the engine emits landing events only on those
    same edges — so an attributed landing inside a JOINed gap is impossible by
    construction. A fork of the logic would silently break the theorem (as an
    unsigned verdict gate briefly did)."""
    from backend.pipeline.etl.grounding import landing_edges_directed
    import backend.pipeline.etl.timeline.split as verdicts_mod
    import backend.pipeline.etl.landing.engine as engine_mod
    assert verdicts_mod.landing_edges_directed is landing_edges_directed
    assert engine_mod.landing_edges_directed is landing_edges_directed


def test_landing_edges_directed_rejects_backwards_edges():
    """Direction matters: a pre-edge CLIMBING is not an arrival, a post-edge
    DESCENDING is not a departure (the mislabels the unsigned signature made)."""
    import numpy as np
    from backend.pipeline.etl.grounding import landing_edges_directed
    lo = 8000.0
    # correct-direction: descend in / climb out -> admitted
    arr, dep = landing_edges_directed(
        np.array([lo]), np.array([-500.0]),
        np.array([lo]), np.array([500.0]))
    assert bool(arr[0]) and bool(dep[0])
    # backwards: climb at pre-edge, descend at post-edge -> rejected
    arr, dep = landing_edges_directed(
        np.array([lo]), np.array([500.0]),
        np.array([lo]), np.array([-500.0]))
    assert not bool(arr[0]) and not bool(dep[0])
    # high altitude -> rejected regardless of rate
    arr, _ = landing_edges_directed(
        np.array([25000.0]), np.array([-2000.0]),
        np.array([25000.0]), np.array([0.0]))
    assert not bool(arr[0])



# --- minimum-usable-points drop (merged from the old prune stage) ---------

def test_apply_min_points_drops_underobserved_flight():
    """A flight with a single usable trace point is dropped; one with
    enough points is kept; dropped ids are returned sorted."""
    from backend.pipeline.etl.timeline.flight import apply_min_points
    published = pd.DataFrame({
        "icao24": ["ae0001", "ae0001"],
        "t_start": [100.0, 5000.0],
        "t_end": [200.0, 6000.0],
        "flight_id": [0, 1],
    })
    T = pd.DataFrame({
        "icao24": ["ae0001"] * 4,
        "ts_s": [100.0, 150.0, 200.0, 5500.0],   # 3 pts in f0, 1 pt in f1
    })
    kept, dropped = apply_min_points(published, T)
    assert dropped == [1]
    assert kept.flight_id.tolist() == [0]


def test_apply_min_points_keeps_all_when_sufficient():
    from backend.pipeline.etl.timeline.flight import apply_min_points
    published = pd.DataFrame({
        "icao24": ["ae0001"], "t_start": [0.0], "t_end": [100.0],
        "flight_id": [7]})
    T = pd.DataFrame({"icao24": ["ae0001"] * 2, "ts_s": [10.0, 20.0]})
    kept, dropped = apply_min_points(published, T)
    assert dropped == [] and kept.flight_id.tolist() == [7]
