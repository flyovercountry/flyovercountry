"""etl verdicts: the type-blind four-gate split/join rule, table-driven."""
import numpy as np
import pandas as pd
import pytest

from backend.pipeline.etl.timeline.split import decide


def _blackout(**kw):
    """One in-scope blackout; defaults are a fast cruise-to-cruise JOIN
    (impl15 well above the SLOW_SPLIT cutoff, no landing signature)."""
    row = {
        "icao24": "ae0001", "interval_id": 0, "pre_ts": 1e9,
        "gap_s": 2 * 3600.0, "gc_km": 1600.0,
        "pre_lat": 45.0, "pre_lon": 10.0, "post_lat": 45.0, "post_lon": 20.0,
        "pre_alt_ft": 35000.0, "post_alt_ft": 35000.0,
        "pre_vert_rate_fpm": 0.0, "post_vert_rate_fpm": 0.0,
        "tc": "C17", "transatlantic_split": False,
    }
    row.update(kw)
    row["impl_kph"] = row["gc_km"] / (row["gap_s"] / 3600.0)
    return row


def _decide(*rows):
    return decide(pd.DataFrame(list(rows)))


# --- gate 1: long split --------------------------------------------------

def test_long_split_over_12h():
    v = _decide(_blackout(gap_s=13 * 3600.0, gc_km=9000.0))  # even fast
    assert v.verdict[0] == "SPLIT" and v.reason[0] == "LONG_SPLIT"


def test_short_gap_asserts():
    with pytest.raises(AssertionError):
        _decide(_blackout(gap_s=600.0))


# --- gate 2: transatlantic split -----------------------------------------

def test_transatlantic_split_flag_splits():
    v = _decide(_blackout(transatlantic_split=True))
    assert v.verdict[0] == "SPLIT" and v.reason[0] == "TRANSATLANTIC_SPLIT"


def test_transatlantic_beats_landing_and_slow():
    # the flag is set upstream (boundaries); it outranks landing and slow
    v = _decide(_blackout(transatlantic_split=True, gap_s=4 * 3600.0,
                          gc_km=300.0, pre_alt_ft=8000.0,
                          pre_vert_rate_fpm=-500.0))
    assert v.reason[0] == "TRANSATLANTIC_SPLIT"


# --- gate 3: landing signature (unsigned, either edge, no guard) ---------

def test_descent_in_alone_splits():
    v = _decide(_blackout(pre_alt_ft=8000.0, pre_vert_rate_fpm=-500.0))
    assert v.verdict[0] == "SPLIT" and v.reason[0] == "LANDING_SPLIT"


def test_climb_out_alone_splits():
    v = _decide(_blackout(post_alt_ft=5000.0, post_vert_rate_fpm=800.0))
    assert v.verdict[0] == "SPLIT" and v.reason[0] == "LANDING_SPLIT"


def test_climb_at_pre_edge_does_not_split():
    # a steep CLIMB at the pre-edge is an aircraft still climbing away as it
    # went dark, NOT an arrival — directional signature, so no landing split
    # (post-edge inert; the fast crossing JOINs)
    v = _decide(_blackout(pre_alt_ft=8000.0, pre_vert_rate_fpm=+500.0))
    assert v.reason[0] != "LANDING_SPLIT"
    assert v.verdict[0] == "JOIN"


def test_descent_at_post_edge_does_not_split():
    # a descent at the post-edge is descending toward a LATER landing (the
    # aircraft was flying during the gap), not a departure — no split
    v = _decide(_blackout(post_alt_ft=8000.0, post_vert_rate_fpm=-500.0))
    assert v.reason[0] != "LANDING_SPLIT"
    assert v.verdict[0] == "JOIN"


def test_both_low_descending_splits_via_arrival():
    # both ends <15k and descending: the pre-edge descent-in is an arrival, so
    # it splits (the same-direction guard is gone); the post descent is inert
    v = _decide(_blackout(pre_alt_ft=8000.0, pre_vert_rate_fpm=-500.0,
                          post_alt_ft=9000.0, post_vert_rate_fpm=-400.0))
    assert v.reason[0] == "LANDING_SPLIT"


def test_shallow_rate_is_no_signal():
    # |vr| = 100 <= 120 threshold -> not a signature
    v = _decide(_blackout(pre_alt_ft=8000.0, pre_vert_rate_fpm=-100.0))
    assert v.reason[0] != "LANDING_SPLIT"


def test_high_altitude_steep_descent_is_no_signal():
    v = _decide(_blackout(pre_alt_ft=25000.0, pre_vert_rate_fpm=-2000.0))
    assert v.verdict[0] == "JOIN"


def test_nan_boundary_is_no_signal():
    v = _decide(_blackout(pre_alt_ft=np.nan, pre_vert_rate_fpm=np.nan))
    assert v.verdict[0] == "JOIN"


def test_landing_beats_slow():
    # slow AND a landing signature: landing wins (ordered above slow)
    v = _decide(_blackout(gap_s=4 * 3600.0, gc_km=300.0,
                          pre_alt_ft=8000.0, pre_vert_rate_fpm=-500.0))
    assert v.reason[0] == "LANDING_SPLIT"


# --- gate 4: slow split (impl15 < 460), else join ------------------------

def test_slow_impl15_splits():
    # 300 km, 4 h gap -> impl15 = 300 / (4 - 0.25) = 80 kph < 460
    v = _decide(_blackout(gap_s=4 * 3600.0, gc_km=300.0))
    assert v.verdict[0] == "SPLIT" and v.reason[0] == "SLOW_SPLIT"


def test_fast_crossing_joins():
    # 1600 km, 2 h -> impl15 = 1600 / 1.75 = 914 kph >= 460 -> JOIN
    v = _decide(_blackout())
    assert v.verdict[0] == "JOIN" and v.reason[0] == "JOIN"


def test_impl15_uses_15min_ground_allowance():
    # 500 km in 1.25 h: raw impl = 400 (< 460) but impl15 = 500/(1.25-0.25)
    # = 500 (>= 460) -> JOIN. The 15-min allowance is what decides it.
    v = _decide(_blackout(gap_s=1.25 * 3600.0, gc_km=500.0))
    assert np.isclose(v.impl15[0], 500.0)
    assert v.verdict[0] == "JOIN"


def test_just_below_cutoff_splits():
    # impl15 = 450 < 460 -> SPLIT
    v = _decide(_blackout(gap_s=(1 + 0.25) * 3600.0, gc_km=450.0))
    assert np.isclose(v.impl15[0], 450.0)
    assert v.verdict[0] == "SPLIT" and v.reason[0] == "SLOW_SPLIT"
