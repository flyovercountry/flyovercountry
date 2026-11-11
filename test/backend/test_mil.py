"""Behavioral tests for backend.util.mil.is_relevant — previously covered
only by architectural import-shape tests, none of which exercised the
range/singles/case logic itself."""
from __future__ import annotations

import backend.util.mil as mil


def _with_list(tmp_data_dir, lines):
    lookup = tmp_data_dir / "build" / "lookup"
    lookup.mkdir(parents=True, exist_ok=True)
    (lookup / "mil_icao24.txt").write_text("\n".join(lines) + "\n")
    mil.reset_mil_cache()


def test_range_and_singles_union(tmp_data_dir):
    _with_list(tmp_data_dir, [
        "adf7c8-afffff, US hex range outside the civil N-number series",
        "a11cf8, mictronics military flag",
    ])
    try:
        assert mil.is_relevant("adf7c8")      # range lower bound, inclusive
        assert mil.is_relevant("afffff")      # range upper bound, inclusive
        assert not mil.is_relevant("adf7c7")  # N99999, last civil N-number address
        assert mil.is_relevant("a11cf8")      # DB-evidenced single
        assert not mil.is_relevant("a11cf9")  # unlisted civil-block hex
    finally:
        mil.reset_mil_cache()


def test_case_and_garbage_handling(tmp_data_dir):
    # the singles set is lowercase; is_relevant must normalize its input
    # (raw payload hex casing is an external contract we don't control)
    _with_list(tmp_data_dir, [
        "adf7c8-afffff, range",
        "a11cf8, single",
    ])
    try:
        assert mil.is_relevant("A11CF8")
        assert mil.is_relevant(" a11cf8 ")
        assert mil.is_relevant("ADF7C9")
        assert not mil.is_relevant("not-hex")
        assert not mil.is_relevant("")
        assert not mil.is_relevant(None)
    finally:
        mil.reset_mil_cache()
