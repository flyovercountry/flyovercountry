"""Theater-theater stage: mark flights by theater involvement.

Reads raw trace parquets + data/build/etl/flights.parquet. For each flight
computes:
  - th_chain:     the flight's MISSION CHAIN touches the theater. An
                  aircraft's flights (sorted by start time) are split
                  into chains wherever the ground gap between one
                  flight's end and the next flight's start exceeds 48
                  hours — the 48 h gap is a chain BREAK condition, not
                  a time window. A chain is theater-relevant iff any of
                  the aircraft's theater evidence falls inside the
                  chain's span extended by half of each delimiting
                  ground gap (so every evidence event belongs to
                  exactly one chain); all flights of a relevant chain
                  are marked.
  - th_ever:      the aircraft has ANY theater evidence in the whole
                  data interval; all its flights are marked.

Theater evidence (per aircraft, over its full trace): any trace point
in the ME bbox, or a Balkans-bbox transit whose smoothed bearing at a
blackout edge lies on the theater axis — heading into the ESE-Levant /
eastern-Mediterranean arc on disappearance, or back out of it on
appearance. The cone excludes the NE-E quadrant (travel to/from Ukraine
and Black-Sea recon).

Updates data/build/etl/flights.parquet in place with the theater columns. No
lookahead-window or displacement column is emitted; any such column left
by an older build (traversed_countries_7d, max_disp_7d_km, in_theater_7d,
max_disp_km) is dropped if present.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backend.util import dimensions
from backend.pipeline.etl.constants import CHAIN_BREAK_S, THEATER_GAP_S
from backend.pipeline.etl.loader import load_traces
from backend.util.geo.haversine import bearing_deg
from backend.util.paths import etl_dir
from backend.util.log import log

# Lookahead-window and displacement columns that this stage never emits;
# dropped from the parquet when re-running the theater stage on an older build that still
# carries them.
_RETIRED_COLUMNS = (
    "max_disp_7d_km", "in_theater_7d", "traversed_countries_7d",
    "max_disp_km",
)


def _bbox_mask(lats: np.ndarray, lons: np.ndarray, bb: dict) -> np.ndarray:
    return (
        (lats >= bb["lat_south"]) & (lats <= bb["lat_north"])
        & (lons >= bb["lon_west"]) & (lons <= bb["lon_east"])
    )


# Bearing cone for the Balkans transit filter.  South cone (95-190 deg):
# aircraft heading into the ME/Levant arc on disappearance.  North cone
# (275-10 deg): heading back out on appearance.  Excludes the NE-E
# quadrant (Ukraine/Black Sea traffic).
_SOUTH_CENTER = 142.5
_NORTH_CENTER = 322.5
_BEARING_TOLERANCE = 47.5
_SMOOTH_POINTS = 4  # number of in-bbox points to average bearing over


def _bearing_in_range(bearing: float, center: float, tolerance: float) -> bool:
    """True if bearing is within +-tolerance of center (wrapping at 360)."""
    diff = (bearing - center + 180) % 360 - 180
    return abs(diff) <= tolerance


def _smoothed_bearing(lats: np.ndarray, lons: np.ndarray) -> float:
    """Average bearing across consecutive point pairs, using circular mean."""
    sins, coss = 0.0, 0.0
    for i in range(len(lats) - 1):
        b = float(bearing_deg(lats[i], lons[i], lats[i + 1], lons[i + 1]))
        sins += np.sin(np.radians(b))
        coss += np.cos(np.radians(b))
    return float((np.degrees(np.arctan2(sins, coss)) + 360) % 360)


def _balkans_event_times(
    ts: np.ndarray, lats: np.ndarray, lons: np.ndarray, bb: dict,
    t_range_start: float, t_range_end: float,
) -> list[float]:
    """Timestamps of Balkans-bbox transit events with ME-consistent bearing.

    Finds runs of consecutive in-bbox points (separated by >30 min
    blackouts) over the aircraft's FULL trace. A run edge adjacent to a
    blackout yields an evidence event when its smoothed bearing is:
    - Disappearance (blackout after run): heading into the theater arc
      (south cone 95-190 deg, ESE-Levant through S)
    - Appearance (blackout before run): heading back out of it
      (north cone 275-10 deg, WNW through N)

    At the data-range edges the blackout status is unknowable, so runs
    ending within 30 min of the range boundary do not produce events.
    """
    in_bb = _bbox_mask(lats, lons, bb)
    if not np.any(in_bb):
        return []
    n = len(ts)
    bb_indices = np.where(in_bb)[0]
    if len(bb_indices) < 2:
        return []

    runs: list[list[int]] = []
    current_run: list[int] = [int(bb_indices[0])]
    for k in range(1, len(bb_indices)):
        prev_idx, cur_idx = int(bb_indices[k - 1]), int(bb_indices[k])
        if cur_idx == prev_idx + 1 and ts[cur_idx] - ts[prev_idx] < THEATER_GAP_S:
            current_run.append(cur_idx)
        else:
            runs.append(current_run)
            current_run = [cur_idx]
    runs.append(current_run)

    events: list[float] = []
    for run in runs:
        if len(run) < 2:
            continue

        last_idx = run[-1]
        first_idx = run[0]

        # Disappearance check
        if last_idx < n - 1:
            is_disappearance = ts[last_idx + 1] - ts[last_idx] >= THEATER_GAP_S
        else:
            is_disappearance = t_range_end - ts[last_idx] >= THEATER_GAP_S
        if is_disappearance:
            tail = run[-_SMOOTH_POINTS:]
            if len(tail) >= 2:
                b = _smoothed_bearing(lats[tail], lons[tail])
                if _bearing_in_range(b, _SOUTH_CENTER, _BEARING_TOLERANCE):
                    events.append(float(ts[last_idx]))

        # Appearance check
        if first_idx > 0:
            is_appearance = ts[first_idx] - ts[first_idx - 1] >= THEATER_GAP_S
        else:
            is_appearance = ts[first_idx] - t_range_start >= THEATER_GAP_S
        if is_appearance:
            head = run[:_SMOOTH_POINTS]
            if len(head) >= 2:
                b = _smoothed_bearing(lats[head], lons[head])
                if _bearing_in_range(b, _NORTH_CENTER, _BEARING_TOLERANCE):
                    events.append(float(ts[first_idx]))

    return events


def _theater_evidence_times(
    h_ts: np.ndarray, h_lat: np.ndarray, h_lon: np.ndarray,
    me_bboxes: list[dict], balkans_bboxes: list[dict],
    t_range_start: float, t_range_end: float,
) -> np.ndarray:
    """Sorted unique timestamps of one aircraft's theater evidence."""
    parts = [h_ts[_bbox_mask(h_lat, h_lon, bb)] for bb in me_bboxes]
    for bb in balkans_bboxes:
        parts.append(np.asarray(_balkans_event_times(
            h_ts, h_lat, h_lon, bb, t_range_start, t_range_end)))
    if not parts:
        return np.empty(0)
    return np.unique(np.concatenate(parts))


