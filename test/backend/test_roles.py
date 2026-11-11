"""Role taxonomy: roles_of, the no-precedence full roleset for filtering.

Role semantics (see the roles taxonomy in data/in/config/roles.json): a type with multiple
roles has all of them at all times, with no precedence between them.
Unmapped or absent designators resolve to an empty frozenset via
roles_of; the taxonomy has no "other" fallback role.
"""
import json
from pathlib import Path

from backend.util.roles import EXCLUDED_ROLES, roles_of, roles_of_desc, taxonomy

_ROLES_JSON = Path(__file__).resolve().parents[2] / "data/in/config/roles.json"


def test_designator_overrides_prefix_for_apache():
    # H64 starts with the "H" prefix (-> rotary_lift) but the designator wins.
    assert roles_of("H64") == frozenset({"rotary_attack"})


def test_us_mil_helo_designators_present():
    tax = taxonomy()["designators"]
    assert {"H60", "H64", "H47", "H53S", "H500", "UH1Y"} <= set(tax)
    assert "rotary_attack" in roles_of("H500")
    assert "rotary_lift" in roles_of("H60")


def test_roles_of_returns_full_multirole_set():
    # No precedence: every possible role of a multirole type is present.
    assert roles_of("C130") >= frozenset(
        {"tactical_airlift", "air_refuelling", "special_ops_lift", "gunship",
         "csar", "sigint_elint", "maritime_patrol"}
    )


def test_roles_of_includes_context_roles():
    # GLF5's sigint role only appears via a context rule; filtering must see it.
    assert "sigint_elint" in roles_of("GLF5")
    assert "vip_executive" in roles_of("GLF5")


def test_unknown_and_empty_resolve_to_empty_set():
    for tc in ("", None, "ZZZZ"):
        assert roles_of(tc) == frozenset()


def test_normalization_is_case_and_punct_insensitive():
    assert roles_of(" h64 ") == roles_of("H64")
    assert roles_of("c-130") == roles_of("C130")


_PSEUDO_ROLES = frozenset({"typecode_unknown", "type_unknown", "role_unknown"})


def test_taxonomy_integrity():
    tax = json.loads(_ROLES_JSON.read_text())
    declared = set(tax["roles"])
    grouped = {r for g in tax["groups"] for r in g["roles"]}
    referenced = set()
    for d in tax["designators"].values():
        referenced |= set(d.get("roles") or [])
        for rule in d.get("context") or []:
            referenced.add(rule["role"])
    assert referenced <= declared, f"undeclared roles: {referenced - declared}"
    # Every declared role must appear in some group, EXCEPT the
    # EXCLUDED_ROLES (rotary/tilt-rotor/non_aircraft), which are dropped at
    # the partition stage before flights ever reach the role catalog and so
    # are deliberately absent from every spec group (the roles taxonomy in data/in/config/roles.json
    # section 3).
    ungroupable = declared - grouped
    assert ungroupable <= EXCLUDED_ROLES, (
        f"roles missing from groups: {ungroupable - EXCLUDED_ROLES}")


def test_other_role_does_not_exist():
    """The literal role "other" does not exist anywhere in the taxonomy
    (declarations, groups, designators)."""
    tax = taxonomy()
    assert "other" not in tax["roles"]
    assert "other" not in {r for g in tax["groups"] for r in g["roles"]}
    for tc, e in tax["designators"].items():
        roles = set(e.get("roles") or [])
        for rule in e.get("context") or []:
            roles.add(rule["role"])
        assert "other" not in roles, f"{tc} still references 'other'"


def test_no_designator_has_empty_roleset():
    """Every mapped designator must carry at least one substantive role
    (non_aircraft covers ground stations / balloons); there is no 'other'
    bucket to fall into."""
    bad = [tc for tc, e in taxonomy()["designators"].items()
           if not (set(e.get("roles") or [])
                   | {r["role"] for r in (e.get("context") or [])})]
    assert not bad, f"designators with empty roleset: {bad}"


def test_no_foreign_country_contexts():
    """US-military focus: context rules must not condition on foreign
    countries."""
    offenders = [tc for tc, e in taxonomy()["designators"].items()
                 for rule in e.get("context") or []
                 if "country" in (rule.get("if") or {})]
    assert not offenders, f"country-conditioned contexts: {offenders}"


def test_desc_roles_reference_valid_designator_roles():
    """Every role in a desc_roles mapping must be in the designator's
    own roles list (or context roles)."""
    tax = taxonomy()
    for tc, e in tax["designators"].items():
        desc_rules = e.get("desc_roles")
        if not desc_rules:
            continue
        own_roles = set(e.get("roles") or [])
        for rule in e.get("context") or []:
            own_roles.add(rule["role"])
        for pattern, roles in desc_rules:
            for r in roles:
                assert r in own_roles, (
                    f"{tc} desc_roles pattern '{pattern}' assigns "
                    f"'{r}' not in {own_roles}")


