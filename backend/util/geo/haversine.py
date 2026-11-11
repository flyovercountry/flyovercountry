"""Vectorized geodesic helpers: great-circle distance and bearing.

The single home for these; import instead of redefining — several etl
stages independently reimplemented the same formulas before this module
absorbed them.
"""
from __future__ import annotations

import numpy as np

R_EARTH_KM = 6371.0


def haversine_km(lat1, lon1, lat2, lon2):
    """Haversine distance in km. All args may be scalars or arrays."""
    r = R_EARTH_KM
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = (np.sin(dlat / 2) ** 2
         + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2))
         * np.sin(dlon / 2) ** 2)
    return r * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2, degrees in [0, 360)."""
    la1, la2 = np.radians(lat1), np.radians(lat2)
    dlon = np.radians(lon2 - lon1)
    y = np.sin(dlon) * np.cos(la2)
    x = np.cos(la1) * np.sin(la2) - np.sin(la1) * np.cos(la2) * np.cos(dlon)
    return (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0


def relative_bearing_deg(lat1, lon1, lat2, lon2, track_deg):
    """Bearing from point 1 to point 2, relative to a heading `track_deg`
    at point 1. Degrees in [-180, 180]."""
    b = bearing_deg(lat1, lon1, lat2, lon2)
    return ((b - track_deg + 180.0) % 360.0) - 180.0
