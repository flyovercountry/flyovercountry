"""etl export: publish helpers that shape the frontend contract.

Pure-helper behavior is covered by test_etl_export_helpers.py; this file
covers the flight_id-input / data/build/etl/-read plumbing plus the
table/index/route builders against the frozen JSON contract.
"""
import json
from datetime import datetime, timezone

import pandas as pd

from backend.pipeline.etl.publish.export import (
    _build_route_features,
    _clip_flat_to_crop,
    _coarse_to_route_segments,
    _copy_roles_reference,
    _date_to_epoch,
    _day_offsets,
    _flight_role_lists,
    _focus_country_list,
    _type_enum,
    _write_flights_table,
    _write_hex_roles,
    _write_manifest,
    _write_sighting_index,
)

START = "2024-03-01"
START_EPOCH = _date_to_epoch(START)

FLIGHTS_JSON_KEYS = {
    "n", "id", "hex", "ti", "day", "eday", "etod", "cc",
    "kft", "path", "thc", "the", "oa", "da", "occ", "dcc",
}


def _role_ids(flights):
    """Test-local stand-in for the exported catalog's role-id assignment:
    sorted-populated-roles -> 0-based id, same rule as _build_role_catalog."""
    lists = _flight_role_lists(flights)
    populated = sorted({r for roles in lists for r in roles})
    role_id = {slug: i for i, slug in enumerate(populated)}
    return [sorted(role_id[r] for r in roles) for roles in lists]


def _ts(day, hour, minute=0, second=0):
    return datetime(2024, 3, day, hour, minute, second,
                    tzinfo=timezone.utc).timestamp()


def _flights_frame():
    return pd.DataFrame({
        "flight_id": [10, 11],
        "icao24": ["ae0001", "ae0002"],
        "type_code": ["C17", "K35R"],
        "roles": ["tactical_airlift", "air_refuelling"],
        "t_start": [_ts(1, 10), _ts(3, 23)],
        "t_end": [_ts(1, 12, 30, 5), _ts(4, 1, 0, 30)],
        "max_alt_ft": [31000.0, float("nan")],
        "total_path_km": [1234.4, 10.6],
        "th_chain": [True, False],
        "th_ever": [True, True],
        "traversed_countries": ["UK,DE", ""],
        "origin_icao": ["EDDF", None],
        "dest_icao": ["LICZ", "EGUN"],
        "origin_country": ["DE", ""],
        "dest_country": ["IT", "UK"],
    })


# -- flights.json --------------------------------------------------------------

def test_flights_table_contract(tmp_path):
    flights = _flights_frame()
    type_names, type_idx = _type_enum(flights)
    day_off = _day_offsets(flights, START_EPOCH)

    _write_flights_table(flights, type_idx, day_off,
                         START_EPOCH, tmp_path)
    table = json.loads((tmp_path / "flights.json").read_text())

    assert set(table) == FLIGHTS_JSON_KEYS
    n = table["n"]
    assert n == 2
    for key in FLIGHTS_JSON_KEYS - {"n"}:
        assert len(table[key]) == n, key

    assert table["id"] == [10, 11]        # flight_id feeds the 'id' key
    assert table["hex"] == ["ae0001", "ae0002"]
    assert table["ti"] == [0, 1]

    # day/eday floor from start epoch; etod = t_end % 86400
    assert table["day"] == [0, 2]
    assert table["eday"] == [0, 3]        # flight 1 ends past midnight
    assert table["etod"] == [12 * 3600 + 30 * 60 + 5, 3600 + 30]

    # UK -> GB normalization, sorted csv, NaN/None fallbacks
    assert table["cc"] == ["DE,GB", ""]
    assert table["kft"] == [31, 0]        # scale 0.001, NaN -> 0
    assert table["path"] == [1234, 11]
    assert table["thc"] == [1, 0]
    assert table["the"] == [1, 1]
    assert table["oa"] == ["EDDF", ""]
    assert table["occ"] == ["DE", ""] and table["dcc"] == ["IT", "GB"]


# -- hex_roles.json -------------------------------------------------------------