def test_roles_of_desc_narrows_c130j():
    assert roles_of_desc("C30J", "Lockheed Martin KC-130J Hercules") \
        == frozenset({"air_refuelling"})
    assert roles_of_desc("C30J", "Lockheed Martin MC-130J Commando II") \
        == frozenset({"special_ops_lift"})


def test_roles_of_desc_no_desc_returns_full_set():
    assert roles_of_desc("C30J", None) == roles_of("C30J")
    assert roles_of_desc("C30J", "") == roles_of("C30J")


def test_roles_of_desc_no_match_returns_full_set():
    assert roles_of_desc("H60", "Sikorsky EH-60A Black Hawk") == roles_of("H60")


def test_roles_of_desc_narrows_f18s_no_slash_variant():
    # Real observed desc strings never carry the "F/A-18" slash (adsb.lol
    # writes "BOEING FA-18 SUPER HORNET"); both the slash and no-slash
    # forms must narrow to multirole_fighter, not fall back to the full
    # {multirole_fighter, sigint_elint} ambiguous set.
    assert roles_of_desc("F18S", "BOEING FA-18 SUPER HORNET") \
        == frozenset({"multirole_fighter"})
    assert roles_of_desc("F18S", "Boeing F/A-18F Super Hornet") \
        == frozenset({"multirole_fighter"})
    assert roles_of_desc("F18S", "Boeing EA-18G Growler") \
        == frozenset({"sigint_elint"})


# -- groups: single source of truth (data/in/config/roles.json) -----------------

def test_groups_shape_matches_design_doc():
    """roles.json "groups" is the sole definition of the six spec groups —
    export.py reads it via taxonomy()["groups"] rather than keeping its own
    copy."""
    groups = taxonomy()["groups"]
    slugs = [g["slug"] for g in groups]
    assert slugs == [
        "airlift", "refuelling", "combat", "isr", "other_roles",
        "role_unknown",
    ]
    on_by_slug = {g["slug"]: g["on"] for g in groups}
    assert on_by_slug == {
        "airlift": True, "refuelling": True, "combat": True, "isr": True,
        "other_roles": False, "role_unknown": True,
    }


def test_groups_have_label_and_nonempty_roles():
    for g in taxonomy()["groups"]:
        assert isinstance(g["label"], str) and g["label"]
        assert isinstance(g["roles"], list) and g["roles"]


def test_group_roles_are_valid_role_names():
    """Every role slug listed in a group must exist either in the declared
    "roles" map or be one of the three synthetic pseudo-roles (the unknown
    buckets), which by design are not designator-derived roles."""
    tax = taxonomy()
    declared = set(tax["roles"])
    for g in tax["groups"]:
        for r in g["roles"]:
            assert r in declared or r in _PSEUDO_ROLES, (
                f"group '{g['slug']}' references unknown role '{r}'")


def test_role_unknown_group_is_exactly_the_pseudo_roles():
    groups_by_slug = {g["slug"]: g for g in taxonomy()["groups"]}
    assert set(groups_by_slug["role_unknown"]["roles"]) == _PSEUDO_ROLES


def test_every_declared_role_is_in_exactly_one_group():
    """Every declared role lands in exactly one group, except EXCLUDED_ROLES
    (dropped at the partition stage, never reach the role catalog — see
    the roles taxonomy in data/in/config/roles.json)."""
    tax = taxonomy()
    declared = set(tax["roles"])
    seen: dict[str, str] = {}
    for g in tax["groups"]:
        for r in g["roles"]:
            if r in declared:
                assert r not in seen, (
                    f"role '{r}' appears in both '{seen[r]}' and "
                    f"'{g['slug']}'")
                seen[r] = g["slug"]
    ungrouped = declared - set(seen)
    assert ungrouped <= EXCLUDED_ROLES, f"ungrouped roles: {ungrouped - EXCLUDED_ROLES}"
    assert not (EXCLUDED_ROLES & set(seen)), (
        f"EXCLUDED_ROLES must not appear in any group: "
        f"{EXCLUDED_ROLES & set(seen)}")


def test_desc_narrowing_is_case_insensitive():
    # live desc casing is an external contract; the boundary normalizes it
    from backend.util.roles import roles_of, roles_of_desc
    lower = roles_of_desc("F15", "boeing f-15e strike eagle")
    upper = roles_of_desc("F15", "BOEING F-15E STRIKE EAGLE")
    assert lower == upper == frozenset({"multirole_fighter"})
    assert lower != roles_of("F15")   # narrowing actually happened


def test_taxonomy_rejects_mixed_case_desc_pattern(tmp_data_dir):
    import json
    import pytest
    import backend.util.roles as roles
    cfg = tmp_data_dir / "in" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "roles.json").write_text(json.dumps({
        "roles": {}, "groups": {}, "prefixes": {},
        "designators": {"XX99": {"roles": ["utility_light"],
                                 "desc_roles": [["Strike Eagle",
                                                 ["multirole_fighter"]]]}},
    }))
    roles.reset_taxonomy_cache()
    try:
        with pytest.raises(ValueError, match="not upper-case"):
            roles.roles_of("XX99")
    finally:
        roles.reset_taxonomy_cache()
