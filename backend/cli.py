"""fly CLI — US military flight tracker."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from backend.util import dimensions
from backend.util.paths import (
    external_dir, raw_dir, etl_dir, lookup_dir, build_dir, frontend_config_dir,
)


@click.group()
def main():
    """US military flight tracker."""


# `./run ingest --direct` sets FLY_NET_DIRECT=1 on the sidecar (which then
# configures no tunnel) and mirrors it onto the ingest service purely so the
# warning below can be printed. The value grants nothing here: egress is
# decided by the sidecar whose network namespace ingest shares.
DIRECT_MODE_WARNING = (
    "WARNING: direct mode (no tunnel). The adsb.lol archives are fetched "
    "from GitHub, which will log this machine's address.")


def direct_mode() -> bool:
    """True only for the exact value `1`, matching the sidecar's own test."""
    return os.environ.get("FLY_NET_DIRECT") == "1"


# ---------------------------------------------------------------------------
# fly status
# ---------------------------------------------------------------------------

@main.command()
@click.option("--check", "check_ctx", default=None, metavar="CONTEXT",
              help="Precondition gate: verify the inputs a flow/stage needs "
                   "exist; exit non-zero naming the fix. Used by the "
                   "./run commands so bash never re-derives file state.")
@click.argument("tokens", nargs=-1)
def status(check_ctx, tokens):
    """Report pipeline state: config, files, containers.

    With --check CONTEXT (one of: all, ingest, etl, serve, gen),
    runs the precondition gate instead of the human report:
    exits 0 if the inputs that flow/stage needs are present, else prints
    each missing prerequisite with its fix and exits 1. TOKENS are the same
    stage/subcommand tokens the wrapper was given (e.g. `--check etl split`)."""
    if check_ctx is not None:
        problems = _gate(check_ctx, list(tokens))
        if problems:
            click.echo(f"cannot run '{check_ctx}': unmet preconditions", err=True)
            for p in problems:
                click.echo(f"  {p}", err=True)
            sys.exit(1)
        return
    _print_config()
    _print_source()
    _print_lookup()
    _print_raw()
    _print_etl()
    _print_build()
    _print_containers()


# ---------------------------------------------------------------------------
# Precondition gate (fly status --check) — single source of truth for "can
# this flow/stage run yet?", shared by every ./run command so the bash
# wrappers never re-derive file state. Each helper returns a list of human
# problem lines (each naming its fix); an empty list means "good to go".
# ---------------------------------------------------------------------------

_GATE_CONTEXTS = ["all", "ingest", "etl", "serve", "gen"]


def _gate(context, tokens):
    if context not in _GATE_CONTEXTS:
        return [f"unknown context '{context}' "
                f"(valid: {', '.join(_GATE_CONTEXTS)})"]
    if context == "all":
        if tokens:
            return [f"unknown 'all' argument(s): {', '.join(tokens)}"]
        return _missing_required_sources()
    if context == "ingest":
        return _ingest_gate(tokens)
    if context == "etl":
        return _etl_gate(tokens)
    if context == "serve":
        if tokens:
            return [f"unknown serve argument(s): {', '.join(tokens)}"]
        return _build_present_gate()
    if context == "gen":
        return _gen_gate(tokens)
    return []


def _source_problem(spec):
    """None if the source exists; else a gen-style 'missing -> download'
    problem line."""
    if _source_entry_path(spec) is not None:
        return None
    return (f"required source data/in/external/{spec['path']} missing "
            f"-> download from {spec['url']}")


def _missing_required_sources(skip=()):
    out = []
    for key, spec in _SOURCES.items():
        if spec.get("group") != "REQUIRED" or key in skip:
            continue
        problem = _source_problem(spec)
        if problem:
            out.append(problem)
    return out