def _chain_relevance(
    flight_indices_sorted: list[int],
    flight_starts: np.ndarray, flight_ends: np.ndarray,
    evidence: np.ndarray,
) -> dict[int, bool]:
    """Per-flight theater relevance under the mission-chain reading.

    Chains: consecutive flights with ground gaps <= CHAIN_BREAK_S; a
    gap above the threshold breaks the chain. Each chain's evidence
    span is its own [first start, last end] extended by half of each
    delimiting gap (first/last chain extended to +-inf), so every
    evidence event is attributed to exactly one chain.
    """
    fs = flight_indices_sorted
    chains: list[list[int]] = [[fs[0]]]
    for a, b in zip(fs, fs[1:]):
        if flight_starts[b] - flight_ends[a] > CHAIN_BREAK_S:
            chains.append([b])
        else:
            chains[-1].append(b)

    rel: dict[int, bool] = {}
    for k, ch in enumerate(chains):
        lo = (-np.inf if k == 0 else
              (flight_ends[chains[k - 1][-1]] + flight_starts[ch[0]]) / 2)
        hi = (np.inf if k == len(chains) - 1 else
              (flight_ends[ch[-1]] + flight_starts[chains[k + 1][0]]) / 2)
        touched = bool(np.searchsorted(evidence, hi, side="right")
                       > np.searchsorted(evidence, lo, side="left"))
        for fi in ch:
            rel[fi] = touched
    return rel


def _relevance_aircraft(
    h_ts: np.ndarray, h_lat: np.ndarray, h_lon: np.ndarray,
    flight_indices_sorted: list[int],
    flight_starts: np.ndarray, flight_ends: np.ndarray,
    me_bboxes: list[dict], balkans_bboxes: list[dict],
    t_range_start: float, t_range_end: float,
    th_chain: np.ndarray, th_ever: np.ndarray,
) -> None:
    """Fill the per-flight output arrays for one aircraft's flights."""
    evidence = _theater_evidence_times(
        h_ts, h_lat, h_lon, me_bboxes, balkans_bboxes,
        t_range_start, t_range_end)
    ever = len(evidence) > 0
    rel = _chain_relevance(
        flight_indices_sorted, flight_starts, flight_ends, evidence)
    for fi in flight_indices_sorted:
        th_ever[fi] = ever
        th_chain[fi] = rel[fi]


