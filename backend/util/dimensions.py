"""Config reader for dimensions.json — single source of truth for dates and
crop bounds. 1:1 with the file it reads; see backend.util.roles for the
sibling reader of roles.json."""
from __future__ import annotations

import json
from datetime import date, timedelta

from backend.util.paths import config_dir

_config: dict | None = None


def _load() -> dict:
    # config_dir() is resolved lazily, per first use — NOT at import time —
    # so FLY_DATA_DIR set after import (e.g. by a test fixture) is honoured,
    # matching paths.py's per-call env-var contract.
    global _config
    if _config is None:
        _config = json.loads((config_dir() / "dimensions.json").read_text())
    return _config


def reset_config_cache() -> None:
    """Forget the cached dimensions.json (mirrors loader.reset_exclusion_cache);
    the next accessor call re-resolves the path and re-reads the file."""
    global _config
    _config = None


def start() -> str:
    return _load()["start"]


def end() -> str:
    return _load()["end"]


def whatif_start() -> str:
    """Earliest refusal date offered in What If mode (start/end may go earlier)."""
    return _load().get("whatif_start", start())


def bboxes() -> list[dict]:
    return _load()["bboxes"]


def crop() -> dict:
    """The crop bbox, selected BY NAME — array position is not a contract
    (a reorder of dimensions.json's bboxes list must not silently change
    what the pipeline treats as its geographic scope)."""
    return next(b for b in bboxes() if b.get("name") == "crop")


def dates() -> list[str]:
    s = date.fromisoformat(start())
    e = date.fromisoformat(end())
    out = []
    d = s
    while d <= e:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def n_days() -> int:
    s = date.fromisoformat(start())
    e = date.fromisoformat(end())
    return (e - s).days + 1
