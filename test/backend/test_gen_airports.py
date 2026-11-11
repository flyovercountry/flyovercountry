"""Behavioral tests for backend/pipeline/gen/airports.py — the standalone
OurAirports-CSV-to-pickle builder. Runs under an
`if __name__ == "__main__"` guard and is meant to run as a script rather
than be imported for its API, so it's loaded via
importlib.util.spec_from_file_location (same pattern as test_gridspec.py
for gen/raster.py) to get a fresh module object whose functions (build,
DEFAULT_RADII_KM, US_MIL_DUAL_USE, MIL_OVERRIDE) tests call/inspect
directly."""
import csv
import importlib.util
import pickle
from pathlib import Path

from sklearn.neighbors import BallTree


def _load_airports():
    src = Path("backend/pipeline/gen/airports.py")
    spec = importlib.util.spec_from_file_location("gen_airports", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FIELDS = ["ident", "type", "name", "latitude_deg", "longitude_deg",
          "iso_country", "icao_code", "gps_code", "iata_code",
          "municipality", "scheduled_service"]

ROWS = [
    # kept, ordinary large_airport, default radius, no reclassification
    {"ident": "EDDF", "type": "large_airport", "name": "Frankfurt Airport",
     "latitude_deg": "50.0333", "longitude_deg": "8.5706",
     "iso_country": "DE", "icao_code": "EDDF", "gps_code": "EDDF",
     "iata_code": "FRA", "municipality": "Frankfurt", "scheduled_service": "yes"},
    # dropped: not in KEEP_TYPES
    {"ident": "XCLOSED", "type": "closed", "name": "Abandoned Field",
     "latitude_deg": "10.0", "longitude_deg": "10.0",
     "iso_country": "US", "icao_code": "", "gps_code": "",
     "iata_code": "", "municipality": "", "scheduled_service": ""},
    # reclassified: US icao in the dual-use table
    {"ident": "KPSM", "type": "large_airport",
     "name": "Portsmouth International Airport at Pease",
     "latitude_deg": "43.0779", "longitude_deg": "-70.8233",
     "iso_country": "US", "icao_code": "KPSM", "gps_code": "KPSM",
     "iata_code": "PSM", "municipality": "Portsmouth", "scheduled_service": "yes"},
    # reclassified: ICAO in OSM military set
    {"ident": "ETAR", "type": "medium_airport", "name": "Ramstein Air Base",
     "latitude_deg": "49.4369", "longitude_deg": "7.6003",
     "iso_country": "DE", "icao_code": "ETAR", "gps_code": "ETAR",
     "iata_code": "", "municipality": "Ramstein", "scheduled_service": "no"},
    # reclassified: ICAO in OSM military set
    {"ident": "EGUN", "type": "medium_airport", "name": "RAF Mildenhall",
     "latitude_deg": "52.3616", "longitude_deg": "0.4864",
     "iso_country": "GB", "icao_code": "EGUN", "gps_code": "EGUN",
     "iata_code": "", "municipality": "Mildenhall", "scheduled_service": "no"},
    # reclassified: ICAO in MIL_OVERRIDE (OSM gap)
    {"ident": "LCRA", "type": "medium_airport", "name": "RAF Akrotiri",
     "latitude_deg": "34.5904", "longitude_deg": "32.9879",
     "iso_country": "CY", "icao_code": "LCRA", "gps_code": "LCRA",
     "iata_code": "", "municipality": "Akrotiri", "scheduled_service": "no"},
    # NOT reclassified: military-sounding name but ICAO not in OSM, not in
    # any override dict -> stays at OurAirports type
    {"ident": "XMIL", "type": "medium_airport",
     "name": "Definitely A Military Air Base",
     "latitude_deg": "20.0", "longitude_deg": "20.0",
     "iso_country": "XX", "icao_code": "XMIL", "gps_code": "XMIL",
     "iata_code": "", "municipality": "", "scheduled_service": "no"},
    # NOT reclassified: heliport type excluded from promotion even if ICAO
    # is in OSM set
    {"ident": "HMIL", "type": "heliport", "name": "Military Heliport",
     "latitude_deg": "51.0", "longitude_deg": "-1.0", "iso_country": "GB",
     "icao_code": "HMIL", "gps_code": "", "iata_code": "", "municipality": "",
     "scheduled_service": "no"},
    # iso_country empty -> falls back to "ZZ"
    {"ident": "ZZZ1", "type": "small_airport", "name": "Unclaimed Strip",
     "latitude_deg": "1.0", "longitude_deg": "1.0",
     "iso_country": "", "icao_code": "", "gps_code": "",
     "iata_code": "", "municipality": "", "scheduled_service": ""},
    # invalid lat -> dropped
    {"ident": "BADLL", "type": "small_airport", "name": "Bad Coordinates Field",
     "latitude_deg": "notanumber", "longitude_deg": "10.0",
     "iso_country": "US", "icao_code": "", "gps_code": "",
     "iata_code": "", "municipality": "", "scheduled_service": ""},
]

OSM_MIL_ROWS = [
    {"icao": "ETAR", "name": "Ramstein Air Base", "lat": "49.4369",
     "lon": "7.6003", "military": "airfield"},
    {"icao": "EGUN", "name": "RAF Mildenhall", "lat": "52.3616",
     "lon": "0.4864", "military": "airfield"},
    {"icao": "HMIL", "name": "Military Heliport", "lat": "51.0",
     "lon": "-1.0", "military": "airfield"},
]


def _write_csv(path):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(ROWS)


def _write_osm_csv(path):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["icao", "name", "lat", "lon", "military"])
        w.writeheader()
        w.writerows(OSM_MIL_ROWS)