def _ingest_gate(tokens):
    valid = {"merge-batches", "downsample"}
    bad = [t for t in tokens if t not in valid]
    if bad:
        return [f"unknown ingest argument(s): {', '.join(bad)} "
                f"(valid: {', '.join(sorted(valid))})"]
    # A fresh download wants its source files present; the maintenance
    # sub-commands operate on already-downloaded raw data and need nothing.
    # aircraft-db is REQUIRED for the pipeline but ingest never reads it —
    # its consumer is the types stage, whose gate checks it instead.
    return (_missing_required_sources(skip={"aircraft-db"})
            if not tokens else [])


def _generated_missing():
    gd = lookup_dir()
    return [(name, cmd) for name, cmd in _GENERATED if not (gd / name).exists()]


def _raw_incomplete():
    raw = raw_dir()
    dates = dimensions.dates()
    missing = [ds for ds in dates
               if not (raw / f"date={ds}" / "aircraft.parquet").exists()]
    if missing:
        return [f"raw data incomplete: {len(missing)}/{len(dates)} dates "
                f"missing -> ./run ingest"]
    return []


def _stage_done(stage):
    from backend.pipeline.etl.run import STAGE_OUTPUTS, EXPORT_OUTPUT, THEATER_COLUMN
    if stage == "export":
        return (build_dir() / EXPORT_OUTPUT).exists()
    out = STAGE_OUTPUTS.get(stage)
    if not out:
        return False
    path = etl_dir() / out
    if not path.exists():
        return False
    if stage == "theater":       # in-place; prove it ran by its added column
        import pyarrow as pa
        import pyarrow.parquet as pq
        try:
            return THEATER_COLUMN in set(pq.read_schema(path).names)
        except (OSError, pa.ArrowException):
            return False
    return True


def _etl_gate(tokens):
    from backend.pipeline.etl.run import STAGE_ORDER, STAGES
    try:
        stages = _resolve_etl_steps(tokens)
    except click.BadArgumentUsage as e:
        return [str(e)]
    if not stages:
        return []
    problems = [f"{name} missing -> {cmd}"
                for name, cmd in _generated_missing()]
    problems += _raw_incomplete()
    if "types" in stages:
        # types.py raises mid-stage without the Mictronics zip and only
        # warns without the aircraft DB; both are REQUIRED, so the gate
        # names the fix up front.
        for key in ("mictronics", "aircraft-db"):
            problem = _source_problem(_SOURCES[key])
            if problem:
                problems.append(problem)
    # Upstream completeness for EVERY requested stage, not just the
    # earliest: a non-contiguous request (e.g. `partition flight`) must not
    # silently skip the stages in the gap. A missing upstream that is
    # itself planned earlier in this same run is fine.
    planned = set(stages)
    stale = dict(_stale_stage_outputs())
    flagged = set()
    for s in stages:
        for upstream in STAGE_ORDER[:STAGE_ORDER.index(s)]:
            if upstream not in STAGES or upstream in planned \
                    or upstream in flagged:
                continue
            if not _stage_done(upstream):
                flagged.add(upstream)
                problems.append(f"stage '{upstream}' must run before '{s}' "
                                f"-> ./run etl {upstream}")
            elif upstream in stale:
                # Existence alone is not enough: an upstream whose output
                # predates its own upstream's output is stale, and running
                # the requested stage would silently consume old data
                # (e.g. a lone `flight` re-run strips the attribute/theater columns
                # while theater's own done-marker file survives).
                flagged.add(upstream)
                problems.append(f"stage '{upstream}' looks stale "
                                f"({stale[upstream]}) -> ./run etl {upstream}")
    return problems


def _build_present_gate():
    problems = []
    if not (build_dir() / "manifest.json").exists():
        problems.append(f"{build_dir()}/manifest.json missing -> ./run etl")
    return problems



