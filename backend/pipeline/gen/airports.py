#!/usr/bin/env python3
"""Build airports.pkl from OurAirports CSV.

Reads airports.csv, filters to usable airport types, promotes airports to
military_base using three sources (checked in this order, first match wins):

  1. US_MIL_DUAL_USE — hand-curated ICAO overrides for US civilian airports
     that host military tenants (ANG/Reserve wings at civil fields). These
     carry per-airport snap radii.
  2. OSM cross-reference — osm_military_airports.csv, a snapshot of all
     aerodromes tagged military=* in OpenStreetMap. Any OurAirports airport
     whose ICAO appears in this file is promoted.
  3. MIL_OVERRIDE — hand-curated ICAO overrides for non-US military bases
     that OSM does not tag (verified gaps in the OSM snapshot).

Builds a sklearn BallTree for fast nearest-airport queries and writes a
pickle.

Output: airports.pkl containing {airports, tree, radii_km, latlon_rad}

Usage:
    python backend/pipeline/gen/airports.py [--csv PATH] [--out PATH]
           [--osm-mil PATH]
"""
from __future__ import annotations

import argparse
import csv
import pickle
from pathlib import Path

import numpy as np
from sklearn.neighbors import BallTree

from backend.util.countries import normalize_country
from backend.util.log import log

KEEP_TYPES = {"large_airport", "medium_airport", "small_airport",
              "heliport", "seaplane_base"}

DEFAULT_RADII_KM = {
    "large_airport": 8.0,
    "medium_airport": 5.0,
    "small_airport": 3.0,
    "heliport": 1.5,
    "seaplane_base": 2.0,
    "military_base": 5.0,
}


# US civilian airports hosting military tenants (ANG/Reserve wings).
# Each entry: ICAO -> (unit_slug, snap_radius_km). These airports are
# civilian in OurAirports and not tagged military in OSM, but regularly
# host military aircraft. Entries that OSM correctly tags are removed
# from here to avoid redundancy (OSM is the primary source).
US_MIL_DUAL_USE = {
    "KPSM": ("ang_kc46_157arw", 5.0),
    "KBGR": ("ang_kc135_101arw", 5.0),
    "KSLC": ("ang_kc135_151arw", 5.0),
    "KFOE": ("ang_kc135_190arw", 5.0),
    "KBHM": ("ang_kc135_117arw", 5.0),
    "KLNK": ("ang_kc135_155arw", 5.0),
    "KPHX": ("ang_kc135_161arw", 5.0),
    "KMSP": ("ang_c130_133aw", 5.0),
    "KDSM": ("ang_f16_132wg", 5.0),
    "KSTJ": ("ang_c130_139aw", 5.0),
    "KSAV": ("ang_c130_165aw", 5.0),
    "KMEM": ("ang_c17_164aw", 5.0),
    "KBDL": ("ang_c130_103aw", 5.0),
    "KSDF": ("ang_c130_123aw", 5.0),
    "KJAN": ("ang_c17_172aw", 5.0),
    "KCLT": ("ang_c17_145aw", 5.0),
    "KILG": ("ang_c130_166aw", 5.0),
    "KMRB": ("ang_c5_167aw", 5.0),
    "KNBG": ("navy_reserve_nola", 5.0),
    "KPDX": ("ang_f15_142fw", 5.0),
    "KTUS": ("ang_f16_162wg", 6.0),
    "KFAT": ("ang_f15_144fw", 5.0),
    "KMGM": ("ang_f16_187fw", 5.0),
    "KBNA": ("ang_c17_118wg", 5.0),
    "KIND": ("ang_a10_181iw", 5.0),
    "KFSD": ("ang_f16_114fw", 5.0),
    "KMSN": ("ang_f35_115fw", 5.0),
    "KGTF": ("ang_f15_120fw", 5.0),
    "KLMT": ("ang_f15_173fw", 5.0),
    "KBTV": ("ang_f35_158fw", 5.0),
    "KTUL": ("ang_f16_138fw", 5.0),
    "KMDT": ("ang_c17_193sow", 5.0),
    "KSYR": ("ang_mq9_174atkw", 5.0),
    "KSUX": ("ang_c130_185arw", 5.0),
    "KCYS": ("ang_c130_153aw", 5.0),
    "KYNG": ("afrc_c130_910aw", 5.0),
    "KIAG": ("afrc_c130_914aw", 5.0),
    "KOQU": ("ang_c130_143aw", 5.0),
    "KGPT": ("ang_crtc_255acs", 5.0),
}

# Non-US military bases that OSM does not tag (verified gaps in the
# 2026-08-01 OSM snapshot). Each entry: ICAO -> snap_radius_km.
MIL_OVERRIDE = {
    "LCRA": 5.0,   # RAF Akrotiri (UK SBA, Cyprus)
    "LERT": 5.0,   # Rota Naval Station (US Navy, Spain)
    "RJOI": 5.0,   # MCAS Iwakuni (US Marines, Japan)
}


