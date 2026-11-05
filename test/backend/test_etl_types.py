"""etl types: type_status cascade, rotary classification, exclusion tests."""
import json

import pandas as pd

from backend.pipeline.etl.preprocess import types as T
from backend.pipeline.etl.preprocess.types import (
    classify_excluded, normalize_type_code, resolve_types,
)


# ---------------------------------------------------------------------------
# type_status cascade (see the roles taxonomy in data/in/config/roles.json)
# ---------------------------------------------------------------------------

def _stub_resolution(monkeypatch, daily_seen, daily_attested,
                     bulk_lookup=None, daily_descs=None,
                     mictronics_descs=None):
    """Wire resolve_types() to a controlled, no-network, no-file scenario:
    one BULK_SOURCES layer (pre-crop, so it always runs after
    daily-records), stubbed. valid_type_codes is left real (DOC 8643 +
    roles.json) so "valid vs invalid candidate" behaves exactly as
    production. _mictronics_descriptions is also stubbed (defaults to
    empty) so tests never touch the real, manually-downloaded mictronics
    snapshot unless a test explicitly asks for a fallback desc."""
    monkeypatch.setattr(T, "_daily_records",
                        lambda: (daily_seen, daily_attested,
                                 daily_descs or {}))
    monkeypatch.setattr(T.dimensions, "end", lambda: "2026-01-01")
    stub_source = T.BulkSource("stub-bulk", "2020-01-01",
                               lambda: bulk_lookup or {})
    monkeypatch.setattr(T, "BULK_SOURCES", [stub_source])
    monkeypatch.setattr(T, "_mictronics_descriptions",
                        lambda: mictronics_descs or {})


def test_type_status_resolved_when_candidate_validates(monkeypatch):
    _stub_resolution(monkeypatch, {"aaaaaa"}, {"aaaaaa": "C17"})
    out = resolve_types().set_index("icao24")
    assert out.loc["aaaaaa"].type_status == "resolved"
    assert out.loc["aaaaaa"].type_code == "C17"


def test_type_status_type_unknown_when_candidate_seen_but_invalid(monkeypatch):
    # A raw candidate was attested but never validates against DOC 8643 +
    # roles.json pseudo-codes in any layer.
    _stub_resolution(monkeypatch, {"bbbbbb"}, {"bbbbbb": "NOTREAL"})
    out = resolve_types().set_index("icao24")
    assert out.loc["bbbbbb"].type_status == "type_unknown"
    assert out.loc["bbbbbb"].type_code is None


def test_type_status_typecode_unknown_when_no_candidate_anywhere(monkeypatch):
    # Seen in the crop interval (US-mil-prefix aircraft existed) but no
    # layer — daily-records or bulk — ever offered a type string.
    _stub_resolution(monkeypatch, {"cccccc"}, {})
    out = resolve_types().set_index("icao24")
    assert out.loc["cccccc"].type_status == "typecode_unknown"
    assert out.loc["cccccc"].type_code is None


def test_type_status_bulk_layer_candidate_seen_counts(monkeypatch):
    # daily-records saw nothing; the bulk DB layer offers a candidate that
    # fails validation -> still type_unknown, not typecode_unknown.
    _stub_resolution(monkeypatch, {"dddddd"}, {},
                     bulk_lookup={"dddddd": "GARBAGE"})
    out = resolve_types().set_index("icao24")
    assert out.loc["dddddd"].type_status == "type_unknown"


def test_etl_makes_no_network_calls():
    # The etl pipeline is network-free: type resolution runs entirely off
    # local daily-records and bulk-source data. This is the standing
    # enforcement: no network library may be imported anywhere under backend/pipeline/etl/.
    import backend.pipeline.etl as etl_pkg
    from pathlib import Path
    etl_dir = Path(etl_pkg.__file__).parent
    banned = ("urllib", "requests", "http.client", "socket", "aiohttp")
    for py in etl_dir.rglob("*.py"):
        srctext = py.read_text()
        for line in srctext.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                for mod in banned:
                    assert not stripped.startswith((f"import {mod}",
                                                    f"from {mod}")), \
                        f"network import in {py}: {stripped}"


def test_type_status_cascade_is_disjoint_and_covers_all_seen(monkeypatch):
    _stub_resolution(monkeypatch, {"aaaaaa", "bbbbbb", "cccccc"},
                     {"aaaaaa": "C17", "bbbbbb": "NOTREAL"})
    out = resolve_types()
    assert set(out.type_status) <= {"resolved", "type_unknown",
                                    "typecode_unknown"}
    assert len(out) == 3
    by_status = dict(zip(out.icao24, out.type_status))
    assert by_status == {
        "aaaaaa": "resolved", "bbbbbb": "type_unknown",
        "cccccc": "typecode_unknown",
    }


