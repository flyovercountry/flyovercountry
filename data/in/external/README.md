# data/in/external — manifest

External inputs downloaded by hand. Nothing in this directory is generated
by the pipeline, and nothing here is fetched automatically — every file is
placed manually. Everything is reproducible from the URLs below. Dates are
the upstream data dates, not download dates.

Groups: **REQUIRED** — the pipeline cannot produce its outputs without it.
**OPTIONAL** — consumed when present; output degrades gracefully without
it. `./run status` prints the same grouping.

| Path (data/in/external/...) | Group | Consumer | Origin | Refresh cadence |
|---|---|---|---|---|
| aircraft/aircraft-database-complete-*.csv | REQUIRED | etl types stage | https://www.adsb.lol/data/ | monthly series (upstream paused since 2025-08); newest by name used |
| airports.csv | REQUIRED | `./run gen airports` | https://davidmegginson.github.io/ourairports-data/airports.csv | rolling daily exports; refresh occasionally |
| ne_10m_admin_0_countries/ | REQUIRED | `./run gen raster`, `./run gen boundaries` | https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip | static (NE 5.x) |
| aircraft/mictronics_indexedDB.zip | REQUIRED | etl types stage, `./run gen mil` | https://www.mictronics.de/aircraft-database/indexedDB_old.php | weekly upstream; manual download only, refresh at your discretion |
| aircraft/plane-alert-db.csv | OPTIONAL | etl types stage | https://raw.githubusercontent.com/sdr-enthusiasts/plane-alert-db/main/plane-alert-db.csv | several updates/month |
| aircraft/basic-ac-db.json.gz | OPTIONAL | `./run gen mil`, etl types stage | https://downloads.adsbexchange.com/downloads/basic-ac-db.json.gz | frozen upstream (2025-09) |
| aircraft/doc8643_aircraft_types.json | OPTIONAL | etl types stage (type validation + rotary backstop) | POST https://www4.icao.int/doc8643/External/AircraftTypes | ICAO revises continuously; refresh occasionally |
| osm_military_airports.csv | OPTIONAL | `./run gen airports` | Overpass API (see query below) | refresh occasionally; OSM is continuously edited |

Everything below is the detailed per-source provenance (origin, dates,
methodology caveats), kept as the authority the table summarizes.

## Top level

### airports.csv
- From: https://davidmegginson.github.io/ourairports-data/airports.csv
- What: OurAirports world airport database (ident, type, lat/lon, country).
  Consumed by `./run gen airports` to build airports.pkl.
- Date: rolling daily exports; refresh occasionally.

### ne_10m_admin_0_countries/
- From: https://www.naturalearthdata.com/downloads/10m-cultural-vectors/
  (direct: https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip)
- What: Natural Earth 1:10m country polygons. Consumed by `./run gen raster`
  and `./run gen boundaries` for airspace attribution.
- Date: Natural Earth 5.x series (see VERSION.txt in the directory).

## aircraft/

Aircraft metadata databases. Two consumers:
- `backend/pipeline/gen/mil.py` (military-status evidence -> mil_icao24.txt; only
  the Mictronics and basic-ac-db sources feed this)
- the etl types stage (type-code resolution -> data/build/etl/aircraft.parquet;
  bulk snapshots are ordered by data date relative to the crop interval
  end — post-crop snapshots first, then in-crop daily attestations, then
  pre-crop snapshots; all local files, no network)

For military status the two sources are consulted independently and their
result is unioned: each carries only positive evidence (absence never
proves civilian), so which one runs first only decides which source leads
a hex's provenance line in mil_icao24.txt, never which hexes are included.
This union was checked against the per-day adsb.lol military `dbFlags` bit
on a larger flown dataset and found to reproduce it exactly, which is why
the pipeline uses the static union instead of recomputing that bit from
every daily record. Owner/operator name matching and other affiliation
heuristics were evaluated and dropped: they flagged aircraft by who
operates them, not by a per-hex military designation.

### 1. mictronics_indexedDB.zip (manual download)
- From: https://www.mictronics.de/aircraft-database/indexedDB_old.php
  (manual download only — nothing in the pipeline fetches it)
- What: Mictronics aircraft DB. The zip's aircrafts.json maps HEX ->
  {r: registration, t: ICAO type, f: flags bitstring, d: description};
  f position 0 is the military bit. Actively maintained (weekly updates
  with real type-code churn, verified at download). Types feed the etl types stage;
  the military flag feeds gen/mil.py. Both are CURRENT information, unlike
  basic-ac-db below.
