#!/usr/bin/env python3
"""Build the airspace.npy georaster from Natural Earth admin-0 boundaries.

Rasterises all European countries at 0.01 deg resolution, erodes each by
one cell to avoid false positives on borders, crops to the European theatre
bounding box, and writes the artifacts to data/build/lookup/airspace/.

Output (in --out-dir):
    airspace.npy      uint16 raster (eroded)
    iso2_to_id.json   ISO2 -> numeric ID mapping
    id_to_iso2.json   numeric ID -> ISO2 mapping
    crop.json         row_offset / col_offset for global coord translation

Usage:
    python backend/pipeline/gen/raster.py [--shapefile PATH] [--out-dir PATH]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import shapefile
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy.ndimage import binary_erosion
from shapely.geometry import shape

from backend.util.countries import normalize_country
from backend.util.log import log

# 0.01-degree cells. Must equal backend/util/geo/raster.py's CELLS_PER_DEG
# (kept duplicated rather than imported, so this script has no dependency
# on the heavier util.geo.raster module; the agreement is pinned by
# test/backend/test_gridspec.py).
CELLS_PER_DEG = 100

PIPELINE_CONFIG = Path("data/in/config/dimensions.json")


def _load_crop():
    cfg = json.loads(PIPELINE_CONFIG.read_text())
    c = next(b for b in cfg["bboxes"] if b["name"] == "crop")
    return c["lat_south"], c["lat_north"], c["lon_west"], c["lon_east"]


def _norm_iso2(raw: str) -> str:
    return normalize_country(raw)


def build(shp_path: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    crop_lat_min, crop_lat_max, crop_lon_min, crop_lon_max = _load_crop()
    crop_rows = int((crop_lat_max - crop_lat_min) * CELLS_PER_DEG)
    crop_cols = int((crop_lon_max - crop_lon_min) * CELLS_PER_DEG)
    row_offset = int((crop_lat_min + 90.0) * CELLS_PER_DEG)
    col_offset = int((crop_lon_min + 180.0) * CELLS_PER_DEG)

    crop_transform = from_origin(
        west=crop_lon_min, north=crop_lat_max,
        xsize=1.0 / CELLS_PER_DEG,
        ysize=1.0 / CELLS_PER_DEG,
    )

    log(f"reading {shp_path.name}...")
    records: list[tuple[str, object]] = []
    seen_iso2: set[str] = set()

    sf = shapefile.Reader(str(shp_path))
    fields = [f[0] for f in sf.fields[1:]]
    continent_idx = fields.index("CONTINENT")
    iso_a2_eh_idx = fields.index("ISO_A2_EH")
    iso_a2_idx = fields.index("ISO_A2") if "ISO_A2" in fields else None
    adm0_a3_idx = fields.index("ADM0_A3") if "ADM0_A3" in fields else None

    for rec in sf.iterShapeRecords():
        continent = (rec.record[continent_idx] or "").strip()
        if continent != "Europe":
            continue
        iso2 = (rec.record[iso_a2_eh_idx] or "").strip().upper()
        if iso2 in ("", "-99") and iso_a2_idx is not None:
            iso2 = (rec.record[iso_a2_idx] or "").strip().upper()
        if iso2 in ("", "-99") and adm0_a3_idx is not None:
            iso2 = (rec.record[adm0_a3_idx] or "").strip().upper()
        iso2 = _norm_iso2(iso2)
        if not iso2 or iso2 == "??":
            continue
        geom = shape(rec.shape.__geo_interface__)
        if geom.is_empty:
            continue
        seen_iso2.add(iso2)
        records.append((iso2, geom))

    log(f"  {len(records)} European polygons, {len(seen_iso2)} unique ISO2 codes")

    iso2_list = sorted(seen_iso2)
    iso2_to_id = {c: i + 1 for i, c in enumerate(iso2_list)}
    id_to_iso2 = {i + 1: c for i, c in enumerate(iso2_list)}

    log(f"rasterising to cropped grid ({crop_rows}x{crop_cols})...")
    t0 = time.time()
    land = np.zeros((crop_rows, crop_cols), dtype=np.uint16)
    shapes_ids = [(g, iso2_to_id[iso2]) for iso2, g in records]
    if shapes_ids:
        rasterize(
            shapes_ids,
            out=land,
            transform=crop_transform,
            fill=0,
            all_touched=False,
            dtype=np.uint16,
        )
    land = np.ascontiguousarray(land[::-1, :])
    log(f"  rasterised in {time.time() - t0:.1f}s")

    n_countries = len(iso2_to_id)
    log(f"eroding {n_countries} countries by 1 cell...")
    t0 = time.time()
    eroded = np.zeros_like(land)
    for i, (iso2, cid) in enumerate(iso2_to_id.items()):
        mask = land == cid
        if not mask.any():
            continue
        shrunk = binary_erosion(mask, iterations=1, border_value=False)
        eroded[shrunk] = cid
        log(f"  {i + 1}/{n_countries} {iso2}")
    log(f"  erosion done in {time.time() - t0:.1f}s")

    log(f"  eroded shape: {eroded.shape}")

    np.save(out_dir / "airspace.npy", eroded)
    (out_dir / "iso2_to_id.json").write_text(
        json.dumps(iso2_to_id, indent=2) + "\n")
    (out_dir / "id_to_iso2.json").write_text(
        json.dumps({str(k): v for k, v in id_to_iso2.items()}, indent=2) + "\n")
    (out_dir / "crop.json").write_text(json.dumps({
        "row_offset": row_offset,
        "col_offset": col_offset,
        "lat_south": crop_lat_min,
        "lat_north": crop_lat_max,
        "lon_west": crop_lon_min,
        "lon_east": crop_lon_max,
    }, indent=2) + "\n")

    nonzero = int(np.count_nonzero(eroded))
    log(f"wrote {out_dir}: {eroded.nbytes / 1e6:.1f} MB, "
         f"{nonzero} land cells, {len(iso2_to_id)} countries")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shapefile", type=Path,
                        default=Path("data/in/external/ne_10m_admin_0_countries/ne_10m_admin_0_countries.shp"))
    parser.add_argument("--out-dir", type=Path,
                        default=Path("data/build/lookup/airspace"))
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    build(args.shapefile, args.out_dir)


if __name__ == "__main__":
    main()
