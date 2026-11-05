"""Aircraft type resolution and rotary classification (first step of etl).

Resolves an ICAO type code for every relevant aircraft (backend.util.mil.is_relevant
— mil-prefix range union DB-evidenced singles) seen in the daily records,
then — separately — classifies each aircraft as rotary or not. Writes
data/build/etl/aircraft.parquet with columns:

    icao24, type_code, type_source, type_status, rotary, rotary_source,
    excluded

Type sources are layered: each fills only the blanks the previous layers
left, never overriding. The order is defined by the date of the information
each source carries, relative to the end of the crop interval:

    1. Bulk DB snapshots dated AFTER the crop interval, newest first
       (registry knowledge that postdates, and may correct, the crop era)
    2. The most recent non-null type attestation in the daily records
       inside the crop interval (what adsb.lol reported at the time;
       traces carry no type field, so daily records are the attestation)
    3. Bulk DB snapshots dated BEFORE the end of the crop interval,
       newest first (stale registry knowledge, still better than nothing)

Then, unconditionally, the evidenced manual corrections in
data/in/config/type_overrides.json win for the icao24s they name
(type_source="manual-override").

Every candidate type code is normalized (uppercase alphanumerics) and
validated against ICAO DOC 8643 designators plus the pseudo-codes mapped in
roles.json; an invalid candidate is rejected at its layer so a later source
can fill the blank. type_status records what the layers saw:

    resolved         — a canonical type_code was determined.
    type_unknown     — at least one candidate was observed, none validated.
    typecode_unknown — no candidate was ever observed in any layer.

The desc column (used to narrow a multi-role designator's roleset, see
roles_of_desc) is resolved independently: a live in-crop desc wins when it
is specific enough to match one of the type's desc_roles patterns; only an
absent or generic one (_is_generic_desc) falls back to the mictronics
snapshot's "d" field (_resolve_desc). The registry is more complete than the
live data, not more trustworthy, so it never overrides a specific live desc.

Rotary classification is independent of the type layering: a resolved type
is rotary when any of its roles is rotary or DOC 8643 lists the designator
as a rotorcraft. Aircraft with no resolved type are never classed rotary.

All sources are local files (manual downloads documented in
data/in/external/README.md); this stage makes no network calls. Snapshot
dates only matter relative to the crop end, so approximate dates are fine
as long as they fall on the correct side.
"""
from __future__ import annotations

import csv
import gzip
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from backend.util import dimensions
from backend.util.mil import is_relevant
from backend.util.paths import raw_dir, external_dir, etl_dir, config_dir
from backend.util.roles import taxonomy, roles_of, is_rotary, roles_string
from backend.util.log import log


# ---------------------------------------------------------------------------
# ICAO DOC 8643: the official type designator registry
# ---------------------------------------------------------------------------

_ROTARY_DESCRIPTIONS = frozenset({"Helicopter", "Gyrocopter", "Tiltrotor"})


def _doc8643_rows() -> list[dict]:
    p = external_dir() / "aircraft" / "doc8643_aircraft_types.json"
    if not p.exists():
        return []
    return json.loads(p.read_text())


def valid_type_codes() -> frozenset[str]:
    """Codes acceptable as a resolved type: official DOC 8643 designators
    plus the pseudo-codes we deliberately map in roles.json (TWR, FFLO,
    the F18 alias, ...). Anything else from a source is noise."""
    return (frozenset(d["Designator"] for d in _doc8643_rows())
            | frozenset(taxonomy()["designators"]))


def doc8643_rotary_designators() -> frozenset[str]:
    """Designators DOC 8643 describes as Helicopter/Gyrocopter/Tiltrotor."""
    return frozenset(d["Designator"] for d in _doc8643_rows()
                     if d["AircraftDescription"] in _ROTARY_DESCRIPTIONS)