def test_hex_roles_filters_and_maps(tmp_path):
    aircraft = pd.DataFrame({
        "icao24": ["ae0001", "ae0002", "ae0003", "ae0004"],
        "excluded": [False, False, True, False],
        "in_bbox": [True, True, True, False],
        "roles": ["strategic_airlift", "air_refuelling,isr", "strategic_airlift", "isr"],
    })
    role_id_map = {"strategic_airlift": 0, "air_refuelling": 1, "isr": 2}
    _write_hex_roles(aircraft, role_id_map, tmp_path)
    hr = json.loads((tmp_path / "hex_roles.json").read_text())
    assert hr == {"ae0001": [0], "ae0002": [1, 2]}


def test_hex_roles_skips_unknown_role_slugs(tmp_path):
    aircraft = pd.DataFrame({
        "icao24": ["ae0001"],
        "excluded": [False],
        "in_bbox": [True],
        "roles": ["strategic_airlift,nonexistent"],
    })
    role_id_map = {"strategic_airlift": 0}
    _write_hex_roles(aircraft, role_id_map, tmp_path)
    hr = json.loads((tmp_path / "hex_roles.json").read_text())
    assert hr == {"ae0001": [0]}


def test_hex_roles_empty_roles_omitted(tmp_path):
    aircraft = pd.DataFrame({
        "icao24": ["ae0001"],
        "excluded": [False],
        "in_bbox": [True],
        "roles": [""],
    })
    _write_hex_roles(aircraft, {}, tmp_path)
    hr = json.loads((tmp_path / "hex_roles.json").read_text())
    assert hr == {}


# -- sightings/<cc>.json --------------------------------------------------------

def test_focus_country_list_is_gated_on_the_stats_flag():
    """_focus_country_list backs the sighting index (What-If's
    actual-sighting lookup) and the per-country stats table — both keyed off
    the `stats` flag now (was `analyze !== false`). A country with stats
    false is excluded regardless of its other two flags."""
    cfg = {"countries": {
        "GB": {"permissiveness": 3, "stats": True, "whatif": True, "info": True},
        "SI": {"permissiveness": 2, "stats": True, "whatif": False, "info": True},
        "CH": {"permissiveness": 0, "stats": False, "whatif": False, "info": True},
        "UA": {"permissiveness": 0, "stats": False, "whatif": False, "info": False},
    }}
    assert sorted(_focus_country_list(cfg)) == ["GB", "SI"]


# -- manifest ---------------------------------------------------------------

def test_manifest_dates_share_day_offset_origin(tmp_path, monkeypatch):
    """manifest.json's days/dates must share one origin (config start, via
    dimensions) with _day_offsets' per-flight day offset — every flight's
    'day' offset must index into `dates`.

    Regression: n_days used to come from the flights table's own t_start
    min/max range while dates/day-offsets came from the config start — two
    different origins that only agreed by coincidence. A flight starting
    partway into the config range (this test) used to produce a `dates`
    list shorter than the true config range and misaligned with day
    offsets computed against config start.
    """
    import backend.util.dimensions as dimensions
    monkeypatch.setattr(dimensions, "start", lambda: "2024-03-01")
    monkeypatch.setattr(dimensions, "end", lambda: "2024-03-05")
    start_epoch = _date_to_epoch("2024-03-01")

    # Only flight starts on day offset 2 (03-03); under the old logic
    # n_days would derive from this single flight's t_start range (== 1
    # day), far short of the real 5-day config range.
    flights = pd.DataFrame({"t_start": [_ts(3, 10)]})
    day_off = _day_offsets(flights, start_epoch)

    _write_manifest([], "2024-03-01", "2024-03-05", "2024-03-01", tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())

    assert manifest["days"] == len(manifest["dates"]) == 5
    assert manifest["dates"] == [
        "2024-03-01", "2024-03-02", "2024-03-03", "2024-03-04", "2024-03-05",
    ]
    for off in day_off:
        assert 0 <= off < len(manifest["dates"]), (
            "flight day offset must index into manifest dates")


