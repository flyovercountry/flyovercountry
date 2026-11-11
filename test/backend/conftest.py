"""Shared test fixtures."""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest

import backend.util.dimensions as _dimensions
import backend.util.roles as _roles
from backend.util.paths import config_dir as _config_dir


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Temporary data directory. Creates build/ingest/ (raw traces); tests
    create the subdirs they need (build/etl/, build/lookup/, ...) themselves.
    FLY_BUILD_DIR (the export/site output, unrelated to data/build/'s working
    state despite the name) points at a separate site/ subdir to avoid
    colliding with build/ingest/ above.

    The repo's real dimensions.json/roles.json are copied into the tmp tree
    and the lazy config caches reset, so code under test reads config through
    the same FLY_DATA_DIR mechanism as everything else (tests that want
    synthetic config overwrite the copies and reset the caches again).
    Before the config readers became lazy, tests silently read production
    config through paths frozen at import time; this keeps their observed
    values identical while making the mechanism honest."""
    real_cfg = _config_dir()          # resolved BEFORE the env override below
    cfg = tmp_path / "in" / "config"
    cfg.mkdir(parents=True)
    for name in ("dimensions.json", "roles.json"):
        src = real_cfg / name
        if src.exists():
            shutil.copy(src, cfg / name)
    (tmp_path / "build" / "ingest").mkdir(parents=True)
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    saved = {}
    for var, val in [("FLY_DATA_DIR", str(tmp_path)),
                     ("FLY_BUILD_DIR", str(site_dir))]:
        saved[var] = os.environ.get(var)
        os.environ[var] = val
    _dimensions.reset_config_cache()
    _roles.reset_taxonomy_cache()
    yield tmp_path
    for var, old in saved.items():
        if old is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = old
    _dimensions.reset_config_cache()
    _roles.reset_taxonomy_cache()
