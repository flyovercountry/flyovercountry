"""Export etl parquets to static JSON/GeoJSON for the frontend.

Every published key has a named consumer (frontend/src/*.js); keys
nobody reads are removed rather than kept
for contract stability. Legacy-era key names the frontend still uses
(e.g. flights.json "n"/"id"/"cc") are kept verbatim even where the
code now says "flight".

Reads data/build/etl/{flights,aircraft,traces,sighting_times,landings}.parquet
and data/build/lookup/{boundaries.geojson,roles.html,roles.reference.json}.

Output: data/out/data/ (build_dir — ETL-generated, gitignored, written
directly; no intermediate to flatten later)
  manifest.json           date range, type enum, dates, metadata
  role_catalog.json       role/group catalog (see the roles taxonomy in data/in/config/roles.json)
  routes.geojson       per-flight route geometry (desktop resolution)
  routes-mobile.geojson  per-flight route geometry (mobile resolution)
  flights.json            columnar per-flight metadata lookup table
  hex_roles.json          {hex: [role_id, ...]} for in-scope aircraft
  boundaries.geojson      country boundary lines (copied from data/build/lookup/)
  sightings/<cc>.json     per-country actual-sighting index
  landings.geojson        per-landing points (icao, h, d, t, cc)
  airports_meta.json      airport tooltip metadata

Also writes, one level up in data/out/ (build_dir().parent — where the
frontend build's static/ assets land, so the served URLs /roles.html and
/roles.reference.json are unaffected by where the pipeline generates them):
  roles.html              aircraft-role reference page (copied from data/build/lookup/)
  roles.reference.json    same taxonomy, as data for the in-app roles view

The three focus-config JSONs (countries.modes.json, common.texts.json,
country.reference.json) are not written here — they are git-tracked source
at data/in/config/frontend/, copied into data/out/data/ by a separate step
(frontend/copy-data.mjs, run as a vite plugin on every `vite build`). This
stage only *reads* them, via load_focus_config(), for the sighting-index
country list. See backend/util/focus_config.py.

Also writes data/build/role_findings.md (not served, working state — see
_write_role_findings).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backend.util.countries import COUNTRY_ALIASES, normalize_country
from backend.util.focus_config import load_focus_config
from backend.util.paths import data_dir, etl_dir, build_dir, frontend_config_dir, lookup_dir
from backend.util.roles import taxonomy, EXCLUDED_ROLES
from backend.util.log import log


_CONFIG_DIR = frontend_config_dir()

_GAP_THRESHOLD_S = 3600
_OUTLIER_DEG = 5.0
_N_ALT_BANDS = 16
_ALT_BAND_KFT = 2.5


def _alt_band(kft: float) -> int:
    """Band index for a kft altitude; -1 for UNKNOWN (NaN) altitude.

    Unknown is distinct from ground level: ~7% of raw reports carry no
    altitude, and rendering them as band 0 painted false ground-level
    segments into cruise routes. Callers fill -1 from neighbouring known
    bands (see _coarse_to_route_segments)."""
    if np.isnan(kft):
        return -1
    if kft < 0:
        return 0
    return min(int(kft / _ALT_BAND_KFT), _N_ALT_BANDS - 1)


def _is_outlier(lat, lon, prev_lat, prev_lon, next_lat, next_lon):
    """Detect a spike: point far from both neighbors while neighbors are close."""
    d_prev = abs(lat - prev_lat) + abs(lon - prev_lon)
    d_next = abs(lat - next_lat) + abs(lon - next_lon)
    d_neighbors = abs(next_lat - prev_lat) + abs(next_lon - prev_lon)
    return d_prev > _OUTLIER_DEG and d_next > _OUTLIER_DEG and d_neighbors < _OUTLIER_DEG



# -- helpers -------------------------------------------------------------------

def _is_nan(v) -> bool:
    if v is None:
        return True
    try:
        return math.isnan(v)
    except (TypeError, ValueError):
        return False


def _safe_int(v, default: int = 0, scale: float = 1.0) -> int:
    if _is_nan(v):
        return default
    return int(round(float(v) * scale))


def _date_to_epoch(iso: str) -> float:
    return datetime.strptime(iso, "%Y-%m-%d").replace(
        tzinfo=timezone.utc,
    ).timestamp()


def _epoch_to_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


_norm_country = normalize_country


def _norm_country_csv(csv) -> str:
    if not csv or (isinstance(csv, float) and math.isnan(csv)):
        return ""
    parts = sorted(set(
        _norm_country(c) for c in str(csv).split(",") if c.strip()
    ))
    return ",".join(parts)


def _atomic_write(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _write_json(path: Path, obj):
    _atomic_write(path, json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode())




# -- role catalog ---------------------------------------------------------------

# Group definitions (order, slug, label, default on-flag, member role
# slugs — see the roles taxonomy in data/in/config/roles.json) are single-sourced in
# data/in/config/roles.json's "groups" key and read here via
# backend.util.roles.taxonomy()["groups"]; this module holds no copy of its own.
_PSEUDO_ROLES = ("typecode_unknown", "type_unknown", "role_unknown")


def _role_label(slug: str) -> str:
    return slug.replace("_", " ")


def _flight_role_lists(flights: pd.DataFrame) -> list[list[str]]:
    """Per-flight list of role slugs from the sorted comma-joined 'roles'
    column (empty string / NaN -> empty list; should not occur post
    roles.py, but tolerated defensively)."""
    out = []
    for v in flights["roles"].fillna("").values:
        s = str(v).strip()
        out.append(s.split(",") if s else [])
    return out


def _build_role_catalog(
    flights: pd.DataFrame, aircraft: pd.DataFrame,
    data_end: str, crop_start: str, crop_end: str,
) -> tuple[dict, np.ndarray, list[list[str]]]:
    """Build the role_catalog.json payload (see the roles taxonomy in data/in/config/roles.json).

    Returns (catalog_dict, role_ids_per_flight, role_lists_per_flight)
    where role_ids_per_flight is an object array (list[int] per flight,
    ragged) suitable for flights.json/geojson "rs" properties.

    Only roles with >=1 flight in the whole flights table (already the
    full crop interval — export reads the post-partition/geography
    flights.parquet) get catalog ids. A role present in flights but
    belonging to no spec group and not in EXCLUDED_ROLES is a build
    ERROR (spec: fail loudly rather than silently drop it).
    """
    flight_role_lists = _flight_role_lists(flights)

    flight_counts: dict[str, int] = {}
    for roles in flight_role_lists:
        for r in roles:
            flight_counts[r] = flight_counts.get(r, 0) + 1

    icao_by_role: dict[str, set] = {}
    icao_vals = flights["icao24"].values
    for i, roles in enumerate(flight_role_lists):
        for r in roles:
            icao_by_role.setdefault(r, set()).add(icao_vals[i])

    populated = sorted(flight_counts)  # roles with >=1 flight

    tax = taxonomy()
    group_spec = tax["groups"]  # single-sourced in config/roles.json

    all_group_roles = {r for g in group_spec for r in g["roles"]}
    orphans = [r for r in populated
               if r not in all_group_roles and r not in EXCLUDED_ROLES]
    if orphans:
        raise ValueError(
            f"role_catalog: role(s) {orphans} have flights but belong to no "
            "spec group and are not in EXCLUDED_ROLES — taxonomy/spec change "
            "required (see data/build/role_findings.md 'other role' review queue)")

    role_id = {slug: i for i, slug in enumerate(populated)}
    roles_out = [
        {
            "id": role_id[slug],
            "slug": slug,
            "label": _role_label(slug),
            "airframes": len(icao_by_role.get(slug, ())),
            "flights": flight_counts.get(slug, 0),
        }
        for slug in populated
    ]

    groups_out = []
    gid = 0
    for g in group_spec:
        members = [r for r in g["roles"] if r in role_id]
        if not members:
            continue
        gid += 1
        groups_out.append({
            "id": gid,
            "slug": g["slug"],
            "label": g["label"],
            "on": g["on"],
            "roles": [role_id[r] for r in members],
        })

    n_rotary = int(aircraft["rotary"].sum())
    n_non_aircraft = int((aircraft["excluded"] & ~aircraft["rotary"]).sum())

    unknown_buckets = {}
    for bucket in _PSEUDO_ROLES:
        unknown_buckets[bucket] = {
            "airframes": len(icao_by_role.get(bucket, ())),
            "flights": flight_counts.get(bucket, 0),
        }
        if unknown_buckets[bucket]["flights"] == 0:
            log(f"export: role_catalog — unknown bucket '{bucket}' is empty, "
                 "absent from populated roles")

    catalog = {
        "generated": data_end,
        "crop": {"start": crop_start, "end": crop_end},
        "roles": roles_out,
        "groups": groups_out,
        "excluded": {
            "rotary_airframes": n_rotary,
            "non_aircraft_airframes": n_non_aircraft,
        },
        "unknown_buckets": unknown_buckets,
        "_note": "role/group ids are stable only within this build; "
                 "regenerated per export, not across builds",
    }

    role_ids_per_flight = [
        sorted(role_id[r] for r in roles) for roles in flight_role_lists
    ]
    return catalog, role_ids_per_flight, flight_role_lists


def _write_role_catalog(catalog: dict, out_dir: Path):
    _atomic_write(out_dir / "role_catalog.json", json.dumps(catalog, indent=1).encode())
    n_roles = len(catalog["roles"])
    n_groups = len(catalog["groups"])
    log(f"export: role_catalog.json — {n_roles} roles, {n_groups} groups")


def _write_role_findings(
    flights: pd.DataFrame, aircraft: pd.DataFrame, catalog: dict,
):
    """Regenerate data/build/role_findings.md (not served, working state —
    see the roles taxonomy in data/in/config/roles.json). Lists the 'other
    role' review queue (types whose roleset
    contains a role outside all groups+EXCLUDED_ROLES — none currently,
    since _build_role_catalog would have raised) plus the three
    unknown-bucket populations with example hexes."""
    lines = [
        "# Role findings",
        "",
        "Generated by `./run etl export`. Definitions for `type_status`, "
        "the `roles` column, and the three pseudo-role unknown buckets live "
        "in the roles taxonomy (data/in/config/roles.json) and backend/util/roles.py.",
        "",
        "## Other-role review queue",
        "",
        "Types whose roleset contains a role outside every catalog group "
        "and outside EXCLUDED_ROLES. Export fails the build loudly if this "
        "queue is non-empty (see role_catalog.json build), so this section "
        "is a record of the last successful export, not a live warning.",
        "",
        "(none — export would have failed otherwise)",
        "",
        "## Unknown buckets",
        "",
    ]

    icao_by_role: dict[str, set] = {}
    icao_vals = flights["icao24"].values
    for i, roles in enumerate(_flight_role_lists(flights)):
        for r in roles:
            icao_by_role.setdefault(r, set()).add(icao_vals[i])

    for bucket in _PSEUDO_ROLES:
        info = catalog["unknown_buckets"][bucket]
        n_af = info["airframes"]
        n_fl = info["flights"]
        lines.append(f"### {bucket}")
        lines.append("")
        if n_fl == 0 and n_af == 0:
            lines.append("(empty)")
            lines.append("")
            continue
        hexes = sorted(icao_by_role.get(bucket, ()))
        lines.append(f"{n_af} aircraft / {n_fl} flights.")
        lines.append("")
        lines.append("Example hexes (<=10): " + ", ".join(hexes[:10]))
        lines.append("")

    n_non_aircraft = catalog["excluded"]["non_aircraft_airframes"]
    non_aircraft_hexes = sorted(
        aircraft.loc[aircraft["excluded"] & ~aircraft["rotary"], "icao24"])
    lines.append("## non_aircraft (excluded, not published as a role)")
    lines.append("")
    lines.append(f"{n_non_aircraft} aircraft.")
    lines.append("")
    lines.append("Hexes: " + ", ".join(non_aircraft_hexes))
    lines.append("")

    out_path = data_dir() / "build" / "role_findings.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    log(f"export: wrote {out_path}")


def _type_enum(flights: pd.DataFrame) -> tuple[list[str], np.ndarray]:
    types = sorted(t for t in flights["type_code"].dropna().unique() if t)
    type_to_i = {t: i for i, t in enumerate(types)}
    idx = np.array([
        type_to_i.get(str(t), -1) if isinstance(t, str) else -1
        for t in flights["type_code"].values
    ], dtype=np.int16)
    return types, idx


def _day_offsets(flights: pd.DataFrame, start_epoch: float) -> np.ndarray:
    ts = flights["t_start"].values.astype(np.float64)
    return np.floor((ts - start_epoch) / 86400.0).astype(np.int16)


# -- trace loading -------------------------------------------------------------

def _load_coarse(etl: Path) -> dict:
    """Load coarse polyline flat arrays keyed by flight_id."""
    path = etl / "traces.parquet"
    if not path.exists():
        log("export: traces.parquet not found — polylines will be empty")
        return {}
    df = pd.read_parquet(path, columns=["flight_id", "pts_coarse"])
    out = {}
    fids = df["flight_id"].values
    pts = df["pts_coarse"].values
    for i in range(len(df)):
        raw = pts[i]
        if raw is not None and hasattr(raw, "__len__") and len(raw) > 0:
            out[int(fids[i])] = raw
    return out


def _str_col(values, indices) -> list[str]:
    return [str(v) if isinstance(v, str) else "" for v in values[indices]]


# -- manifest ------------------------------------------------------------------

def _write_manifest(
    type_names: list[str],
    data_start: str,
    data_end: str,
    whatif_start: str,
    out_dir: Path,
):
    """Manifest keys and their consumers:
    data_start/data_end/whatif_start  date pickers + What-If gating
    alt_bands/alt_band_kft            altitude legend (app.js)
    days                              frontend date-offset lookup
    dates                             per-day-offset ISO date lookup
    types                             type filter + tooltips
    bboxes                            theater-box hover overlay (controls.js)

    role_catalog.json is authoritative for role/group filtering; the
    manifest carries no role enum.
    """
    tax = taxonomy()
    designators = tax.get("designators", {})
    type_list = []
    for tc in type_names:
        d = designators.get(tc.upper(), {})
        type_list.append({
            "code": tc,
            "label": d.get("label", tc),
        })

    # dates/days must share one origin with _day_offsets' per-flight offset
    # (config start, via dimensions) — every flight's 'day' indexes into
    # this same list. Previously n_days came from the flights table's own
    # t_start min/max range while dates/offsets came from the config start:
    # two different origins that only agreed by coincidence, and could put
    # a flight's day offset past the end of `dates`.
    from backend.util import dimensions
    dates = dimensions.dates()
    n_days = len(dates)

    _write_json(out_dir / "manifest.json", {
        "data_start": data_start,
        "data_end": data_end,
        "whatif_start": whatif_start,
        "alt_bands": _N_ALT_BANDS,
        "alt_band_kft": _ALT_BAND_KFT,
        "days": n_days,
        "dates": dates,
        "types": type_list,
        "bboxes": _pipeline_bboxes(),
    })


def _pipeline_bboxes():
    from backend.util import dimensions
    return dimensions.bboxes()


# -- stats ---------------------------------------------------------------------

def _load_focus_config() -> dict:
    return load_focus_config(_CONFIG_DIR)


def _focus_country_list(cfg: dict) -> list[str]:
    """Countries the sighting index is built for: those with the `stats`
    flag set in the flat countries map (this list backs the per-country
    stats table, see panel-data.js's getCountryStatsData)."""
    return [cc for cc, info in cfg["countries"].items() if info.get("stats") is True]


# Ramer-Douglas-Peucker simplification tolerance, in degrees: a route point
# is dropped when it lies within this distance of the straight line between
# its neighbours. Mobile is simplified hard (~11 km) to keep the payload
# small. NOTE the desktop value (~111 m) is a re-simplification of input
# that the trace stage (trace.py, _COARSE_TOL_M = 2000 m) already decimated —
# the coarser upstream tolerance is the real precision floor; tightening
# the value below cannot recover sub-2 km detail. Publication only —
# analysis never uses these polylines.
#
# To experiment, edit these two lines AND trace.py's _COARSE_TOL_M as a
# pair, then re-run `./run etl trace export`.
_ROUTE_EPSILON = 0.001
_ROUTE_EPSILON_MOBILE = 0.1


def _clip_flat_to_crop(arr, bbox, stride: int = 4):
    """Split a flat coarse array into pieces clipped to a lat/lon box.

    Keeps every point inside the box, plus the single outside point
    adjacent to each inside run, so a boundary-crossing line still
    draws past the map edge. An exit-and-reenter yields separate
    pieces (no straight line through the outside region). Outlier
    spikes are dropped first, with the same predicate as
    _coarse_to_route_segments, so a spike jumping beyond the box does
    not masquerade as a boundary crossing.

    Returns a list of flat arrays in the same (ts, lat, lon, kft)
    stride-4 packing as the input.
    """
    n_pts = len(arr) // stride
    if n_pts == 0:
        return []
    lat_s, lat_n = bbox["lat_south"], bbox["lat_north"]
    lon_w, lon_e = bbox["lon_west"], bbox["lon_east"]

    lats = [float(arr[i * stride + 1]) for i in range(n_pts)]
    lons = [float(arr[i * stride + 2]) for i in range(n_pts)]

    idxs = [
        i for i in range(n_pts)
        if not (0 < i < n_pts - 1 and _is_outlier(
            lats[i], lons[i],
            lats[i - 1], lons[i - 1],
            lats[i + 1], lons[i + 1],
        ))
    ]
    inside = [
        lat_s <= lats[i] <= lat_n and lon_w <= lons[i] <= lon_e
        for i in idxs
    ]

    pieces = []
    m = len(idxs)
    j = 0
    while j < m:
        if not inside[j]:
            j += 1
            continue
        k = j
        while k + 1 < m and inside[k + 1]:
            k += 1
        piece: list[float] = []
        for t in range(max(0, j - 1), min(m - 1, k + 1) + 1):
            base = idxs[t] * stride
            chunk = list(arr[base:base + stride])
            chunk.extend([0.0] * (stride - len(chunk)))
            piece.extend(chunk)
        pieces.append(piece)
        j = k + 1
    return pieces


def _coarse_to_route_segments(arr, epsilon: float, stride: int = 4):
    """Parse coarse flat array into gap-split, RDP-simplified segments.

    Returns list of (coords, bands) tuples where coords is [[lon,lat],...]
    and bands is [int,...] with one band index per coordinate point.
    RDP operates on the full segment for maximum simplification.
    """
    n_pts = len(arr) // stride
    if n_pts < 2:
        return []

    points = []
    for i in range(n_pts):
        base = i * stride
        points.append((
            float(arr[base]),
            float(arr[base + 1]),
            float(arr[base + 2]),
            float(arr[base + 3]) if base + 3 < len(arr) else 0.0,
        ))

    keep = [True] * n_pts
    for i in range(1, n_pts - 1):
        _, lat, lon, _ = points[i]
        _, plat, plon, _ = points[i - 1]
        _, nlat, nlon, _ = points[i + 1]
        if _is_outlier(lat, lon, plat, plon, nlat, nlon):
            keep[i] = False

    segments: list[list[tuple]] = []
    current: list[tuple] = []
    prev_ts = None

    for i in range(n_pts):
        if not keep[i]:
            continue
        ts, lat, lon, alt = points[i]
        if prev_ts is not None and (ts - prev_ts) > _GAP_THRESHOLD_S:
            if len(current) >= 2:
                segments.append(current)
            current = []
        current.append((lat, lon, alt))
        prev_ts = ts

    if len(current) >= 2:
        segments.append(current)

    result = []
    for seg in segments:
        coords = [[round(p[1], 3), round(p[0], 3)] for p in seg]
        bands = [_alt_band(p[2]) for p in seg]
        # unknown-altitude points inherit the nearest known band (forward
        # fill, then backfill a leading unknown run) instead of rendering
        # as ground level; an all-unknown segment falls back to band 0
        last = -1
        for i, b in enumerate(bands):
            if b >= 0:
                last = b
            elif last >= 0:
                bands[i] = last
        nxt = 0
        for i in range(len(bands) - 1, -1, -1):
            if bands[i] >= 0:
                nxt = bands[i]
            else:
                bands[i] = nxt
        simplified = _rdp_simplify_with_mask(coords, epsilon)
        if len(simplified) < 2:
            continue
        kept_coords = [coords[i] for i in simplified]
        kept_bands = [bands[i] for i in simplified]
        result.append((kept_coords, kept_bands))
    return result


def _rdp_simplify_with_mask(pts, epsilon):
    """RDP that returns kept indices instead of kept points."""
    n = len(pts)
    if n <= 2:
        return list(range(n))
    keep = [False] * n
    keep[0] = True
    keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        p1, p2 = pts[a], pts[b]
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        norm_sq = dx * dx + dy * dy
        dmax = 0.0
        idx = a
        for i in range(a + 1, b):
            if norm_sq < 1e-18:
                d = math.hypot(pts[i][0] - p1[0], pts[i][1] - p1[1])
            else:
                t = ((pts[i][0] - p1[0]) * dx + (pts[i][1] - p1[1]) * dy) / norm_sq
                t = max(0.0, min(1.0, t))
                px = p1[0] + t * dx
                py = p1[1] + t * dy
                d = math.hypot(pts[i][0] - px, pts[i][1] - py)
            if d > dmax:
                dmax = d
                idx = i
        if dmax > epsilon:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [i for i in range(n) if keep[i]]


def _build_route_features(coarse, flights, role_ids, day_off, epsilon):
    """Build route GeoJSON features at a given simplification epsilon.

    role_ids: per-flight list[int] of catalog role ids, emitted as the
    "rs" property.
    """
    from backend.util import dimensions

    # Clipped to the crop box at publication only; traces.parquet keeps
    # the full geometry.
    crop_bbox = dimensions.crop()

    tc_col = flights.get("traversed_countries", pd.Series(dtype=str)).fillna("")
    fid_vals = flights["flight_id"].values
    fid_to_idx = {int(fid_vals[i]): i for i in range(len(fid_vals))}
    tc_vals = tc_col.values

    thc_vals = flights.get("th_chain", pd.Series(dtype=bool)).fillna(False).values
    the_vals = flights.get("th_ever", pd.Series(dtype=bool)).fillna(False).values

    features = []

    n_orphan = 0
    for fid, flat in coarse.items():
        if flat is None:
            continue
        arr = flat if isinstance(flat, (list, tuple)) else list(flat)
        idx = fid_to_idx.get(fid)
        if idx is None:
            # traces.parquet carries a flight_id that flights.parquet
            # doesn't know — the staleness signature of a flight re-run
            # without a traces re-run; count it loudly instead of masking
            n_orphan += 1
            continue
        props = {
            "id": int(fid),
            "cc": _norm_country_csv(tc_vals[idx]),
            "rs": role_ids[idx],
            "day": int(day_off[idx]),
            "thc": int(thc_vals[idx]),
            "the": int(the_vals[idx]),
        }

        for piece in _clip_flat_to_crop(arr, crop_bbox):
            for coords, bands in _coarse_to_route_segments(piece, epsilon):
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coords,
                    },
                    "properties": {**props, "bi": bands},
                })
    if n_orphan:
        log(f"export: WARNING {n_orphan} traces.parquet flight_ids have no "
             f"match in flights.parquet — stale traces? re-run "
             f"./run etl trace")
    return features