def test_resolve_types_includes_desc(monkeypatch):
    _stub_resolution(monkeypatch, {"aaaaaa"},
                     {"aaaaaa": "C30J"},
                     daily_descs={"aaaaaa": "Lockheed Martin KC-130J Hercules"})
    out = resolve_types().set_index("icao24")
    assert "desc" in out.columns
    assert out.loc["aaaaaa", "desc"] == "Lockheed Martin KC-130J Hercules"


def test_resolve_types_desc_null_when_absent(monkeypatch):
    _stub_resolution(monkeypatch, {"aaaaaa"}, {"aaaaaa": "C17"})
    out = resolve_types().set_index("icao24")
    assert pd.isna(out.loc["aaaaaa", "desc"])


# ---------------------------------------------------------------------------
# desc resolution: mictronics fallback for absent/generic live desc
# ---------------------------------------------------------------------------

def test_is_generic_desc_true_when_desc_matches_no_desc_roles_pattern():
    # B350's desc_roles are [["MC-12", ...], ["UC-12", ...]]; a plain
    # model-family desc matches neither, so it can't narrow the roleset.
    assert T._is_generic_desc("B350", "BEECHCRAFT Super King Air 350")


def test_is_generic_desc_false_when_desc_matches_a_pattern():
    assert not T._is_generic_desc(
        "B350", "Hawker Beechcraft Corp MC-12W Liberty")


def test_is_generic_desc_false_for_designator_without_desc_roles():
    # C17 is single-role with no desc_roles block: there's no more-specific
    # pattern to ask for, so no desc counts as "generic" for it.
    assert not T._is_generic_desc("C17", "anything at all")


def test_is_generic_desc_false_when_desc_absent():
    assert not T._is_generic_desc("B350", None)
    assert not T._is_generic_desc("B350", "")


def test_resolve_desc_prefers_specific_live_desc_over_mictronics():
    assert T._resolve_desc(
        "B350", "Hawker Beechcraft Corp MC-12W Liberty",
        "some other mictronics text",
    ) == "Hawker Beechcraft Corp MC-12W Liberty"


def test_resolve_desc_falls_back_to_mictronics_when_live_absent():
    assert T._resolve_desc("B350", None, "Hawker Beechcraft Corp UC-12W Huron") \
        == "Hawker Beechcraft Corp UC-12W Huron"


def test_resolve_desc_falls_back_to_mictronics_when_live_generic():
    assert T._resolve_desc(
        "B350", "BEECHCRAFT Super King Air 350",
        "Hawker Beechcraft Corp UC-12W Huron",
    ) == "Hawker Beechcraft Corp UC-12W Huron"


def test_resolve_desc_keeps_generic_live_desc_when_mictronics_empty():
    # Mictronics has nothing better to offer -> the generic live desc is
    # still better than None.
    assert T._resolve_desc(
        "B350", "BEECHCRAFT Super King Air 350", None,
    ) == "BEECHCRAFT Super King Air 350"


def test_resolve_types_uses_mictronics_desc_fallback_end_to_end(monkeypatch):
    _stub_resolution(
        monkeypatch, {"aaaaaa"}, {"aaaaaa": "B350"},
        daily_descs={"aaaaaa": "BEECHCRAFT Super King Air 350"},
        mictronics_descs={"aaaaaa": "Hawker Beechcraft Corp UC-12W Huron"})
    out = resolve_types().set_index("icao24")
    assert out.loc["aaaaaa", "desc"] == "Hawker Beechcraft Corp UC-12W Huron"


def test_resolve_types_mictronics_never_overrides_specific_live_desc(monkeypatch):
    _stub_resolution(
        monkeypatch, {"aaaaaa"}, {"aaaaaa": "B350"},
        daily_descs={"aaaaaa": "Hawker Beechcraft Corp MC-12W Liberty"},
        mictronics_descs={"aaaaaa": "some other, unrelated text"})
    out = resolve_types().set_index("icao24")
    assert out.loc["aaaaaa", "desc"] == "Hawker Beechcraft Corp MC-12W Liberty"


