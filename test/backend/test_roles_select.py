"""backend.util.roles relevance predicates (rotary + non_aircraft exclusion).

Role semantics (see the roles taxonomy in data/in/config/roles.json):
EXCLUDED_ROLES = rotary roles plus non_aircraft; is_excluded_type is the
partition-stage exclusion gate (see util/roles.py's EXCLUDED_ROLES comment).
"""
from backend.util.roles import EXCLUDED_ROLES, is_excluded_type, is_rotary


def test_excluded_roles_is_rotary_plus_non_aircraft():
    assert EXCLUDED_ROLES == frozenset(
        {"rotary_lift", "rotary_attack", "tilt_rotor", "non_aircraft"})


def test_is_excluded_type_rotary():
    assert is_excluded_type("H60")     # rotary_lift
    assert is_excluded_type("H64")     # rotary_attack


def test_is_excluded_type_non_aircraft():
    assert is_excluded_type("TWR")     # non_aircraft ground station


def test_is_excluded_type_ordinary_aircraft_not_excluded():
    assert not is_excluded_type("C17")


def test_is_excluded_type_unknown_code_not_excluded():
    # An unmapped/absent designator has an empty roleset -> not excluded
    # (unknown types are handled downstream by type_status, not exclusion).
    assert not is_excluded_type("ZZZZ")
    assert not is_excluded_type(None)
    assert not is_excluded_type("")


def test_is_excluded_type_agrees_with_is_rotary_on_rotary_types():
    for tc in ("H60", "H64", "H47", "C17", "TWR"):
        if is_rotary(tc):
            assert is_excluded_type(tc)
