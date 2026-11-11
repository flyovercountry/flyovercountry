"""Naming — the abstention-first place readout for landing events.

Strictly decoupled from counting: no outcome here changes how many landings
exist. Field naming uses the validated field classifier x traffic weight
posterior with the top-vs-second abstain ratio (field_R, calibrated to the
~80%-named operating point). Merged stays are named jointly, guarded: the
named field must carry at least NAME_GUARD_FLOOR posterior mass in EACH
edge's own posterior — a wrong merge therefore degrades to country-level
naming instead of mis-naming a field. Country: a named field's country wins
(table lookup); otherwise the footprint's per-country mass with the
country_R odds gate; otherwise unplaced.

Pure functions; candidate geometry is passed in, no I/O.
"""
from __future__ import annotations

import numpy as np

from backend.pipeline.etl.constants import NAME_GUARD_FLOOR
from backend.util.geo.haversine import haversine_km, relative_bearing_deg


def field_posterior(edge, cand_idx, flat, flon, model_dir, log_prior):
    """Normalized posterior over the candidate airfields for one edge.

    cand_idx: indices into the eligible-field arrays (flat/flon/log_prior),
    the fields within reach of the edge. Returns (posterior array aligned
    with cand_idx) or None when no candidate carries any mass."""
    if len(cand_idx) == 0:
        return None
    dk = haversine_km(edge["lat"], edge["lon"], flat[cand_idx], flon[cand_idx])
    bk = relative_bearing_deg(edge["lat"], edge["lon"], flat[cand_idx],
                              flon[cand_idx], edge["track"])
    X = np.column_stack([
        np.full(len(cand_idx), edge["gs"]),
        np.full(len(cand_idx), edge["vr"]),
        np.full(len(cand_idx), edge["alt"]), dk, bk])
    z = (X - model_dir["mu"]) / model_dir["sd"]
    pp = np.clip(model_dir["clf"].predict_proba(z)[:, 1], 1e-6, 1 - 1e-6)
    post = (pp / (1 - pp)) * np.clip(log_prior[cand_idx], 1e-6, None)
    s = post.sum()
    return post / s if s > 0 else None


def name_from_posterior(post, cand_idx, idents, ratio):
    """argmax iff it beats the runner-up by `ratio` (odds); else None.
    Returns (ident or None, top-5 [(ident, prob)] audit log)."""
    if post is None:
        return None, []
    o = np.argsort(post)[::-1]
    log = [(idents[int(cand_idx[k])], float(post[k])) for k in o[:5]]
    second = post[o[1]] if len(o) > 1 else 0.0
    if post[o[0]] / max(second, 1e-12) >= ratio:
        return idents[int(cand_idx[o[0]])], log
    return None, log


def name_merged(post_a, post_b, cand_a, cand_b, log_prior, idents, ratio,
                guard=NAME_GUARD_FLOOR):
    """Joint naming for a merged stay, with the per-edge support guard.

    Combines the two edges' evidence over the candidate intersection
    (odds product / shared prior — the prior enters once), names the argmax
    iff it clears `ratio` AND holds >= `guard` posterior in EACH edge's own
    posterior. Returns (ident or None, audit log)."""
    if post_a is None or post_b is None:
        return None, []
    common, ia, ib = np.intersect1d(cand_a, cand_b, return_indices=True)
    if len(common) == 0:
        return None, []
    prior = np.clip(log_prior[common], 1e-6, None)
    joint = (post_a[ia] / prior) * (post_b[ib] / prior) * prior
    s = joint.sum()
    if s <= 0:
        return None, []
    joint = joint / s
    o = np.argsort(joint)[::-1]
    log = [(idents[int(common[k])], float(joint[k])) for k in o[:5]]
    second = joint[o[1]] if len(o) > 1 else 0.0
    if joint[o[0]] / max(second, 1e-12) < ratio:
        return None, log
    if post_a[ia[o[0]]] < guard or post_b[ib[o[0]]] < guard:
        return None, log            # guard: an edge doesn't support it
    return idents[int(common[o[0]])], log


def name_country(cmass, iso_lut, country_R):
    """Country from a footprint's per-country mass ({cid: mass}, cid 0 =
    water/uncovered excluded). argmax iff its mass beats the runner-up by
    country_R. Returns (iso2 or None, [(iso2, share)] audit log)."""
    items = [(cid, m) for cid, m in cmass.items() if cid != 0]
    if not items:
        return None, []
    items.sort(key=lambda kv: -kv[1])
    tot = sum(m for _, m in items)
    log = [(iso_lut.get(cid) or "", m / tot) for cid, m in items[:5]]
    second = items[1][1] if len(items) > 1 else 0.0
    if items[0][1] / max(second, 1e-12) >= country_R:
        return iso_lut.get(items[0][0]), log
    return None, log