def normalize_type_code(tc: str) -> str:
    """Canonical designator form: uppercase alphanumerics only (drops the
    ' ?' suffix some community DBs append, stray spaces, etc.)."""
    return "".join(c for c in (tc or "").strip().upper() if c.isalnum())


# ---------------------------------------------------------------------------
# Bulk DB snapshots
# ---------------------------------------------------------------------------

def _opensky_csv_path() -> Path | None:
    """Newest aircraft-database-complete-*.csv by filename — lexicographic
    sort is chronological given the YYYY-MM zero-padded upstream naming
    (see data/in/external/README.md: "newest by name used"). None if the
    optional layer has no snapshot on disk at all."""
    candidates = sorted(
        (external_dir() / "aircraft").glob("aircraft-database-complete-*.csv"))
    return candidates[-1] if candidates else None


def _opensky_snapshot_id() -> tuple[str, str]:
    """(BulkSource name, data_date) derived from the newest snapshot's
    filename, e.g. aircraft-database-complete-2025-08.csv ->
    ("opensky-2025-08", "2025-08-01") — coarsened to month granularity,
    like the other bulk sources (see BulkSource.data_date), since the
    filename carries no day. Falls back to a name/date that always sorts
    pre-crop-end when no snapshot is present; harmless since that source
    contributes nothing (an empty dict) in that case anyway."""
    p = _opensky_csv_path()
    if p is None:
        return "opensky", "1970-01-01"
    yyyymm = p.stem.removeprefix("aircraft-database-complete-")
    return f"opensky-{yyyymm}", f"{yyyymm}-01"


def _opensky_type_codes() -> dict[str, str]:
    p = _opensky_csv_path()
    if p is None:
        # The manifest classifies this source REQUIRED (and the etl gate
        # blocks on it); if we get here anyway, degrade loudly, not
        # silently — losing the primary type source quietly lowers
        # resolution quality with no visible signal.
        log("types: WARNING aircraft-database-complete-*.csv missing — "
            "primary type-resolution source skipped")
        return {}
    log(f"types: opensky snapshot -> {p.name}")
    db = pd.read_csv(p, quotechar="'", usecols=["icao24", "typecode"],
                     dtype=str, low_memory=False)
    db["icao24"] = db.icao24.str.lower().str.strip()
    db = db[db.typecode.notna() & (db.typecode != "")]
    return dict(zip(db.icao24, db.typecode))


def _mictronics_path() -> Path:
    return external_dir() / "aircraft" / "mictronics_indexedDB.zip"


def _mictronics_type_codes() -> dict[str, str]:
    """aircrafts.json: {HEX: {r: reg, t: type, f: flags, d: description}}.

    Reads the manually-downloaded snapshot only (no network — see
    data/in/external/README.md for the URL and refresh guidance)."""
    p = _mictronics_path()
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found — download it manually; see the aircraft "
            "sources table in data/in/external/README.md")
    with zipfile.ZipFile(p) as z:
        db = json.loads(z.read("aircrafts.json"))
    return {h.lower(): v["t"] for h, v in db.items() if v.get("t")}


def _mictronics_descriptions() -> dict[str, str]:
    """aircrafts.json "d" field, keyed by icao24.  Fallback only: a live
    in-crop desc always wins when specific enough to narrow the role (see
    _resolve_desc).  Not treated as more trustworthy than live data --
    just more complete (85.5% populated vs. daily-records' flown-only
    coverage)."""
    p = _mictronics_path()
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found — download it manually; see the aircraft "
            "sources table in data/in/external/README.md")
    with zipfile.ZipFile(p) as z:
        db = json.loads(z.read("aircrafts.json"))
    return {h.lower(): v["d"] for h, v in db.items() if v.get("d")}


