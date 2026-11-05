"""Local-fit residual filter (denoise stage).

First stage after `types`. Flags position reports that disagree with the
aircraft's OWN surrounding track, and writes them to
data/build/etl/exclusions.parquet as (icao24, ts_s) pairs that every later
raw-trace read drops (see backend.pipeline.etl.loader).

Rule, in one sentence: a report is excluded when its distance from a
least-squares fit of its time-neighbours exceeds both K_SIGMA times the
local scatter and FLOOR_KM in absolute terms.

Why this shape rather than a speed threshold:
  - MLAT position error is scale-free (constant log-log tail slope from
    0.3 to 100 km under every conditioning tested), so no single speed or
    distance threshold separates "noise" from "anomaly". A test relative
    to the aircraft's own local behaviour does separate them: a report is
    wrong when it departs from what the surrounding track predicts, scaled
    by how noisy that track is right there.
  - It uses only neighbours on the SAME side when a long reception gap sits
    on the other side, so a report at a gap edge is still testable (the old
    speed test compared adjacent points and broke at gaps).
  - Provenance-blind and geography-blind: MLAT, ADS-B, and TIS-B all pass
    through the same test, everywhere, not just inside the analysis bbox --
    a visible map spur is a spur wherever it is.

Measured cost ~0.4% of all reports, concentrated in the tail (median
flagged report is ~13x its local scatter).
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from backend.pipeline.etl.constants import AIRCRAFT_BATCH, TELEPORT_KMH
from backend.pipeline.etl.loader import _is_placeholder
from backend.util import dimensions
from backend.util.geo.haversine import haversine_km
from backend.util.paths import etl_dir, raw_dir
from backend.util.log import log

# --- Detector parameters ---
K = 4                 # time-neighbours considered on each side
WINDOW_S = 180.0      # ...but only those within this many seconds
MIN_NEIGH = 3         # need at least this many usable neighbours to judge
K_SIGMA = 6.0         # residual must exceed this multiple of local scatter
FLOOR_KM = 3.0        # ...and this absolute distance
ROLL = 21             # rolling window for the local-scatter median
LOCAL_FLOOR_KM = 0.05  # local scatter never taken below this (quiet tracks)

BASE_COLS = ["icao24", "ts_s", "lat", "lon", "source"]


def _line_at_zero(t, y):
    """(value, slope) of the least-squares line y(t), evaluated at t=0."""
    m = len(t)
    st, stt = t.sum(), (t * t).sum()
    den = m * stt - st * st
    if abs(den) < 1e-9:
        return y.mean(), 0.0
    sy, sty = y.sum(), (t * y).sum()
    b = (m * sty - st * sy) / den
    return (sy - b * st) / m, b


def _predict(t, la, lo):
    """Predicted (lat, lon) at t=0 from neighbours, with leave-worst-out:
    drop the single most deviant neighbour before refitting so one bad
    report can't drag the fit. Needs > MIN_NEIGH to afford the drop."""
    a_la, b_la = _line_at_zero(t, la)
    a_lo, b_lo = _line_at_zero(t, lo)
    if len(t) > MIN_NEIGH:
        d = haversine_km(a_la + b_la * t, a_lo + b_lo * t, la, lo)
        keep = np.ones(len(t), dtype=bool)
        keep[int(np.argmax(d))] = False
        a_la, _ = _line_at_zero(t[keep], la[keep])
        a_lo, _ = _line_at_zero(t[keep], lo[keep])
    return a_la, a_lo


def _fit_residuals(ts, lat, lon):
    """Scalar reference implementation of the leave-worst-out residual.
    Kept as the correctness oracle for _fit_residuals_vec; the pipeline
    uses the vectorised version. NaN where too few neighbours."""
    n = len(ts)
    res = np.full(n, np.nan)
    if n < 2 * MIN_NEIGH + 1:
        return res
    for i in range(n):
        lo, hi = max(0, i - K), min(n, i + K + 1)
        idx = np.arange(lo, hi)
        idx = idx[idx != i]
        idx = idx[np.abs(ts[idx] - ts[i]) <= WINDOW_S]
        if len(idx) < MIN_NEIGH:
            continue
        pla, plo = _predict(ts[idx] - ts[i], lat[idx], lon[idx])
        res[i] = haversine_km(pla, plo, lat[i], lon[i])
    return res


_OFFSETS = np.array([d for d in range(-K, K + 1) if d != 0])


def _shift(a, d):
    """a[i+d], with NaN where out of bounds (no wraparound)."""
    out = np.full(a.shape, np.nan)
    if d >= 0:
        if d < len(a):
            out[:len(a) - d] = a[d:]
    else:
        out[-d:] = a[:len(a) + d]
    return out


