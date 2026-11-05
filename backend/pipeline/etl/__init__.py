"""etl — interval-partition pipeline.

Partitions each relevant aircraft's crop-interval trace into a ternary
bin sequence (OFF/GROUND/AIR), derives ground and airborne intervals and
the blackouts inside them, decides split/join verdicts per blackout, and
assembles flights. Four phases: preprocess, timeline, landing, publish
— see backend.pipeline.etl.run for the stage registry.
"""