def _plane_alert_type_codes() -> dict[str, str]:
    """plane-alert-db master list (all categories - we only take types,
    and the type chain is already scoped to relevant hexes per
    backend.util.mil.is_relevant)."""
    p = external_dir() / "aircraft" / "plane-alert-db.csv"
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    with open(p, newline="") as f:
        for row in csv.DictReader(f):
            h = (row.get("$ICAO") or "").strip().lower()
            tc = (row.get("$ICAO Type") or "").strip()
            if h and tc:
                out[h] = tc
    return out


def _basic_ac_type_codes() -> dict[str, str]:
    p = external_dir() / "aircraft" / "basic-ac-db.json.gz"
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    with gzip.open(p, "rt") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            h = (d.get("icao") or "").strip().lower()
            tc = (d.get("icaotype") or "").strip()
            if h and tc:
                out[h] = tc
    return out


@dataclass(frozen=True)
class BulkSource:
    name: str                            # written to type_source
    data_date: str                       # ISO date of the source's information
    load: Callable[[], dict[str, str]]   # icao24 -> type_code


BULK_SOURCES = [
    # Mictronics DB is maintained weekly upstream; the date is the
    # snapshot's generation month, coarsened to month granularity (see
    # data/in/external/README.md). Only its position relative to the crop
    # interval matters — keep it after crop end when refreshing.
    BulkSource("mictronics", "2026-06-01", _mictronics_type_codes),
    # plane-alert-db types are hand-curated in-repo; date of the last
    # upstream data commit at download time.
    BulkSource("plane-alert-db", "2026-05-07", _plane_alert_type_codes),
    # basic-ac-db.json.gz upstream Last-Modified; publication frozen since.
    BulkSource("basic-ac-db", "2025-09-02", _basic_ac_type_codes),
    # OpenSky aircraft-database-complete-*.csv: newest snapshot by filename
    # (see _opensky_csv_path); name/date derived from its YYYY-MM.
    BulkSource(*_opensky_snapshot_id(), _opensky_type_codes),
]


# ---------------------------------------------------------------------------
# Manual type_code overrides: evidenced corrections, not a general resolver
# ---------------------------------------------------------------------------

def _type_overrides_path() -> Path:
    return config_dir() / "type_overrides.json"


def _type_overrides() -> dict[str, str]:
    """icao24 -> type_code for the small, evidenced correction list in
    data/in/config/type_overrides.json (see that file's "_doc" and each
    entry's "evidence" field). Applied as the last step of
    resolve_types(), after every layered bulk-source/daily-records
    candidate has already been considered -- this always wins for the
    hexes it names. Distinct from the typecode/desc disagreement check
    below, which only logs and never resolves anything."""
    p = _type_overrides_path()
    if not p.exists():
        return {}
    doc = json.loads(p.read_text())
    out: dict[str, str] = {}
    for entry in doc.get("overrides", []):
        for h in entry["icao24s"]:
            out[h.lower()] = entry["type_code"]
    return out


# ---------------------------------------------------------------------------
# desc resolution: live daily-records desc, with a mictronics fallback for
# absent/generic descs (see roles taxonomy in data/in/config/roles.json for how
# desc narrows a multi-role designator's roleset).
# ---------------------------------------------------------------------------

def _is_generic_desc(type_code: str | None, desc: str | None) -> bool:
    """True if `desc` is present but too generic to narrow a multi-role
    designator's roleset -- i.e. roles_of_desc would fall through to the
    type's full ambiguous roleset because desc matches none of the
    designator's own desc_roles patterns. Designators with no desc_roles
    block (single-role, or multi-role with no configured patterns) are
    never "generic" by this definition: there is no more-specific pattern
    to ask desc to match in the first place."""
    if not desc:
        return False
    tax = taxonomy()
    tc = normalize_type_code(type_code)
    d = tax["designators"].get(tc)
    if d is None:
        return False
    desc_rules = d.get("desc_roles")
    if not desc_rules:
        return False
    return not any(pattern in desc for pattern, _ in desc_rules)


