"""Merge-rule calibration: fit the model that decides whether two ground
silences are at the same place or different places.

Produces three outputs consumed by merge.py at runtime:

  g_kernel_adsb / g_kernel_mlat
      Empirical radial ground-fix kernels from within-stay taxi
      displacement.  These tell the footprint scorer how much spatial
      uncertainty a ground fix carries (MLAT is wider than ADS-B).

  odds table  (2-D: overlap score x gap duration)
      log-odds(same place | overlap score, gap) =
          log-LR from class-conditional score densities
        + log prior-odds from G-silence-G pairs.
      Class-conditional densities come from labeled pairs: same-stay
      (one stay's own arrival/departure edges) vs different-place
      (consecutive stays that snap to different fields).  Scores use
      the SAME footprint code as deployment so densities match exactly.

  G-silence-G prior
      From real silences bounded by ground fixes on both sides: what
      fraction involved no movement, by gap duration.  Measured
      96.6-100% same-place at every gap bucket.

Called once per train run.  Second pass over the raw traces (after the
GMM direction models are fitted).
"""
from __future__ import annotations

import time

import numpy as np

from backend.pipeline.etl.constants import (
    BLACKOUT_MIN_S, LAND_ALT_FT, LAND_VR_FPM, MAXAGE_S, SAME_PLACE_KM,
)
from backend.pipeline.etl.landing import footprint as fp
from backend.pipeline.etl.loader import dated_trace_partitions, read_trace_partition
from backend.util.geo.haversine import haversine_km
from backend.util.log import log

PAIR_CAP = 4000
MAX_SEP_KM = 320.0
_EDGE_SEARCH_LIMIT = 50
_KDE_BW = 0.25
_RNG = np.random.default_rng(7)


# ------------------------------------------------------------- extraction

def _ground_fix(src, lat, lon, rs, re):
    """First ADS-B ground position in a ground run, falling back to MLAT."""
    for i in range(rs, re + 1):
        if src[i] != "mlat":
            return float(lat[i]), float(lon[i]), True
    return float(lat[rs]), float(lon[rs]), False


def _airborne_edge(ts, seg, lat, lon, alt, gs, vr, track, src, og, air,
                   j0, step, want_descent):
    """Search outward from a ground-run boundary for a landing-shaped
    airborne point: below LAND_ALT_FT with vertical rate past LAND_VR_FPM
    in the expected direction.  Returns a dict of edge evidence or None."""
    if not (0 <= j0 - step < len(ts)):
        return None
    s0 = seg[j0 - step]
    ref = ts[j0 - step]
    j = j0
    for _ in range(_EDGE_SEARCH_LIMIT):
        if j < 0 or j >= len(ts) or seg[j] != s0:
            return None
        if abs(ts[j] - ref) > MAXAGE_S or og[j]:
            return None
        if air[j] and alt[j] < LAND_ALT_FT and (
                vr[j] < -LAND_VR_FPM if want_descent
                else vr[j] > LAND_VR_FPM):
            return dict(ts=float(ts[j]), lat=float(lat[j]),
                        lon=float(lon[j]), gs=float(gs[j]),
                        vr=float(vr[j]), alt=float(alt[j]),
                        track=float(track[j]),
                        source=str(src[j]))
        j += step
    return None


