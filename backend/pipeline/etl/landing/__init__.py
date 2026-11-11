"""Landing phase: ground truth — model training, attribution, theater flags.

Stages (canonical order): train, attribute, theater. Each stage module
owns its runner; see backend.pipeline.etl.run for the registry the CLI uses.
"""
