"""Publish phase: frontend artifacts.

Two stages: trace (RDP-simplified polylines, render-prep only) and
export (per-country stats and all other published artifacts; there is
no separate summarize stage). See backend.pipeline.etl.run for the
registry the CLI uses.
"""
