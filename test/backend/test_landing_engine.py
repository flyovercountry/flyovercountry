"""Landing engine pure modules: footprint, merge, naming."""
import numpy as np
import pytest

from backend.pipeline.etl.landing import footprint as fp
from backend.pipeline.etl.landing import merge as mg
from backend.pipeline.etl.landing import naming as nm


# --- stubs ----------------------------------------------------------------

class _StubDensity:
    """GMM stand-in: gaussian in (d, relbrg) around (d0, b0), ignoring state."""

    def __init__(self, d0=20.0, b0=0.0, sd=8.0, sb=25.0):
        self.d0, self.b0, self.sd, self.sb = d0, b0, sd, sb

    def score_samples(self, z):
        d, b = z[:, 3], z[:, 4]
        return (-0.5 * ((d - self.d0) / self.sd) ** 2
                - 0.5 * ((b - self.b0) / self.sb) ** 2)


def _model_dir(**kw):
    # identity standardization so the stub sees raw (gs, vr, alt, d, relbrg)
    return {"mu": np.zeros(5), "sd": np.ones(5), "density": _StubDensity(**kw)}


def _edge(lat=50.0, lon=10.0, track=0.0, source="adsb"):
    return dict(lat=lat, lon=lon, gs=150.0, vr=-500.0, alt=3000.0,
                track=track, source=source)


def _gkernel(scale_km=3.0):
    edges = np.linspace(0, 25, 126)
    c = 0.5 * (edges[:-1] + edges[1:])
    return {"r_edges": edges, "radial": np.exp(-c / scale_km)}


# --- footprint ------------------------------------------------------------

def test_l_footprint_unit_mass_and_peak_ahead():
    """Mass sums to 1 and concentrates ~20 km along the track direction
    (stub density peaks at d=20, relbrg=0; track=0 means due north)."""
    e = _edge(track=0.0)
    g = fp.Grid([e["lat"]], [e["lon"]])
    m = fp.l_footprint(g, e, _model_dir())
    assert m.sum() == pytest.approx(1.0)
    clat, clon = fp.mass_centre(g, m)
    assert clat > e["lat"]                      # ahead = north
    assert abs(clon - e["lon"]) < 0.3
    d_km = (clat - e["lat"]) * fp.KM_PER_DEG
    assert 10.0 < d_km < 35.0


def test_l_footprint_mlat_widening_spreads_mass():
    e_adsb = _edge(source="adsb")
    e_mlat = _edge(source="mlat")
    g = fp.Grid([e_adsb["lat"]], [e_adsb["lon"]])
    m1 = fp.l_footprint(g, e_adsb, _model_dir())
    m2 = fp.l_footprint(g, e_mlat, _model_dir())
    assert m2.max() < m1.max()                  # blurred -> flatter
    assert m2.sum() == pytest.approx(1.0)


def test_g_footprint_peaks_at_fix():
    g = fp.Grid([50.0], [10.0])
    m = fp.g_footprint(g, 50.0, 10.0, _gkernel())
    assert m.sum() == pytest.approx(1.0)
    clat, clon = fp.mass_centre(g, m)
    assert abs(clat - 50.0) < 0.05 and abs(clon - 10.0) < 0.05


def test_overlap_high_when_colocated_zero_when_far():
    """Two G fixes 2 km apart overlap strongly; 300 km apart, none."""
    g = fp.Grid([50.0, 50.018], [10.0, 10.0])
    a = fp.g_footprint(g, 50.0, 10.0, _gkernel())
    b = fp.g_footprint(g, 50.018, 10.0, _gkernel())
    near = fp.overlap(a, b)
    g2 = fp.Grid([50.0, 52.7], [10.0, 10.0])
    a2 = fp.g_footprint(g2, 50.0, 10.0, _gkernel())
    b2 = fp.g_footprint(g2, 52.7, 10.0, _gkernel())
    far = fp.overlap(a2, b2)
    assert near > 0.01
    assert far < 1e-6


# --- merge ----------------------------------------------------------------

def _table(lo=-3.0, hi=3.0):
    """Odds table: log-odds rises with score, independent of gap."""
    sg = np.linspace(-12, 0, 5)
    gg = np.linspace(-1, 2, 4)
    vals = np.tile(np.linspace(lo, hi, 5)[:, None], (1, 4))
    return mg.OddsTable(sg, gg, vals)


def test_decide_same_at_high_score():
    v, lo = mg.decide(score=0.5, gap_s=3600.0, sep_km=5.0, table=_table())
    assert v == mg.SAME and lo > 0


def test_decide_different_needs_feasible_movement():
    t = _table()
    # low score, huge gap -> movement feasible -> DIFFERENT
    v, _ = mg.decide(score=1e-11, gap_s=24 * 3600.0, sep_km=500.0, table=t)
    assert v == mg.DIFFERENT
    # low score but 500 km in 20 minutes is unflyable -> CONTRADICTORY
    v, _ = mg.decide(score=1e-11, gap_s=1200.0, sep_km=500.0, table=t)
    assert v == mg.CONTRADICTORY


def test_floor_margin_biases_toward_same():
    """log-odds slightly negative: plain odds say 'different', the floor
    margin keeps it SAME until 'different' is margin-times more probable."""
    t = _table(lo=-0.8, hi=-0.8)                # constant log-odds -0.8
    v, _ = mg.decide(0.1, 3600.0, 5.0, t, margin=3.0)
    assert v == mg.SAME                          # -0.8 >= -ln(3) = -1.10
    v, _ = mg.decide(0.1, 3600.0, 5.0, t, margin=1.0)
    assert v == mg.DIFFERENT                     # -0.8 < 0