def _resolve_desc(type_code: str | None, live_desc: str | None,
                  mictronics_desc: str | None) -> str | None:
    """Live in-crop desc wins when specific enough to narrow the roleset;
    mictronics fills in only when live desc is absent or generic."""
    if live_desc and not _is_generic_desc(type_code, live_desc):
        return live_desc
    return mictronics_desc or live_desc


# ---------------------------------------------------------------------------
# Daily records: in-crop type attestations
# ---------------------------------------------------------------------------

def _daily_records() -> tuple[set[str], dict[str, str], dict[str, str]]:
    """All relevant aircraft (backend.util.mil.is_relevant) seen in the crop
    interval, the most recent non-null type attestation for each, and
    the most recent non-null desc for each (dates scanned ascending,
    so later values overwrite earlier ones)."""
    seen: set[str] = set()
    attested: dict[str, str] = {}
    descs: dict[str, str] = {}
    for d in dimensions.dates():
        p = raw_dir() / f"date={d}" / "aircraft.parquet"
        if not p.exists():
            continue
        import pyarrow.parquet as pq
        pf_schema = pq.read_schema(str(p))
        cols = ["icao24", "type_code"]
        has_desc = "desc" in pf_schema.names
        if has_desc:
            cols.append("desc")
        a = pd.read_parquet(p, columns=cols)
        for row in a.itertuples(index=False):
            ic = row.icao24
            if not is_relevant(ic):
                continue
            seen.add(ic)
            tc = row.type_code
            if isinstance(tc, str) and tc:
                attested[ic] = tc
            if has_desc:
                desc = row.desc
                if isinstance(desc, str) and desc:
                    descs[ic] = desc
    return seen, attested, descs


# ---------------------------------------------------------------------------
# Type resolution: layered fill-blanks
# ---------------------------------------------------------------------------

def resolve_types() -> pd.DataFrame:
    """Resolve type codes for every relevant aircraft (backend.util.mil.is_relevant)
    in the crop interval. Returns columns
    [icao24, type_code, type_source, type_status];
    type_code and type_source are None where no source knew the aircraft.

    type_status is the disjoint cascade documented in the module docstring
    (resolved / type_unknown / typecode_unknown). Every layer's raw lookup
    dict is scanned for a candidate on each still-missing hex before that
    candidate is normalized/validated, so a rejected candidate still counts
    as "seen" — this is what distinguishes type_unknown (garbage was
    observed) from typecode_unknown (nothing was ever observed). Because
    `missing` only shrinks across layers, a hex that ends up unresolved was
    in `missing` at every layer's call, so this scan never misses a
    candidate for the hexes that matter (already-resolved hexes don't need
    the flag).
    """
    seen, attested, descs = _daily_records()
    crop_end = dimensions.end()

    post_crop = sorted((s for s in BULK_SOURCES if s.data_date > crop_end),
                       key=lambda s: s.data_date, reverse=True)
    pre_crop = sorted((s for s in BULK_SOURCES if s.data_date <= crop_end),
                      key=lambda s: s.data_date, reverse=True)

    layers: list[tuple[str, Callable[[], dict[str, str]]]] = (
        [(s.name, s.load) for s in post_crop]
        + [("daily-records", lambda: attested)]
        + [(s.name, s.load) for s in pre_crop]
    )

    valid = valid_type_codes()
    type_code: dict[str, str] = {}
    type_source: dict[str, str] = {}
    candidate_seen: set[str] = set()
    missing = set(seen)
    log(f"types: {len(seen)} relevant aircraft in crop interval")

    def _fill(name: str, lookup: dict[str, str]):
        """Accept normalized, valid candidates; reject noise so a later
        source can fill the blank. Records raw-candidate-seen regardless of
        acceptance (type_unknown vs typecode_unknown, see docstring)."""
        filled = rejected = 0
        for h in list(missing):
            raw = lookup.get(h, "")
            if raw:
                candidate_seen.add(h)
            tc = normalize_type_code(raw)
            if not tc:
                continue
            if tc not in valid:
                rejected += 1
                continue
            type_code[h] = tc
            type_source[h] = name
            missing.discard(h)
            filled += 1
        note = f", rejected {rejected} invalid" if rejected else ""
        log(f"  {name}: filled {filled}, {len(missing)} missing{note}")

    for name, load in layers:
        if not missing:
            break
        _fill(name, load())

    # Manual, evidenced corrections (data/in/config/type_overrides.json) always
    # win, overriding a layer's result if one was already found. Unlike the
    # layers above, this runs unconditionally (not gated on `missing`) and
    # only touches the specific hexes it names.
    applied = 0
    for h, raw_tc in _type_overrides().items():
        if h not in seen:
            continue
        tc = normalize_type_code(raw_tc)
        if tc not in valid:
            raise ValueError(
                f"type_overrides.json: override type_code {raw_tc!r} for "
                f"{h} is not a valid designator")
        type_code[h] = tc
        type_source[h] = "manual-override"
        candidate_seen.add(h)
        missing.discard(h)
        applied += 1
    if applied:
        log(f"  manual-override: applied {applied}")

    def _status(h: str) -> str:
        if h in type_code:
            return "resolved"
        if h in candidate_seen:
            return "type_unknown"
        return "typecode_unknown"

    mictronics_desc = _mictronics_descriptions()
    sorted_hexes = sorted(seen)
    final_desc = [
        _resolve_desc(type_code.get(h), descs.get(h), mictronics_desc.get(h))
        for h in sorted_hexes
    ]
    n_desc_fallback = sum(
        1 for h, d in zip(sorted_hexes, final_desc)
        if d and d != descs.get(h) and mictronics_desc.get(h) == d
    )
    if n_desc_fallback:
        log(f"  desc: mictronics fallback used for {n_desc_fallback} "
            "aircraft (live desc absent or too generic to narrow role)")

    return pd.DataFrame({
        "icao24": sorted_hexes,
        "type_code": [type_code.get(h) for h in sorted_hexes],
        "desc": final_desc,
        "type_source": [type_source.get(h) for h in sorted_hexes],
        "type_status": [_status(h) for h in sorted_hexes],
    })


