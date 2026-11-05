"""Split/join verdict per blackout (split stage).

A type-blind, four-gate rule. Each blackout takes the first gate that
matches (np.select priority order):

  LONG_SPLIT          gap > 12 h → SPLIT. A multi-day absence cannot be a
                      single continuous flight.
  TRANSATLANTIC_SPLIT gap >= 3 h and the two endpoints straddle the 31°W
                      meridian (the analysis bbox's western edge), with an
                      antimeridian-wrap guard → SPLIT. One end lies outside
                      the study area; we neither can nor need to classify it.
                      (Flag computed in the edge stage as
                      transatlantic_split.)
  LANDING_SPLIT       a directional landing edge at either boundary — below
                      15,000 ft with vertical rate past 120 fpm in the landing
                      direction: descending INTO the gap at the pre-edge
                      (arrival) or climbing OUT of it at the post-edge
                      (departure); a climbing pre-edge or descending post-edge
                      is still-in-flight and does NOT split
                      (grounding.landing_edges_directed) → SPLIT. Evaluated
                      ABOVE any JOIN so the shared-signature landings/verdict
                      consistency guarantee holds structurally (a landing can
                      never sit inside a JOIN).
  SLOW_SPLIT          otherwise, if impl15 < SLOW_SPLIT_IMPL15_KPH → SPLIT;
                      else → JOIN. impl15 = gc_km / (gap_h - 0.25) is the
                      average speed needed to cover the displacement allowing
                      a 15-minute ground stop; below the threshold the time is
                      unaccounted for.

Why there is no "impossibly fast → JOIN" gate (an earlier open question):
with SLOW_SPLIT's absolute cutoff, any silence fast enough to be
"impossible" (impl15 far above the cutoff) already JOINs by default, so such
a gate would be redundant — its only non-redundant effect would be to JOIN a
silence that also carries a landing signature, which the landings/verdict
invariant forbids. It is therefore omitted, and this rule consumes no speed
distribution: it does NOT read speed_bands.parquet.

The SLOW_SPLIT threshold is a policy choice in an irreducibly ambiguous
overlap. The classified population — aircraft that may have landed inside a
coverage hole — is unobservable by construction and censored
missing-not-at-random (transponders off for operational reasons), so no
ground truth exists to tune against; the threshold is placed at the observed
density valley between the landing-shaped and transit-shaped populations, and
loitering aircraft are systematically rounded to SPLIT. See the methodology
page's blackout-verdict section.

Reads  data/build/etl/edges.parquet.
Writes data/build/etl/verdicts.parquet keyed (icao24, interval_id, pre_ts)
       with a single payload column: verdict (SPLIT|JOIN). The rule's
       intermediates (reason, impl15) stay inside decide() and the stage
       log; only verdict is published (the sole downstream consumer,
       flight.py, reads verdict).

Blackouts <= 15 min never reach this stage (definite joins, not emitted by
the partition stage) — asserted here, which also keeps gap_h - 0.25 > 0.
"""
import numpy as np
import pandas as pd

from backend.pipeline.etl.grounding import landing_edges_directed
from backend.pipeline.etl.constants import (
    BLACKOUT_MIN_S, LONG_SPLIT_S, SLOW_SPLIT_IMPL15_KPH,
)
from backend.util.paths import etl_dir
from backend.util.log import log


def decide(pb):
    """Verdict frame for a blackout-boundaries frame. Pure; no IO."""
    assert (pb.gap_s > BLACKOUT_MIN_S).all(), \
        "blackouts <= 15 min must not reach the split stage"

    v = pb[["icao24", "interval_id", "pre_ts", "gap_s", "gc_km"]].copy()

    # impl15: average speed required allowing a 15-min ground stop. gap_s >
    # BLACKOUT_MIN_S (900 s) is asserted above, so gap_h - 0.25 > 0 always.
    gap_h = pb.gap_s / 3600.0
    v["impl15"] = pb.gc_km / (gap_h - 0.25)

    # Directional landing signature, shared with the landing stage via
    # grounding.landing_edges_directed.  LANDING_SPLIT is ordered above
    # every JOIN so the split/landings consistency guarantee is structural.
    arrival, departure = landing_edges_directed(
        pb.pre_alt_ft, pb.pre_vert_rate_fpm,
        pb.post_alt_ft, pb.post_vert_rate_fpm)

    long_split = v.gap_s > LONG_SPLIT_S
    ta_split = pb.transatlantic_split.to_numpy()
    landing = (arrival | departure).to_numpy()
    slow = v.impl15 < SLOW_SPLIT_IMPL15_KPH

    v["reason"] = np.select(
        [long_split, ta_split, landing, slow],
        ["LONG_SPLIT", "TRANSATLANTIC_SPLIT", "LANDING_SPLIT", "SLOW_SPLIT"],
        default="JOIN")
    v["verdict"] = np.where(v.reason == "JOIN", "JOIN", "SPLIT")
    return v


def run_split():
    """Decide every blackout; write data/build/etl/verdicts.parquet."""
    boundaries_path = etl_dir() / "edges.parquet"
    if not boundaries_path.exists():
        log("split: edges.parquet not found — run edge first")
        return

    pb = pd.read_parquet(boundaries_path)
    v = decide(pb)
    _characterize(v)
    published = v[["icao24", "interval_id", "pre_ts", "verdict"]]
    published.to_parquet(etl_dir() / "verdicts.parquet", index=False)
    return published


def _characterize(v):
    n = len(v)
    scope = v[v.gap_s <= LONG_SPLIT_S]
    ns = len(scope)
    log(f"split: {n:,} blackouts "
         f"({int((v.verdict == 'SPLIT').sum()):,} SPLIT, "
         f"{int((v.verdict == 'JOIN').sum()):,} JOIN)")
    log("split:   by reason: " + "  ".join(
        f"{r}={c:,}" for r, c in v.reason.value_counts().items()))
    if ns:
        sp = int((scope.verdict == "SPLIT").sum())
        log(f"split:   in 15min-12h scope (n={ns:,}): SPLIT {sp:,} "
             f"({100*sp/ns:.1f}%) JOIN {ns-sp:,} ({100*(ns-sp)/ns:.1f}%)")
