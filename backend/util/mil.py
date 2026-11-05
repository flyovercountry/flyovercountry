"""Authoritative US military aircraft filter.

Single source of truth for which icao24 hex codes are considered relevant
US military aircraft: `is_relevant` (mil-prefix range union DB-evidenced
singles). Used identically by ingest and by the etl relevance selection
(see the is_excluded_type/is_rotary predicates in util/roles.py) so the
two never disagree.

Backed entirely by data/build/lookup/mil_icao24.txt (see backend/pipeline/gen/mil.py).
Each line is '<spec>, <provenance comment>' where spec is either a single
icao24 hex or an inclusive 'lo-hi' hex range; only the spec is actionable.
Range lines express the US military hex prefixes; single lines are
DB-evidenced military aircraft outside those ranges (the union of the
Mictronics and basic-ac-db per-hex military flags — see backend/pipeline/gen/mil.py).
"""
from __future__ import annotations

from backend.util.paths import lookup_dir

_ranges: list[tuple[int, int]] | None = None
_singles: frozenset[str] | None = None


def _load() -> tuple[list[tuple[int, int]], frozenset[str]]:
    global _ranges, _singles
    if _ranges is None:
        path = lookup_dir() / "mil_icao24.txt"
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found - run `./run gen mil` first")
        ranges: list[tuple[int, int]] = []
        singles: set[str] = set()
        for line in path.read_text().splitlines():
            spec = line.split(",", 1)[0].strip().lower()
            if not spec:
                continue
            if "-" in spec:
                lo, hi = spec.split("-", 1)
                ranges.append((int(lo, 16), int(hi, 16)))
            else:
                singles.add(spec)
        _ranges, _singles = ranges, frozenset(singles)
    return _ranges, _singles


def reset_mil_cache() -> None:
    """Forget the cached list; next is_relevant() call re-reads the file."""
    global _ranges, _singles
    _ranges = _singles = None


def is_relevant(icao24: str) -> bool:
    """True if the aircraft should be kept: in a mil range OR DB-evidenced.

    Case-insensitive: the singles set is lowercase, and upstream callers
    are not guaranteed to normalize (raw payload hex casing is an external
    contract we don't control), so normalize here — the one shared place.
    """
    if not isinstance(icao24, str):
        return False
    icao24 = icao24.strip().lower()
    ranges, singles = _load()
    if icao24 in singles:
        return True
    try:
        hv = int(icao24, 16)
    except (ValueError, TypeError):
        return False
    return any(lo <= hv <= hi for lo, hi in ranges)