def _gen_gate(tokens):
    # Only targets backed by a REQUIRED source can hard-fail; mil and roles
    # degrade gracefully (roles needs nothing but the git-tracked
    # roles.json), so neither imposes a precondition.
    needs = {"mil": [], "airports": ["airports"],
             "raster": ["natural-earth"], "boundaries": ["natural-earth"],
             "roles": []}
    want = list(tokens) or ["mil", "airports", "raster", "boundaries", "roles"]
    problems = []
    for t in want:
        if t not in needs:
            problems.append(f"unknown gen target '{t}' "
                            f"(valid: {', '.join(needs)})")
            continue
        for key in needs[t]:
            spec = _SOURCES.get(key)
            if spec and _source_entry_path(spec) is None:
                problems.append(f"gen {t} needs data/in/external/{spec['path']} "
                                f"-> download from {spec['url']}")
    return problems


def _print_config():
    click.echo("Pipeline config")
    click.echo(f"  Date range:  {dimensions.start()} .. {dimensions.end()}  "
               f"({dimensions.n_days()} days)")
    for b in dimensions.bboxes():
        click.echo(f"  Bbox {b['name']:8s} lat {b['lat_south']}..{b['lat_north']}  "
                   f"lon {b['lon_west']}..{b['lon_east']}")
    click.echo()


def _check_file(path: Path, label: str | None = None,
                fix: str | None = None, indent: int = 2) -> bool:
    """Report one artifact; when missing, name the command that produces it."""
    name = label or path.name
    pad = " " * indent
    if path.exists():
        if path.is_file():
            size = path.stat().st_size
            if size > 1_000_000:
                click.echo(f"{pad}{name:<30s} ok  ({size / 1e6:.1f} MB)")
            else:
                click.echo(f"{pad}{name:<30s} ok")
        else:
            click.echo(f"{pad}{name:<30s} ok  (dir)")
        return True
    hint = f"  -> {fix}" if fix else ""
    click.echo(f"{pad}{name:<30s} MISSING{hint}")
    return False


def _source_entry_path(spec) -> Path | None:
    """Resolve a _SOURCES path spec (glob allowed) to the newest match."""
    src = external_dir()
    rel = spec["path"].rstrip("/")
    if "*" in rel:
        matches = sorted(src.glob(rel))
        return matches[-1] if matches else None
    p = src / rel
    return p if p.exists() else None


def _print_source():
    click.echo("Source files (data/in/external; manifest: data/in/external/README.md)")
    for group in _SOURCE_GROUPS:
        entries = [s for s in _SOURCES.values() if s["group"] == group]
        if not entries:
            continue
        click.echo(f"  {group}")
        for spec in entries:
            p = _source_entry_path(spec)
            label = spec["path"]
            if p is not None:
                _check_file(p, label=label, indent=4)
            else:
                click.echo(f"    {label:<30s} MISSING")
                click.echo(f"    {'':<30s}   get: {spec['url']}")
    click.echo()


# Generated artifacts and the host command that (re)creates each.
_GENERATED = [
    ("mil_icao24.txt", "./run gen mil"),
    ("airports.pkl", "./run gen airports"),
    ("airspace/airspace.npy", "./run gen raster"),
    ("airspace/iso2_to_id.json", "./run gen raster"),
    ("airspace/id_to_iso2.json", "./run gen raster"),
    ("airspace/crop.json", "./run gen raster"),
    ("boundaries.geojson", "./run gen boundaries"),
    ("roles.html", "./run gen roles"),
    ("roles.reference.json", "./run gen roles"),
]


def _print_lookup():
    click.echo("Lookup files (data/build/lookup)")
    gd = lookup_dir()
    for name, cmd in _GENERATED:
        p = gd / name
        if p.exists() and name == "mil_icao24.txt":
            n = sum(1 for line in p.read_text().splitlines() if line.strip())
            click.echo(f"  {name:<30s} ok  ({n:,} entries)")
        elif p.exists() and name == "boundaries.geojson":
            try:
                fc = json.loads(p.read_text())
                click.echo(f"  {name:<30s} ok  ({len(fc['features'])} countries)")
            except (OSError, ValueError, KeyError, TypeError):
                _check_file(p, fix=cmd)
        else:
            _check_file(p, label=name, fix=cmd)
    click.echo()


