"""Behavioral tests for backend/pipeline/gen/boundaries.py — the standalone
Natural Earth shapefile clipper. It reads its inputs (SHAPEFILE,
FOCUS_CONFIG, PIPELINE_CONFIG, OUT_PATH) from module-level path constants
rather than arguments, and runs under an `if __name__ == "__main__"` guard,
so it's loaded via importlib.util.spec_from_file_location (same pattern as
test_gridspec.py for gen/raster.py) and those constants are overridden on
the loaded module object after exec, before calling main()."""
import importlib.util
import json
from pathlib import Path

import pytest
import shapefile
from shapely.geometry import shape


def _load_boundaries():
    src = Path("backend/pipeline/gen/boundaries.py")
    spec = importlib.util.spec_from_file_location("gen_boundaries", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_shapefile(path):
    """Two unit-ish squares, ISO_A2_EH AA and BB, well inside the crop box."""
    w = shapefile.Writer(str(path), shapeType=shapefile.POLYGON)
    w.field("ISO_A2_EH", "C")
    w.poly([[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]])
    w.record("AA")
    w.poly([[[5, 5], [5, 6], [6, 6], [6, 5], [5, 5]]])
    w.record("BB")
    w.close()


def _write_json(path, obj):
    path.write_text(json.dumps(obj))


def _crop_config(path):
    _write_json(path, {"bboxes": [
        {"name": "crop", "lon_west": -10, "lat_south": -10,
         "lon_east": 10, "lat_north": 10},
    ]})


def test_main_clips_only_focus_countries(tmp_path):
    mod = _load_boundaries()
    shp = tmp_path / "ne.shp"
    _write_shapefile(shp)
    focus_cfg = tmp_path / "focus.json"
    _write_json(focus_cfg, {"countries": {"AA": {}}})
    dims_cfg = tmp_path / "dimensions.json"
    _crop_config(dims_cfg)
    out = tmp_path / "boundaries.geojson"

    mod.SHAPEFILE = str(shp)
    mod.FOCUS_CONFIG = str(focus_cfg)
    mod.PIPELINE_CONFIG = str(dims_cfg)
    mod.OUT_PATH = str(out)

    mod.main()

    geojson = json.loads(out.read_text())
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 1
    feat = geojson["features"][0]
    assert feat["properties"]["cc"] == "AA"
    poly = shape(feat["geometry"])
    # the crop box fully encloses the AA square, so clipping must not
    # shrink it, and BB (not in focus_codes) must not appear at all
    assert poly.bounds == pytest.approx((0.0, 0.0, 1.0, 1.0), abs=0.05)


def test_main_exits_nonzero_when_focus_code_missing_from_shapefile(tmp_path):
    mod = _load_boundaries()
    shp = tmp_path / "ne.shp"
    _write_shapefile(shp)
    focus_cfg = tmp_path / "focus.json"
    # ZZ is not in the shapefile at all
    _write_json(focus_cfg, {"countries": {"AA": {}, "ZZ": {}}})
    dims_cfg = tmp_path / "dimensions.json"
    _crop_config(dims_cfg)
    out = tmp_path / "boundaries.geojson"

    mod.SHAPEFILE = str(shp)
    mod.FOCUS_CONFIG = str(focus_cfg)
    mod.PIPELINE_CONFIG = str(dims_cfg)
    mod.OUT_PATH = str(out)

    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1

    # the file is still written (for inspection) even though the run fails
    assert out.exists()
    geojson = json.loads(out.read_text())
    assert {f["properties"]["cc"] for f in geojson["features"]} == {"AA"}
