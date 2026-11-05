"""Military-weighted airport snap — the second-pass airfield-attribution
rule for OBSERVED ground intervals.

Used by the attribute stage (_merge_and_attribute_intervals) to name the
airfield whose weighted score wins among an observed interval's lowest
points. The landing engine (landing/naming.py) answers a different
question — which airfield a landing-shaped AIRBORNE edge most likely
names — with its own model-based field posterior; the two are not
required to agree.

Scoring: distance / (radius * effective_weight), lowest wins, among
airfields whose min(record radius, per-type cap) contains the point.
Types absent from AIRPORT_SNAP_RADIUS_KM (heliports, seaplane bases,
small strips) can never win. effective_weight blends linearly from 1.0
(pure distance — a point on the ground is physically at one field) up to
AIRPORT_TYPE_WEIGHT[type] as the point's field-relative altitude reaches
LOW_APPROACH_AGL_FT: importance only matters when there is real ambiguity
about which field a low point belongs to.

Field-relative altitude uses each candidate's own elevation_ft
(airports.pkl); a record with unknown elevation is treated as sea level —
conservative in flat regions, strict near high terrain.

Batched: one BallTree.query_radius per _BATCH points (a per-point loop
measured out at ~10 minutes over this pipeline's ground points; the batch
form is seconds). Build a SnapIndex once per run and reuse it.
"""
from __future__ import annotations

import numpy as np

from backend.pipeline.etl.constants import (
    AIRPORT_SNAP_RADIUS_KM, AIRPORT_TYPE_WEIGHT, LOW_APPROACH_AGL_FT,
)
from backend.util.geo.haversine import R_EARTH_KM

_BATCH = 300_000


class SnapIndex:
    """Per-run precomputed candidate arrays over gi.airports."""

    def __init__(self, gi):
        self.gi = gi
        self.elevs = np.array(
            [a.get("elevation_ft") if a.get("elevation_ft") is not None
             else 0.0 for a in gi.airports], dtype=np.float64)
        self.caps = np.array(
            [AIRPORT_SNAP_RADIUS_KM.get(a.get("type", ""), 0.0)
             for a in gi.airports], dtype=np.float64)
        self.weights = np.array(
            [AIRPORT_TYPE_WEIGHT.get(a.get("type", ""), 1.0)
             for a in gi.airports], dtype=np.float64)
        self.max_km = max(AIRPORT_SNAP_RADIUS_KM.values())


def snap_points(index: SnapIndex, lats: np.ndarray, lons: np.ndarray,
                alt_ft: np.ndarray, *, enforce_agl_gate: bool = True) -> list:
    """Snap many points to airfields. Returns one entry per point: the
    winning airport dict (live reference into gi.airports — copy before
    mutating) or None.

    alt_ft: barometric MSL altitude per point (NaN treated as ground
    level, matching the on_ground flag's own meaning).

    enforce_agl_gate: when True, a candidate is eligible only if the
    point's altitude is within LOW_APPROACH_AGL_FT of that candidate's
    elevation — the low-approach evidence gate. Pass False when the
    caller has already established the point's groundedness some other
    way, so re-gating here would not wrongly reject a point whose
    reception died high.
    """
    gi = index.gi
    out: list = []
    n = len(lats)
    for start in range(0, n, _BATCH):
        end = min(start + _BATCH, n)
        pts_rad = np.radians(np.column_stack([lats[start:end], lons[start:end]]))
        idxs_list, dists_list = gi._airport_tree.query_radius(
            pts_rad, r=index.max_km / R_EARTH_KM, return_distance=True)
        for pt_offset, (idxs, dists) in enumerate(zip(idxs_list, dists_list)):
            if len(idxs) == 0:
                out.append(None)
                continue
            dist_km = dists * R_EARTH_KM
            radii = gi._airport_radii_km[idxs]
            alt = alt_ft[start + pt_offset]
            if np.isnan(alt):
                alt = 0.0
            rel_alt = alt - index.elevs[idxs]

            ok = dist_km <= np.minimum(radii, index.caps[idxs])
            if enforce_agl_gate:
                ok &= rel_alt <= LOW_APPROACH_AGL_FT
            if not ok.any():
                out.append(None)
                continue

            blend = np.clip(rel_alt[ok] / LOW_APPROACH_AGL_FT, 0.0, 1.0)
            eff_w = 1.0 + blend * (index.weights[idxs][ok] - 1.0)
            score = dist_km[ok] / (radii[ok] * eff_w)
            j = int(np.argmin(score))
            out.append(gi.airports[int(idxs[ok][j])])
    return out


def snap_point(index: SnapIndex, lat: float, lon: float, alt_ft: float,
               *, enforce_agl_gate: bool = True):
    """Single-point convenience wrapper (flight endpoints)."""
    return snap_points(
        index,
        np.array([lat]), np.array([lon]),
        np.array([alt_ft], dtype=np.float64),
        enforce_agl_gate=enforce_agl_gate,
    )[0]