# ---------------------------------------------------------------------------
# manual type_code overrides (data/in/config/type_overrides.json) -- evidenced,
# narrow corrections that always win over the layered bulk/daily-records
# resolution (see the T38/B762 real-world case this closes).
# ---------------------------------------------------------------------------

def test_manual_override_wins_over_layered_resolution(monkeypatch):
    # Bulk layer resolves "aaaaaa" to T38; the override corrects it to B762.
    _stub_resolution(monkeypatch, {"aaaaaa"}, {},
                     bulk_lookup={"aaaaaa": "T38"})
    monkeypatch.setattr(T, "_type_overrides", lambda: {"aaaaaa": "B762"})
    out = resolve_types().set_index("icao24")
    assert out.loc["aaaaaa", "type_code"] == "B762"
    assert out.loc["aaaaaa", "type_source"] == "manual-override"
    assert out.loc["aaaaaa", "type_status"] == "resolved"


def test_manual_override_resolves_an_otherwise_typecode_unknown_hex(monkeypatch):
    # No layer ever offers a candidate for "bbbbbb"; the override alone
    # resolves it (missing.discard path).
    _stub_resolution(monkeypatch, {"bbbbbb"}, {})
    monkeypatch.setattr(T, "_type_overrides", lambda: {"bbbbbb": "C17"})
    out = resolve_types().set_index("icao24")
    assert out.loc["bbbbbb", "type_code"] == "C17"
    assert out.loc["bbbbbb", "type_status"] == "resolved"


def test_manual_override_ignored_for_hex_not_seen(monkeypatch):
    # An override entry for a hex never seen in this crop's daily records
    # is silently ignored, not an error -- overrides are keyed by a fixed
    # config file, not scoped to any one crop window.
    _stub_resolution(monkeypatch, {"aaaaaa"}, {"aaaaaa": "C17"})
    monkeypatch.setattr(T, "_type_overrides",
                        lambda: {"zzzzzz": "B762"})  # not in `seen`
    out = resolve_types().set_index("icao24")
    assert "zzzzzz" not in out.index
    assert out.loc["aaaaaa", "type_code"] == "C17"
    assert out.loc["aaaaaa", "type_source"] == "daily-records"


def test_manual_override_invalid_type_code_raises(monkeypatch):
    _stub_resolution(monkeypatch, {"aaaaaa"}, {"aaaaaa": "C17"})
    monkeypatch.setattr(T, "_type_overrides",
                        lambda: {"aaaaaa": "NOTREAL"})
    try:
        resolve_types()
        assert False, "expected ValueError for an invalid override type_code"
    except ValueError as e:
        assert "NOTREAL" in str(e)


def test_type_overrides_config_file_is_well_formed():
    """Every entry in the real data/in/config/type_overrides.json must name a
    valid designator and at least one icao24 -- catches a config typo
    before it silently no-ops or breaks a build."""
    from backend.pipeline.etl.preprocess.types import valid_type_codes

    doc = json.loads(T._type_overrides_path().read_text())
    valid = valid_type_codes()
    seen_hexes: set[str] = set()
    for entry in doc["overrides"]:
        assert entry["icao24s"], "override entry has no icao24s"
        assert normalize_type_code(entry["type_code"]) in valid, (
            f"type_overrides.json: {entry['type_code']!r} is not a valid "
            "designator")
        assert entry.get("evidence"), (
            "type_overrides.json: every entry must cite its evidence")
        for h in entry["icao24s"]:
            assert h == h.lower(), f"icao24 not lowercase: {h}"
            assert h not in seen_hexes, f"icao24 listed twice: {h}"
            seen_hexes.add(h)


# ---------------------------------------------------------------------------
# typecode/desc family disagreement check -- logs only, never resolves
# ---------------------------------------------------------------------------

def _aircraft_frame(rows):
    """rows: list of (icao24, type_code, type_status, desc)."""
    return pd.DataFrame(rows, columns=["icao24", "type_code", "type_status", "desc"])


_DOC8643_STUB = [
    {"Designator": "T38", "AircraftDescription": "LandPlane", "WTC": "L"},
    {"Designator": "B762", "AircraftDescription": "LandPlane", "WTC": "H"},
    {"Designator": "A10", "AircraftDescription": "LandPlane", "WTC": "M"},
    {"Designator": "H60", "AircraftDescription": "Helicopter", "WTC": "M"},
    {"Designator": "B350", "AircraftDescription": "LandPlane", "WTC": "L/M"},
    {"Designator": "BE20", "AircraftDescription": "LandPlane", "WTC": "L/M"},
    {"Designator": "BE30", "AircraftDescription": "LandPlane", "WTC": "L"},
]