def test_build_filters_reclassifies_and_defaults(tmp_path):
    mod = _load_airports()
    csv_path = tmp_path / "airports.csv"
    osm_path = tmp_path / "osm_military_airports.csv"
    out_path = tmp_path / "airports.pkl"
    _write_csv(csv_path)
    _write_osm_csv(osm_path)

    mod.build(csv_path, out_path, osm_path)

    with out_path.open("rb") as fh:
        data = pickle.load(fh)

    by_ident = {a["ident"]: a for a in data["airports"]}
    assert set(by_ident) == {"EDDF", "KPSM", "ETAR", "EGUN", "LCRA",
                             "XMIL", "HMIL", "ZZZ1"}

    eddf = by_ident["EDDF"]
    assert eddf["type"] == "large_airport"
    assert eddf["radius_km"] == mod.DEFAULT_RADII_KM["large_airport"]
    assert eddf["country"] == "DE"
    assert eddf["_source"] == "ourairports"

    # US dual-use dict promotion
    kpsm = by_ident["KPSM"]
    assert kpsm["type"] == "military_base"
    assert kpsm["radius_km"] == mod.US_MIL_DUAL_USE["KPSM"][1]
    assert kpsm["_source"] == "osm_dual_use"

    # OSM cross-reference promotion
    etar = by_ident["ETAR"]
    assert etar["type"] == "military_base"
    assert etar["radius_km"] == mod.DEFAULT_RADII_KM["military_base"]
    assert etar["_source"] == "osm_military"

    egun = by_ident["EGUN"]
    assert egun["type"] == "military_base"
    assert egun["_source"] == "osm_military"

    # MIL_OVERRIDE promotion (OSM gap)
    lcra = by_ident["LCRA"]
    assert lcra["type"] == "military_base"
    assert lcra["radius_km"] == mod.MIL_OVERRIDE["LCRA"]
    assert lcra["_source"] == "mil_override"

    # military-sounding name but NOT in OSM/override -> stays civilian
    xmil = by_ident["XMIL"]
    assert xmil["type"] == "medium_airport"
    assert xmil["_source"] == "ourairports"

    # heliport excluded from promotion even though ICAO is in OSM set
    hmil = by_ident["HMIL"]
    assert hmil["type"] == "heliport"
    assert hmil["_source"] == "ourairports"

    assert by_ident["ZZZ1"]["country"] == "ZZ"

    n = len(data["airports"])
    assert data["radii_km"].shape == (n,)
    assert data["latlon_rad"].shape == (n, 2)
    assert isinstance(data["tree"], BallTree)