# ---------------------------------------------------------------------------
# Rotary classification (independent of the type layering above)
# ---------------------------------------------------------------------------

def classify_rotary(aircraft: pd.DataFrame) -> pd.DataFrame:
    """Add rotary + rotary_source columns to a resolve_types() frame.

    An aircraft is rotary if any role of its type is rotary
    (rotary_source="type-roles") or DOC 8643 describes the designator as
    Helicopter/Gyrocopter/Tiltrotor (rotary_source="icao8643-species");
    the official registry is a safety net for codes roles.json has not
    mapped. Untyped aircraft are not rotary.
    """
    by_roles = aircraft.type_code.map(
        lambda tc: is_rotary(tc) if isinstance(tc, str) else False)
    by_species = aircraft.type_code.isin(doc8643_rotary_designators())

    out = aircraft.copy()
    out["rotary"] = by_roles | by_species
    # Non-rotary rows also get "type-roles": the column only carries
    # provenance where rotary is True.
    out["rotary_source"] = np.where(by_species & ~by_roles,
                                    "icao8643-species", "type-roles")
    return out


def classify_excluded(aircraft: pd.DataFrame) -> pd.DataFrame:
    """Add the excluded column: rotary OR a non_aircraft-role type.

    Exclusion happens first (partition stage) and outranks everything.
    non_aircraft is a pure roleset check; it has no DOC 8643 analogue.
    """
    is_non_aircraft = aircraft.type_code.map(
        lambda tc: "non_aircraft" in roles_of(tc) if isinstance(tc, str) else False)
    out = aircraft.copy()
    out["excluded"] = aircraft.rotary | is_non_aircraft
    return out


