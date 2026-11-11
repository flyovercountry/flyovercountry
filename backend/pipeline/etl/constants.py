"""Physics and rule constants shared across the etl stages.

One home for every tuned threshold. Import these instead of redefining
them — two stages disagreeing on a quantity is exactly the kind of drift
this module exists to prevent.
"""

# --- Time binning (partition stage) ------------------------------------------
# Ternary-profile bin width. Matches the ingest downsample interval, which is
# itself ~the native adsb.lol trace cadence.
BIN_S = 10

# --- Whole-range trace loads (partition, denoise, edge stages) ---------------
# Aircraft per load_traces()/raw-parquet pass in the stages that walk every
# relevant aircraft's whole-range trace: bounds each stage's resident trace
# frame regardless of crop-window length. Each batch re-scans every date's
# trace.parquet (the icao24 filter is pushed down into the parquet read), so
# this trades repeated I/O for peak memory — sized against an OOM on a
# 7.8 GB-RAM host loading ~6800 aircraft x 51 days as one DataFrame.
AIRCRAFT_BATCH = 300

# --- Blackout definition (partition + split stages) ------------------------
# Minimum off-gap that counts as a blackout. Shorter off-runs are receiver
# dropouts at cruise — definite joins, never emitted by the partition stage;
# the split stage asserts it never sees one.
BLACKOUT_MIN_S = 900

# --- Verdict rule (split stage) -------------------------------------------
# Gap above which a blackout is always a SPLIT, no kinematics consulted.
LONG_SPLIT_S = 12 * 3600.0
# Landing signature: boundary below LAND_ALT_FT with |vertical rate| past
# LAND_VR_FPM (unsigned — a steep climb or descent at either boundary is a
# landing-shaped edge). Defined once in grounding.landing_signature and
# shared VERBATIM by the verdict rule (LANDING_SPLIT gate) and the landing
# engine's silence-edge landing scan — that sharing is the split/landings
# consistency guarantee.
LAND_ALT_FT = 15000.0
LAND_VR_FPM = 120.0

# --- Teleport residue (denoise stage) ---------------------------------------
# A report is excluded when every surviving neighbor within 30 min demands
# implied travel above this speed — physically impossible, the same
# 1 km/s standard the fast-jump analysis uses.
# Catches what the local-fit filter misses when neighbors are sparse
# (the fit needs several points; this rule needs one neighbor).
# CAUTION: any bound below ~700 kt ground speed flags normal jet cruise
# wholesale. Thresholds validated on slow-report neighborhoods do not
# transfer to cruise.
TELEPORT_KMH = 3600.0

# --- Landing evidence (attribute stage) --------------------------------------
# Flights inherit origin/destination from landing evidence ONLY within a
# continuously observed span: no reception gap >= BLACKOUT_MIN_S between
# the evidence and the flight edge, and no other observed flight in
# between. There is NO staleness constant — the project's premise is that
# any blackout-length silence may conceal a flight, so no claim is ever
# carried across one; conversely, continuous observation licenses the
# link regardless of elapsed time (see attribute._same_observation_run).

# Ground indication (Q1, field-free): an
# airborne report indicates ground contact when it is BOTH slow (at/below
# the transport fleet's approach regime — C-17 ~140 kt, C-130 ~120 kt)
# AND below a FIXED MSL ceiling. Fixed-MSL knowingly under-fits elevated
# terrain (a plane on Bavarian ground sits near the ceiling) — accepted
# for consistency: which-field/which-country is a SECOND pass over each
# assembled interval's lowest points, never an admission gate.
LOW_APPROACH_GS_KT = 160.0
LOW_SLOW_MSL_FT = 2500.0
# Field-relative ceiling used by the snap's weight blending.
LOW_APPROACH_AGL_FT = 2500.0

# Second-pass attribution: an interval's "lowest points" are its reports
# within this many feet of the interval's minimum altitude (ground-flag
# reports always included); the airfield containing most of them wins
# (weighted score breaks ties), else the unanimous raster country, else
# no place is named. Convention, documented.
LOWEST_BAND_FT = 300.0

# Military-weighted airport snap (backend/util/geo/airport_snap.py): score
# is distance / (radius * effective_weight); effective weight blends from
# 1.0 (point on the ground: pure distance, no ambiguity) up to the type
# weight as the point's field-relative altitude approaches
# LOW_APPROACH_AGL_FT. Types absent from these maps cannot win a snap at
# all (heliports, seaplane bases, small strips).
# Validated on the full corpus with a per-field-radius truth rule and a
# conditional-logit fit rounded to simple numbers: bbox test accuracy
# 59.7% vs 47.9% under the previous 10/4/1. The gain lives at bands
# >=2k ft AGL; at the 0-2k naming bands the change measured as a wash,
# so adoption is robustness, not a correction of published outputs.
# Note the score divides by min(radius, cap) x weight, and radius is a
# pure function of type in the airport data — the effective per-type
# multiplier is that product, not this number alone.
AIRPORT_TYPE_WEIGHT = {
    "military_base": 2.5,
    "large_airport":  5.0,
    "medium_airport": 1.0,
}
AIRPORT_SNAP_RADIUS_KM = {
    "military_base": 18.0,
    "large_airport":  6.0,
    "medium_airport": 3.0,
}

