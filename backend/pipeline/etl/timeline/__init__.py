"""Timeline phase: dataset -> each aircraft's timeline skeleton.

Stages (canonical order): partition, edge, split, flight. Each stage
module owns its runner; see backend.pipeline.etl.run for the registry the CLI
uses. (speeds.py remains as a standalone characterization tool, no longer a
pipeline stage — the type-blind split rule stopped consuming its output.)
"""