def _write_route_file(features, name, build_dir):
    """Serialize route features to .geojson."""
    raw = json.dumps({
        "type": "FeatureCollection",
        "features": features,
    }, separators=(",", ":")).encode()
    _atomic_write(build_dir / f"{name}.geojson", raw)
    raw_mb = len(raw) / (1024 * 1024)
    log(f"export: {name}.geojson — {len(features)} segments, {raw_mb:.1f} MB")


def _write_routes(
    coarse: dict, flights: pd.DataFrame,
    role_ids: list, day_off: np.ndarray, build_dir: Path,
    epsilon: float = _ROUTE_EPSILON,
    epsilon_mobile: float = _ROUTE_EPSILON_MOBILE,
):
    """Write desktop and mobile route files at different resolutions."""
    features = _build_route_features(
        coarse, flights, role_ids, day_off, epsilon)
    _write_route_file(features, "routes", build_dir)

    mobile_features = _build_route_features(
        coarse, flights, role_ids, day_off, epsilon_mobile)
    _write_route_file(mobile_features, "routes-mobile", build_dir)


def _write_hex_roles(
    aircraft: pd.DataFrame, role_id_map: dict, build_dir: Path,
):
    """Write hex_roles.json: {hex: [role_id, ...]} for in-scope aircraft."""
    in_scope = aircraft[~aircraft["excluded"] & aircraft["in_bbox"]]
    hex_roles = {}
    for icao24, roles_str in zip(in_scope["icao24"], in_scope["roles"]):
        ids = sorted(
            role_id_map[s] for s in str(roles_str).split(",")
            if s in role_id_map
        )
        if ids:
            hex_roles[icao24] = ids
    _write_json(build_dir / "hex_roles.json", hex_roles)
    log(f"export: hex_roles.json — {len(hex_roles)} aircraft with roles")