def test_odds_table_clamps_outside_grid():
    t = _table()
    inside = t.log_odds(1e-6, 3600.0)
    below = t.log_odds(1e-30, 3600.0)
    assert np.isfinite(below) and below <= inside


# --- naming ---------------------------------------------------------------

class _StubClf:
    """Favours the geometrically nearest candidate (smallest d column)."""

    def predict_proba(self, z):
        d = z[:, 3]
        p1 = np.clip(1.0 - d / 60.0, 0.02, 0.98)
        return np.column_stack([1 - p1, p1])


def _cands():
    # two fields: one 10 km north of (50, 10), one 40 km north
    flat = np.array([50.09, 50.36])
    flon = np.array([10.0, 10.0])
    idents = ["NEAR", "FAR"]
    log_prior = np.array([1.0, 1.0])
    md = {"mu": np.zeros(5), "sd": np.ones(5), "clf": _StubClf()}
    return flat, flon, idents, log_prior, md


def test_field_posterior_and_abstain_ratio():
    flat, flon, idents, lp, md = _cands()
    e = _edge(track=0.0)
    post = nm.field_posterior(e, np.array([0, 1]), flat, flon, md, lp)
    name, log = nm.name_from_posterior(post, np.array([0, 1]), idents,
                                       ratio=1.5)
    assert name == "NEAR"
    assert log[0][0] == "NEAR" and log[0][1] > log[1][1]
    # a demanding ratio abstains
    name, _ = nm.name_from_posterior(post, np.array([0, 1]), idents,
                                     ratio=1e9)
    assert name is None


def test_name_merged_guard_blocks_unsupported_field():
    flat, flon, idents, lp, md = _cands()
    e = _edge()
    post = nm.field_posterior(e, np.array([0, 1]), flat, flon, md, lp)
    # both edges support NEAR -> named
    name, _ = nm.name_merged(post, post, np.array([0, 1]), np.array([0, 1]),
                             lp, idents, ratio=1.2, guard=0.1)
    assert name == "NEAR"
    # second edge gives NEAR almost nothing -> guard degrades to None
    post_b = np.array([0.02, 0.98])
    name, _ = nm.name_merged(post, post_b, np.array([0, 1]),
                             np.array([0, 1]), lp, idents, ratio=1.01,
                             guard=0.1)
    assert name is None


def test_name_merged_empty_candidate_intersection():
    flat, flon, idents, lp, md = _cands()
    e = _edge()
    post = nm.field_posterior(e, np.array([0]), flat, flon, md, lp)
    post2 = nm.field_posterior(e, np.array([1]), flat, flon, md, lp)
    name, log = nm.name_merged(post, post2, np.array([0]), np.array([1]),
                               lp, idents, ratio=1.2)
    assert name is None and log == []


def test_name_country_odds_gate():
    lut = {1: "DE", 2: "PL"}
    cc, log = nm.name_country({1: 0.9, 2: 0.05, 0: 0.05}, lut, country_R=3.0)
    assert cc == "DE" and log[0][0] == "DE"
    cc, _ = nm.name_country({1: 0.5, 2: 0.45}, lut, country_R=3.0)
    assert cc is None
    cc, _ = nm.name_country({0: 1.0}, lut, country_R=3.0)
    assert cc is None


# --- engine: dark-gap stay merging ----------------------------------------

def test_dark_gap_stays_merge_same_field():
    from backend.pipeline.etl.landing.engine import merge_dark_gap_stays
    stays = [dict(t_start=0.0, t_end=100.0, apt="AAAA", cc="DE",
                  glat=50.0, glon=10.0),
             dict(t_start=2000.0, t_end=2100.0, apt="AAAA", cc="DE",
                  glat=50.001, glon=10.0)]
    runs = [[0.0, 100.0], [2000.0, 2100.0]]
    groups = merge_dark_gap_stays(stays, runs)
    assert len(groups) == 1 and len(groups[0]) == 2


def test_dark_gap_stays_not_merged_when_air_observed_after():
    """The first stay's run extends 200 s past the stay (airborne points
    observed after takeoff) -> the gap is not pure silence -> two groups."""
    from backend.pipeline.etl.landing.engine import merge_dark_gap_stays
    stays = [dict(t_start=0.0, t_end=100.0, apt="AAAA", cc="DE",
                  glat=50.0, glon=10.0),
             dict(t_start=2000.0, t_end=2100.0, apt="AAAA", cc="DE",
                  glat=50.0, glon=10.0)]
    runs = [[0.0, 300.0], [2000.0, 2100.0]]
    assert len(merge_dark_gap_stays(stays, runs)) == 2


def test_dark_gap_stays_split_on_different_field():
    from backend.pipeline.etl.landing.engine import merge_dark_gap_stays
    stays = [dict(t_start=0.0, t_end=100.0, apt="AAAA", cc="DE",
                  glat=50.0, glon=10.0),
             dict(t_start=2000.0, t_end=2100.0, apt="BBBB", cc="DE",
                  glat=51.0, glon=10.0)]
    runs = [[0.0, 100.0], [2000.0, 2100.0]]
    assert len(merge_dark_gap_stays(stays, runs)) == 2