def _print_raw():
    click.echo("Raw data")
    raw = raw_dir()
    all_dates = dimensions.dates()
    complete = []
    missing = []
    incomplete = []
    for ds in all_dates:
        seg_path = raw / f"date={ds}" / "aircraft.parquet"
        if not seg_path.exists():
            missing.append(ds)
        elif seg_path.stat().st_size == 0:
            incomplete.append(ds)
        else:
            complete.append(ds)
    click.echo(f"  Complete:    {len(complete)}/{len(all_dates)}")
    if missing:
        if len(missing) <= 5:
            click.echo(f"  Missing:     {', '.join(missing)}  -> ./run ingest")
        else:
            click.echo(f"  Missing:     {len(missing)} dates "
                        f"({missing[0]} .. {missing[-1]})  -> ./run ingest")
    if incomplete:
        click.echo(f"  Incomplete:  {', '.join(incomplete)}  -> ./run ingest")
    if not missing and not incomplete:
        click.echo("  Missing:     (none)")
    click.echo()


def _stale_stage_outputs():
    """Mtime-ordering check: outputs whose mtime is older than an
    earlier stage's, walking STAGE_OUTPUTS in its (STAGE_ORDER-matching)
    pipeline order. `_stage_done`/the precondition gate only check
    existence, so a stage re-run after only SOME of its upstreams were
    re-run (stale intermediate) is otherwise invisible. Missing files are
    skipped without breaking the chain (optional stages, a partially-run
    pipeline). Read-only: stat() only, no data access. Returns (stage,
    reason) pairs; `./run status` prints them as warnings, and the etl
    precondition gate BLOCKS a requested stage whose (unplanned) upstream
    appears here, since running against a stale intermediate silently
    consumes old data."""
    from backend.pipeline.etl.run import STAGE_OUTPUTS
    ed = etl_dir()
    out_pairs = []
    prev = None   # (stage, path, mtime) of the last stage output that exists
    for stage, out in STAGE_OUTPUTS.items():
        if not out:
            continue
        path = ed / out
        if not path.exists():
            continue
        mtime = path.stat().st_mtime
        if prev is not None and mtime < prev[2]:
            out_pairs.append((stage,
                              f"{out} is older than {prev[1].name} "
                              f"(stage '{prev[0]}')"))
        prev = (stage, path, mtime)
    return out_pairs


def _print_etl():
    click.echo("ETL data (data/build/etl)")
    ed = etl_dir()

    # timeline phase artifacts, in stage order
    for name, stage in [
        ("aircraft.parquet", "types"), ("exclusions.parquet", "denoise"),
        ("intervals.parquet", "partition"),
        ("blackouts.parquet", "partition"),
        ("edges.parquet", "edge"),
        ("verdicts.parquet", "split"),
    ]:
        _check_file(ed / name, fix=f"./run etl {stage}")

    _check_file(ed / "flight_dropped.parquet", fix="./run etl flight")

    flights_path = ed / "flights.parquet"
    if flights_path.exists():
        import pyarrow as pa
        import pyarrow.parquet as pq
        try:
            meta = pq.read_metadata(flights_path)
            click.echo(f"  {'flights.parquet':<30s} ok  "
                       f"({meta.num_rows:,} flights)")
            cols = set(pq.read_schema(flights_path).names)
            for col, label, stage in [
                    ("traversed_countries", "traversed", "attribute"),
                    ("th_chain", "theater", "theater")]:
                status = ("ok" if col in cols
                          else f"MISSING  -> ./run etl {stage}")
                click.echo(f"  {label:<30s} {status}")
        except (OSError, pa.ArrowException):
            _check_file(flights_path, fix="./run etl flight")
    else:
        _check_file(flights_path, fix="./run etl flight")

    for name, stage in [("traces.parquet", "trace"),
                        ("sighting_times.parquet", "attribute")]:
        _check_file(ed / name, fix=f"./run etl {stage}")

    stale = _stale_stage_outputs()
    if stale:
        click.echo("  Staleness warnings:")
        for stage, reason in stale:
            click.echo(f"    ! {reason} -> possibly stale, "
                       f"re-run ./run etl {stage}")

    click.echo()


