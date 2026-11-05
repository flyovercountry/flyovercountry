"""train stage — train the landing-attribution model artifact.

Extracts admission-gated approach (arrival) and climb (departure) legs from the
raw traces and learns, per direction:
  - a DISCRIMINATIVE field scorer (true field vs decoy candidate) that names the
    airfield among candidates within REACH_KM;
  - a GMM conditional density P(d, btl | h, gs, vr) that supplies the country
    probability-mass readout over countries in the landing engine;
plus a log(1+count) base-traffic prior and a field abstain odds threshold
calibrated to the ~80%-attribution / <2.5%-false operating point on the in-bbox
held-out fold. Both levels ABSTAIN rather than guess (journalistic bar).

Writes data/build/etl/attribution_model.pkl (pickle). Regenerates by default;
set TRAIN_USE_CACHED=1 to keep an existing artifact.

Admission = the silence-endpoint test itself (grounding.landing_edges_directed):
arrival alt<LAND_ALT_FT & vr<-LAND_VR_FPM, departure alt<LAND_ALT_FT & vr>+LAND_VR_FPM;
continuity via 15-min-silence segments, time-nearest ground obs, non-descent gate.
Validated: field ~2.3% false at 80% attribution in-bbox; country density
integral ~100% on foreign landings.
"""
import json
import os
import pickle

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.mixture import GaussianMixture
from sklearn.neighbors import BallTree

from backend.pipeline.etl.constants import (
    AIRPORT_SNAP_RADIUS_KM, BLACKOUT_MIN_S, LAND_ALT_FT, LAND_VR_FPM,
    MAXAGE_S, REACH_KM, SNAP_KM,
)
from backend.pipeline.etl.loader import dated_trace_partitions, read_trace_partition
from backend.util.paths import etl_dir, lookup_dir
from backend.util.geo.haversine import (
    R_EARTH_KM, haversine_km, relative_bearing_deg,
)
from backend.util.geo.raster import GeoRaster
from backend.util.log import log

GATE_W_S = 120.0        # non-descent gate: tolerate <=this of not-going-the-landing-direction
GMM_K = 48              # GMM components for the P(d,btl|state) country density
                        # (parametric: score cost ~O(points x K), independent
                        # of training size)
FIELD_ATTR_RATE = 0.80  # calibrate the field abstain threshold to attribute this share on in-bbox eval
COUNTRY_R = 3.0         # country near-tie guard (validated ~100% precision; light abstain)
RNG = np.random.RandomState(0)


def _gate_stop(gaps, right):
    """Index of the first point where cumulative non-descent time exceeds
    GATE_W_S.  'right' marks points moving in the expected direction
    (descending for arrivals, climbing for departures); gaps between
    wrong-direction points accumulate until the budget is spent."""
    c = np.cumsum(np.where(right, 0.0, gaps)); base = np.maximum.accumulate(np.where(right, c, -1e18))
    base = np.where(base < -1e17, 0.0, base); over = np.where((c - base) > GATE_W_S)[0]
    return over[0] if len(over) else len(gaps)