# ---------------------------------------------------------------------------
# typecode/desc family disagreement check: logs only, never resolves. It
# generalises the T38/B762 contradiction corrected in type_overrides.json,
# and is conservative so same-manufacturer variant collisions (the King
# Air 350 desc under both B350 and BE20/BE30) are not flagged.
# ---------------------------------------------------------------------------

# DOC 8643 wake turbulence categories run L, L/M, M, M/H, H. Only the two
# extremes count as a disagreement; adjacent classes are routine
# manufacturer-family variation.
_WTC_EXTREMES = frozenset({"L", "H"})


def _designator_family(tc: str, doc8643_by_designator: dict) -> tuple[str | None, str | None]:
    """(AircraftDescription species, WTC) for a designator, from DOC 8643.
    Both fields are None for designators DOC 8643 doesn't carry (roles.json
    pseudo-codes like TWR) -- those never trigger a disagreement here."""
    info = doc8643_by_designator.get(tc)
    if info is None:
        return None, None
    return info.get("AircraftDescription"), info.get("WTC")


def _families_disagree(fam_a: tuple, fam_b: tuple) -> bool:
    species_a, wtc_a = fam_a
    species_b, wtc_b = fam_b
    if species_a and species_b and species_a != species_b:
        return True  # e.g. LandPlane vs Helicopter
    if wtc_a in _WTC_EXTREMES and wtc_b in _WTC_EXTREMES and wtc_a != wtc_b:
        return True  # e.g. "L" vs "H"
    return False


def check_typecode_desc_family_agreement(aircraft: pd.DataFrame) -> list[dict]:
    """For every desc string shared by >=2 distinct type_codes among
    resolved aircraft, flag the type_code(s) in the *minority* if any
    pairwise comparison in that desc-cluster disagrees in family (species
    or WTC-extreme, see _families_disagree). Only the minority is flagged
    -- the majority is, purely by volume, the more likely correct read of
    an ambiguous desc string; this is a logging heuristic, not a
    resolution (a 50/50 split flags both sides).

    Returns a list of {"icao24", "type_code", "desc"} dicts, one per
    flagged aircraft. Never mutates `aircraft` or resolves anything --
    callers are expected to log this and move on."""
    doc8643 = {d["Designator"]: d for d in _doc8643_rows()}
    resolved = aircraft[aircraft["type_status"] == "resolved"]

    by_desc: dict[str, list[tuple[str, str]]] = {}
    for icao24, tc, desc in zip(resolved["icao24"], resolved["type_code"],
                                resolved["desc"]):
        if isinstance(desc, str) and desc:
            by_desc.setdefault(desc, []).append((icao24, tc))

    flagged = []
    for desc, entries in by_desc.items():
        tcs = sorted({tc for _, tc in entries})
        if len(tcs) < 2:
            continue
        families = {tc: _designator_family(tc, doc8643) for tc in tcs}
        disagreeing_tcs: set[str] = set()
        for i, a in enumerate(tcs):
            for b in tcs[i + 1:]:
                if _families_disagree(families[a], families[b]):
                    disagreeing_tcs.add(a)
                    disagreeing_tcs.add(b)
        if not disagreeing_tcs:
            continue
        counts: dict[str, int] = {}
        for _, tc in entries:
            counts[tc] = counts.get(tc, 0) + 1
        majority_n = max(counts[tc] for tc in disagreeing_tcs)
        for icao24, tc in entries:
            if tc in disagreeing_tcs and counts[tc] < majority_n:
                flagged.append({"icao24": icao24, "type_code": tc, "desc": desc})
    return flagged


def _log_typecode_desc_disagreements(flagged: list[dict]):
    if not flagged:
        log("  typecode/desc disagreement check: none flagged")
        return
    log(f"  typecode/desc disagreement check: {len(flagged)} aircraft "
        "flagged (logged only, not resolved):")
    for f in flagged:
        log(f"    icao24={f['icao24']} type_code={f['type_code']} "
            f"desc={f['desc']!r}")