def _fit_at_zero(m, St, Stt, Sla, Stla):
    """Vectorised least-squares intercept at t=0 from pre-accumulated
    neighbour sums. Falls back to the mean where t has no spread."""
    den = m * Stt - St * St
    ok = np.abs(den) > 1e-9
    b = np.where(ok, (m * Stla - St * Sla) / np.where(ok, den, 1.0), 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        a = np.where(m > 0, (Sla - b * St) / m, np.nan)
    return a, b


def _fit_residuals_vec(ts, lat, lon):
    """Vectorised leave-worst-out residual: identical result to
    _fit_residuals but computed over all points at once via shifted
    neighbour columns (no per-point Python loop)."""
    n = len(ts)
    res = np.full(n, np.nan)
    if n < 2 * MIN_NEIGH + 1:
        return res

    # Build neighbour columns: relative time and position at each offset
    T = np.stack([_shift(ts, d) - ts for d in _OFFSETS], axis=1)
    LA = np.stack([_shift(lat, d) for d in _OFFSETS], axis=1)
    LO = np.stack([_shift(lon, d) for d in _OFFSETS], axis=1)
    W = np.isfinite(T) & (np.abs(T) <= WINDOW_S)

    def sums(w):
        wf = w.astype(np.float64)
        Tm = np.where(w, T, 0.0)
        m = wf.sum(1)
        St = (wf * Tm).sum(1)
        Stt = (wf * Tm * Tm).sum(1)
        Sla = np.where(w, LA, 0.0).sum(1)
        Stla = (np.where(w, LA, 0.0) * Tm).sum(1)
        Slo = np.where(w, LO, 0.0).sum(1)
        Stlo = (np.where(w, LO, 0.0) * Tm).sum(1)
        return m, St, Stt, Sla, Stla, Slo, Stlo

    m, St, Stt, Sla, Stla, Slo, Stlo = sums(W)
    enough = m >= MIN_NEIGH
    a_la, b_la = _fit_at_zero(m, St, Stt, Sla, Stla)
    a_lo, b_lo = _fit_at_zero(m, St, Stt, Slo, Stlo)

    # Leave-worst-out: drop the single most deviant neighbour where affordable
    can_drop = m > MIN_NEIGH
    nres = haversine_km(a_la[:, None] + b_la[:, None] * T,
                         a_lo[:, None] + b_lo[:, None] * T, LA, LO)
    nres = np.where(W, nres, -np.inf)
    worst = np.argmax(nres, axis=1)
    W2 = W.copy()
    rows = np.nonzero(can_drop)[0]
    W2[rows, worst[rows]] = False

    m2, St2, Stt2, Sla2, Stla2, Slo2, Stlo2 = sums(W2)
    a_la2, _ = _fit_at_zero(m2, St2, Stt2, Sla2, Stla2)
    a_lo2, _ = _fit_at_zero(m2, St2, Stt2, Slo2, Stlo2)
    fa_la = np.where(can_drop, a_la2, a_la)
    fa_lo = np.where(can_drop, a_lo2, a_lo)

    r = haversine_km(fa_la, fa_lo, lat, lon)
    res[enough] = r[enough]
    return res


def _flag_aircraft(ts, lat, lon):
    """Boolean mask of reports to exclude for one aircraft's sorted trace."""
    res = _fit_residuals_vec(ts, lat, lon)
    ok = np.isfinite(res)
    if not ok.any():
        return np.zeros(len(ts), dtype=bool)
    local = (pd.Series(res).rolling(ROLL, center=True, min_periods=5)
             .median().to_numpy())
    local = np.where(np.isfinite(local) & (local > LOCAL_FLOOR_KM),
                     local, LOCAL_FLOOR_KM)
    return ok & (res > K_SIGMA * local) & (res > FLOOR_KM)


def _teleport_mask(ts, lat, lon):
    """Catch physically impossible jumps that the local-fit filter misses.

    The local-fit filter needs several neighbours to form a fit; this
    needs only one, so it covers the sparse-coverage residue.  A report
    is flagged when EVERY neighbour within 30 minutes (previous and/or
    next, where present) implies travel above TELEPORT_KMH (1 km/s).

    A conflicting PAIR flags both members: with one report against one
    there is no adjudicating which is real, and floor-counting drops both."""
    n = len(ts)
    if n < 2:
        return np.zeros(n, dtype=bool)

    dt_prev = np.full(n, np.inf)
    dt_prev[1:] = ts[1:] - ts[:-1]
    d_prev = np.full(n, np.inf)
    d_prev[1:] = haversine_km(lat[1:], lon[1:], lat[:-1], lon[:-1])
    dt_next = np.roll(dt_prev, -1); dt_next[-1] = np.inf
    d_next = np.roll(d_prev, -1); d_next[-1] = np.inf

    with np.errstate(invalid="ignore", divide="ignore"):
        v_prev = np.where(dt_prev > 0, d_prev / (dt_prev / 3600.0), np.inf)
        v_next = np.where(dt_next > 0, d_next / (dt_next / 3600.0), np.inf)

    near_prev = dt_prev <= 1800.0
    near_next = dt_next <= 1800.0
    return np.where(
        near_prev & near_next, np.minimum(v_prev, v_next) > TELEPORT_KMH,
        np.where(near_prev, v_prev > TELEPORT_KMH,
                 np.where(near_next, v_next > TELEPORT_KMH, False)))


def run_denoise():
    t0 = time.time()
    etl = etl_dir()
    aircraft_path = etl / "aircraft.parquet"
    if not aircraft_path.exists():
        log("denoise: aircraft.parquet not found — run types first")
        return

    aircraft = pd.read_parquet(aircraft_path)
    keep_list = sorted(aircraft.loc[~aircraft["excluded"], "icao24"])
    keep = set(keep_list)
    log(f"denoise: scanning {len(keep):,} relevant aircraft for local-fit "
         f"residual outliers (k_sigma={K_SIGMA}, floor={FLOOR_KM} km)")

    # Process in bounded batches so the whole-range trace frame is never
    # resident at once. Reads raw parquet directly rather than through
    # loader.load_traces() because this stage PRODUCES the exclusion list
    # that loader's other callers rely on -- routing through it would be
    # circular.
    n_pts = 0
    excl_hex, excl_ts = [], []
    n_air = 0
    n_tele = 0
    for b in range(0, len(keep_list), AIRCRAFT_BATCH):
        batch = keep_list[b:b + AIRCRAFT_BATCH]
        frames = []
        for d in dimensions.dates():
            p = raw_dir() / f"date={d}" / "trace.parquet"
            if not p.exists() or _is_placeholder(p):
                continue
            t = pd.read_parquet(p, columns=BASE_COLS,
                                filters=[("icao24", "in", batch)])
            if len(t):
                frames.append(t)
        if not frames:
            continue
        T = pd.concat(frames, ignore_index=True)
        n_pts += len(T)
        for hex24, sub in T.groupby("icao24", sort=False):
            s = sub.sort_values("ts_s")
            ts = s["ts_s"].to_numpy(np.float64)
            la = s["lat"].to_numpy(np.float64)
            lo = s["lon"].to_numpy(np.float64)
            mask = _flag_aircraft(ts, la, lo)
            # Teleport check runs on surviving reports so a lone spike
            # already caught by the local-fit filter is not double-counted.
            kept = ~mask
            tk = _teleport_mask(ts[kept], la[kept], lo[kept])
            if tk.any():
                mask[np.where(kept)[0][tk]] = True
                n_tele += int(tk.sum())
            if mask.any():
                n_air += 1
                excl_hex.extend([hex24] * int(mask.sum()))
                excl_ts.extend(s["ts_s"].to_numpy()[mask].tolist())

    if n_pts == 0:
        log("denoise: no raw traces found")
        _write(pd.DataFrame({"icao24": [], "ts_s": []}), etl)
        return

    excl = pd.DataFrame({"icao24": excl_hex, "ts_s": excl_ts}) \
        .sort_values(["icao24", "ts_s"], kind="mergesort") \
        .reset_index(drop=True)
    _write(excl, etl)

    log(f"denoise: teleport residue excluded: {n_tele:,} reports")
    log(f"denoise: {len(excl):,} reports flagged across {n_air} aircraft "
         f"({100 * len(excl) / max(1, n_pts):.3f}% of {n_pts:,} points) "
         f"in {time.time() - t0:.1f}s")


def _write(excl: pd.DataFrame, etl) -> None:
    from backend.pipeline.etl.loader import reset_exclusion_cache
    etl.mkdir(parents=True, exist_ok=True)
    # Empty DataFrames lose column dtypes on round-trip; force int64 when
    # there are rows so downstream readers get a consistent schema.
    excl.astype({"ts_s": "int64"} if len(excl) else {}) \
        .to_parquet(etl / "exclusions.parquet", index=False)
    # Reset the loader's exclusion cache so later stages in the same
    # process pick up the list just written (types stage may have
    # populated an empty cache before this stage existed).
    reset_exclusion_cache()