def _write_flights_table(
    flights: pd.DataFrame,
    type_idx: np.ndarray,
    day_off: np.ndarray,
    start_epoch: float,
    build_dir: Path,
):
    """Write columnar flights lookup table for tooltip/CSV (no geometry)."""
    n = len(flights)
    indices = np.arange(n)
    tc_vals = flights.get("traversed_countries", pd.Series(dtype=str)).fillna("").values
    thc_vals = flights.get("th_chain", pd.Series(dtype=bool)).fillna(False).values
    the_vals = flights.get("th_ever", pd.Series(dtype=bool)).fillna(False).values

    end_ts = flights["t_end"].values.astype(np.float64)
    eday = np.floor((end_ts - start_epoch) / 86400.0).astype(int)
    etod = (end_ts.astype(np.int64) % 86400).astype(int)

    table = {
        "n": n,
        "id": flights["flight_id"].values.astype(int).tolist(),
        "hex": _str_col(flights["icao24"].values, indices),
        "ti": type_idx[indices].tolist(),
        "day": day_off[indices].tolist(),
        "eday": eday.tolist(),
        "etod": etod.tolist(),
        "cc": [_norm_country_csv(v) for v in tc_vals],
        "kft": [_safe_int(v, scale=0.001)
                for v in flights["max_alt_ft"].values],
        "path": [_safe_int(v)
                 for v in flights["total_path_km"].values],
        "thc": [int(v) for v in thc_vals],
        "the": [int(v) for v in the_vals],
        "oa": _str_col(flights["origin_icao"].values, indices),
        "da": _str_col(flights["dest_icao"].values, indices),
        "occ": [_norm_country(v) if v else ""
               for v in flights.get("origin_country", pd.Series(dtype=str)).fillna("").values],
        "dcc": [_norm_country(v) if v else ""
               for v in flights.get("dest_country", pd.Series(dtype=str)).fillna("").values],
    }
    _write_json(build_dir / "flights.json", table)
    size_kb = (build_dir / "flights.json").stat().st_size / 1024
    log(f"export: flights.json — {n:,} flights, {size_kb:.0f} KB")



