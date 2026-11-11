"""Cross-file grid-constant agreement: the raster builder (a standalone
script run under an `if __name__ == "__main__"` guard, loaded here via
importlib rather than imported normally) and the runtime reader must use
the same cells-per-degree, or every lookup silently lands in the wrong
cell."""
import importlib.util
from pathlib import Path

from backend.util.geo.raster import CELLS_PER_DEG


def test_builder_and_reader_agree_on_cells_per_degree():
    src = Path("backend/pipeline/gen/raster.py")
    spec = importlib.util.spec_from_file_location("gen_raster", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.CELLS_PER_DEG == CELLS_PER_DEG == 100


def test_no_stats_country_lacks_raster_coverage():
    """Enforces the user's focus table mechanically: a country may only be
    given a stats table (interaction >= 2) if the attribution raster can
    actually attribute it. Prevents a config-only interaction bump from
    ever publishing silent zeros for an uncovered country (KW/SA are
    deliberately info-only and deliberately uncovered)."""
    import json
    from pathlib import Path
    import pytest
    codes_path = Path("data/build/lookup/airspace/id_to_iso2.json")
    if not codes_path.exists():
        pytest.skip("raster not built in this environment")
    # coverage means actual attributable CELLS, not codec membership — a
    # country fully erased by the one-cell erosion (e.g. Vatican-sized)
    # would still appear in the codec while attributing nothing
    import numpy as np
    codec = {int(k): v for k, v in
             json.loads(codes_path.read_text()).items()}
    airspace = np.load(codes_path.parent / "airspace.npy", mmap_mode="r")
    present_ids = set(np.unique(airspace).tolist()) - {0}
    covered = {codec[i] for i in present_ids if i in codec}
    modes = json.loads(
        Path("data/in/config/frontend/countries.modes.json").read_text())
    stats_ccs = {cc for cc, m in modes["countries"].items()
                 if m.get("interaction", 0) >= 2}
    missing = stats_ccs - covered
    assert not missing, (
        f"stats-enabled countries lack raster coverage: {sorted(missing)}")