def test_families_disagree_on_species():
    assert T._families_disagree(("LandPlane", "M"), ("Helicopter", "M"))


def test_families_disagree_on_wtc_extremes():
    assert T._families_disagree(("LandPlane", "L"), ("LandPlane", "H"))


def test_families_agree_on_adjacent_wtc():
    # L vs L/M is routine manufacturer-family variation, not a disagreement.
    assert not T._families_disagree(("LandPlane", "L"), ("LandPlane", "L/M"))


def test_families_agree_when_species_and_wtc_both_match():
    assert not T._families_disagree(("LandPlane", "M"), ("LandPlane", "M"))


def test_check_typecode_desc_family_agreement_flags_minority(monkeypatch):
    monkeypatch.setattr(T, "_doc8643_rows", lambda: _DOC8643_STUB)
    # Mirrors the real T38/B762 case: 7 T38-typed aircraft vs 26
    # B762-typed aircraft all sharing the identical desc string.
    aircraft = _aircraft_frame(
        [(f"t{i:02d}", "T38", "resolved", "BOEING 767-200") for i in range(7)]
        + [(f"b{i:02d}", "B762", "resolved", "BOEING 767-200") for i in range(26)]
    )
    flagged = T.check_typecode_desc_family_agreement(aircraft)
    assert {f["icao24"] for f in flagged} == {f"t{i:02d}" for i in range(7)}
    assert all(f["type_code"] == "T38" for f in flagged)
    assert all(f["desc"] == "BOEING 767-200" for f in flagged)


def test_check_typecode_desc_family_agreement_ignores_same_family_variant(monkeypatch):
    # BE20/B350 sharing a desc is routine King-Air-family variation
    # (WTC "L/M" both) -- must not be flagged.
    monkeypatch.setattr(T, "_doc8643_rows", lambda: _DOC8643_STUB)
    aircraft = _aircraft_frame([
        ("aaaaaa", "BE20", "resolved", "BEECHCRAFT Super King Air 350"),
        ("bbbbbb", "B350", "resolved", "BEECHCRAFT Super King Air 350"),
    ])
    assert T.check_typecode_desc_family_agreement(aircraft) == []


def test_check_typecode_desc_family_agreement_ignores_single_typecode_desc(monkeypatch):
    monkeypatch.setattr(T, "_doc8643_rows", lambda: _DOC8643_STUB)
    aircraft = _aircraft_frame([
        ("aaaaaa", "T38", "resolved", "Northrop T-38A Talon"),
        ("bbbbbb", "T38", "resolved", "Northrop T-38A Talon"),
    ])
    assert T.check_typecode_desc_family_agreement(aircraft) == []


def test_check_typecode_desc_family_agreement_ignores_unresolved_rows(monkeypatch):
    monkeypatch.setattr(T, "_doc8643_rows", lambda: _DOC8643_STUB)
    aircraft = _aircraft_frame([
        ("aaaaaa", "T38", "type_unknown", "BOEING 767-200"),
        ("bbbbbb", "B762", "resolved", "BOEING 767-200"),
    ])
    assert T.check_typecode_desc_family_agreement(aircraft) == []


def test_check_typecode_desc_family_agreement_real_corpus_low_false_positive_rate():
    """Run the real check against the real, currently-resolved
    data/build/etl/aircraft.parquet + the real DOC 8643 snapshot (both local,
    manually-downloaded files -- skip if absent, e.g. a fresh checkout
    that hasn't run ./run etl yet). Documents the actual corpus-wide flag
    count so a future change that suddenly flags hundreds of aircraft is
    caught by this test, not just noticed in a build log."""
    ac_path = T.etl_dir() / "aircraft.parquet"
    doc_path = T.external_dir() / "aircraft" / "doc8643_aircraft_types.json"
    if not ac_path.exists() or not doc_path.exists():
        return
    aircraft = pd.read_parquet(ac_path)
    flagged = T.check_typecode_desc_family_agreement(aircraft)
    # Conservative by design (see _families_disagree): expect a small
    # handful, not hundreds, of flags on the real corpus.
    assert len(flagged) < 20, (
        f"typecode/desc disagreement check flagged {len(flagged)} aircraft "
        "-- unexpectedly loose, investigate before trusting the count")


# ---------------------------------------------------------------------------
# excluded column (rotary OR non_aircraft role)
# ---------------------------------------------------------------------------