def _write_sighting_index(start_epoch: float, focus: list[str], build_dir: Path):
    """Per focus country, write build/sightings/<cc>.json = {hex: {day_off:
    first_tod, ..., "g": [day_off, ...]}} for What-If's actual-sighting
    lookup and the stats table's Landed column. day_off matches the
    flights-table 'day' (offset from start_epoch); first_tod = first
    sighting clock-time (s of UTC day). "g" lists the hex's corroborated
    landing days (landings.parquet full-span expansion) — a non-numeric
    key, so readers iterating numeric day keys are unaffected."""
    path = etl_dir() / "sighting_times.parquet"
    if not path.exists():
        log("export: sighting_times.parquet missing — skipping sighting index")
        return
    sdf = pd.read_parquet(path)
    sdf = sdf.copy()
    sdf["cc"] = sdf["cc"].map(lambda c: COUNTRY_ALIASES.get(c, c))
    sdf = sdf[sdf["cc"].isin(set(focus))]
    start_day = int(start_epoch // 86400)

    landings_path = etl_dir() / "landings.parquet"
    if landings_path.exists():
        # published basis: the landing-engine entity table. Day-based
        # surfaces credit the FULL SPAN of each stay (arrival day through
        # departure day — parked-dark days included, per the G-silence-G
        # measurement).
        lg = pd.read_parquet(landings_path)
        lg = lg[lg.cc != ""].copy()
        t0_ = lg.t_arrival.fillna(lg.t_departure)
        t1_ = lg.t_departure.fillna(lg.t_arrival)
        lg["day0"] = (t0_ // 86400).astype(int)
        lg["day1"] = (t1_ // 86400).astype(int)
        rows = []
        for r in lg.itertuples(index=False):
            for d in range(r.day0, r.day1 + 1):
                rows.append((r.icao24, d, r.cc))
        ldf = pd.DataFrame(rows, columns=["icao24", "day", "cc"])
        ldf["cc"] = ldf["cc"].map(lambda c: COUNTRY_ALIASES.get(c, c))
        ldf = ldf[ldf["cc"].isin(set(focus))]
    else:
        log("export: WARNING landings.parquet missing — sighting index "
             "will carry no landing days (re-run the attribute stage)")
        ldf = pd.DataFrame(columns=["icao24", "day", "cc"])
    landed_by_cc: dict = {}
    for row in ldf.itertuples(index=False):
        day_off = int(row.day) - start_day
        if day_off < 0:
            continue
        # set: rows are per-airfield, so one aircraft can have several
        # rows for the same (country, day)
        landed_by_cc.setdefault(row.cc, {}).setdefault(
            row.icao24, set()).add(day_off)

    out_dir = build_dir / "sightings"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    n_landed = 0
    for cc in sorted(set(sdf["cc"]) | set(landed_by_cc)):
        grp = sdf[sdf["cc"] == cc]
        by_hex: dict = {}
        for row in grp.itertuples(index=False):
            day_off = int(row.day) - start_day
            if day_off < 0:
                continue
            by_hex.setdefault(row.icao24, {})[str(day_off)] = int(row.first_ts) % 86400
        for hex24, days in landed_by_cc.get(cc, {}).items():
            by_hex.setdefault(hex24, {})["g"] = sorted(days)
            n_landed += 1
        _write_json(out_dir / f"{cc}.json", by_hex)
        written += 1
    log(f"export: sighting index — {written} countries, "
         f"{n_landed:,} (aircraft,country) landed entries")

    # build_dir is overwritten in place, never wiped first (see
    # paths.build_dir()'s docstring), so a country dropped from the focus
    # list leaves its old sightings/<cc>.json sitting there unpublished-but-
    # present — it will still ship. Warn-only: never delete it here (a
    # user's data, out of scope to remove without asking).
    focus_set = set(focus)
    stale = sorted(p.stem for p in out_dir.glob("*.json")
                   if p.stem not in focus_set)
    if stale:
        log(f"export: WARNING {len(stale)} stale sightings/*.json not in "
             f"the current focus list (will still ship, not removed): "
             f"{', '.join(stale)}")


def _write_landings_map(start_epoch: float, build_dir_: Path):
    """Per-airfield landings for the map circles, from landings.parquet.

    Count unit: landing events (one per ground stay). h/d/t are parallel
    per-event arrays (hex, arrival-day offset, evidence tier initial
    o/t/e). Outputs: landings.geojson + airports_meta.json (tooltips)."""
    from backend.pipeline.gen.airports import US_MIL_DUAL_USE
    from backend.util.geo.raster import GeoRaster

    path = etl_dir() / "landings.parquet"
    if not path.exists():
        log("export: landings.parquet missing — skipping map landings "
             "(re-run the attribute stage)")
        return
    lg = pd.read_parquet(path)
    lg = lg[lg.field != ""].copy()
    t0_ = lg.t_arrival.fillna(lg.t_departure)
    lg["day_off"] = (t0_ // 86400).astype(int) - int(start_epoch // 86400)
    lg = lg[lg.day_off >= 0]
    tier_char = {"observed": "o", "two-edge": "t", "one-edge": "e"}

    gi = GeoRaster.load(lookup_dir())
    apt_by_key = {}
    for a in gi.airports:
        key = a.get("icao") or a.get("ident")
        if key and key not in apt_by_key:
            apt_by_key[key] = a

    def _mil_type(apt):
        if apt.get("type") != "military_base":
            return "civilian"
        return ("dual_use" if apt.get("icao") in US_MIL_DUAL_USE
                else "military")

    features, meta, unknown = [], {}, 0
    for key, sub in lg.groupby("field", sort=True):
        apt = apt_by_key.get(key)
        if apt is None:
            unknown += 1
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [
                round(apt["lon"], 5), round(apt["lat"], 5)]},
            "properties": {
                "icao": key,
                "h": sub["icao24"].tolist(),
                "d": [int(d) for d in sub["day_off"].tolist()],
                "t": [tier_char.get(t, "?") for t in sub["tier"].tolist()],
                "cc": apt.get("country") or "",
            },
        })
        meta[key] = {
            "name": apt.get("name") or "", "type": apt.get("type") or "",
            "mil_type": _mil_type(apt),
            "lat": round(apt["lat"], 5), "lon": round(apt["lon"], 5),
            "country": apt.get("country") or "",
            "iata": apt.get("iata") or "",
            "municipality": apt.get("municipality") or "",
        }
    _write_json(build_dir_ / "landings.geojson",
                {"type": "FeatureCollection", "features": features})
    _write_json(build_dir_ / "airports_meta.json", meta)
    log(f"export: landings map — {len(features)} airfields, "
         f"{len(lg):,} events"
         + (f" ({unknown} unknown-field keys dropped)" if unknown else ""))


def _copy_roles_reference(site_root: Path) -> None:
    """Copy roles.html and roles.reference.json from lookup_dir() into
    site_root (data/out/ — where the frontend build's other static/ assets
    land) so the served URLs /roles.html and /roles.reference.json are
    unaffected by where the pipeline generates the files."""
    for name in ("roles.html", "roles.reference.json"):
        src = lookup_dir() / name
        if src.exists():
            tmp = site_root / f"{name}.tmp"
            shutil.copy2(src, tmp)
            os.replace(tmp, site_root / name)
        else:
            # without this the reader tab's role explainer and the
            # standalone /roles.html page both 404 — a failed `gen roles`
            # must not pass through silently
            log(f"export: WARNING data/build/lookup/{name} missing "
                 "— roles reference would 404; run ./run gen roles")


# -- main entry point ----------------------------------------------------------

def run_export(*, route_epsilon: float = _ROUTE_EPSILON,
               route_epsilon_mobile: float = _ROUTE_EPSILON_MOBILE):
    from backend.util import dimensions
    start, end = dimensions.start(), dimensions.end()
    t0 = time.time()
    etl = etl_dir()

    flights_path = etl / "flights.parquet"
    if not flights_path.exists():
        log("export: flights.parquet not found — run the etl stages first")
        return

    flights = pd.read_parquet(flights_path)
    log(f"export: {len(flights):,} flights loaded")

    aircraft_path = etl / "aircraft.parquet"
    if not aircraft_path.exists():
        log("export: aircraft.parquet not found — run the etl stages first")
        return
    aircraft = pd.read_parquet(aircraft_path)

    coarse = _load_coarse(etl)
    log(f"export: {len(coarse):,} coarse polylines loaded")

    start_epoch = _date_to_epoch(start)
    catalog, role_ids, _flight_role_slugs = _build_role_catalog(
        flights, aircraft, end, start, end,
    )
    type_names, type_idx = _type_enum(flights)
    day_off = _day_offsets(flights, start_epoch)

    out = build_dir()
    out.mkdir(parents=True, exist_ok=True)

    _write_role_catalog(catalog, out)
    _write_role_findings(flights, aircraft, catalog)

    _write_routes(coarse, flights, role_ids, day_off, out,
                     route_epsilon, route_epsilon_mobile)
    del coarse

    _write_flights_table(flights, type_idx, day_off, start_epoch, out)

    role_id_map = {r["slug"]: r["id"] for r in catalog["roles"]}
    _write_hex_roles(aircraft, role_id_map, out)

    focus_cfg = _load_focus_config()

    _write_manifest(
        type_names,
        start, end, dimensions.whatif_start(), out,
    )

    focus = _focus_country_list(focus_cfg)
    _write_sighting_index(start_epoch, focus, out)
    _write_landings_map(start_epoch, out)

    boundaries = lookup_dir() / "boundaries.geojson"
    if boundaries.exists():
        tmp = out / "boundaries.geojson.tmp"
        shutil.copy2(boundaries, tmp)
        os.replace(tmp, out / "boundaries.geojson")
    else:
        # without this file the site ships with NO country outlines at
        # all — a failed `gen boundaries` must not pass through silently
        log("export: WARNING data/build/lookup/boundaries.geojson missing "
             "— site would render without country outlines; run "
             "./run gen boundaries")

    _copy_roles_reference(out.parent)

    elapsed = time.time() - t0
    n_files = sum(1 for _ in out.rglob("*.json"))
    size_mb = sum(
        f.stat().st_size for f in out.rglob("*") if f.is_file()
    ) / 1e6
    log(f"export: {n_files:,} files, {size_mb:.0f} MB, {elapsed:.0f}s")
