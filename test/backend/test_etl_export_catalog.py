"""role_catalog.json generation (see the roles taxonomy in data/in/config/roles.json).

Covers: populated-only role/type inclusion, group ordering + skip-if-empty,
id assignment, the orphan-role build error, unknown-bucket counts/labels,
and data/build/role_findings.md content.
"""
import json

import pandas as pd
import pytest

from backend.pipeline.etl.publish.export import (
    _PSEUDO_ROLES,
    _build_role_catalog,
    _write_role_catalog,
    _write_role_findings,
)
from backend.util.roles import taxonomy


def _aircraft_frame(rows):
    """rows: list of (icao24, type_code, type_status, rotary, rotary_source,
    excluded)."""
    return pd.DataFrame(rows, columns=[
        "icao24", "type_code", "type_status", "rotary", "rotary_source",
        "excluded",
    ])


def _flights_frame(rows):
    """rows: list of (icao24, type_code, roles)."""
    return pd.DataFrame(rows, columns=["icao24", "type_code", "roles"])


# -- populated-only inclusion, ordering, ids -------------------------------

def test_catalog_only_populated_roles_get_entries():
    # K35R (air_refuelling) has a flight; C5 (strategic_airlift) has none —
    # C5 must be absent from roles[].
    flights = _flights_frame([
        ("ae0001", "K35R", "air_refuelling"),
        ("ae0002", "K35R", "air_refuelling"),
    ])
    aircraft = _aircraft_frame([
        ("ae0001", "K35R", "resolved", False, "type-roles", False),
        ("ae0002", "K35R", "resolved", False, "type-roles", False),
        ("ae0003", "C5", "resolved", False, "type-roles", False),
    ])
    catalog, role_ids, _ = _build_role_catalog(
        flights, aircraft, "2026-04-16", "2026-02-26", "2026-04-16")

    role_slugs = {r["slug"] for r in catalog["roles"]}
    assert role_slugs == {"air_refuelling"}

    role = catalog["roles"][0]
    assert role["id"] == 0
    assert role["airframes"] == 2 and role["flights"] == 2
    assert role_ids == [[0], [0]]


def test_catalog_groups_skip_empty_and_number_sequentially():
    # Only air_refuelling (Refuelling group) is populated -> Airlift group
    # (spec-first) must be skipped entirely, and Refuelling gets id 1
    # (not 2) since ids are assigned only over populated groups.
    flights = _flights_frame([("ae0001", "K35R", "air_refuelling")])
    aircraft = _aircraft_frame([
        ("ae0001", "K35R", "resolved", False, "type-roles", False),
    ])
    catalog, _, _ = _build_role_catalog(
        flights, aircraft, "2026-04-16", "2026-02-26", "2026-04-16")

    assert len(catalog["groups"]) == 1
    g = catalog["groups"][0]
    assert g["id"] == 1
    assert g["slug"] == "refuelling"
    assert g["on"] is True


def test_catalog_groups_are_config_driven_not_hardcoded(monkeypatch):
    """Prove _build_role_catalog has no group definition of its own: patch
    backend.util.roles.taxonomy() to return a single ad-hoc group and confirm the
    catalog reflects exactly that, not the real six-group spec."""
    import backend.pipeline.etl.publish.export as export_mod

    fake_tax = {
        "designators": {},
        "groups": [
            {"slug": "custom", "label": "Custom", "on": False,
             "roles": ["air_refuelling"]},
        ],
    }
    monkeypatch.setattr(export_mod, "taxonomy", lambda: fake_tax)

    flights = _flights_frame([("ae0001", "K35R", "air_refuelling")])
    aircraft = _aircraft_frame([
        ("ae0001", "K35R", "resolved", False, "type-roles", False),
    ])
    catalog, _, _ = _build_role_catalog(
        flights, aircraft, "2026-04-16", "2026-02-26", "2026-04-16")

    assert len(catalog["groups"]) == 1
    g = catalog["groups"][0]
    assert g["slug"] == "custom" and g["on"] is False


def test_catalog_role_unknown_group_on_and_pseudo_roles_only():
    flights = _flights_frame([("ae0001", None, "typecode_unknown")])
    aircraft = _aircraft_frame([
        ("ae0001", None, "typecode_unknown", False, "type-roles", False),
    ])
    catalog, role_ids, _ = _build_role_catalog(
        flights, aircraft, "2026-04-16", "2026-02-26", "2026-04-16")

    groups_by_slug = {g["slug"]: g for g in catalog["groups"]}
    assert set(groups_by_slug) == {"role_unknown"}
    assert groups_by_slug["role_unknown"]["on"] is True
    role_slugs = {r["slug"] for r in catalog["roles"]}
    assert role_slugs == {"typecode_unknown"}
    assert role_ids == [[0]]


def test_catalog_group_spec_matches_design_doc_shape():
    # airlift/refuelling/combat/isr default on; other_roles off;
    # role_unknown on; spec order preserved (before empty-skip filtering).
    # Group definitions are single-sourced in data/in/config/roles.json
    # "groups" (see test_roles.py::test_groups_shape_matches_design_doc for
    # the config-level assertion); this checks _build_role_catalog reads
    # that same source rather than an export.py-local copy.
    group_spec = taxonomy()["groups"]
    slugs = [g["slug"] for g in group_spec]
    assert slugs == [
        "airlift", "refuelling", "combat", "isr", "other_roles",
        "role_unknown",
    ]
    on_by_slug = {g["slug"]: g["on"] for g in group_spec}
    assert on_by_slug == {
        "airlift": True, "refuelling": True, "combat": True, "isr": True,
        "other_roles": False, "role_unknown": True,
    }


# -- multi-role union (no precedence) ---------------------------------------