def _print_build():
    click.echo("Build")
    bd = build_dir()
    manifest = bd / "manifest.json"
    if manifest.exists():
        try:
            m = json.loads(manifest.read_text())
            built = datetime.fromtimestamp(
                manifest.stat().st_mtime, tz=timezone.utc,
            ).strftime("%Y-%m-%d %H:%M")
            click.echo(f"  manifest.json         ok  "
                       f"(data {m.get('data_start', '?')}..{m.get('data_end', '?')}, "
                       f"built {built})")
        except (OSError, ValueError, AttributeError):
            _check_file(manifest, fix="./run etl export")
    else:
        _check_file(manifest, fix="./run etl export")

    for name in ["routes.geojson", "flights.json", "role_catalog.json"]:
        _check_file(bd / name, fix="./run etl export")

    # Config files are git-tracked in data/in/config/frontend/, not generated —
    # missing here means a bad checkout, not a stale export.
    cd = frontend_config_dir()
    for name in ["countries.modes.json", "common.texts.json",
                 "country.reference.json"]:
        _check_file(cd / name, fix="git checkout -- data/in/config/frontend/")
    click.echo()


def _print_containers():
    click.echo("Docker containers")
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--format",
             "{{.Name}}\t{{.State}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t")
                name = parts[0] if len(parts) > 0 else "?"
                state = parts[1] if len(parts) > 1 else "?"
                status_str = parts[2] if len(parts) > 2 else ""
                click.echo(f"  {name:<24s} {state:<12s} {status_str}")
        else:
            click.echo("  (none running)")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        click.echo("  (docker not available)")
    click.echo()


# ---------------------------------------------------------------------------
# fly clean
# ---------------------------------------------------------------------------

@main.command()
@click.confirmation_option(prompt="Remove lookup/ and build/ data?")
def clean():
    """Remove lookup assets and export output (data/build/lookup/,
    data/out/data/ — the latter only, not the built frontend app alongside
    it). Keeps data/build/ingest/ and data/in/external/."""
    for d in [lookup_dir(), build_dir()]:
        if d.exists():
            shutil.rmtree(d)
            click.echo(f"removed {d}")
        else:
            click.echo(f"{d} does not exist")


# ---------------------------------------------------------------------------
# fly gen <target>
# ---------------------------------------------------------------------------

@main.group(invoke_without_command=True)
@click.pass_context
def gen(ctx):
    """Generate static assets from source data.

    With no subcommand, runs every pipeline-consumed generator in
    dependency order: mil, airports, raster, boundaries, roles."""
    if ctx.invoked_subcommand is None:
        for cmd in (mil, airports, raster, boundaries, roles):
            click.echo(f"[gen] {cmd.name}")
            ctx.invoke(cmd)


def _require(path: Path, label: str):
    if not path.exists():
        click.echo(f"error: {label} not found at {path}", err=True)
        sys.exit(1)


