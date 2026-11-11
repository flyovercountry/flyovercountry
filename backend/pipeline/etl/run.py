"""etl stage registry.

Maps stage names to runners and groups stages into the four pipeline
phases. The CLI (`fly run etl`) resolves group/stage tokens against
this module; stages always execute in STAGE_ORDER.

Phases:
  preprocess classify aircraft, scrub bad reports (types, denoise)
  timeline   dataset -> each aircraft's timeline skeleton (partition,
             edge, split, flight)
  landing    ground truth: train the attribution model, attribute
             stays/countries, theater relevance (train,
             attribute, theater)
  publish    frontend artifacts (trace, export)

"""
from backend.util.log import log


def _types():
    from backend.pipeline.etl.preprocess.types import build_aircraft_table
    build_aircraft_table()


def _denoise():
    from backend.pipeline.etl.preprocess.denoise import run_denoise
    run_denoise()


def _partition():
    from backend.pipeline.etl.timeline.partition import run_partition
    run_partition()


def _edge():
    from backend.pipeline.etl.timeline.edge import run_edge
    run_edge()


def _split():
    from backend.pipeline.etl.timeline.split import run_split
    run_split()


def _flight():
    from backend.pipeline.etl.timeline.flight import run_flight
    run_flight()


def _train():
    from backend.pipeline.etl.landing.train import run_train
    run_train()


def _attribute():
    from backend.pipeline.etl.landing.attribute import run_attribute
    run_attribute()


def _theater():
    from backend.pipeline.etl.landing.theater import run_theater
    run_theater()


def _trace():
    from backend.pipeline.etl.publish.trace import run_trace
    run_trace()


def _export():
    from backend.pipeline.etl.publish.export import run_export
    run_export()


STAGES = {
    "types": _types,
    "denoise": _denoise,
    "partition": _partition,
    "edge": _edge,
    "split": _split,
    "flight": _flight,
    "train": _train,
    "attribute": _attribute,
    "theater": _theater,
    "trace": _trace,
    "export": _export,
}

GROUPS = {
    # Classify the fleet and scrub bad position reports before any
    # flight assembly.
    "preprocess": ["types", "denoise"],
    "timeline": ["partition", "edge", "split", "flight"],
    # Analysis only — attribute/theater read full-resolution raw points.
    "landing": ["train", "attribute", "theater"],
    # trace's RDP polylines are render-prep consumed only by export.
    "publish": ["trace", "export"],
}

STAGE_ORDER = (GROUPS["preprocess"] + GROUPS["timeline"]
               + GROUPS["landing"] + GROUPS["publish"])

# every ordered stage must have a runner (and vice versa) — catches a
# stage name added to GROUPS before its implementation exists
assert set(STAGE_ORDER) == set(STAGES), (
    set(STAGE_ORDER) ^ set(STAGES))

# Primary output artifact per stage, relative to data/build/etl/. Single source of
# truth for "has this stage run?", consumed by `fly status` and the
# precondition gate (`fly status --check`). Several special cases:
#   flight    writes flights.parquet AND flight_dropped.parquet (the ids it
#             dropped for having too few usable points). The marker is
#             flight_dropped.parquet because flights.parquet is mutated in
#             place downstream (attribute/theater add columns) — a marker
#             must be a file no later stage touches or the staleness
#             chain's mtime ordering breaks.
#   theater   writes no new file — it adds columns to flights.parquet in place,
#           so completion is a column check (THEATER_COLUMN), not a file check.
#   export  writes to data/out/data/ (build_dir), not data/build/etl/ (see EXPORT_OUTPUT).
STAGE_OUTPUTS = {
    "types": "aircraft.parquet",
    "denoise": "exclusions.parquet",
    "partition": "intervals.parquet",
    "edge": "edges.parquet",
    "split": "verdicts.parquet",
    "flight": "flight_dropped.parquet",
    "train": "attribution_model.pkl",
    # Ordered to match STAGE_ORDER: `fly status`'s staleness check
    # (cli.py) walks this dict in order and needs consecutive entries to
    # mean consecutive stages.
    "attribute": "sighting_times.parquet",
    "theater": "flights.parquet",
    "trace": "traces.parquet",
    "export": None,
}

# the theater stage adds columns to flights.parquet in place; the column proves it ran.
THEATER_COLUMN = "th_chain"
# export's completion marker, relative to build_dir (data/out/data/).
EXPORT_OUTPUT = "manifest.json"


def run_stages(names):
    """Run the given stages in canonical order (duplicates ignored)."""
    want = set(names)
    for name in STAGE_ORDER:
        if name in want:
            log(f"[etl] {name}")
            STAGES[name]()


if __name__ == "__main__":
    run_stages(list(STAGES))