def test_catalog_multi_role_flight_counts_in_every_role():
    # A flight with two roles from different groups counts once toward
    # each role's flight/aircraft totals (no-precedence union semantics).
    flights = _flights_frame([
        ("ae0001", "GLF5", "sigint_elint,vip_executive"),
    ])
    aircraft = _aircraft_frame([
        ("ae0001", "GLF5", "resolved", False, "type-roles", False),
    ])
    catalog, role_ids, _ = _build_role_catalog(
        flights, aircraft, "2026-04-16", "2026-02-26", "2026-04-16")

    counts = {r["slug"]: r["flights"] for r in catalog["roles"]}
    assert counts["sigint_elint"] == 1 and counts["vip_executive"] == 1
    assert len(role_ids[0]) == 2


# -- orphan-role build error --------------------------------------------------

def test_catalog_role_with_no_group_and_not_excluded_raises():
    flights = _flights_frame([("ae0001", "ZZZFAKE", "made_up_role")])
    aircraft = _aircraft_frame([
        ("ae0001", "ZZZFAKE", "resolved", False, "type-roles", False),
    ])
    with pytest.raises(ValueError, match="made_up_role"):
        _build_role_catalog(
            flights, aircraft, "2026-04-16", "2026-02-26", "2026-04-16")


def test_catalog_excluded_role_with_flights_does_not_raise():
    # non_aircraft is in EXCLUDED_ROLES, not in any group — must NOT raise
    # even if (contrary to profile-stage exclusion) it somehow has flights.
    flights = _flights_frame([("ae0001", "TWR", "non_aircraft")])
    aircraft = _aircraft_frame([
        ("ae0001", "TWR", "resolved", False, "type-roles", True),
    ])
    catalog, _, _ = _build_role_catalog(
        flights, aircraft, "2026-04-16", "2026-02-26", "2026-04-16")
    assert {r["slug"] for r in catalog["roles"]} == {"non_aircraft"}
    # non_aircraft belongs to no spec group -> absent from groups[]
    assert all(g["slug"] != "non_aircraft" for g in catalog["groups"])


# -- excluded / unknown_buckets summary blocks --------------------------------

def test_catalog_excluded_and_unknown_buckets_counts():
    flights = _flights_frame([
        ("ae0001", "K35R", "air_refuelling"),
        ("ae0002", None, "typecode_unknown"),
    ])
    aircraft = _aircraft_frame([
        ("ae0001", "K35R", "resolved", False, "type-roles", False),
        ("ae0002", None, "typecode_unknown", False, "type-roles", False),
        ("ae0003", "H60", "resolved", True, "type-roles", True),
        ("ae0004", "TWR", "resolved", False, "type-roles", True),
    ])
    catalog, _, _ = _build_role_catalog(
        flights, aircraft, "2026-04-16", "2026-02-26", "2026-04-16")

    assert catalog["excluded"] == {
        "rotary_airframes": 1,
        "non_aircraft_airframes": 1,
    }
    assert catalog["unknown_buckets"]["typecode_unknown"] == {
        "airframes": 1, "flights": 1,
    }
    for bucket in ("type_unknown", "role_unknown"):
        assert catalog["unknown_buckets"][bucket] == {
            "airframes": 0, "flights": 0,
        }


def test_all_pseudo_roles_covered_in_unknown_buckets():
    flights = _flights_frame([])
    aircraft = _aircraft_frame([])
    catalog, _, _ = _build_role_catalog(
        flights, aircraft, "2026-04-16", "2026-02-26", "2026-04-16")
    assert set(catalog["unknown_buckets"]) == set(_PSEUDO_ROLES)


# -- schema / write --------------------------------------------------------

def test_write_role_catalog_schema_and_indent(tmp_path):
    flights = _flights_frame([("ae0001", "K35R", "air_refuelling")])
    aircraft = _aircraft_frame([
        ("ae0001", "K35R", "resolved", False, "type-roles", False),
    ])
    catalog, _, _ = _build_role_catalog(
        flights, aircraft, "2026-04-16", "2026-02-26", "2026-04-16")
    _write_role_catalog(catalog, tmp_path)

    raw = (tmp_path / "role_catalog.json").read_text()
    assert "\n " in raw  # indent=1 pretty-prints with newlines
    parsed = json.loads(raw)
    assert set(parsed) >= {
        "generated", "crop", "roles", "groups", "excluded",
        "unknown_buckets",
    }
    assert parsed["crop"] == {"start": "2026-02-26", "end": "2026-04-16"}
    assert parsed["generated"] == "2026-04-16"


def test_role_findings_written_with_expected_sections(tmp_path, monkeypatch):
    import backend.util.paths as paths
    monkeypatch.setenv("FLY_DATA_DIR", str(tmp_path))

    flights = _flights_frame([
        ("ae0001", "K35R", "air_refuelling"),
        ("ae0002", None, "typecode_unknown"),
    ])
    aircraft = _aircraft_frame([
        ("ae0001", "K35R", "resolved", False, "type-roles", False),
        ("ae0002", None, "typecode_unknown", False, "type-roles", False),
        ("ae0003", "TWR", "resolved", False, "type-roles", True),
    ])
    catalog, _, _ = _build_role_catalog(
        flights, aircraft, "2026-04-16", "2026-02-26", "2026-04-16")
    _write_role_findings(flights, aircraft, catalog)

    text = (paths.data_dir() / "build" / "role_findings.md").read_text()
    assert "data/in/config/roles.json" in text
    assert "typecode_unknown" in text
    assert "ae0002" in text  # example hex for the bucket
    assert "type_unknown" in text and "role_unknown" in text
    assert "(empty)" in text  # the two zero-count buckets
    assert "non_aircraft" in text
