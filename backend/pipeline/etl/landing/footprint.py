"""Footprints: spatial ground-location evidence for the landing engine.

A footprint is a unit-mass probability distribution over map cells answering
"given this evidence, where was the aircraft on the ground?".  Two producers:

  L (landing-shaped airborne edge): the trained GMM density over
     (distance, relative bearing | gs, vr, alt), mapped onto the grid with
     the polar Jacobian 1/d.  If the edge position is MLAT-sourced the
     footprint is blurred by the measured MLAT position kernel
     (MLAT_POS_SIGMA_KM), since the whole footprint inherits that error.

  G (observed ground fix): the empirical radial kernel fitted from measured
     within-stay taxi displacement (calibrate.fit_g_kernels; separate ADS-B
     and MLAT kernels).

The overlap score of two footprints on a shared grid,
  S = sum(mass_a * mass_b),
is the merge rule's primary evidence.  Cell size is pinned to
FOOTPRINT_CELL_KM: the odds table in the model artifact is calibrated on
scores at exactly this resolution, so changing it without recalibrating
would invalidate the merge decision.

Pure functions; no I/O.  The model artifact supplies the fitted objects.
"""
from __future__ import annotations

import numpy as np

from backend.pipeline.etl.constants import (
    FOOTPRINT_CELL_KM, MLAT_POS_SIGMA_KM, REACH_KM,
)
from backend.util.geo.haversine import R_EARTH_KM

KM_PER_DEG = 111.32
# The polar Jacobian 1/d diverges as d->0; floor it at this radius so
# cells very close to the edge position don't dominate the footprint.
_MIN_D_KM = 1.0


class Grid:
    """Local flat-earth grid centred on a set of evidence points, extended
    by REACH_KM in every direction.  Cell size = FOOTPRINT_CELL_KM."""

    def __init__(self, lats, lons, reach_km=REACH_KM,
                 cell_km=FOOTPRINT_CELL_KM):
        self.lat0 = float(np.mean(lats))
        self.lon0 = float(np.mean(lons))
        self.coslat = max(np.cos(np.radians(self.lat0)), 0.2)
        xs = [(lo - self.lon0) * KM_PER_DEG * self.coslat for lo in lons]
        ys = [(la - self.lat0) * KM_PER_DEG for la in lats]
        self.cell_km = float(cell_km)
        self.x0 = min(xs) - reach_km
        self.y0 = min(ys) - reach_km
        self.nx = int(np.ceil((max(xs) + reach_km - self.x0) / cell_km))
        self.ny = int(np.ceil((max(ys) + reach_km - self.y0) / cell_km))
        cx = self.x0 + (np.arange(self.nx) + 0.5) * cell_km
        cy = self.y0 + (np.arange(self.ny) + 0.5) * cell_km
        self.gx, self.gy = np.meshgrid(cx, cy)      # shape (ny, nx)

    def xy_of(self, lat, lon):
        return ((lon - self.lon0) * KM_PER_DEG * self.coslat,
                (lat - self.lat0) * KM_PER_DEG)

    def latlon_of_cells(self):
        """(lats, lons) of every cell centre, flat arrays of length ny*nx."""
        lat = self.lat0 + self.gy.ravel() / KM_PER_DEG
        lon = self.lon0 + self.gx.ravel() / (KM_PER_DEG * self.coslat)
        return lat, lon

    def index_of(self, lat, lon):
        """Flat cell index containing (lat, lon), or -1 if off-grid."""
        x, y = self.xy_of(lat, lon)
        i = int((x - self.x0) // self.cell_km)
        j = int((y - self.y0) // self.cell_km)
        if 0 <= i < self.nx and 0 <= j < self.ny:
            return j * self.nx + i
        return -1


def _normalize(dens):
    s = dens.sum()
    return dens / s if s > 0 else dens


def _blur(mass, grid, sigma_km):
    """Separable Gaussian blur on the grid, used to widen MLAT footprints."""
    sig = sigma_km / grid.cell_km
    n = max(int(np.ceil(3 * sig)), 1)
    k = np.exp(-0.5 * (np.arange(-n, n + 1) / sig) ** 2)
    k /= k.sum()
    m = mass.reshape(grid.ny, grid.nx)
    m = np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 1, m)
    m = np.apply_along_axis(lambda c: np.convolve(c, k, mode="same"), 0, m)
    return _normalize(m.ravel())


def l_footprint(grid, edge, model_dir):
    """Unit-mass L footprint from a landing-shaped airborne edge.

    Uses the trained GMM to evaluate P(distance, bearing | gs, vr, alt)
    at every grid cell, then applies the polar Jacobian 1/d to convert
    from (d, bearing) density to spatial density.  MLAT edges get an
    additional Gaussian blur to account for position uncertainty.

    edge: dict with lat, lon, gs, vr, alt, track, source.
    model_dir: per-direction model dict from the train artifact."""
    ex, ey = grid.xy_of(edge["lat"], edge["lon"])
    dx = grid.gx.ravel() - ex
    dy = grid.gy.ravel() - ey
    d = np.hypot(dx, dy)
    brg = (np.degrees(np.arctan2(dx, dy)) + 360.0) % 360.0
    rel = ((brg - edge["track"] + 180.0) % 360.0) - 180.0
    ok = (d >= _MIN_D_KM) & (d <= REACH_KM)
    X = np.column_stack([
        np.full(d.size, edge["gs"]), np.full(d.size, edge["vr"]),
        np.full(d.size, edge["alt"]), d, rel])
    z = (X - model_dir["mu"]) / model_dir["sd"]
    dens = np.zeros(d.size)
    dens[ok] = np.exp(model_dir["density"].score_samples(z[ok])) / d[ok]
    mass = _normalize(dens)
    if edge.get("source") == "mlat":
        mass = _blur(mass, grid, MLAT_POS_SIGMA_KM)
    return mass


def g_footprint(grid, lat, lon, kernel):
    """Unit-mass G footprint from an observed ground fix.

    Interpolates the empirical taxi-displacement kernel (from
    calibrate.fit_g_kernels) at the distance of each grid cell from
    the fix position.  ADS-B and MLAT kernels differ only in their
    fitted arrays.

    kernel: {"r_edges": bin edges km, "radial": density per km^2}."""
    fx, fy = grid.xy_of(lat, lon)
    d = np.hypot(grid.gx.ravel() - fx, grid.gy.ravel() - fy)
    dens = np.interp(d, 0.5 * (kernel["r_edges"][:-1] + kernel["r_edges"][1:]),
                     kernel["radial"], left=kernel["radial"][0], right=0.0)
    return _normalize(dens)


def overlap(mass_a, mass_b):
    """Overlap score: the merge rule's primary evidence statistic.
    Identical computation to the calibration harness, so the fitted
    odds table applies directly."""
    return float((mass_a * mass_b).sum())


def mass_centre(grid, mass):
    """(lat, lon) of the footprint's centre of mass."""
    x = float((grid.gx.ravel() * mass).sum())
    y = float((grid.gy.ravel() * mass).sum())
    return (grid.lat0 + y / KM_PER_DEG,
            grid.lon0 + x / (KM_PER_DEG * grid.coslat))


def country_mass(grid, mass, gi):
    """Footprint mass per georaster country id (0 = water/uncovered).
    Used by the naming readout to attribute a landing to a country."""
    lat, lon = grid.latlon_of_cells()
    cids = gi.country_id_at_many(lat, lon)
    out: dict = {}
    tot = np.bincount(cids, weights=mass)
    for cid in np.nonzero(tot)[0]:
        out[int(cid)] = float(tot[cid])
    return out