# Source-data manifest (mirrors data/in/external/README.md). Groups:
#   REQUIRED  the pipeline cannot produce its outputs without it
#   OPTIONAL  consumed when present; output degrades gracefully without it
# All sources are manual downloads; nothing is fetched automatically.
# `path` is relative to data/in/external/ (glob allowed). `howto` lines are the
# download instructions the `gen` commands print when a REQUIRED input is
# missing.
_SOURCES = {
    "aircraft-db": {
        "group": "REQUIRED",
        "path": "aircraft/aircraft-database-complete-*.csv",
        "url": "https://www.adsb.lol/data/",
        "consumer": "etl types stage",
        "howto": [
            "Download the latest aircraft database from adsb.lol:",
            "  https://www.adsb.lol/data/",
            "Look for aircraft-database-complete-YYYY-MM.csv and place it in",
            "data/in/external/aircraft/ . The newest file (by name) is used "
            "automatically.",
        ],
    },
    "airports": {
        "group": "REQUIRED",
        "path": "airports.csv",
        "url": "https://davidmegginson.github.io/ourairports-data/airports.csv",
        "consumer": "fly gen airports",
        "howto": [
            "Download the OurAirports airport database:",
            "  https://davidmegginson.github.io/ourairports-data/airports.csv",
            "Save it as data/in/external/airports.csv .",
        ],
    },
    "natural-earth": {
        "group": "REQUIRED",
        "path": "ne_10m_admin_0_countries/",
        "url": "https://naciscdn.org/naturalearth/10m/cultural/"
               "ne_10m_admin_0_countries.zip",
        "consumer": "fly gen raster, fly gen boundaries",
        "howto": [
            "Download Natural Earth 1:10m admin-0 countries:",
            "  https://www.naturalearthdata.com/downloads/10m-cultural-vectors/",
            "  (direct: https://naciscdn.org/naturalearth/10m/cultural/"
            "ne_10m_admin_0_countries.zip )",
            "Unzip it into data/in/external/ne_10m_admin_0_countries/ so the .shp,",
            ".dbf, .shx, and .prj files sit directly inside that directory.",
        ],
    },
    "mictronics": {
        "group": "REQUIRED",
        "path": "aircraft/mictronics_indexedDB.zip",
        "url": "https://www.mictronics.de/aircraft-database/indexedDB_old.php",
        "consumer": "etl types stage, fly gen mil",
        "howto": [
            "Download the Mictronics aircraft database (manual browser",
            "download; the page serves the zip directly):",
            "  https://www.mictronics.de/aircraft-database/indexedDB_old.php",
            "Save it as data/in/external/aircraft/mictronics_indexedDB.zip .",
            "See data/in/external/README.md for provenance details.",
        ],
    },
    "plane-alert": {
        "group": "OPTIONAL",
        "path": "aircraft/plane-alert-db.csv",
        "url": "https://raw.githubusercontent.com/sdr-enthusiasts/"
               "plane-alert-db/main/plane-alert-db.csv",
        "consumer": "etl types stage",
    },
    "basic-ac": {
        "group": "OPTIONAL",
        "path": "aircraft/basic-ac-db.json.gz",
        "url": "https://downloads.adsbexchange.com/downloads/basic-ac-db.json.gz",
        "consumer": "fly gen mil, etl types stage",
    },
    "doc8643": {
        "group": "OPTIONAL",
        "path": "aircraft/doc8643_aircraft_types.json",
        "url": "https://www4.icao.int/doc8643/External/AircraftTypes (POST)",
        "consumer": "etl types stage (type validation + rotary backstop)",
    },
    "osm-mil-airports": {
        "group": "OPTIONAL",
        "path": "osm_military_airports.csv",
        "url": "https://overpass-api.de/ (Overpass API query; see "
               "data/in/external/README.md)",
        "consumer": "fly gen airports",
    },
}

_SOURCE_GROUPS = ["REQUIRED", "OPTIONAL"]


def _require_source(path: Path, source_key: str):
    """Exit with download instructions if a required source file is missing."""
    if path.exists():
        return
    spec = _SOURCES[source_key]
    click.echo(f"error: required input not found at data/in/external/{spec['path']}",
               err=True)
    click.echo("", err=True)
    for line in spec["howto"]:
        click.echo("  " + line, err=True)
    sys.exit(1)


