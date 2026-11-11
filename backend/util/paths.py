"""Shared path resolution for the fly pipeline."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("FLY_DATA_DIR", "data"))


def raw_dir() -> Path:
    """Downloaded ADS-B traces (1.2 GB, irreplaceable). Working state, not an
    input — lives under data/build/, not data/in/."""
    return data_dir() / "build" / "ingest"


def etl_dir() -> Path:
    """Stage intermediates. Working state — lives under data/build/."""
    return data_dir() / "build" / "etl"


def external_dir() -> Path:
    return data_dir() / "in" / "external"


def lookup_dir() -> Path:
    """gen assets (mil list, airports, raster, boundaries). Working state —
    lives under data/build/."""
    return data_dir() / "build" / "lookup"


def build_dir() -> Path:
    """Export output — the site's data files, written directly into THE SHIP
    ARTIFACT (data/out/; gitignored, mkdir'd then overwritten file-by-file
    on each export — NOT wiped first, so a file this run no longer writes
    stays behind from a prior run; see run_export's stale-sightings check
    for one place that matters). No intermediate: this is the same tree
    `vite build` writes into."""
    override = os.environ.get("FLY_BUILD_DIR")
    return Path(override) if override else data_dir() / "out" / "data"


def config_dir() -> Path:
    """Git-tracked pipeline config (dimensions.json, roles.json). Input, never
    written by the pipeline — lives under data/in/, not data/build/."""
    return data_dir() / "in" / "config"


def frontend_config_dir() -> Path:
    """Git-tracked focus-config JSONs, served directly by frontend/serve.mjs."""
    return data_dir() / "in" / "config" / "frontend"
