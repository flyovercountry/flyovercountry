"""Georaster runtime — fast country-at-point lookup via mmap'd raster.

Loads the pre-built raster + country-code codecs from
data/build/lookup/airspace/ (airspace.npy, iso2_to_id.json, id_to_iso2.json,
crop.json) and airports.pkl (sklearn BallTree) from data/build/lookup/.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np

CELLS_PER_DEG = 100   # 0.01-degree cells; gen/raster.py must agree (pinned by test_gridspec)


class GeoRaster:

    def __init__(self, airspace: np.ndarray, iso2_to_id: dict,
                 id_to_iso2: dict, airports: dict,
                 row_offset: int = 0, col_offset: int = 0):
        self._airspace = airspace
        self._iso2_to_id = iso2_to_id
        self._id_to_iso2 = id_to_iso2
        self.airports = airports["airports"]
        self._airport_tree = airports["tree"]
        self._airport_radii_km = airports["radii_km"]
        self._row_offset = row_offset
        self._col_offset = col_offset
        self._max_row = airspace.shape[0] - 1
        self._max_col = airspace.shape[1] - 1

    @classmethod
    def load(cls, data_dir: str | Path) -> "GeoRaster":
        d = Path(data_dir)
        ad = d / "airspace"          # raster + country-code codecs
        airspace = np.load(ad / "airspace.npy", mmap_mode="r")
        iso2_to_id = {k: int(v) for k, v in json.loads(
            (ad / "iso2_to_id.json").read_text()).items()}
        id_to_iso2 = {int(k): v for k, v in json.loads(
            (ad / "id_to_iso2.json").read_text()).items()}
        # pickle.load on an untrusted file is an RCE vector; airports.pkl is
        # a self-generated artifact (backend/pipeline/gen/airports.py writes
        # it into data/build/lookup/), never external/downloaded input, so
        # its integrity is trusted the same way the rest of data/build/ is.
        with (d / "airports.pkl").open("rb") as fh:
            airports = pickle.load(fh)
        # sanity-guard the unpickled structure: a same-version rebuild is
        # trusted, but sklearn drift has silently changed this artifact's
        # bytes before (see README's dependency section) — fail loudly
        # rather than query a half-compatible BallTree
        if ("tree" not in airports
                or len(airports.get("airports", []))
                != len(airports.get("radii_km", []))):
            raise ValueError(
                "airports.pkl malformed or inconsistent — regenerate with "
                "./run gen airports (possible sklearn version drift)")
        row_offset = col_offset = 0
        crop_path = ad / "crop.json"
        if crop_path.exists():
            crop = json.loads(crop_path.read_text())
            row_offset = crop["row_offset"]
            col_offset = crop["col_offset"]
        elif airspace.shape != (18000, 36000):
            # a cropped raster without its crop.json would compute global
            # cell coordinates against the cropped array: every lookup
            # falls out of bounds and attribution silently returns nothing
            raise ValueError(
                f"crop.json missing but airspace.npy is cropped "
                f"{airspace.shape} — every attribution would silently "
                f"miss; regenerate with ./run gen raster")
        return cls(airspace, iso2_to_id, id_to_iso2, airports,
                   row_offset, col_offset)

    def _raw_at(self, lats, lons) -> np.ndarray:
        # NaN lat/lon casts to INT32_MIN, which after the offset subtraction
        # wraps to a huge positive index and fails in_bounds -> attributed 0.
        # That fail-safe is a magnitude coincidence, not a designed guard —
        # revisit if CELLS_PER_DEG or the coordinate scale ever changes.
        rows = ((np.asarray(lats) + 90.0) * CELLS_PER_DEG).astype(np.int32)
        rows = rows - self._row_offset
        v = np.asarray(lons, dtype=np.float64)
        v = np.where(v >= 180.0, v - 360.0, v)
        v = np.where(v < -180.0, v + 360.0, v)
        cols = ((v + 180.0) * CELLS_PER_DEG).astype(np.int32)
        cols = cols - self._col_offset
        in_bounds = (rows >= 0) & (rows <= self._max_row) & \
                    (cols >= 0) & (cols <= self._max_col)
        rows = np.clip(rows, 0, self._max_row)
        cols = np.clip(cols, 0, self._max_col)
        result = np.asarray(self._airspace[rows, cols])
        result[~in_bounds] = 0
        return result

    def country_id_at_many(self, lats, lons) -> np.ndarray:
        """Country id per point; 0 = unattributed (any water, or outside
        the crop). The raster stores plain land-polygon ids — there is no
        territorial-sea layer, and none is planned."""
        return self._raw_at(lats, lons).astype(np.int32)

