"""backend.util.paths suffix contract + the lazy-config behaviour of its
two heaviest callers (dimensions.py, roles.py).

The lazy-config test is a regression guard for a real defect: both modules
previously resolved config_dir() into a module constant at import time and
cached file content forever, so FLY_DATA_DIR set by a fixture after import
was silently ignored and accessors returned production config. Two test
files carried manual monkeypatch workarounds for exactly this.
"""
from __future__ import annotations

import json

import backend.util.dimensions as dimensions
import backend.util.roles as roles
from backend.util import paths


def test_directory_suffixes(tmp_data_dir):
    root = tmp_data_dir
    assert paths.data_dir() == root
    assert paths.raw_dir() == root / "build" / "ingest"
    assert paths.etl_dir() == root / "build" / "etl"
    assert paths.lookup_dir() == root / "build" / "lookup"
    assert paths.external_dir() == root / "in" / "external"
    assert paths.config_dir() == root / "in" / "config"
    assert paths.frontend_config_dir() == root / "in" / "config" / "frontend"
    assert paths.build_dir() == root / "site"


def test_default_paths_without_env(monkeypatch):
    monkeypatch.delenv("FLY_DATA_DIR", raising=False)
    monkeypatch.delenv("FLY_BUILD_DIR", raising=False)
    assert str(paths.data_dir()) == "data"
    assert str(paths.build_dir()) == str(paths.data_dir() / "out" / "data")


def test_config_readers_honour_late_fly_data_dir(tmp_data_dir):
    cfg = tmp_data_dir / "in" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "dimensions.json").write_text(json.dumps(
        {"start": "2099-01-01", "end": "2099-01-02",
         "bboxes": [{"name": "crop"}]}))
    (cfg / "roles.json").write_text(json.dumps(
        {"roles": {}, "groups": {}, "designators": {}, "prefixes": {}}))
    dimensions.reset_config_cache()
    roles.reset_taxonomy_cache()
    try:
        # FLY_DATA_DIR was set by the fixture AFTER these modules were
        # imported; lazy resolution must still pick it up.
        assert dimensions.start() == "2099-01-01"
        assert dimensions.crop() == {"name": "crop"}
        assert roles.roles_of("C17") == frozenset()
    finally:
        # never leak the fixture's config into later tests
        dimensions.reset_config_cache()
        roles.reset_taxonomy_cache()


def test_crop_selected_by_name_not_position(tmp_data_dir):
    """Reordering dimensions.json's bboxes must not change what the
    pipeline treats as its geographic scope (the audit found five call
    sites trusting array position; dimensions.crop() is now name-keyed
    and the others delegate)."""
    import backend.util.dimensions as dimensions
    cfg = tmp_data_dir / "in" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "dimensions.json").write_text(json.dumps(
        {"start": "2099-01-01", "end": "2099-01-02",
         "bboxes": [{"name": "me", "lat_south": 10},
                    {"name": "crop", "lat_south": 9}]}))
    dimensions.reset_config_cache()
    try:
        assert dimensions.crop() == {"name": "crop", "lat_south": 9}
    finally:
        dimensions.reset_config_cache()