def run_theater():
    t0 = time.time()
    etl = etl_dir()
    flights_path = etl / "flights.parquet"
    if not flights_path.exists():
        log("theater: flights.parquet not found — run flight first")
        return

    flights = pd.read_parquet(flights_path)
    log(f"theater: {len(flights):,} flights")

    bboxes = dimensions.bboxes()
    crop_bbox = dimensions.crop()
    me_bboxes = [b for b in bboxes[1:] if b.get("name") == "me"]
    balkans_bboxes = [b for b in bboxes[1:] if b.get("name") == "balkans"]

    # Explicit UTC parse (matches partition.py's _crop_interval), rather than
    # pd.Timestamp(date).timestamp() — both happen to agree (pandas treats a
    # naive Timestamp as UTC too) but this is the one pattern the pipeline
    # documents, so drift can't creep in if that pandas behavior ever changes.
    t_range_start = float(datetime.strptime(dimensions.start(), "%Y-%m-%d")
                          .replace(tzinfo=timezone.utc).timestamp())
    t_range_end = float(datetime.strptime(dimensions.end(), "%Y-%m-%d")
                        .replace(tzinfo=timezone.utc).timestamp()) + 86400

    th_chain = np.zeros(len(flights), dtype=bool)
    th_ever = np.zeros(len(flights), dtype=bool)

    flight_hexes = flights["icao24"].values
    flight_starts = flights["t_start"].values.astype(np.float64)
    flight_ends = flights["t_end"].values.astype(np.float64)

    hex_to_flights: dict[str, list[int]] = {}
    for i in range(len(flights)):
        hex_to_flights.setdefault(flight_hexes[i], []).append(i)

    log("theater: loading trace points")
    # load_traces excludes denoise-flagged outliers; unsorted because
    # per-hex time order is applied below in the per-aircraft loop.
    traces = load_traces(set(hex_to_flights),
                         columns=["icao24", "ts_s", "lat", "lon"], sort=False)
    # category dtype avoids millions of repeated hex string allocations
    traces["icao24"] = traces["icao24"].astype("category")
    log(f"theater: {len(traces):,} trace points loaded")

    if crop_bbox:
        before = len(traces)
        traces = traces[_bbox_mask(
            traces["lat"].values, traces["lon"].values, crop_bbox,
        )].reset_index(drop=True)
        log(f"theater: crop filter {before:,} -> {len(traces):,} points")

    trace_ts = traces["ts_s"].values.astype(np.float64)
    trace_lat = traces["lat"].values.astype(np.float64)
    trace_lon = traces["lon"].values.astype(np.float64)

    hex_groups = traces.groupby("icao24", sort=False, observed=True)
    n_done = 0

    for hex24, flight_indices in hex_to_flights.items():
        if hex24 not in hex_groups.groups:
            n_done += 1
            continue

        grp_idx = np.asarray(hex_groups.groups[hex24])
        # Per-aircraft time sort avoids sorting the whole frame at once
        grp_idx = grp_idx[np.argsort(trace_ts[grp_idx], kind="mergesort")]
        h_ts = trace_ts[grp_idx]
        h_lat = trace_lat[grp_idx]
        h_lon = trace_lon[grp_idx]

        flight_indices_sorted = sorted(
            flight_indices, key=lambda i: flight_starts[i])

        _relevance_aircraft(
            h_ts, h_lat, h_lon, flight_indices_sorted,
            flight_starts, flight_ends,
            me_bboxes, balkans_bboxes,
            t_range_start, t_range_end,
            th_chain, th_ever,
        )

        n_done += 1
        if n_done % 200 == 0:
            log(f"  ...{n_done}/{len(hex_to_flights)} aircraft")

    flights = flights.drop(
        columns=[c for c in _RETIRED_COLUMNS if c in flights.columns])
    flights["th_chain"] = th_chain
    flights["th_ever"] = th_ever

    flights.to_parquet(flights_path, index=False)
    elapsed = time.time() - t0
    log(f"theater: updated {flights_path} "
         f"({len(flights):,} flights, {elapsed:.0f}s)")

    log(f"theater: theater — chain {int(flights['th_chain'].sum()):,} flights, "
         f"ever {int(flights['th_ever'].sum()):,} flights")
