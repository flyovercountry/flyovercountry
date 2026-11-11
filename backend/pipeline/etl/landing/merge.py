"""The merge decision: given a silence with ground-location evidence on
both sides, is the aircraft at one place or two?

The trained odds table (from calibrate.py) encodes:
  log-odds(same place | overlap score, gap) =
      log-LR from class-conditional score densities (same-stay vs
      different-place labeled pairs)
    + log prior-odds from G-silence-G pairs (real ground-bounded silences
      where 96.6-100% involved no movement at every gap bucket).

Runtime is a bilinear interpolation into this table, plus the floor
margin that biases toward merging (splitting a true single stay
fabricates a landing -- the forbidden error direction):

  SAME           odds >= 1/MERGE_FLOOR_MARGIN
  DIFFERENT      otherwise, IF the implied invisible flight is physically
                 feasible (ferry speed within the gap minus a minimum
                 ground stop)
  CONTRADICTORY  otherwise (evidence supports neither hypothesis -- the
                 MLAT-mislocation shape; never counted as two landings)

Pure functions; no I/O.
"""
from __future__ import annotations

import numpy as np

from backend.pipeline.etl.constants import (
    BLACKOUT_MIN_S, MAX_FERRY_KPH, MERGE_FLOOR_MARGIN,
)

SAME = "same"
DIFFERENT = "different"
CONTRADICTORY = "contradictory"

# Floor for log10(score) to avoid log10(0) in the odds lookup
_SCORE_FLOOR = 1e-12


class OddsTable:
    """Bilinear interpolation of log-odds over (log10 score, log10 gap-hours).

    Built by calibrate.build_odds_table; consumed here and by the engine.
    Values outside the calibrated grid clamp to the nearest edge."""

    def __init__(self, log10_score_grid, log10_gap_h_grid, log_odds):
        self.sg = np.asarray(log10_score_grid, float)
        self.gg = np.asarray(log10_gap_h_grid, float)
        self.lo = np.asarray(log_odds, float)      # shape (len(sg), len(gg))
        assert self.lo.shape == (len(self.sg), len(self.gg))

    def log_odds(self, score, gap_s):
        s = np.log10(max(score, _SCORE_FLOOR))
        g = np.log10(max(gap_s / 3600.0, 1e-3))
        i = np.clip(np.searchsorted(self.sg, s) - 1, 0, len(self.sg) - 2)
        j = np.clip(np.searchsorted(self.gg, g) - 1, 0, len(self.gg) - 2)
        ts = np.clip((s - self.sg[i]) / (self.sg[i + 1] - self.sg[i]), 0, 1)
        tg = np.clip((g - self.gg[j]) / (self.gg[j + 1] - self.gg[j]), 0, 1)
        return float(
            self.lo[i, j] * (1 - ts) * (1 - tg)
            + self.lo[i + 1, j] * ts * (1 - tg)
            + self.lo[i, j + 1] * (1 - ts) * tg
            + self.lo[i + 1, j + 1] * ts * tg)


def movement_feasible(sep_km, gap_s):
    """Could the aircraft have flown sep_km within the gap?  Subtracts
    BLACKOUT_MIN_S as the minimum ground stop at each end, then checks
    whether ferry speed covers the distance in the remaining time."""
    fly_h = gap_s / 3600.0 - BLACKOUT_MIN_S / 3600.0
    if fly_h <= 0:
        return sep_km <= 0.0
    return sep_km <= fly_h * MAX_FERRY_KPH


def decide(score, gap_s, sep_km, table, margin=MERGE_FLOOR_MARGIN):
    """The merge verdict for one silence with ground evidence on both sides.

    score: footprint overlap (from footprint.overlap).
    gap_s: silence duration in seconds.
    sep_km: distance between footprint mass centres (for feasibility).
    Returns (verdict, log_odds)."""
    lo = table.log_odds(score, gap_s)
    if lo >= -np.log(margin):
        return SAME, lo
    if movement_feasible(sep_km, gap_s):
        return DIFFERENT, lo
    return CONTRADICTORY, lo