def _extract(gi, tree, flat, flon, exc, bbox):
    """Scan the raw traces; return per-direction training tuples, decoy anchors,
    in-bbox eval edges (for threshold calibration), and the per-field landing
    count (the traffic prior)."""
    def snapf(la, lo):
        dq, iq = tree.query(np.radians([[la, lo]]), k=1)
        return int(iq[0, 0]) if dq[0, 0] * R_EARTH_KM <= SNAP_KM else -1

    # TR: training (d,btl,state) tuples; POS: point geometry for decoy
    # generation; EV: in-bbox eval edges for threshold calibration;
    # prior: per-field landing count. Even partitions = fold 0 (train),
    # odd = fold 1 (eval, in-bbox only).
    TR = {"arr": [], "dep": []}; POS = {"arr": [], "dep": []}
    EV = {"arr": [], "dep": []}; prior = np.zeros(len(flat))
    parts = dated_trace_partitions()
    log(f"train: scanning {len(parts)} trace partitions")
    for fi, p in enumerate(parts):
        fold = fi % 2
        df = read_trace_partition(
            p / "trace.parquet",
            columns=["icao24", "ts_s", "lat", "lon", "alt_ft", "gs_kt",
                     "on_ground", "vert_rate_fpm", "track_deg"])
        df = df[~df.icao24.isin(exc)]
        for ic, sub in df.sort_values("ts_s").groupby("icao24"):
            ts = sub.ts_s.values; la = sub.lat.values; lo = sub.lon.values; al = sub.alt_ft.values
            gs = sub.gs_kt.values; vr = sub.vert_rate_fpm.values; tk = sub.track_deg.values
            og = sub.on_ground.astype(bool).values
            air = (~og) & np.isfinite(al) & np.isfinite(gs) & np.isfinite(tk) & np.isfinite(vr)
            if not og.any() or not air.any() or len(ts) < 2:
                continue
            # Segment by BLACKOUT_MIN_S silences; within each segment
            # find ground runs (consecutive on-ground points) and extract
            # approach/departure legs from the airborne points flanking them.
            seg = np.concatenate([[0], np.cumsum(np.diff(ts) >= BLACKOUT_MIN_S)])
            for s in np.unique(seg):
                m = seg == s; A = np.where(m & air)[0]; gidx = np.where(m & og)[0]
                if len(A) == 0 or len(gidx) == 0:
                    continue
                runs = []; a0 = gidx[0]; prev = gidx[0]
                for x in gidx[1:]:
                    if x != prev + 1: runs.append((a0, prev)); a0 = x
                    prev = x
                runs.append((a0, prev))
                for ri, (rs, re) in enumerate(runs):
                    tf = snapf(la[rs], lo[rs])
                    if tf < 0:
                        continue
                    if fold == 0:
                        prior[tf] += 1
                    lob = runs[ri - 1][1] if ri > 0 else -1
                    hib = runs[ri + 1][0] if ri < len(runs) - 1 else len(ts)
                    # lob/hib: boundaries of the PREVIOUS/NEXT ground run
                    # (arrival candidates must postdate the prior run,
                    # departure candidates must predate the next).
                    for dirn, gref, cand, ok_dir in (
                        ("arr", rs, A[(A < rs) & (A > lob) & (ts[A] >= ts[rs] - MAXAGE_S)][::-1], vr < -LAND_VR_FPM),
                        ("dep", re, A[(A > re) & (A < hib) & (ts[A] <= ts[re] + MAXAGE_S)], vr > LAND_VR_FPM),
                    ):
                        if not len(cand):
                            continue
                        gaps = np.empty(len(cand)); gaps[0] = 0.0
                        gaps[1:] = np.abs(ts[cand[:-1]] - ts[cand[1:]])
                        right = ok_dir[cand]
                        k = cand[:_gate_stop(gaps, right)]
                        k = k[ok_dir[k] & (al[k] < LAND_ALT_FT)]
                        if not len(k):
                            continue
                        for j in (k if len(k) <= 4 else RNG.choice(k, 4, replace=False)):
                            ds = float(haversine_km(la[j], lo[j], flat[tf], flon[tf]))
                            bs = float(relative_bearing_deg(la[j], lo[j], flat[tf], flon[tf], tk[j]))
                            if fold == 0:
                                TR[dirn].append((gs[j], vr[j], al[j], ds, bs))
                                POS[dirn].append((la[j], lo[j], tk[j], tf))
                            elif ic in bbox:
                                ci = tree.query_radius(np.radians([[la[j], lo[j]]]), r=REACH_KM / R_EARTH_KM)[0]
                                if tf not in ci or len(ci) < 2:
                                    continue
                                dk = haversine_km(la[j], lo[j], flat[ci], flon[ci])
                                bk = relative_bearing_deg(la[j], lo[j], flat[ci], flon[ci], tk[j])
                                EV[dirn].append((gs[j], vr[j], al[j], ci, dk, bk, int(tf),
                                                 la[j], lo[j], tk[j]))   # +edge geo for country validation
        if (fi + 1) % 25 == 0:
            log(f"  ...partition {fi + 1}/{len(parts)}")
    return TR, POS, EV, prior