def test_classify_excluded_non_aircraft_role():
    df = pd.DataFrame({
        "icao24": ["aaaaaa"],
        "type_code": ["TWR"],   # non_aircraft designator in roles.json
        "rotary": [False],
    })
    out = classify_excluded(df)
    assert out.loc[0, "excluded"]


def test_classify_excluded_rotary_still_excluded():
    df = pd.DataFrame({
        "icao24": ["bbbbbb"],
        "type_code": ["H60"],
        "rotary": [True],
    })
    out = classify_excluded(df)
    assert out.loc[0, "excluded"]


def test_classify_excluded_ordinary_type_kept():
    df = pd.DataFrame({
        "icao24": ["cccccc"],
        "type_code": ["C17"],
        "rotary": [False],
    })
    out = classify_excluded(df)
    assert not out.loc[0, "excluded"]


def test_classify_excluded_untyped_not_excluded():
    df = pd.DataFrame({
        "icao24": ["dddddd"],
        "type_code": [None],
        "rotary": [False],
    })
    out = classify_excluded(df)
    assert not out.loc[0, "excluded"]


# ---------------------------------------------------------------------------
# Type code normalization and rotary classification
# ---------------------------------------------------------------------------

def test_normalize_type_code():
    assert normalize_type_code("P8 ?") == "P8"
    assert normalize_type_code(" c17 ") == "C17"
    assert normalize_type_code("H60") == "H60"
    assert normalize_type_code("") == ""
    assert normalize_type_code(None) == ""


def test_classify_rotary_species_safety_net(monkeypatch):
    """A code unmapped by roles.json but rotary per DOC 8643 is excluded."""
    monkeypatch.setattr(T, "doc8643_rotary_designators",
                        lambda: frozenset({"XXHE"}))
    df = pd.DataFrame({
        "icao24": ["aaaaaa", "bbbbbb", "cccccc", "dddddd"],
        "type_code": ["XXHE", None, "C17", "H60"],
        "type_source": ["daily-records", None, "mictronics", "mictronics"],
    })
    out = T.classify_rotary(df).set_index("icao24")
    assert out.loc["aaaaaa"].rotary
    assert out.loc["aaaaaa"].rotary_source == "icao8643-species"
    assert not out.loc["bbbbbb"].rotary      # untyped: never rotary
    assert not out.loc["cccccc"].rotary
    assert out.loc["cccccc"].rotary_source == "type-roles"
    assert out.loc["dddddd"].rotary
    assert out.loc["dddddd"].rotary_source == "type-roles"


# ---------------------------------------------------------------------------
# opensky snapshot: newest aircraft-database-complete-*.csv by filename
# (README: "newest by name is used")
# ---------------------------------------------------------------------------

def _write_opensky_csv(tmp_data_dir, yyyymm, rows):
    ac_dir = tmp_data_dir / "in" / "external" / "aircraft"
    ac_dir.mkdir(parents=True, exist_ok=True)
    lines = ["icao24,typecode"] + [f"{h},{tc}" for h, tc in rows]
    (ac_dir / f"aircraft-database-complete-{yyyymm}.csv").write_text(
        "\n".join(lines) + "\n")


def test_opensky_picks_newest_csv_by_filename(tmp_data_dir):
    _write_opensky_csv(tmp_data_dir, "2024-01", [("old001", "OLD1")])
    _write_opensky_csv(tmp_data_dir, "2025-08", [("new001", "NEW1")])
    assert T._opensky_type_codes() == {"new001": "NEW1"}


def test_opensky_csv_path_none_and_empty_dict_when_absent(tmp_data_dir):
    assert T._opensky_csv_path() is None
    assert T._opensky_type_codes() == {}


def test_opensky_snapshot_id_derives_yyyymm_from_filename(tmp_data_dir):
    _write_opensky_csv(tmp_data_dir, "2025-08", [])
    assert T._opensky_snapshot_id() == ("opensky-2025-08", "2025-08-01")


def test_opensky_snapshot_id_falls_back_when_absent(tmp_data_dir):
    assert T._opensky_snapshot_id() == ("opensky", "1970-01-01")


def test_opensky_logs_which_file_was_chosen(tmp_data_dir, capsys):
    _write_opensky_csv(tmp_data_dir, "2025-08", [("new001", "NEW1")])
    T._opensky_type_codes()
    err = capsys.readouterr().err
    assert "aircraft-database-complete-2025-08.csv" in err