def extract(exc, snapf):
    """Scan all raw traces to collect calibration inputs.

    Returns (stays, taxi, g2g):
      stays: per-ground-run dicts with edges, ground fix, and field snap
      taxi:  (max_displacement_km, has_mlat) per run with 2+ points
      g2g:   (gap_s, same_place) for ground-bounded silences
    """
    stays, taxi, g2g = [], [], []
    # Carry forward last observation per aircraft across partition files,
    # so G-silence-G pairs that span file boundaries are not missed.
    carry = {}
    parts = dated_trace_partitions()
    log(f"train: calibration scan over {len(parts)} partitions")

    for fi, p in enumerate(parts):
        df = read_trace_partition(
            p / "trace.parquet",
            columns=["icao24", "ts_s", "lat", "lon", "alt_ft", "gs_kt",
                     "on_ground", "vert_rate_fpm", "track_deg", "source"])
        df = df[~df.icao24.isin(exc)]

        for ic, sub in df.sort_values("ts_s").groupby("icao24"):
            ts = sub.ts_s.values; la = sub.lat.values; lo = sub.lon.values
            al = sub.alt_ft.values; gs = sub.gs_kt.values
            vr = sub.vert_rate_fpm.values; tk = sub.track_deg.values
            og = sub.on_ground.astype(bool).values
            src = sub.source.astype(str).values
            air = (~og) & np.isfinite(al) & np.isfinite(gs) \
                & np.isfinite(tk) & np.isfinite(vr)
            if len(ts) == 0:
                continue
            if not og.any():
                carry[ic] = (float(ts[-1]), False, None, None)
                continue

            # Segment boundaries: a gap >= BLACKOUT_MIN_S starts a new segment
            seg = np.concatenate([[0], np.cumsum(np.diff(ts) >= BLACKOUT_MIN_S)])

            # Identify contiguous ground runs
            gidx = np.where(og)[0]
            runs = []
            a0 = prev = gidx[0]
            for x in gidx[1:]:
                if x != prev + 1 or seg[x] != seg[prev]:
                    runs.append((a0, prev)); a0 = x
                prev = x
            runs.append((a0, prev))

            # Cross-file G-silence-G: compare carried state to first ground run
            first_run = runs[0]
            if ic in carry:
                cts, cg, cla, clo = carry[ic]
                gap = ts[0] - cts
                if cg and gap >= BLACKOUT_MIN_S and first_run[0] == 0:
                    fla, flo, _ = _ground_fix(src, la, lo, *first_run)
                    f1, f2 = snapf(cla, clo), snapf(fla, flo)
                    same = (f1 == f2) if (f1 >= 0 and f2 >= 0) else \
                        (haversine_km(cla, clo, fla, flo) <= SAME_PLACE_KM)
                    g2g.append((float(gap), bool(same)))

            for ri, (rs, re) in enumerate(runs):
                # Taxi displacement: max distance from the first ground point
                if re - rs + 1 >= 2:
                    d0 = haversine_km(la[rs], lo[rs],
                                      la[rs:re + 1], lo[rs:re + 1])
                    taxi.append((float(np.nanmax(d0)),
                                 bool((src[rs:re + 1] == "mlat").any())))

                fla, flo, adsb_g = _ground_fix(src, la, lo, rs, re)
                stays.append(dict(
                    seg=(fi, ic, int(seg[rs])),
                    t0=float(ts[rs]), t1=float(ts[re]),
                    glat=fla, glon=flo, adsb_g=adsb_g,
                    field=snapf(fla, flo),
                    arr=_airborne_edge(ts, seg, la, lo, al, gs, vr, tk,
                                       src, og, air, rs - 1, -1, True),
                    dep=_airborne_edge(ts, seg, la, lo, al, gs, vr, tk,
                                       src, og, air, re + 1, +1, False)))

                # Within-file G-silence-G: adjacent runs separated by a gap
                if ri > 0 and runs[ri - 1][1] == rs - 1 \
                        and ts[rs] - ts[rs - 1] >= BLACKOUT_MIN_S:
                    pla, plo, _ = _ground_fix(src, la, lo, *runs[ri - 1])
                    f1, f2 = snapf(pla, plo), snapf(fla, flo)
                    same = (f1 == f2) if (f1 >= 0 and f2 >= 0) else \
                        (haversine_km(pla, plo, fla, flo) <= SAME_PLACE_KM)
                    g2g.append((float(ts[rs] - ts[rs - 1]), bool(same)))

            last = runs[-1]
            if og[-1]:
                lla, llo, _ = _ground_fix(src, la, lo, *last)
                carry[ic] = (float(ts[-1]), True, lla, llo)
            else:
                carry[ic] = (float(ts[-1]), False, None, None)

        if (fi + 1) % 25 == 0:
            log(f"  ...partition {fi + 1}/{len(parts)}")

    return stays, taxi, g2g