def _gen_mil() -> None:
    """Generate mil_icao24.txt from the Mictronics + basic-ac-db per-hex
    military flags (the union of both is the whole mechanism — see
    backend/pipeline/gen/mil.py). Both sources are optional at runtime; the script
    itself warns to stderr and carries on if one is missing. Shared by the
    standalone `fly gen mil` command and `fly run ingest` (which needs the
    output before it can filter incoming traffic)."""
    src = external_dir() / "aircraft"
    out_path = lookup_dir() / "mil_icao24.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "backend/pipeline/gen/mil.py", str(out_path)]
    for flag, name in (("--mictronics", "mictronics_indexedDB.zip"),
                       ("--basic-ac", "basic-ac-db.json.gz")):
        p = src / name
        if p.exists():
            cmd += [flag, str(p)]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(result.returncode)


@gen.command()
def mil():
    """Generate mil_icao24.txt from the Mictronics + basic-ac-db military
    flags. Runs on bare Python."""
    _gen_mil()


@gen.command()
def airports():
    """Generate airports.pkl from OurAirports CSV."""
    csv_path = external_dir() / "airports.csv"
    _require_source(csv_path, "airports")
    out_path = lookup_dir() / "airports.pkl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    _run_script("backend/pipeline/gen/airports.py",
                "--csv", str(csv_path), "--out", str(out_path))


@gen.command()
def raster():
    """Generate airspace.npy and lookup files from Natural Earth."""
    shp_dir = external_dir() / "ne_10m_admin_0_countries"
    _require_source(shp_dir, "natural-earth")
    out_dir = lookup_dir() / "airspace"
    out_dir.mkdir(parents=True, exist_ok=True)

    _run_script("backend/pipeline/gen/raster.py",
                "--shapefile",
                str(shp_dir / "ne_10m_admin_0_countries.shp"),
                "--out-dir", str(out_dir))


@gen.command()
def boundaries():
    """Generate boundaries.geojson from Natural Earth."""
    shp_dir = external_dir() / "ne_10m_admin_0_countries"
    _require_source(shp_dir, "natural-earth")
    lookup_dir().mkdir(parents=True, exist_ok=True)

    _run_script("backend/pipeline/gen/boundaries.py")


@gen.command()
def roles():
    """Generate roles.html and roles.reference.json from the roles
    taxonomy. Runs on bare Python, like mil — no external source required."""
    from backend.pipeline.gen.roles_page import main as _roles_main
    _roles_main()