def _load_osm_mil_icaos(path: Path) -> set[str]:
    """Load the set of ICAO codes from the OSM military airports CSV."""
    icaos: set[str] = set()
    if not path.exists():
        log(f"WARNING: {path} not found — OSM military cross-reference "
             f"disabled. Only US_MIL_DUAL_USE and MIL_OVERRIDE will promote.")
        return icaos
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            code = (row.get("icao") or "").strip()
            if code:
                icaos.add(code)
    log(f"  loaded {len(icaos)} ICAO codes from {path.name}")
    return icaos


def _norm_iso2(raw: str) -> str:
    return normalize_country(raw)


def build(csv_path: Path, out_path: Path, osm_mil_path: Path | None = None):
    out_path.parent.mkdir(parents=True, exist_ok=True)

    osm_mil_icaos = _load_osm_mil_icaos(
        osm_mil_path or csv_path.parent / "osm_military_airports.csv")

    rows: list[dict] = []
    n_dual_use = 0
    n_osm = 0
    n_override = 0
    n_bad_latlon = 0

    log(f"reading {csv_path.name}...")
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for x in csv.DictReader(fh):
            t = x.get("type", "")
            if t not in KEEP_TYPES:
                continue
            try:
                lat = float(x["latitude_deg"])
                lon = float(x["longitude_deg"])
            except (KeyError, ValueError):
                n_bad_latlon += 1
                continue

            iso2 = _norm_iso2(x.get("iso_country", "") or "")
            if not iso2:
                iso2 = "ZZ"
            name = x.get("name", "")
            icao = x.get("icao_code") or x.get("gps_code") or None
            radius_km = DEFAULT_RADII_KM.get(t, 2.0)
            source = "ourairports"

            if t not in ("heliport", "seaplane_base") and icao:
                if icao in US_MIL_DUAL_USE:
                    _, radius_km = US_MIL_DUAL_USE[icao]
                    t = "military_base"
                    source = "osm_dual_use"
                    n_dual_use += 1
                elif icao in osm_mil_icaos:
                    t = "military_base"
                    radius_km = DEFAULT_RADII_KM["military_base"]
                    source = "osm_military"
                    n_osm += 1
                elif icao in MIL_OVERRIDE:
                    radius_km = MIL_OVERRIDE[icao]
                    t = "military_base"
                    source = "mil_override"
                    n_override += 1

            try:
                elevation_ft = float(x.get("elevation_ft") or "")
            except ValueError:
                # ~8% of OurAirports rows carry no elevation; consumers
                # (field-relative altitude gating in the landing evidence
                # scan) must treat None as "unknown", not sea level
                elevation_ft = None

            rows.append({
                "ident": x.get("ident") or "",
                "type": t,
                "name": name,
                "lat": lat,
                "lon": lon,
                "elevation_ft": elevation_ft,
                "country": iso2,
                "iata": x.get("iata_code") or None,
                "icao": icao,
                "municipality": x.get("municipality", ""),
                "scheduled_service": x.get("scheduled_service") == "yes",
                "radius_km": radius_km,
                "_source": source,
            })

    n_total_mil = n_dual_use + n_osm + n_override
    log(f"  {len(rows)} airports after type filter")
    log(f"  {n_total_mil} promoted to military_base "
         f"(dual-use: {n_dual_use}, OSM: {n_osm}, override: {n_override})")
    if n_bad_latlon:
        log(f"  WARNING: {n_bad_latlon} rows dropped for missing/invalid "
             f"lat/lon (a jump here signals an OurAirports format change)")

    latlon_deg = np.array([[a["lat"], a["lon"]] for a in rows], dtype=np.float64)
    latlon_rad = np.radians(latlon_deg)
    radii_km = np.array([a["radius_km"] for a in rows], dtype=np.float64)
    tree = BallTree(latlon_rad, metric="haversine")

    data = {
        "airports": rows,
        "latlon_rad": latlon_rad,
        "radii_km": radii_km,
        "tree": tree,
    }

    with out_path.open("wb") as fh:
        pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)

    log(f"wrote {out_path} ({out_path.stat().st_size / 1e6:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path,
                        default=Path("data/in/external/airports.csv"))
    parser.add_argument("--osm-mil", type=Path,
                        default=Path("data/in/external/osm_military_airports.csv"))
    parser.add_argument("--out", type=Path,
                        default=Path("data/build/lookup/airports.pkl"))
    args = parser.parse_args()
    build(args.csv, args.out, args.osm_mil)


if __name__ == "__main__":
    main()