# ------------------------------------------------------------- fitting

def fit_g_kernels(taxi):
    """Empirical radial ground-fix kernels from taxi displacement samples.

    Separate kernels for ADS-B and MLAT: MLAT positions are noisier, so
    the kernel is wider (60 km vs 25 km support).  Each kernel is a
    histogram density normalized to a radial density (per km^2), which
    footprint.g_footprint() interpolates at runtime."""
    radii = np.array([t for t, _ in taxi])
    is_mlat = np.array([m for _, m in taxi])
    out = {}
    for name, sel, rmax in (("adsb", ~is_mlat, 25.0), ("mlat", is_mlat, 60.0)):
        edges = np.linspace(0, rmax, 61)
        hist, _ = np.histogram(np.clip(radii[sel], 0, rmax - 1e-6),
                               bins=edges, density=True)
        centres = 0.5 * (edges[:-1] + edges[1:])
        # Convert linear density to radial density (per km^2) by dividing
        # out the circumference at each radius
        radial = (hist + 1e-9) / np.maximum(2 * np.pi * centres, 1e-6)
        out[name] = {"r_edges": edges, "radial": radial}
    return out["adsb"], out["mlat"]


def _score_pair(model, ea, ed):
    """Footprint overlap score for one (arrival, departure) edge pair."""
    if haversine_km(ea["lat"], ea["lon"], ed["lat"], ed["lon"]) > MAX_SEP_KM:
        return 0.0
    grid = fp.Grid([ea["lat"], ed["lat"]], [ea["lon"], ed["lon"]])
    ma = fp.l_footprint(grid, ea, model["arr"])
    md = fp.l_footprint(grid, ed, model["dep"])
    return fp.overlap(ma, md)


def score_labeled_pairs(stays, model):
    """Build same-place and different-place pair sets from observed stays,
    score each pair with the deployment footprint code.

    Same-place pairs: a single stay's own arrival and departure edges
    (the aircraft landed and took off from the same spot).

    Different-place pairs: consecutive stays in one segment whose ADS-B
    ground fixes snap to different fields."""
    by_seg: dict = {}
    for s in stays:
        by_seg.setdefault(s["seg"], []).append(s)

    same_pairs, diff_pairs = [], []
    for ss in by_seg.values():
        ss.sort(key=lambda r: r["t0"])
        for s in ss:
            if s["arr"] and s["dep"]:
                same_pairs.append((s["arr"], s["dep"]))
        for k in range(len(ss) - 1):
            a, b = ss[k], ss[k + 1]
            if not (a["arr"] and b["dep"] and a["adsb_g"] and b["adsb_g"]):
                continue
            if a["field"] >= 0 and b["field"] >= 0:
                same = a["field"] == b["field"]
            else:
                same = haversine_km(a["glat"], a["glon"],
                           b["glat"], b["glon"]) <= SAME_PLACE_KM
            if not same:
                diff_pairs.append((a["arr"], b["dep"]))

    def cap(lst):
        if len(lst) <= PAIR_CAP:
            return lst
        idx = _RNG.choice(len(lst), PAIR_CAP, replace=False)
        return [lst[i] for i in idx]

    same_pairs, diff_pairs = cap(same_pairs), cap(diff_pairs)
    s_same = np.array([_score_pair(model, a, d) for a, d in same_pairs])
    s_diff = np.array([_score_pair(model, a, d) for a, d in diff_pairs])
    return s_same, s_diff