# --- Theater mission chains (theater stage) ----------------------------------
# Ground gap that BREAKS a mission chain (not a window): a stop longer than
# this between consecutive flights ends one chain and starts the next. 48 h
# separates mid-mission crew rest from a genuine stand-down (24 h over-
# splits, 72 h under-splits).
CHAIN_BREAK_S = 48 * 3600

# --- Transatlantic wind-correction gate (split stage) -----------------------
# A blackout qualifies as an Atlantic crossing (wind correction applies in
# the verdict rule) when it spans this longitude band with enough distance
# Transatlantic split gate: a silence lasting at least TA_MIN_GAP_S whose two
# endpoints straddle the TA_MERIDIAN (the analysis bbox's western edge) has one
# end outside the study area and is split outright — we neither can nor need to
# classify its far side.
TA_MERIDIAN = -31.0
TA_MIN_GAP_S = 3 * 3600
# SLOW_SPLIT gate: a silence whose implied speed allowing a 15-min ground stop
# (impl15 = gc_km / (gap_h - 0.25)) falls below this is split as unaccounted
# ground time. A policy threshold in an irreducibly ambiguous overlap (the
# classified population — landings hidden inside coverage holes — is
# unobservable and missing-not-at-random, so no ground truth exists to tune
# against); placed by eye at the density valley between the landing-shaped and
# transit-shaped populations. Loitering aircraft are systematically split.
SLOW_SPLIT_IMPL15_KPH = 460.0

# --- Theater-evidence gap (theater stage) ----------------------------------
# Reception gap that counts as a disappearance/reappearance at the Balkans
# box edge. DELIBERATELY coarser than BLACKOUT_MIN_S: this asks "did the
# aircraft genuinely leave coverage here", not "should flights be split".
THEATER_GAP_S = 1800

# --- Trace-point sufficiency (flight stage) -----------------------------------
FLIGHT_MIN_PTS = 2  # flights with fewer post-denoise points are dropped (flight stage)

# --- Landing engine (landing/footprint.py, merge.py, naming.py) --------------
# Footprint grid cell size. MUST match the calibration grid: the merge odds
# table is calibrated on overlap scores computed at this resolution
# (landing/calibrate.py).
FOOTPRINT_CELL_KM = 10.0
# MLAT position-noise widening: an L-edge footprint whose edge position came
# from MLAT is blurred by a gaussian of this sigma (measured MLAT ground
# scatter p95 ~16 km -> sigma ~ half of that).
MLAT_POS_SIGMA_KM = 8.0
# Two-stay verdicts imply an invisible flight between the footprint centres;
# it must be flyable within the gap (minus a 15-min ground stop) at ferry
# speed, else the evidence is CONTRADICTORY (noise), not a second landing.
MAX_FERRY_KPH = 900.0
# Floor-doctrine margin: declare TWO stays only when the calibrated odds say
# "different places" is at least this many times more probable than "same".
# Splitting a true single stay fabricates a landing (forbidden direction);
# merging two true stays undercounts by one (tolerated). Final value subject
# to replay-validation sign-off (fabricated-landings <= 1% anchor).
MERGE_FLOOR_MARGIN = 3.0
# Naming-consistency guard for merged stays: the jointly named field must
# carry at least this much posterior mass in EACH edge's own field posterior,
# else the merged stay degrades to country-level naming. Makes wrong merges
# harmless to naming (they undercount but never mis-name).
NAME_GUARD_FLOOR = 0.10

# --- Landing model geometry & timing (landing/train.py, calibrate.py, --------
# --- engine.py, footprint.py) -------------------------------------------
# Candidate-airfield / footprint support radius: how far from an edge or
# footprint centre a field is still considered a candidate.
REACH_KM = 150.0
# A landing's true field = the eligible airfield within this of the
# on-ground point (train's labeling, calibrate's field snap).
SNAP_KM = 6.0
# A leg/edge point may be at most this far (seconds) from the landing it's
# associated with.
MAXAGE_S = 2700.0
# Ground-fix agreement radius for the same-place test (engine's dark-gap
# stay merge, calibrate's labeled-pair construction).
SAME_PLACE_KM = 10.0
