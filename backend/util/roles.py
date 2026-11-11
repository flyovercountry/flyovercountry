"""Operational-role lookup from config/roles.json, plus the rotary/excluded
role-set predicates (see ROTARY, EXCLUDED_ROLES, is_rotary, is_excluded_type
below) — all role semantics in one place."""
from __future__ import annotations

import json

from backend.util.paths import config_dir

_TAX: dict | None = None


def _load() -> dict:
    # config_dir() resolved lazily per first use, not at import time, so a
    # late FLY_DATA_DIR (test fixtures) is honoured — see dimensions._load.
    global _TAX
    if _TAX is None:
        with open(config_dir() / "roles.json") as f:
            tax = json.load(f)
        # One role mechanism: base roles plus desc_roles narrowing. The
        # retired "prefixes" and "context" keys are rejected rather than
        # ignored, and desc_roles patterns must be upper-case because
        # roles_of_desc matches them against an upper-cased desc.
        if tax.get("prefixes"):
            raise ValueError(
                "roles.json: 'prefixes' is retired — use explicit "
                "designator entries")
        for tc, d in tax.get("designators", {}).items():
            if "context" in d:
                raise ValueError(
                    f"roles.json: designator {tc} carries a retired "
                    f"'context' rule — fold the role into 'roles' or use "
                    f"'desc_roles' variant narrowing")
            for pattern, _roles in d.get("desc_roles") or []:
                if pattern != pattern.upper():
                    raise ValueError(
                        f"roles.json: designator {tc} desc_roles pattern "
                        f"{pattern!r} is not upper-case and can never "
                        f"match an upper-cased description")
        _TAX = tax
    return _TAX


def reset_taxonomy_cache() -> None:
    """Forget the cached roles.json; next call re-resolves and re-reads."""
    global _TAX
    _TAX = None


def roles_of(type_code: str | None) -> frozenset[str]:
    """All roles an aircraft of this type could fill.

    Use for filtering: an aircraft is included when its roleset intersects
    the active filter. No-precedence, no fallback: unmapped or absent
    designators return an EMPTY frozenset — callers handle emptiness (see
    the roles taxonomy in data/in/config/roles.json). There is no literal role "other".
    """
    tax = _load()
    tc = "".join(c for c in (type_code or "").strip().upper() if c.isalnum())
    if not tc:
        return frozenset()
    d = tax["designators"].get(tc)
    if d is not None:
        return frozenset(d.get("roles") or [])
    return frozenset()


def roles_of_desc(type_code: str | None, desc: str | None) -> frozenset[str]:
    """Narrow roles using the aircraft description string.

    If the designator has a desc_roles mapping and the desc matches a
    pattern (substring, first match wins), return just those roles.
    Otherwise fall back to roles_of() (the full unnarrowed set).
    """
    if not desc:
        return roles_of(type_code)
    tax = _load()
    tc = "".join(c for c in (type_code or "").strip().upper() if c.isalnum())
    if not tc:
        return frozenset()
    d = tax["designators"].get(tc)
    if d is None:
        return roles_of(type_code)
    desc_rules = d.get("desc_roles")
    if not desc_rules:
        return roles_of(type_code)
    # case is normalized at this boundary (desc upper-cased here, patterns
    # validated upper-case at taxonomy load), so the matcher itself stays a
    # plain exact-substring test — live desc casing is an external contract
    # we don't control, and a silent case miss under-narrows invisibly
    desc = desc.upper()
    for pattern, roles in desc_rules:
        if pattern in desc:
            return frozenset(roles)
    return roles_of(type_code)


def taxonomy() -> dict:
    return _load()


# ---------------------------------------------------------------------------
# Cheap per-aircraft relevance predicates for etl. Relevance here means:
# icao24 is relevant per backend.util.mil.is_relevant (US-mil hex prefix
# range union DB-evidenced singles) and non-excluded (see EXCLUDED_ROLES).
# The remaining criterion — at least one point inside the crop interval
# and crop bbox — is applied where the trace is read (types.py).
# ---------------------------------------------------------------------------

ROTARY = frozenset({"rotary_lift", "rotary_attack", "tilt_rotor"})

# Rotary exclusion outranks everything and happens first (partition stage);
# non_aircraft is excluded the same way.
EXCLUDED_ROLES = ROTARY | frozenset({"non_aircraft"})


def is_rotary(type_code) -> bool:
    """True if any role the type could fill is rotary (conservative exclusion)."""
    return bool(roles_of(type_code) & ROTARY)


def is_excluded_type(type_code) -> bool:
    """True if any role the type could fill is in EXCLUDED_ROLES (rotary or
    non_aircraft) — conservative exclusion, same policy as is_rotary."""
    return bool(roles_of(type_code) & EXCLUDED_ROLES)


def roles_string(type_code, type_status, desc=None) -> str:
    """Sorted, comma-joined roles value for one aircraft.

        type_status resolved AND roles_of(tc) nonempty -> those roles
        type_status resolved AND roles_of(tc) empty     -> "role_unknown"
        type_status type_unknown                        -> "type_unknown"
        type_status typecode_unknown                    -> "typecode_unknown"
    """
    if type_status == "resolved":
        roles = roles_of_desc(type_code, desc)
        return ",".join(sorted(roles)) if roles else "role_unknown"
    if type_status == "type_unknown":
        return "type_unknown"
    if type_status == "typecode_unknown":
        return "typecode_unknown"
    raise ValueError(f"unrecognized type_status: {type_status!r}")