def _kde(x, grid, bw=_KDE_BW):
    """Gaussian KDE on log10-scores, fixed bandwidth."""
    x = x[np.isfinite(x)]
    d = (grid[:, None] - x[None, :]) / bw
    return np.exp(-0.5 * d * d).sum(axis=1) / max(len(x), 1) / (
        bw * np.sqrt(2 * np.pi))


def build_odds_table(s_same, s_diff, g2g):
    """Combine score densities and the G-silence-G prior into a 2-D
    log-odds table: axes are log10(overlap score) and log10(gap hours).

    The merge decision at runtime is a bilinear interpolation into this
    table, so it encodes the full Bayesian posterior in a single lookup."""
    log10 = lambda a: np.log10(np.maximum(a, 1e-12))

    # Class-conditional KDE on log10(overlap score)
    sgrid = np.linspace(-12.5, 0.5, 105)
    d_same = _kde(log10(s_same), sgrid)
    d_diff = _kde(log10(s_diff), sgrid)
    log_lr = np.log(d_same + 1e-9) - np.log(d_diff + 1e-9)

    # Prior odds from G-silence-G pairs, bucketed by gap duration.
    # Haldane-Anscombe smoothing: (s + 0.5) / (n - s + 0.5)
    buckets = [(0.25, 1), (1, 3), (3, 6), (6, 12), (12, 24), (24, 48),
               (48, 96), (96, 400)]
    gap_h = np.array([x[0] for x in g2g]) / 3600.0
    same_flag = np.array([x[1] for x in g2g])
    centres, lodds, ns = [], [], []
    for lo_, hi_ in buckets:
        k = (gap_h >= lo_) & (gap_h < hi_)
        n = int(k.sum())
        if n < 20:
            continue
        s = int(same_flag[k].sum())
        centres.append(np.log10(np.sqrt(lo_ * hi_)))
        lodds.append(np.log((s + 0.5) / (n - s + 0.5)))
        ns.append(n)

    # Interpolate prior onto a regular log10(gap-hours) grid
    ggrid = np.linspace(-1.0, 2.6, 40)
    prior = np.interp(ggrid, centres, lodds)

    # Full table: log-odds = log-LR(score) + log-prior-odds(gap)
    table = log_lr[:, None] + prior[None, :]

    report = {
        "pairs_same": int(len(s_same)), "pairs_diff": int(len(s_diff)),
        "g2g_pairs": int(len(gap_h)),
        "prior_buckets": [
            {"center_log10_h": float(c), "log_odds": float(o), "n": n}
            for c, o, n in zip(centres, lodds, ns)],
    }
    if len(s_same) and len(s_diff):
        # AUC via Wilcoxon-Mann-Whitney rank-sum statistic
        v = np.concatenate([s_same, s_diff])
        rk = np.argsort(np.argsort(v)) + 1
        report["auc"] = float(
            (rk[:len(s_same)].mean() - (len(s_same) + 1) / 2) / len(s_diff))

    return {"odds_log10s": sgrid, "odds_log10gap_h": ggrid,
            "odds_log_odds": table}, report


def run_calibration(model, exc, snapf):
    """Entry point.  Returns (merge_artifact_dict, audit_report_dict)."""
    t0 = time.time()
    stays, taxi, g2g = extract(exc, snapf)
    log(f"train: {len(stays):,} stays, {len(taxi):,} taxi, "
         f"{len(g2g):,} G-silence-G pairs ({time.time()-t0:.0f}s)")
    k_adsb, k_mlat = fit_g_kernels(taxi)
    ts = time.time()
    s_same, s_diff = score_labeled_pairs(stays, model)
    log(f"train: scored {len(s_same):,}+{len(s_diff):,} labeled pairs "
         f"({time.time()-ts:.0f}s)")
    tab, report = build_odds_table(s_same, s_diff, g2g)
    taxi_r = np.array([t for t, _ in taxi])
    report["taxi_p95_km"] = float(np.percentile(taxi_r, 95))
    merge = dict(tab, g_kernel_adsb=k_adsb, g_kernel_mlat=k_mlat)
    return merge, report