# ---------------------------------------------------------------------------
# Known-unresolved identities: specific icao24s whose type/role assignment
# was investigated and deliberately left unresolved pending a dedicated
# follow-up pass, kept here for future reference.
# ---------------------------------------------------------------------------

KNOWN_UNRESOLVED_IDENTITIES = [
    {
        "icao24s": ["ae63cf", "ae63d0", "ae63d2", "ae63d3", "ae63d4",
                    "ae63d5", "ae63d6"],
        "summary": "T38/767 hex family: type_overrides.json corrects these "
                    "to B762 on decisive range evidence, but WHY mictronics "
                    "assigned T38 (stale registry entry vs. ICAO hex reuse "
                    "vs. an upstream data error) is not established.",
    },
    {
        "icao24s": None,  # all 11 flown F15 aircraft; no distinguishing
                          # per-hex evidence was found to name individuals
        "summary": "11 flown F15 aircraft share one generic desc "
                    "('MCDONNELL-DOUGLAS F-15 Eagle'); whether any are "
                    "F-15E Strike Eagles (multirole, doctrinally distinct "
                    "from an air-superiority F-15C/D) under that generic "
                    "text is unknown -- not individually checked.",
    },
    {
        "icao24s": ["ae7466", "af83f0"],
        "summary": "GLEX aircraft with desc 'BOMBARDIER BD-700 Global "
                    "6000/6500': whether these are genuine E-11A BACN "
                    "aircraft is unresolved -- sources conflict on whether "
                    "a Global-6000-based desc is consistent with, or "
                    "distinct from, the E-11A program's aircraft lineage.",
    },
    {
        "icao24s": ["ae5752"],
        "summary": "B350 aircraft with mictronics desc 'MC-12W Liberty': "
                    "the MC-12W Liberty program's last aircraft was "
                    "reportedly retired 2025-08-02, 6-8 months before this "
                    "crop window -- whether this specific serial was still "
                    "active, transferred, or the mictronics record is "
                    "simply stale is unresolved.",
    },
]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_aircraft_table() -> pd.DataFrame:
    """Resolve types, classify rotary, write data/build/etl/aircraft.parquet.

    Lives under data/build/etl (not build_dir) because build_dir
    (data/out/data) is the export stage's own published-artifact tree —
    export overwrites the files it writes there in place (mkdir + per-file
    write, not a wipe first; see paths.build_dir()'s docstring), but
    aircraft.parquet is upstream ETL working state, not something export
    publishes, so it has no business there regardless.
    """
    aircraft = classify_excluded(classify_rotary(resolve_types()))

    aircraft["roles"] = [
        roles_string(
            tc or "",
            status,
            desc if pd.notna(desc) else None,
        )
        for tc, status, desc in zip(
            aircraft.type_code, aircraft.type_status, aircraft.desc,
        )
    ]

    out = etl_dir() / "aircraft.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    aircraft.to_parquet(out, index=False)

    by_source = aircraft.type_source.fillna("(unresolved)").value_counts()
    log("  type_source: "
        + "  ".join(f"{s}={n}" for s, n in by_source.items()))
    by_status = aircraft.type_status.value_counts()
    log("types: "
        + "  ".join(f"{s}={by_status.get(s, 0)}"
                    for s in ("resolved", "type_unknown", "typecode_unknown")))
    n_rot = int(aircraft.rotary.sum())
    n_non_aircraft = int((aircraft.excluded & ~aircraft.rotary).sum())
    log(f"  rotary: {n_rot}  "
        f"non_aircraft: {n_non_aircraft}  "
        f"non-excluded: {len(aircraft) - int(aircraft.excluded.sum())}")

    _log_typecode_desc_disagreements(check_typecode_desc_family_agreement(aircraft))

    log(f"wrote {out}")
    return aircraft


if __name__ == "__main__":
    build_aircraft_table()