def _train_direction(TR, POS, EV, prior, flat, flon, tree):
    """Fit the field classifier + GMM density for one direction; calibrate the
    field abstain odds threshold to FIELD_ATTR_RATE on the in-bbox eval edges."""
    TR = np.array(TR)
    if len(TR) > 200000:
        TR = TR[RNG.choice(len(TR), 200000, replace=False)]
    mu = TR.mean(0); sd = TR.std(0) + 1e-9
    z = lambda X: (np.asarray(X, float) - mu) / sd
    # Parametric density for the country integral: a full-covariance GMM over
    # the standardized (gs,vr,alt,d,btl) tuples. score_samples() returns
    # per-sample log-density, which the country-mass integral downstream
    # consumes the same way regardless of estimator; cost here is O(K), not
    # O(n_train).
    k = min(GMM_K, max(1, len(TR) // 20))
    density = GaussianMixture(n_components=k, covariance_type="full",
                              reg_covar=1e-5, random_state=2).fit(z(TR))
    # field classifier: true tuple vs decoy tuples (same state, geometry to a wrong candidate)
    POS = np.array(POS, dtype=object)
    sub = RNG.choice(len(TR), min(50000, len(TR)), replace=False)
    Xd, yd = [], []
    for i in sub:
        gsv, vrv, hv, ds, bs = TR[i]; laj, loj, tkj, tf = POS[i]
        Xd.append([gsv, vrv, hv, ds, bs]); yd.append(1)
        ci = tree.query_radius(np.radians([[laj, loj]]), r=REACH_KM / R_EARTH_KM)[0]
        ci = ci[ci != int(tf)]
        for dc in ([] if not len(ci) else RNG.choice(ci, min(3, len(ci)), replace=False)):
            Xd.append([gsv, vrv, hv, float(haversine_km(laj, loj, flat[dc], flon[dc])),
                       float(relative_bearing_deg(laj, loj, flat[dc], flon[dc], tkj))]); yd.append(0)
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, max_depth=4, random_state=1).fit(z(np.array(Xd)), np.array(yd))
    lp = np.log1p(prior)
    # calibrate field_R: attribute the most-confident FIELD_ATTR_RATE of in-bbox eval edges
    odds = []
    for gsv, vrv, hv, ci, dk, bk, tf, *_ in EV:
        X5 = np.column_stack([np.full(len(ci), gsv), np.full(len(ci), vrv), np.full(len(ci), hv), dk, bk])
        pp = np.clip(clf.predict_proba(z(X5))[:, 1], 1e-6, 1 - 1e-6)
        post = (pp / (1 - pp)) * np.clip(lp[ci], 1e-6, None)
        if post.sum() <= 0:
            continue
        post = post / post.sum(); o = np.argsort(post)[::-1]
        odds.append(post[o[0]] / max(post[o[1]], 1e-12))
    field_R = float(np.quantile(odds, 1 - FIELD_ATTR_RATE)) if odds else 1.0
    log(f"train:   field_R={field_R:.2f} from {len(odds):,} in-bbox eval edges")
    return {"clf": clf, "mu": mu, "sd": sd, "density": density, "field_R": field_R}


def run_train():
    out = etl_dir() / "attribution_model.pkl"
    if os.environ.get("TRAIN_USE_CACHED") and out.exists():
        log("train: TRAIN_USE_CACHED set and artifact exists — keeping it")
        return
    gi = GeoRaster.load(lookup_dir())
    elig = [a for a in gi.airports if AIRPORT_SNAP_RADIUS_KM.get(a.get("type", ""), 0) > 0]
    flat = np.array([a["lat"] for a in elig]); flon = np.array([a["lon"] for a in elig])
    idents = [a.get("icao") or a.get("ident") or "" for a in elig]
    tree = BallTree(np.radians(np.column_stack([flat, flon])), metric="haversine")
    ac = pd.read_parquet(etl_dir() / "aircraft.parquet")
    exc = set(ac.query("excluded").icao24); bbox = set(ac.query("in_bbox").icao24)

    TR, POS, EV, prior = _extract(gi, tree, flat, flon, exc, bbox)
    model = {"elig_lat": flat, "elig_lon": flon, "elig_idents": idents,
             "log_prior": np.log1p(prior), "reach_km": REACH_KM, "country_R": COUNTRY_R}
    for dirn in ("arr", "dep"):
        log(f"train: {dirn} — {len(TR[dirn]):,} train tuples, {len(EV[dirn]):,} in-bbox eval edges")
        model[dirn] = _train_direction(TR[dirn], POS[dirn], EV[dirn], prior, flat, flon, tree)

    # merge-rule calibration: G kernels + odds(score, gap) table,
    # measured with the deployment footprint code against labeled pairs and
    # the G-silence-G prior. Second trace scan; report JSON kept for audit.
    from backend.pipeline.etl.landing import calibrate

    def snapf(la, lo):
        dq, iq = tree.query(np.radians([[la, lo]]), k=1)
        return int(iq[0, 0]) if dq[0, 0] * R_EARTH_KM <= SNAP_KM else -1

    model["merge"], cal_report = calibrate.run_calibration(model, exc, snapf)
    with open(etl_dir() / "train_calibration.json", "w") as f:
        json.dump(cal_report, f, indent=2)
    log(f"train: merge calibration — AUC {cal_report.get('auc', float('nan')):.3f}, "
         f"{cal_report['g2g_pairs']:,} G-silence-G prior pairs")

    with open(out, "wb") as f:
        pickle.dump(model, f)
    log(f"train: wrote {out} ({len(elig):,} eligible fields, {int((prior>0).sum()):,} with traffic)")
