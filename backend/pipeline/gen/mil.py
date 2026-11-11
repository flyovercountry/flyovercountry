#!/usr/bin/env python3
"""Generate the US military icao24 list with per-entry provenance.

Output format (data/build/lookup/mil_icao24.txt), one entry per line:

    <hex>|<hex_lo>-<hex_hi>, <provenance comment>

Only the part before the first comma is actionable (a single icao24 hex or
an inclusive lo-hi range); everything after it documents why that entry is
considered military. Consumers parse via backend.util.mil.

Line 1 is always the US hex range outside the civil N-number series
(ADF7C8-AFFFFF). Subsequent lines are single icao24s in the civil part of
the US block (A00000-ADF7C7), evidenced military by a per-hex database flag.

The single-hex list is the union of two independent per-hex military flags:

    1. Mictronics DB mil flag   (aircrafts.json, f-flag position 0)
    2. adsbexchange basic-ac-db mil boolean

Both sources carry only positive evidence (absence never proves civilian),
so their order only determines provenance, not membership - the output is
the union. This two-source union was verified (on a larger flown dataset)
to exactly match the set of hexes that ever carry the adsb.lol per-day
military dbFlags bit, so the pipeline uses this static union instead of
decompressing every daily record to recompute that bit. Owner/operator
name matching and other affiliation-based heuristics were deliberately
dropped: they flag aircraft by who operates them, not by a per-hex
military designation.

Usage:
    python backend/pipeline/gen/mil.py [output]
        [--mictronics <indexedDB.zip>]
        [--basic-ac <basic-ac-db.json.gz>]

Both source files are optional at runtime - if one is missing, a warning
is printed and the other alone determines the singles list.
"""
import argparse
import gzip
import json
import zipfile
from pathlib import Path
from backend.util.log import log

US_HEX_LO = 0xA00000
US_HEX_HI = 0xAFFFFF
# The FAA assigns civil N-numbers algorithmically to A00001-ADF7C7
# (N1..N99999); ADF7C8 is the first address outside that series. The
# sub-allocation above it is not published; readsb's isMilRange() uses the
# same boundary, and every airframe observed there carries a military serial.
US_MIL_HEX_LO = 0xADF7C8
US_MIL_HEX_HI = 0xAFFFFF


def _in_scope(icao24: str) -> bool:
    """Civil part of the US hex range, i.e. excluding the line-1 range."""
    try:
        hv = int(icao24, 16)
    except (ValueError, TypeError):
        return False
    return US_HEX_LO <= hv <= US_HEX_HI and not (
        US_MIL_HEX_LO <= hv <= US_MIL_HEX_HI)


def scan_mictronics(path: Path, evidenced: dict[str, list[str]]):
    """Mictronics aircrafts.json: military bit is position 0 of the f field."""
    with zipfile.ZipFile(path) as z:
        db = json.loads(z.read("aircrafts.json"))
    for h, v in db.items():
        icao24 = h.strip().lower()
        if (v.get("f") or "").startswith("1") and _in_scope(icao24):
            evidenced.setdefault(icao24, []).append(
                "mictronics military flag")


def scan_basic_ac(path: Path, evidenced: dict[str, list[str]]):
    """adsbexchange basic-ac-db: explicit mil boolean."""
    with gzip.open(path, "rt") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            icao24 = (d.get("icao") or "").strip().lower()
            if not _in_scope(icao24):
                continue
            if d.get("mil"):
                evidenced.setdefault(icao24, []).append(
                    "basic-ac-db mil=true")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, nargs="?",
                        default=Path("data/build/lookup/mil_icao24.txt"))
    parser.add_argument("--mictronics", type=Path, default=None,
                        help="Mictronics indexedDB zip (aircrafts.json)")
    parser.add_argument("--basic-ac", type=Path, default=None,
                        help="adsbexchange basic-ac-db.json.gz")
    args = parser.parse_args()

    sources = [
        ("mictronics", args.mictronics, scan_mictronics),
        ("basic-ac-db", args.basic_ac, scan_basic_ac),
    ]

    evidenced: dict[str, list[str]] = {}
    contributions: list[tuple[str, int, int]] = []
    for name, path, scan in sources:
        if path is None or not path.exists():
            log(f"warning: {name} source missing, skipping"
                + (f" ({path})" if path else ""))
            continue
        before = set(evidenced)
        try:
            scan(path, evidenced)
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError,
                EOFError, OSError) as e:
            # A corrupt/truncated source degrades exactly like an absent
            # one (warn and carry on) — a partial manual download must not
            # abort the generator with a traceback.
            log(f"warning: {name} source unreadable "
                f"({type(e).__name__}: {e}), skipping ({path})")
            continue
        hits = sum(1 for m in evidenced.values()
                   if any(s.startswith(name) for s in m))
        contributions.append((name, hits, len(set(evidenced) - before)))

    if not contributions:
        log("warning: NO military-flag source was readable — the list "
            "will contain only the hex prefix range; DB-evidenced "
            "aircraft outside it will be missed")

    lines = [f"{US_MIL_HEX_LO:06x}-{US_MIL_HEX_HI:06x}, US hex range outside the civil N-number series"]
    for icao24 in sorted(evidenced):
        lines.append(f"{icao24}, {'; '.join(evidenced[icao24])}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n")

    log(f"  range line: {US_MIL_HEX_LO:06x}-{US_MIL_HEX_HI:06x}")
    for name, hits, new in contributions:
        log(f"  {name}: {hits:,} hits, {new:,} newly discovered")
    log(f"  total singles: {len(evidenced):,}")
    log(f"wrote {args.output}")


if __name__ == "__main__":
    main()