- Why direct: this is tar1090-db's primary upstream. tar1090-db is just
  Mictronics merged with basic-ac-db, both of which we consume directly,
  so the derived DB adds no information and blurs dates (frozen basic-ac
  types would enter under the weekly build's date).
- Date: the zip comment carries the upstream generation stamp for any
  given copy.

### 2. plane-alert-db.csv
- From: https://raw.githubusercontent.com/sdr-enthusiasts/plane-alert-db/main/plane-alert-db.csv
- What: the hand-edited master list of the plane-alert-db project (hex,
  registration, operator, type, #CMPG class, Category). Actively
  maintained, several data updates per month. The $ICAO Type column is a
  type-code source for the etl types stage (all rows; the type chain is
  scoped to relevant hexes anyway). Not consulted for military-status
  evidence: its CMPG/Category classification is curator affiliation
  judgment, not a per-hex database flag (see the union rationale above),
  so gen/mil.py does not use this file.
- Methodology caveat: curation methodology is not published. Rows are
  community research against spotting references (scramble.nl,
  live-military-mode-s.eu, rotorspot.nl); the maintainers explicitly
  disclaim type-variant precision, and there is no per-row citation.
- Date: last upstream data commit 2026-05-07.

### 3. basic-ac-db.json.gz (frozen 2025-09-02)
- From: https://downloads.adsbexchange.com/downloads/basic-ac-db.json.gz
- What: ADS-B Exchange basic aircraft DB, JSON-lines with an explicit
  per-aircraft `mil` boolean (plus reg, type, ownop, PIA/LADD). gen/mil.py
  uses only the `mil` boolean, one of the two sources unioned into
  mil_icao24.txt (the other is Mictronics). The icaotype field is a
  separate type-code source for the etl types stage.
- Methodology caveat: ADS-B Exchange does not document how the mil flag or
  ownop are derived. The ownop text reads like FAA registry data for US
  hexes, but that is inference from content, not a published statement;
  the mil flag bottoms out at "ADS-B Exchange says so".
- Date: Last-Modified 2025-09-02; publication appears paused since.

### doc8643_aircraft_types.json (reference)
- From: POST https://www4.icao.int/doc8643/External/AircraftTypes
  (requires Content-Length: 0; returns the full dataset as one JSON array)
- What: ICAO DOC 8643 "Aircraft Type Designators" - the official registry
  that issues ICAO type codes. 7,260 rows / 2,612 distinct designators
  with ModelFullName, AircraftDescription (LandPlane/SeaPlane/Amphibian/
  Helicopter/Gyrocopter/Tiltrotor), engine count/type, WTC. This is the
  authority for type code -> physical species (the rotary question);
  it carries no military mission/role information. Used by the etl types stage
  in two places: (1) every candidate type code from every source is
  normalized and validated against 8643 designators (plus roles.json
  pseudo-codes) before being accepted, so garbage strings are rejected at
  their layer and a later source can fill the blank; (2) designators 8643
  describes as Helicopter/Gyrocopter/Tiltrotor are rotary regardless of
  roles.json (rotary_source="icao8643-species"), a safety net for unmapped
  codes - the audit that motivated it found SUCO = AH-1W/Z SuperCobra,
  helicopter, previously unmapped. Note: species
  letters in derived DBs differ (Mictronics encodes Tiltrotor as R, this
  API's Description field uses T) - trust AircraftDescription here.
- Date: ICAO revises DOC 8643 continuously; refresh occasionally.

### 4. aircraft-database-complete-2025-08.csv (2025-08)
- From: https://www.adsb.lol/data/ (redistribution; the file follows the
  OpenSky Network aircraft-database-complete-YYYY-MM naming and schema, see
  https://opensky-network.org/datasets/metadata/ -
  s3.opensky-network.org/data-samples, prefix metadata/)
- What: crowdsourced aircraft metadata (registration, typecode, operator,
  owner). No explicit military flag, and not consulted for military-status
  evidence (see the union rationale above); the etl types stage uses only
  the typecode column, as one of the type-code fallback layers.
- Date: 2025-08 snapshot; the newest in the series, monthly publication is
  on hold upstream (as of our last check).

### osm_military_airports.csv
- From: OpenStreetMap via the Overpass API (https://overpass-api.de/).
  Exact query:
  ```
  [out:csv(::lat,::lon,"icao","name","military","aeroway";true;",")][timeout:120];
  (
    node["aeroway"="aerodrome"]["military"];
    way["aeroway"="aerodrome"]["military"];
    relation["aeroway"="aerodrome"]["military"];
  );
  out center;
  ```
- What: all aerodromes in OSM carrying a `military` tag (values: mostly
  `airfield`, occasionally `base`, `yes`, `airport`). 1918 entries, 984
  with ICAO codes. Consumed by `./run gen airports` as the second of three
  military-classification sources, checked in order: `US_MIL_DUAL_USE`
  (hand-curated US civilian fields with military tenants), this OSM
  cross-reference (any OurAirports airport whose ICAO code appears here is
  promoted to `military_base`), then `MIL_OVERRIDE` (hand-curated non-US
  bases OSM does not tag). There is no name-pattern or keyword matching
  anywhere in this classification.
- Methodology caveat: OSM is community-curated; the `military` tag is
  applied by contributors and is not an official designation. Coverage
  gaps exist (e.g., RAF Akrotiri/LCRA and Rota Naval Station/LERT are
  absent as of this snapshot — both are covered instead by
  `MIL_OVERRIDE`). If this file is absent, `./run gen airports` warns and
  continues, promoting fields from `US_MIL_DUAL_USE`/`MIL_OVERRIDE` only.
- Date: the snapshot date of the export you save. OSM data is under ODbL.
- Refresh: run the Overpass query above and save the output, then clean
  with: `python3 -c "import csv; ..."` (see pipeline commit history for
  the exact cleaning script, or regenerate from the query — the cleaning
  is just CSV validation and column normalization).

## Surveyed but not adopted

Sources evaluated for grounding type-code -> military-role mappings:
- DAFI 16-401 (official USAF designation instruction): defines the MDS
  mission letters an earlier internal audit projected onto the taxonomy
  in `data/in/config/roles.json`. That audit's letter table is not
  checked into the active test/backend suite.
- Wikidata: structured per-type "has use" roles under CC0, but no ICAO
  type designator property exists - joining requires fuzzy name matching.
  Possible secondary cross-check, not adopted.
- VRS standing-data, Eurocontrol performance DB, FAA ACFTREF, Joe
  Baugher serial lists, Scramble.nl: all either duplicate DOC 8643 /
  Mictronics species data, carry no role information, or are per-serial
  rather than per-type. Skipped.
