"""Tests for the `fly run ingest` CLI guard and the single-relevance-
predicate invariant."""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from click.testing import CliRunner

import backend
import backend.cli as cli
import backend.util.mil as mil


# ---------------------------------------------------------------------------
# FLY_TUNNELED guard (fly run ingest refuses without the sidecar marker)
# ---------------------------------------------------------------------------

def _invoke(monkeypatch, env):
    """Invoke `fly run ingest --all` (skip the mil_icao24.txt precondition)
    with run_ingest stubbed out so a passing guard doesn't try to actually
    ingest anything."""
    called = []
    monkeypatch.setattr("backend.pipeline.ingest.ingest.run_ingest",
                        lambda *a, **k: called.append((a, k)))
    result = CliRunner().invoke(
        cli.main, ["run", "ingest", "--all",
                   "--start", "2026-01-01", "--end", "2026-01-01"],
        env=env)
    return result, called


def test_ingest_refused_without_fly_tunneled(monkeypatch):
    result, called = _invoke(monkeypatch, {"FLY_TUNNELED": None})
    assert result.exit_code != 0
    assert "FLY_TUNNELED" in result.output
    assert called == []


def test_ingest_refused_with_wrong_fly_tunneled_value(monkeypatch):
    result, called = _invoke(monkeypatch, {"FLY_TUNNELED": "0"})
    assert result.exit_code != 0
    assert called == []


# Deliberately no "runs when FLY_TUNNELED=1 is set" test that reaches
# run_ingest for real — run_ingest is stubbed above specifically so this
# suite never triggers actual network I/O; the guard's job is to block the
# unset/wrong-value cases above.


# ---------------------------------------------------------------------------
# FLY_NET_DIRECT warning (`./run ingest --direct`): printed inside the
# sidecar namespace only, and only for the exact value "1". run_ingest stays
# stubbed, so the passing-guard path here touches no network.
# ---------------------------------------------------------------------------

def test_direct_mode_prints_warning(monkeypatch):
    result, called = _invoke(
        monkeypatch, {"FLY_TUNNELED": "1", "FLY_NET_DIRECT": "1"})
    assert result.exit_code == 0, result.output
    assert cli.DIRECT_MODE_WARNING in result.output
    assert len(called) == 1


@pytest.mark.parametrize("value", [None, "0", "yes"])
def test_tunneled_mode_prints_no_warning(monkeypatch, value):
    result, called = _invoke(
        monkeypatch, {"FLY_TUNNELED": "1", "FLY_NET_DIRECT": value})
    assert result.exit_code == 0, result.output
    assert "direct mode" not in result.output
    assert len(called) == 1


def test_direct_mode_does_not_bypass_tunnel_guard(monkeypatch):
    """FLY_NET_DIRECT grants nothing: without the sidecar marker ingest is
    still refused, warning or not."""
    result, called = _invoke(
        monkeypatch, {"FLY_TUNNELED": None, "FLY_NET_DIRECT": "1"})
    assert result.exit_code != 0
    assert called == []


# ---------------------------------------------------------------------------
# one relevance predicate everywhere: backend.util.mil is the single source of
# truth for US-military hex relevance, and every consumer imports its
# predicate from there rather than re-implementing the logic.
# ---------------------------------------------------------------------------

def test_mil_module_exposes_a_single_public_relevance_predicate():
    """backend.util.mil's public surface holds exactly one relevance predicate. If a
    second one is ever added (resurrecting a parallel hex-range check),
    this fails without needing to name it."""
    public = {name for name, obj in vars(mil).items()
              if not name.startswith("_") and inspect.isfunction(obj)
              and obj.__module__ == mil.__name__}
    # reset_mil_cache is a cache hook (test/reload support), not a
    # relevance predicate — the invariant this test guards is that no
    # SECOND relevance check ever appears alongside is_relevant.
    assert public == {"is_relevant", "reset_mil_cache"}


def test_ingest_and_types_import_relevance_from_backend_mil():
    backend_dir = Path(backend.__file__).parent
    ingest_src = (backend_dir / "pipeline" / "ingest" / "ingest.py").read_text()
    types_src = (backend_dir / "pipeline" / "etl" / "preprocess" / "types.py").read_text()
    assert "from backend.util.mil import is_relevant" in ingest_src
    assert "from backend.util.mil import is_relevant" in types_src
