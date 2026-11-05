"""The landing signature — THE shared, directional test for a landing at a gap edge.

One vectorized predicate over a blackout's two boundary points, used by BOTH
the split stage (LANDING_SPLIT gate) and the landing engine's silence-edge
attribution. The signature is DIRECTIONAL: a landing shows as descending INTO
the gap at the pre-edge (an arrival) or climbing OUT of it at the post-edge (a
departure). The reverse — a pre-edge that is climbing, or a post-edge that is
descending — is an aircraft still in flight as it went dark or as it returned,
not a landing, and must not be treated as one. Neither the verdict nor the
attribution splits on those.

Sharing this one function is the split/landings consistency guarantee, made
structural by identity: the landing engine emits a landing event at an edge
only when this test admits it, and the LANDING_SPLIT gate ORs the same two
edges and is evaluated before any JOIN can be returned — so a landing recorded
inside a gap the verdict called one continuous flight is impossible by
construction. (An earlier revision briefly made the verdict's gate unsigned;
that split on the wrong-direction edges and broke the correspondence. It is
directional again, shared verbatim. See
test_landing_attribution_edges_shared_verbatim.)
"""
from __future__ import annotations

from backend.pipeline.etl.constants import LAND_ALT_FT, LAND_VR_FPM


def landing_edges_directed(pre_alt_ft, pre_vr_fpm, post_alt_ft, post_vr_fpm):
    """(arrival, departure) — the directed landing edges. Vectorized over
    aligned Series or arrays; NaN altitude/rate fails every comparison (no
    signal).

    arrival:   pre-gap edge below LAND_ALT_FT and SINKING past LAND_VR_FPM
               (descending toward a landing that began after this last report).
    departure: post-gap edge below LAND_ALT_FT and CLIMBING past LAND_VR_FPM
               (a takeoff whose ground time ended before this first report).

    A climbing pre-edge or a descending post-edge yields neither — that
    aircraft was flying, not landing.
    """
    arrival = (pre_alt_ft < LAND_ALT_FT) & (pre_vr_fpm < -LAND_VR_FPM)
    departure = (post_alt_ft < LAND_ALT_FT) & (post_vr_fpm > LAND_VR_FPM)
    return arrival, departure
