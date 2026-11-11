"""Precondition gate: `fly status --check CONTEXT [tokens]`.

The gate is the single source of truth the ./run commands call before
doing work, so these tests pin its exit codes and messages. Files are created
under the tmp_data_dir fixture (FLY_DATA_DIR) to simulate pipeline state.
"""
from __future__ import annotations

import json
import os
import time

from click.testing import CliRunner

import backend.cli as cli


def _check(*args):
    return CliRunner().invoke(cli.main, ["status", "--check", *args])


def _write(path, text="{}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# --- unknown context / tokens -------------------------------------------------

def test_unknown_context_fails(tmp_data_dir):
    result = _check("bogus")
    assert result.exit_code == 1
    assert "unknown context" in result.output


def test_etl_unknown_stage_fails(tmp_data_dir):
    result = _check("etl", "bogus")
    assert result.exit_code == 1
    assert "unknown step" in result.output


def test_etl_out_of_order_fails(tmp_data_dir):
    result = _check("etl", "partition", "types")
    assert result.exit_code == 1
    assert "out of order" in result.output


def test_ingest_unknown_subcommand_fails(tmp_data_dir):
    result = _check("ingest", "bogus")
    assert result.exit_code == 1
    assert "unknown ingest argument" in result.output


def test_gen_unknown_target_fails(tmp_data_dir):
    result = _check("gen", "bogus")
    assert result.exit_code == 1
    assert "unknown gen target" in result.output


# --- missing preconditions ----------------------------------------------------

def test_etl_missing_generated_and_raw_fails(tmp_data_dir):
    result = _check("etl")
    assert result.exit_code == 1
    # both a generated asset and raw completeness should be flagged
    assert "mil_icao24.txt" in result.output
    assert "raw data incomplete" in result.output


def test_serve_without_build_fails(tmp_data_dir):
    result = _check("serve")
    assert result.exit_code == 1
    assert "manifest.json missing" in result.output


def test_gen_airports_without_source_fails(tmp_data_dir):
    result = _check("gen", "airports")
    assert result.exit_code == 1
    assert "airports.csv" in result.output


def test_ingest_bare_without_sources_fails(tmp_data_dir):
    result = _check("ingest")
    assert result.exit_code == 1
    assert "required source" in result.output


# --- satisfied preconditions --------------------------------------------------

def test_serve_with_build_passes(tmp_data_dir):
    _write(tmp_data_dir / "site" / "manifest.json")
    result = _check("serve")
    assert result.exit_code == 0, result.output


def test_gen_mil_needs_nothing(tmp_data_dir):
    # mil degrades gracefully -> no hard precondition, always passes.
    assert _check("gen", "mil").exit_code == 0


def test_ingest_maintenance_subcommands_need_nothing(tmp_data_dir):
    # merge-batches / downsample operate on already-downloaded raw data.
    assert _check("ingest", "downsample").exit_code == 0
    assert _check("ingest", "merge-batches").exit_code == 0


def test_gen_airports_with_source_passes(tmp_data_dir):
    _write(tmp_data_dir / "in" / "external" / "airports.csv", "col\n")
    result = _check("gen", "airports")
    assert result.exit_code == 0, result.output


# --- etl gate: mictronics zip (types stage hard-requires it) ------------------

def _satisfy_generated_and_raw(tmp_data_dir, monkeypatch):
    """Isolate the mictronics check from the (already covered elsewhere)
    generated-assets / raw-completeness checks."""
    import backend.util.dimensions as dimensions
    monkeypatch.setattr(dimensions, "dates", lambda: [])
    for name, _ in cli._GENERATED:
        _write(tmp_data_dir / "build" / "lookup" / name)


def test_etl_types_missing_mictronics_fails(tmp_data_dir, monkeypatch):
    _satisfy_generated_and_raw(tmp_data_dir, monkeypatch)
    result = _check("etl", "types")
    assert result.exit_code == 1
    assert "mictronics_indexedDB.zip" in result.output
    assert "download from" in result.output


def test_etl_types_with_sources_present_passes(tmp_data_dir, monkeypatch):
    _satisfy_generated_and_raw(tmp_data_dir, monkeypatch)
    _write(tmp_data_dir / "in" / "external" / "aircraft" / "mictronics_indexedDB.zip", "x")
    _write(tmp_data_dir / "in" / "external" / "aircraft"
           / "aircraft-database-complete-2026-01.csv", "icao24\n")
    result = _check("etl", "types")
    assert result.exit_code == 0, result.output


def test_etl_types_missing_aircraft_db_fails(tmp_data_dir, monkeypatch):
    # aircraft-db is REQUIRED and types is its only consumer: the types
    # gate blocks on it (ingest's gate deliberately does not, below).
    _satisfy_generated_and_raw(tmp_data_dir, monkeypatch)
    _write(tmp_data_dir / "in" / "external" / "aircraft" / "mictronics_indexedDB.zip", "x")
    result = _check("etl", "types")
    assert result.exit_code == 1
    assert "aircraft-database-complete" in result.output


def test_ingest_gate_does_not_require_aircraft_db(tmp_data_dir):
    # ingest never reads the aircraft CSV; with every other REQUIRED
    # source present, a bare ingest must pass without it.
    _write(tmp_data_dir / "in" / "external" / "airports.csv", "col\n")
    _write(tmp_data_dir / "in" / "external" / "ne_10m_admin_0_countries"
           / "ne_10m_admin_0_countries.shp", "x")
    _write(tmp_data_dir / "in" / "external" / "aircraft" / "mictronics_indexedDB.zip", "x")
    result = _check("ingest")
    assert result.exit_code == 0, result.output


def test_etl_later_stage_does_not_require_mictronics(tmp_data_dir, monkeypatch):
    # The mictronics check is scoped to the 'types' stage; a gate for a
    # later-only stage shouldn't demand it (its own upstream-stage check —
    # "types must run before denoise" — is a separate concern).
    _satisfy_generated_and_raw(tmp_data_dir, monkeypatch)
    _write(tmp_data_dir / "build" / "etl" / "aircraft.parquet")
    result = _check("etl", "denoise")
    assert result.exit_code == 0, result.output


# --- etl gate: upstream gaps and staleness blocking ---------------------------

def test_etl_gap_between_requested_stages_fails(tmp_data_dir, monkeypatch):
    # `partition flight` skips edge/split; with none of them
    # done, the gate must flag the gap rather than let flight silently
    # no-op against missing inputs.
    _satisfy_generated_and_raw(tmp_data_dir, monkeypatch)
    etl = tmp_data_dir / "build" / "etl"
    _write(etl / "aircraft.parquet")
    _write(etl / "exclusions.parquet")
    result = _check("etl", "partition", "flight")
    assert result.exit_code == 1
    assert "'edge' must run before 'flight'" in result.output
    assert "'split' must run before 'flight'" in result.output


def test_etl_stale_upstream_blocks(tmp_data_dir, monkeypatch):
    # Simulate a lone flight re-run: every stage output exists, but
    # attribute's sighting_times.parquet now predates flight's output.
    # Requesting theater must BLOCK naming attribute, not proceed
    # against the stripped flights.parquet.
    _satisfy_generated_and_raw(tmp_data_dir, monkeypatch)
    etl = tmp_data_dir / "build" / "etl"
    order = ["aircraft.parquet", "exclusions.parquet", "intervals.parquet",
             "edges.parquet",
             "verdicts.parquet", "flight_dropped.parquet",
             "attribution_model.pkl",
             "sighting_times.parquet"]
    now = time.time()
    for i, name in enumerate(order):
        _write(etl / name)
        os.utime(etl / name, (now - 3600 + i, now - 3600 + i))
    os.utime(etl / "flight_dropped.parquet", (now, now))
    result = _check("etl", "theater")
    assert result.exit_code == 1
    assert "looks stale" in result.output


def test_status_report_shows_theater_state(tmp_data_dir):
    import pandas as pd
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir(parents=True)
    pd.DataFrame({"flight_id": [1], "traversed_countries": ["DE"]}) \
        .to_parquet(etl / "flights.parquet", index=False)
    result = CliRunner().invoke(cli.main, ["status"])
    assert result.exit_code == 0, result.output
    assert "theater" in result.output
    assert "./run etl theater" in result.output


# --- status staleness (mtime-ordering; blocking via the etl gate above) --------

def test_stale_stage_outputs_flags_out_of_order_mtimes(tmp_data_dir):
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir(parents=True)
    newer = etl / "flight_dropped.parquet"   # flight's output
    older = etl / "sighting_times.parquet"   # attribute's output (runs after flight)
    newer.write_text("x")
    older.write_text("x")
    now = time.time()
    os.utime(newer, (now, now))
    os.utime(older, (now - 3600, now - 3600))   # attribute's file is OLDER than flight's

    stale = cli._stale_stage_outputs()
    assert len(stale) >= 1
    stages = {s for s, _ in stale}
    assert "attribute" in stages or "train" in stages


def test_stale_stage_outputs_no_warning_when_in_order(tmp_data_dir):
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir(parents=True)
    older = etl / "flight_dropped.parquet"
    newer = etl / "sighting_times.parquet"
    older.write_text("x")
    newer.write_text("x")
    now = time.time()
    os.utime(older, (now - 3600, now - 3600))
    os.utime(newer, (now, now))

    assert cli._stale_stage_outputs() == []


def test_stale_stage_outputs_skips_missing_without_breaking_chain(tmp_data_dir):
    # flight_dropped.parquet etc. are all absent; only
    # types' and attribute's outputs exist, with several registry entries
    # between them missing on disk. The chain must still compare across
    # the gap rather than treating the missing entries as resets.
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir(parents=True)
    types_out = etl / "aircraft.parquet"
    attribute_out = etl / "sighting_times.parquet"
    types_out.write_text("x")
    attribute_out.write_text("x")
    now = time.time()
    os.utime(types_out, (now, now))
    os.utime(attribute_out, (now - 3600, now - 3600))

    stale = cli._stale_stage_outputs()
    assert len(stale) == 1
    stage, reason = stale[0]
    assert stage == "attribute"
    assert "aircraft.parquet" in reason and "sighting_times.parquet" in reason


def test_status_report_includes_staleness_warning(tmp_data_dir):
    etl = tmp_data_dir / "build" / "etl"
    etl.mkdir(parents=True)
    newer = etl / "flight_dropped.parquet"
    older = etl / "sighting_times.parquet"
    newer.write_text("x")
    older.write_text("x")
    now = time.time()
    os.utime(newer, (now, now))
    os.utime(older, (now - 3600, now - 3600))

    result = CliRunner().invoke(cli.main, ["status"])
    assert result.exit_code == 0, result.output
    assert "Staleness warnings" in result.output
    assert "sighting_times.parquet" in result.output
