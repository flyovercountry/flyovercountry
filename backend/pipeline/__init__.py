"""pipeline — the three phases of `./run`, in order: gen, ingest, etl.

  gen/     asset generators (mil, airports, raster, boundaries, roles_page)
           — build data/build/lookup/ lookup files, run as standalone
           scripts (see backend.cli._gen_mil / _run_script); export also
           imports gen.airports.US_MIL_DUAL_USE directly
  ingest/  download + filter raw ADS-B, and the downsample maintenance task
  etl/     the interval-partition pipeline (preprocess, timeline,
           landing, publish) — see backend.pipeline.etl.run

Consumes backend.util (readers + pure helpers); nothing in util imports
from pipeline.
"""
