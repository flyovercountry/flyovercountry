"""Tests for backend.pipeline.etl.publish.export pure helpers: the frontend-contract
building blocks (banding, date/epoch conversion, role-list splitting,
country normalization, outlier flagging) used by the etl publish stage.
"""
import math

import numpy as np
import pandas as pd

from backend.pipeline.etl.publish.export import (
    _alt_band,
    _date_to_epoch,
    _day_offsets,
    _epoch_to_date,
    _flight_role_lists,
    _is_outlier,
    _norm_country,
    _norm_country_csv,
    _safe_int,
    _type_enum,
)


def test_alt_band_clamps_and_handles_nan():
    # NaN is UNKNOWN (-1), not ground level — callers fill it from
    # neighbouring known bands so cruise routes with missing altitude
    # stop rendering false ground-level segments
    assert _alt_band(float("nan")) == -1
    assert _alt_band(-2.0) == 0
    assert _alt_band(0.0) == 0
    assert _alt_band(2.4) == 0
    assert _alt_band(2.5) == 1
    assert _alt_band(37.4) == 14
    assert _alt_band(100.0) == 15  # clamped to _N_ALT_BANDS - 1


def test_is_outlier_spike_detection():
    # point 5.1 deg from both close neighbors -> spike
    assert _is_outlier(55.1, 10.0, 50.0, 10.0, 50.0, 10.1)
    # point on the line between neighbors -> not a spike
    assert not _is_outlier(50.0, 10.05, 50.0, 10.0, 50.0, 10.1)
    # neighbors far apart (real movement) -> not a spike
    assert not _is_outlier(55.1, 10.0, 50.0, 10.0, 60.0, 10.0)


def test_norm_country_uk_to_gb():
    assert _norm_country("UK") == "GB"
    assert _norm_country("DE") == "DE"
    assert _norm_country("") == ""


def test_norm_country_csv_sorts_dedups_normalizes():
    assert _norm_country_csv("UK,DE, DE ,FR") == "DE,FR,GB"
    assert _norm_country_csv("") == ""
    assert _norm_country_csv(None) == ""
    assert _norm_country_csv(float("nan")) == ""


def test_safe_int_defaults_and_scaling():
    assert _safe_int(None) == 0
    assert _safe_int(float("nan"), default=7) == 7
    assert _safe_int(3.6) == 4
    assert _safe_int(1.234, scale=100) == 123


def test_date_epoch_roundtrip_utc():
    ts = _date_to_epoch("2026-03-01")
    assert _epoch_to_date(ts) == "2026-03-01"
    assert math.isclose(ts % 86400, 0.0)


def test_type_enum():
    legs = pd.DataFrame({
        "type_code": ["K35R", "C17", "K35R", None],
    })
    types, ti = _type_enum(legs)
    assert types == ["C17", "K35R"]
    assert ti.tolist() == [1, 0, 1, -1]  # None -> -1 sentinel


def test_flight_role_lists_splits_comma_joined_column():
    legs = pd.DataFrame({
        "roles": ["air_refuelling,tactical_airlift", "csar", "", None],
    })
    assert _flight_role_lists(legs) == [
        ["air_refuelling", "tactical_airlift"], ["csar"], [], [],
    ]


def test_day_offsets_floor_from_start():
    start = _date_to_epoch("2026-03-01")
    legs = pd.DataFrame({"t_start": np.array(
        [start, start + 86399, start + 86400, start + 3 * 86400 + 1])})
    assert _day_offsets(legs, start).tolist() == [0, 0, 1, 3]
