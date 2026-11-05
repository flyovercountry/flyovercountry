#!/usr/bin/env python3
"""Generate simplified GeoJSON boundaries for focus countries from Natural Earth shapefiles.

Reads the 10m admin-0 shapefile directly (via pyshp), clips each focus country
to the map bounding box, simplifies, and writes a single GeoJSON file.

Usage (inside container):
    python backend/pipeline/gen/boundaries.py
"""
import json
import sys
from pathlib import Path

import shapefile
from shapely.geometry import shape, box, mapping
from shapely.ops import unary_union
from backend.util.log import log

SHAPEFILE = "data/in/external/ne_10m_admin_0_countries/ne_10m_admin_0_countries.shp"
PIPELINE_CONFIG = "data/in/config/dimensions.json"
FOCUS_CONFIG = "data/in/config/frontend/countries.modes.json"
OUT_PATH = "data/build/lookup/boundaries.geojson"

SIMPLIFY_TOLERANCE = 0.02

# Natural Earth marks a few disputed territories ISO_A2_EH="-99"; skip them.
SKIP_ISO = {"-99"}


def load_focus_codes():
    """Every country that needs a boundary polygon: every entry in the flat
    ``countries`` map, regardless of its permissiveness level or
    interaction flags — display-only countries (e.g. Ukraine/Belarus/Russia,
    permissiveness 0 with stats/whatif/info all false) are ordinary entries
    now, not a separate mechanism, so they need no special-casing here to
    still get a boundary polygon (see backend/util/focus_config.py)."""
    cfg = json.loads(Path(FOCUS_CONFIG).read_text())
    return set(cfg["countries"].keys())


def build_ne_lookup(sf):
    """Build ISO2 -> list of shapely geometries from the shapefile."""
    fields = [f[0] for f in sf.fields[1:]]
    iso_idx = fields.index("ISO_A2_EH")

    lookup: dict[str, list] = {}
    for rec in sf.iterShapeRecords():
        cc = rec.record[iso_idx]
        if cc is None or cc in SKIP_ISO:
            continue
        geom = shape(rec.shape.__geo_interface__)
        if geom.is_valid and not geom.is_empty:
            lookup.setdefault(cc, []).append(geom)
    return lookup


def load_clip_box():
    cfg = json.loads(Path(PIPELINE_CONFIG).read_text())
    c = next(b for b in cfg["bboxes"] if b["name"] == "crop")
    return box(c["lon_west"], c["lat_south"], c["lon_east"], c["lat_north"])


def main():
    clip_box = load_clip_box()
    focus_codes = load_focus_codes()
    sf = shapefile.Reader(SHAPEFILE)
    ne_lookup = build_ne_lookup(sf)

    features = []
    for cc in sorted(focus_codes):
        geoms = ne_lookup.get(cc)
        if not geoms:
            log(f"  {cc}: not found in shapefile, skipping")
            continue

        merged = unary_union(geoms)
        clipped = merged.intersection(clip_box)
        if clipped.is_empty:
            log(f"  {cc}: empty after clipping")
            continue

        simplified = clipped.simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)
        geom_json = mapping(simplified)
        n_coords = str(geom_json).count(",")

        features.append({
            "type": "Feature",
            "properties": {"cc": cc},
            "geometry": geom_json,
        })
        log(f"  {cc}: {simplified.geom_type}, ~{n_coords} coords")

    geojson = {"type": "FeatureCollection", "features": features}
    out = Path(OUT_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(geojson))

    produced = {f["properties"]["cc"] for f in features}
    skipped = sorted(set(focus_codes) - produced)
    if skipped:
        # a config typo or shapefile mismatch must fail the build, not
        # silently ship a map without that country's outline (the file is
        # still written above, for inspection)
        log(f"ERROR: {len(skipped)} focus countr"
            f"{'y' if len(skipped) == 1 else 'ies'} produced no feature: "
            f"{', '.join(skipped)}")
        sys.exit(1)
    size_kb = out.stat().st_size / 1024
    log(f"Wrote {out} ({size_kb:.0f} KB, {len(features)} countries)")


if __name__ == "__main__":
    main()