def test_sighting_index(tmp_data_dir, tmp_path):
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir()
    start_day = int(START_EPOCH // 86400)
    pd.DataFrame({
        "icao24": ["ae0001", "ae0001", "ae0002", "ae0003", "ae0001"],
        "day":    [start_day, start_day + 2, start_day,
                   start_day + 1, start_day - 1],
        "cc":     ["UK", "UK", "PL", "DE", "UK"],
        "first_ts": [START_EPOCH + 3600.9, START_EPOCH + 2 * 86400 + 50,
                     START_EPOCH + 7200, START_EPOCH + 86400 + 10,
                     START_EPOCH - 86400 + 5],
        "last_ts": [0.0] * 5,
    }).to_parquet(etl / "sighting_times.parquet", index=False)

    _write_sighting_index(START_EPOCH, ["GB", "PL"], tmp_path)

    sightings = tmp_path / "sightings"
    # DE is not focus; UK rows publish under GB; pre-start day dropped
    assert sorted(p.name for p in sightings.iterdir()) == ["GB.json", "PL.json"]
    gb = json.loads((sightings / "GB.json").read_text())
    assert gb == {"ae0001": {"0": 3600, "2": 50}}
    # ae0001 was sighted in GB on two different days (rows in the source
    # parquet), but the output has exactly ONE top-level key for it — dict
    # keys can't duplicate. This is the structural guarantee the stats table's
    # `planes` numerator relies on: a distinct-aircraft count built from
    # this file's keys (e.g. Object.keys(idx).length, or a Set of hexes)
    # cannot double-count one aircraft no matter how many flights/days
    # contributed to it.
    assert len(gb) == 1
    pl = json.loads((sightings / "PL.json").read_text())
    assert pl == {"ae0002": {"0": 7200}}


def test_sighting_index_missing_parquet_is_noop(tmp_data_dir, tmp_path):
    (tmp_data_dir / "build" / "etl").mkdir()
    _write_sighting_index(START_EPOCH, ["GB"], tmp_path)
    assert not (tmp_path / "sightings").exists()


def test_copy_roles_reference_copies_both_files(tmp_data_dir, tmp_path):
    """export's roles.html/roles.reference.json copy step: files land in
    lookup_dir() (via `./run gen roles`) and this moves them into the site
    root (data/out/) untouched, byte for byte."""
    lookup = tmp_data_dir / "build" / "lookup"
    lookup.mkdir(parents=True)
    (lookup / "roles.html").write_text("<html>roles page</html>", encoding="utf-8")
    (lookup / "roles.reference.json").write_text('{"groups": []}', encoding="utf-8")

    site_root = tmp_path / "site_root"
    site_root.mkdir()
    _copy_roles_reference(site_root)

    assert (site_root / "roles.html").read_text(encoding="utf-8") \
        == "<html>roles page</html>"
    assert (site_root / "roles.reference.json").read_text(encoding="utf-8") \
        == '{"groups": []}'
    # no leftover temp files from the atomic copy-then-replace
    assert sorted(p.name for p in site_root.iterdir()) == \
        ["roles.html", "roles.reference.json"]


def test_copy_roles_reference_missing_files_warns_not_raises(
    tmp_data_dir, tmp_path, capsys,
):
    """A failed `gen roles` must not pass through silently — mirrors the
    boundaries.geojson missing-input handling in run_export()."""
    (tmp_data_dir / "build" / "lookup").mkdir(parents=True)
    site_root = tmp_path / "site_root"
    site_root.mkdir()

    _copy_roles_reference(site_root)

    assert list(site_root.iterdir()) == []
    err = capsys.readouterr().err
    assert "roles.html" in err and "roles.reference.json" in err
    assert "./run gen roles" in err


def test_sighting_index_warns_on_stale_leftover_not_deleted(
    tmp_data_dir, tmp_path, capsys,
):
    """build_dir is overwritten in place, never wiped first (see
    paths.build_dir()'s docstring) — a country dropped from the focus
    config leaves its old sightings/<cc>.json behind. That file must not
    be silently deleted (out of scope without asking), but its presence
    must be logged so it isn't shipped unnoticed."""
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir()
    start_day = int(START_EPOCH // 86400)
    pd.DataFrame({
        "icao24": ["ae0001"], "day": [start_day], "cc": ["GB"],
        "first_ts": [START_EPOCH + 100], "last_ts": [START_EPOCH + 100],
    }).to_parquet(etl / "sighting_times.parquet", index=False)

    sightings = tmp_path / "sightings"
    sightings.mkdir(parents=True)
    (sightings / "XX.json").write_text("{}")   # XX is not in the focus list

    _write_sighting_index(START_EPOCH, ["GB"], tmp_path)

    assert (sightings / "XX.json").exists(), "stale leftover must not be deleted"
    assert (sightings / "GB.json").exists()
    err = capsys.readouterr().err
    assert "XX" in err and "stale" in err.lower()


def test_sighting_index_focus_country_with_zero_sightings_is_omitted(tmp_data_dir, tmp_path):
    """A focus country nobody was ever sighted in — the natural "zero
    flights" case for the stats table's `planes` column — must not crash and
    must not produce a spurious file. The frontend's loadSightingIndex()
    treats a missing/non-OK fetch as `{}` (data.js `.then(r => r.ok
    ? r.json() : {})`), so "no file" and "an empty file" are equivalent
    inputs to the planes numerator/denominator; the ETL takes the cheaper
    "no file" path via groupby simply never yielding that country."""
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir()
    start_day = int(START_EPOCH // 86400)
    pd.DataFrame({
        "icao24": ["ae0001"], "day": [start_day], "cc": ["GB"],
        "first_ts": [START_EPOCH + 100], "last_ts": [START_EPOCH + 100],
    }).to_parquet(etl / "sighting_times.parquet", index=False)

    _write_sighting_index(START_EPOCH, ["GB", "FR"], tmp_path)

    sightings = tmp_path / "sightings"
    assert sorted(p.name for p in sightings.iterdir()) == ["GB.json"]
    assert not (sightings / "FR.json").exists()


def test_sighting_index_publishes_overflight(tmp_data_dir, tmp_path):
    """An aircraft that overflew a country (recorded via its trace, in
    sighting_times.parquet -> sightings/<cc>.json) must appear in the
    published sighting index."""
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir()
    start_day = int(START_EPOCH // 86400)

    pd.DataFrame({
        "icao24": ["ae0009"], "day": [start_day], "cc": ["GB"],
        "first_ts": [START_EPOCH + 3600], "last_ts": [START_EPOCH + 3700],
    }).to_parquet(etl / "sighting_times.parquet", index=False)
    _write_sighting_index(START_EPOCH, ["GB"], tmp_path)

    gb = json.loads((tmp_path / "sightings" / "GB.json").read_text())
    assert "ae0009" in gb


# -- route geometry -------------------------------------------------------------

def test_route_segments_rdp_and_bands():
    # collinear 3-point track: RDP drops the middle at tight epsilon
    arr = [0.0, 50.0, 10.0, 0.0,
           60.0, 50.0, 10.1, 5.0,
           120.0, 50.0, 10.2, 5.0]
    segs = _coarse_to_route_segments(arr, epsilon=0.001)
    assert len(segs) == 1
    coords, bands = segs[0]
    assert coords == [[10.0, 50.0], [10.2, 50.0]]     # [lon,lat], 3 decimals
    assert bands == [0, 2]                            # 0 kft -> 0, 5 kft -> 2


def test_route_segments_epsilon_controls_simplification():
    # dogleg of 0.05 deg survives eps=0.001, collapses at mobile eps=0.1
    arr = [0.0, 50.0, 10.0, 0.0,
           60.0, 50.05, 10.1, 0.0,
           120.0, 50.0, 10.2, 0.0]
    assert len(_coarse_to_route_segments(arr, 0.001)[0][0]) == 3
    assert len(_coarse_to_route_segments(arr, 0.1)[0][0]) == 2


def test_route_segments_gap_split_and_outlier():
    # >3600 s gap splits; the +6 deg spike between close neighbors is dropped
    arr = [0.0, 50.0, 10.0, 0.0,
           60.0, 56.0, 10.05, 0.0,       # spike
           120.0, 50.0, 10.1, 0.0,
           5000.0, 51.0, 11.0, 0.0,
           5060.0, 51.0, 11.1, 0.0]
    segs = _coarse_to_route_segments(arr, epsilon=0.001)
    assert [c for c, _ in segs] == [
        [[10.0, 50.0], [10.1, 50.0]],
        [[11.0, 51.0], [11.1, 51.0]],
    ]


def test_route_segments_too_short():
    assert _coarse_to_route_segments([0.0, 50.0, 10.0, 0.0], 0.001) == []


def test_build_route_features_props():
    flights = _flights_frame()
    role_ids = _role_ids(flights)
    day_off = _day_offsets(flights, START_EPOCH)
    coarse = {
        10: [0.0, 50.0, 10.0, 0.0, 60.0, 50.5, 10.5, 5.0],
        99: [0.0, 50.0, 10.0, 0.0, 60.0, 50.5, 10.5, 5.0],  # unknown id
    }
    feats = _build_route_features(coarse, flights, role_ids, day_off, 0.001)
    assert len(feats) == 1                 # unknown flight_id skipped
    f = feats[0]
    assert f["geometry"]["type"] == "LineString"
    assert set(f["properties"]) == {"id", "cc", "rs", "day", "thc", "the", "bi"}
    assert f["properties"]["id"] == 10
    assert f["properties"]["rs"] == [1]    # flight 10 -> tactical_airlift only
    assert f["properties"]["cc"] == "DE,GB"
    assert f["properties"]["thc"] == 1
    assert f["properties"]["the"] == 1
    assert f["properties"]["bi"] == [0, 2]


# -- crop-box clipping -----------------------------------------------------------

# Synthetic box for helper-level tests (real crop box is lat 9..73,
# lon -31..66; a small box keeps the fixtures readable).
CLIP_BOX = {"lat_south": 40, "lat_north": 60, "lon_west": 0, "lon_east": 20}


def _pt(ts, lat, lon, kft=0.0):
    return [float(ts), float(lat), float(lon), float(kft)]


def test_clip_fully_inside_unchanged():
    arr = _pt(0, 50, 10) + _pt(60, 51, 11) + _pt(120, 52, 12)
    assert _clip_flat_to_crop(arr, CLIP_BOX) == [arr]


def test_clip_fully_outside_no_pieces_no_feature():
    # entirely west of the box -> no pieces from the helper
    arr = _pt(0, 50, -40) + _pt(60, 51, -41) + _pt(120, 52, -42)
    assert _clip_flat_to_crop(arr, CLIP_BOX) == []

    # and end-to-end: a CONUS track (outside the real crop box) yields
    # no route feature at all
    flights = _flights_frame()
    role_ids = _role_ids(flights)
    day_off = _day_offsets(flights, START_EPOCH)
    coarse = {10: _pt(0, 39, -100) + _pt(60, 39.5, -99) + _pt(120, 40, -98)}
    assert _build_route_features(coarse, flights, role_ids, day_off, 0.001) == []


def test_clip_keeps_one_adjacent_outside_point():
    # exits west: the first outside point after the inside run is kept
    # (line draws past the map edge), the second is dropped
    arr = (_pt(0, 50, 10) + _pt(60, 50, 5) + _pt(120, 50, -5)
           + _pt(180, 50, -15))
    pieces = _clip_flat_to_crop(arr, CLIP_BOX)
    assert pieces == [_pt(0, 50, 10) + _pt(60, 50, 5) + _pt(120, 50, -5)]


def test_clip_exit_and_reenter_splits():
    # inside -> two points outside -> inside again: two pieces, each
    # ending/starting with its own adjacent outside point (no straight
    # line drawn through the outside region)
    arr = (_pt(0, 50, 10) + _pt(60, 50, 19)
           + _pt(120, 50, 22) + _pt(180, 50, 23)
           + _pt(240, 50, 19.5) + _pt(300, 50, 15))
    pieces = _clip_flat_to_crop(arr, CLIP_BOX)
    assert pieces == [
        _pt(0, 50, 10) + _pt(60, 50, 19) + _pt(120, 50, 22),
        _pt(180, 50, 23) + _pt(240, 50, 19.5) + _pt(300, 50, 15),
    ]

    # end-to-end against the real crop box (lon_west = -31): each piece
    # survives segment building as its own feature
    real_arr = (_pt(0, 50, 10) + _pt(60, 50, -25)
                + _pt(120, 50, -33) + _pt(180, 50, -34)
                + _pt(240, 50, -26) + _pt(300, 50, -10))
    flights = _flights_frame()
    role_ids = _role_ids(flights)
    day_off = _day_offsets(flights, START_EPOCH)
    feats = _build_route_features(
        {10: real_arr}, flights, role_ids, day_off, 0.001)
    assert len(feats) == 2
    assert [f["geometry"]["coordinates"][0] for f in feats] == \
        [[10.0, 50.0], [-34.0, 50.0]]


def test_clip_outlier_spike_beyond_box_is_dropped_not_split():
    # a +25 deg lon spike lands outside the box; the outlier filter
    # removes it before clipping, so the track stays one piece instead
    # of splitting into two darts toward the spike
    arr = (_pt(0, 50, 10) + _pt(60, 50, 35) + _pt(120, 50, 10.2)
           + _pt(180, 50, 10.4))
    pieces = _clip_flat_to_crop(arr, CLIP_BOX)
    assert pieces == [_pt(0, 50, 10) + _pt(120, 50, 10.2) + _pt(180, 50, 10.4)]




def test_unknown_altitude_bands_inherit_neighbours():
    """A NaN-altitude point mid-cruise must render in the surrounding
    cruise band, not as a false ground-level segment; a leading unknown
    run backfills from the first known band."""
    import numpy as np
    from backend.pipeline.etl.publish.export import _coarse_to_route_segments
    kft = 30.0
    flat = []
    alts = [float("nan"), kft, float("nan"), kft, float("nan")]
    for i, a in enumerate(alts):
        flat += [10.0 + i, 50.0 + i * 0.01, 8.0, a]
    segs = _coarse_to_route_segments(flat, epsilon=1e-9)
    assert segs, "expected one segment"
    _, bands = segs[0]
    expected = {int(kft / 2.5)}
    assert set(bands) == expected, bands


def test_sighting_index_merges_landing_events(tmp_data_dir, tmp_path):
    """landings.parquet events publish as a per-hex "g" day list merged
    into sightings/<cc>.json — non-numeric key, invisible to readers that
    iterate numeric day keys. Day surfaces credit the FULL SPAN of a stay
    (arrival day through departure day). A hex with landings but no
    sightings still gets an entry; pre-start days are dropped; aliases
    (UK->GB) apply."""
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir()
    start_day = int(START_EPOCH // 86400)
    d = 86400.0
    pd.DataFrame({
        "icao24": ["ae0001"],
        "day": [start_day],
        "cc": ["UK"],
        "first_ts": [START_EPOCH + 3600.0],
        "last_ts": [0.0],
    }).to_parquet(etl / "sighting_times.parquet", index=False)
    pd.DataFrame({
        # ae0001: a two-day stay (full span credits days 0 AND 1) plus a
        # separate same-day event (one day entry, not two); ae0002 landed
        # but was never sighted; ae0003's event predates the window.
        "icao24": ["ae0001", "ae0001", "ae0002", "ae0003"],
        "t_arrival": [START_EPOCH + 100.0, START_EPOCH + 50.0,
                      START_EPOCH + d + 100.0, START_EPOCH - d],
        "t_departure": [START_EPOCH + d + 200.0, START_EPOCH + 90.0,
                        float("nan"), START_EPOCH - d + 60.0],
        "field": ["ETAR", "EDDF", "", "ETAR"],
        "cc": ["UK", "UK", "UK", "UK"],
        "tier": ["observed", "observed", "one-edge", "observed"],
    }).to_parquet(etl / "landings.parquet", index=False)

    _write_sighting_index(START_EPOCH, ["GB"], tmp_path)

    gb = json.loads((tmp_path / "sightings" / "GB.json").read_text())
    assert gb["ae0001"] == {"0": 3600, "g": [0, 1]}
    # landed-but-never-sighted hex still appears, with only the g list
    assert gb["ae0002"] == {"g": [1]}
    # pre-start landing day dropped entirely
    assert "ae0003" not in gb


def test_sighting_index_missing_landings_still_writes(tmp_data_dir, tmp_path):
    """Absent landings.parquet degrades to sightings-only files rather
    than failing the export."""
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir()
    start_day = int(START_EPOCH // 86400)
    pd.DataFrame({
        "icao24": ["ae0001"], "day": [start_day], "cc": ["GB"],
        "first_ts": [START_EPOCH + 60.0], "last_ts": [0.0],
    }).to_parquet(etl / "sighting_times.parquet", index=False)
    _write_sighting_index(START_EPOCH, ["GB"], tmp_path)
    gb = json.loads((tmp_path / "sightings" / "GB.json").read_text())
    assert gb == {"ae0001": {"0": 60}}
