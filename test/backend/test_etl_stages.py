"""CLI stage/group resolution for `fly run etl`."""
from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

import backend.cli as cli
import backend.pipeline.etl.run as run2


@pytest.fixture()
def record_stages(monkeypatch):
    """Record etl stage invocations without running them."""
    calls: list[str] = []
    fake = {name: (lambda n: lambda: calls.append(n))(name)
            for name in run2.STAGES}
    monkeypatch.setattr(run2, "STAGES", fake)
    return calls


def _invoke(*args):
    return CliRunner().invoke(cli.main, ["run", "etl", *args])


def test_no_args_runs_all_stages_in_order(record_stages):
    result = _invoke()
    assert result.exit_code == 0, result.output
    assert record_stages == list(run2.STAGE_ORDER)


def test_single_stage(record_stages):
    result = _invoke("partition")
    assert result.exit_code == 0, result.output
    assert record_stages == ["partition"]


def test_group_expands_to_its_stages(record_stages):
    result = _invoke("timeline")
    assert result.exit_code == 0, result.output
    assert record_stages == list(run2.GROUPS["timeline"])


def test_in_order_stages_run(record_stages):
    result = _invoke("types", "partition")
    assert result.exit_code == 0, result.output
    assert record_stages == ["types", "partition"]


def test_out_of_order_stages_rejected(record_stages):
    # strict order: tokens must be given in pipeline order, not reordered.
    result = _invoke("partition", "types")
    assert result.exit_code != 0
    assert "out of order" in result.output
    assert record_stages == []


def test_duplicate_stage_rejected(record_stages):
    # a repeated token is an ordering violation (not silently deduped).
    result = _invoke("types", "types")
    assert result.exit_code != 0
    assert "out of order" in result.output
    assert record_stages == []


def test_group_then_overlapping_stage_rejected(record_stages):
    # `partition` already covers `types`; naming it again is out of order.
    result = _invoke("timeline", "types")
    assert result.exit_code != 0
    assert "out of order" in result.output
    assert record_stages == []


def test_unknown_stage_rejected(record_stages):
    result = _invoke("bogus")
    assert result.exit_code != 0
    assert record_stages == []


def test_group_and_stage_order_covers_all_names():
    # every group stage appears exactly once in the canonical order
    flat = [s for g in ("preprocess", "timeline", "landing", "publish")
            for s in run2.GROUPS[g]]
    assert flat == run2.STAGE_ORDER
    assert set(run2.STAGES) <= set(run2.STAGE_ORDER)


def test_resolve_group_plus_later_stage():
    # a group followed by a strictly-later stage resolves in canonical order.
    names = cli._resolve_etl_steps(("timeline", "trace"))
    expected = [s for s in run2.STAGE_ORDER
                if s in run2.STAGES and
                (s in run2.GROUPS["timeline"] or s == "trace")]
    assert names == expected


def test_resolve_group_plus_overlapping_stage_raises():
    with pytest.raises(click.BadArgumentUsage):
        cli._resolve_etl_steps(("timeline", "types"))


def test_resolve_out_of_order_phases_raises():
    with pytest.raises(click.BadArgumentUsage):
        cli._resolve_etl_steps(("landing", "timeline"))


def test_resolve_unknown_token_raises():
    with pytest.raises(click.BadArgumentUsage):
        cli._resolve_etl_steps(("nope",))