def _run_script(*args: str):
    """Run a Python script as a subprocess."""
    cmd = [sys.executable, *args]
    click.echo(f"running: {' '.join(args)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# fly run <step>
# ---------------------------------------------------------------------------

@main.group()
def run():
    """Run pipeline steps."""


@run.command()
@click.option("--start", type=str, default=None,
              help="Override start date (default: dimensions.json)")
@click.option("--end", type=str, default=None,
              help="Override end date (default: dimensions.json)")
@click.option("--sample-sec", type=int, default=10, show_default=True,
              help="Trace downsample interval in seconds (0 = full fidelity)")
@click.option("--all", "-a", "all_icaos", is_flag=True,
              help="Ingest every aircraft (skip the mil/gov icao24 filter)")
def ingest(start, end, sample_sec, all_icaos):
    """Download and filter raw ADS-B data. Idempotent — skips existing dates."""
    # Ingest fetches from external services and must never run untunneled.
    # FLY_TUNNELED=1 is set only on compose's ingest service (shares the
    # wireguard sidecar's network namespace) — its presence is our proxy
    # for "actually running inside the tunneled container," which catches
    # accidental host-python runs and non-sidecar container runs alike.
    # There is deliberately no override flag.
    if os.environ.get("FLY_TUNNELED") != "1":
        raise click.ClickException(
            "ingest refused: FLY_TUNNELED is not set, so this doesn't "
            "look like the tunneled sidecar container. Run ingest via "
            "`docker compose run --rm ingest` (or `./run ingest`), "
            "not directly.")
    if direct_mode():
        click.echo(DIRECT_MODE_WARNING, err=True)
    if not all_icaos:
        click.echo("generating mil_icao24.txt")
        _gen_mil()
        _require(lookup_dir() / "mil_icao24.txt", "mil_icao24.txt")
    from backend.pipeline.ingest.ingest import run_ingest
    s = start or dimensions.start()
    e = end or dimensions.end()
    run_ingest(s, e, sample_sec=sample_sec, all_icaos=all_icaos)


def _resolve_etl_steps(steps):
    """Parse `etl` tokens (phase group or stage names) into stage names in
    canonical order. No tokens -> all stages.

    Enforces strict pipeline order: each token must begin after the previous
    token ended, so tokens given out of order (or overlapping/duplicated) are
    rejected rather than silently reordered. Raises click.BadArgumentUsage on
    an unknown token or an ordering violation."""
    from backend.pipeline.etl.run import STAGES, GROUPS, STAGE_ORDER
    if not steps:
        return list(STAGE_ORDER)

    resolved = []      # stage names, in typed (== canonical) order
    last_idx = -1      # highest canonical index consumed so far
    for tok in steps:
        if tok in GROUPS:
            span = list(GROUPS[tok])
        elif tok in STAGES:
            span = [tok]
        else:
            raise click.BadArgumentUsage(
                f"unknown step '{tok}'. Valid: a group "
                f"({', '.join(GROUPS)}) or a stage ({', '.join(STAGES)}).")
        first = STAGE_ORDER.index(span[0])
        if first <= last_idx:
            raise click.BadArgumentUsage(
                f"'{tok}' is out of order: give etl stages in pipeline order "
                f"({' '.join(STAGE_ORDER)}), each after the previous. "
                f"Reorder your arguments.")
        last_idx = STAGE_ORDER.index(span[-1])
        resolved.extend(span)
    return resolved


@run.command()
@click.argument("steps", nargs=-1)
def etl(steps):
    """Run etl stages (correct flights via blackout split/join verdicts).

    With no STEPS, runs all implemented stages in canonical order. Tokens
    may be phase groups (timeline, landing, publish) or stage names;
    they must be given in canonical order, each starting after the
    previous ended — out-of-order, overlapping, or duplicate tokens are
    rejected, not silently reordered or deduplicated:

        run etl timeline           the whole timeline phase
        run etl types partition    individual stages

    Phases and stages:
        preprocess types denoise
        timeline   partition edge split flight
        landing    train attribute theater
        publish    trace export
    """
    from backend.pipeline.etl.run import run_stages
    run_stages(_resolve_etl_steps(steps))


@run.command("merge-batches")
def merge_batches():
    """Merge leftover trace batch files from interrupted ingest."""
    from backend.pipeline.ingest.ingest import run_merge_batches
    run_merge_batches()


@run.command()
@click.option("--interval", type=int, default=10, show_default=True,
              help="Downsample interval in seconds")
@click.option("--start", type=str, default=None,
              help="First date=YYYY-MM-DD partition to thin "
                   "[default: the configured window start].")
@click.option("--end", type=str, default=None,
              help="Last partition to thin [default: the configured "
                   "window end].")
@click.option("--backup-dir", type=str, default=None,
              help="Backup directory (default: data/build/ingest_full)")
def downsample(interval, backup_dir, start, end):
    """Back up full-fidelity traces and replace with downsampled versions.

    NOTE: disk usage temporarily INCREASES — originals are preserved under
    the backup dir (default data/build/ingest_full/) and space is only
    reclaimed once you delete that directory by hand after verifying the
    thinned data. Defaults to the configured analysis window; pass
    --start/--end to reach partitions outside it.
    """
    from backend.pipeline.ingest.downsample import run_downsample
    run_downsample(
        interval_sec=interval, backup_dir=backup_dir,
        start=start or dimensions.start(), end=end or dimensions.end(),
    )



