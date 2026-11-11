"""countries.modes.json's flat `countries` map (permissiveness
0-3 + a single ordinal `interaction` field 0-3 — see
backend/util/focus_config.py's module docstring).

`interaction` replaced three independent booleans (stats/whatif/info) that
used to permit 8 combinations, 4 of them meaningless (e.g. What-If without
stats). The ordinal makes those states unrepresentable: whatif implies
stats implies info, with no exceptions, by construction of
has_info/has_stats/has_whatif (>= 1/2/3).

UA/BY/RU-style display-only countries are no longer a bolted-on
`display_only` key — they're simply interaction-0 entries. Things that must
hold:
  - merge_focus_config() reads the flat `countries` map straight through
    (no tier walk, no separate display-only union), and stamps the derived
    info/stats/whatif booleans onto every entry from its `interaction`.
  - gen/boundaries.py's load_focus_codes() (which decides which countries
    get a boundary polygon) includes every entry regardless of interaction,
    so a display-only country still renders.
  - every country in the real, checked-in config matches the owner's
    interaction table exactly (regression guard against silent drift).
  - `permissiveness` and `interaction` are independent — never derived from
    each other.
"""
import json
import re

from backend.pipeline.gen.boundaries import load_focus_codes
from backend.util.focus_config import (
    has_info, has_stats, has_whatif, merge_focus_config,
)


def _modes():
    return {
        "permissiveness_levels": {
            "0": {"label": "Prohibits all overflight", "color": "#e53935"},
        },
        "countries": {
            "AT": {"permissiveness": 0, "interaction": 2, "name": "Austria"},
            "UA": {"permissiveness": 0, "interaction": 0, "name": "Ukraine"},
            "BY": {"permissiveness": 0, "interaction": 0, "name": "Belarus"},
        },
    }


def test_has_info_stats_whatif_are_strictly_nested_on_the_interaction_ordinal():
    """whatif (>=3) implies stats (>=2) implies info (>=1) by construction,
    for every possible interaction value, not just the ones the real config
    happens to use today."""
    for level in range(4):
        entry = {"interaction": level}
        info, stats, whatif = has_info(entry), has_stats(entry), has_whatif(entry)
        if whatif:
            assert stats, f"interaction={level}: whatif but not stats"
        if stats:
            assert info, f"interaction={level}: stats but not info"
    assert [has_info({"interaction": n}) for n in range(4)] == [False, True, True, True]
    assert [has_stats({"interaction": n}) for n in range(4)] == [False, False, True, True]
    assert [has_whatif({"interaction": n}) for n in range(4)] == [False, False, False, True]


def test_merge_focus_config_passes_the_flat_countries_map_straight_through():
    out = merge_focus_config(_modes(), texts={}, reference={})
    assert set(out["countries"].keys()) == {"AT", "UA", "BY"}
    assert out["countries"]["AT"]["interaction"] == 2
    assert out["countries"]["AT"]["stats"] is True
    assert out["countries"]["AT"]["whatif"] is False
    assert out["countries"]["UA"]["stats"] is False
    assert out["countries"]["UA"]["info"] is False


def test_merge_focus_config_carries_permissiveness_levels_through():
    out = merge_focus_config(_modes(), texts={}, reference={})
    assert out["permissiveness_levels"]["0"]["color"] == "#e53935"


def test_load_focus_codes_includes_every_country_regardless_of_interaction(tmp_path, monkeypatch):
    cfg_path = tmp_path / "countries.modes.json"
    cfg_path.write_text(json.dumps(_modes()))

    import backend.pipeline.gen.boundaries as boundaries_mod
    monkeypatch.setattr(boundaries_mod, "FOCUS_CONFIG", str(cfg_path))

    codes = load_focus_codes()
    assert codes == {"AT", "UA", "BY"}


def test_bosnia_and_serbia_are_absent_from_the_real_config():
    """Owner decision: BA and RS are dropped entirely, not merely
    display-only or flagged off — they must not appear in the real
    checked-in config at all."""
    from backend.util.paths import frontend_config_dir
    modes = json.loads((frontend_config_dir() / "countries.modes.json").read_text())
    assert "BA" not in modes["countries"]
    assert "RS" not in modes["countries"]


# The interaction table — the single source of
# truth this test pins the real config against. permissiveness is NOT part
# of this table: it's an orthogonal field (e.g. CH/AT are permissiveness 0
# but interaction 2; SA/KW are permissiveness 2 but interaction 1) and must
# never be derived from, or used to derive, interaction.
EXPECTED_INTERACTION = {
    # 0: nothing — drawn/shaded, never selectable, no panel.
    "UA": 0, "BY": 0, "RU": 0,
    # 1: info only.
    "SA": 1, "KW": 1,
    # 2: info + stats.
    "ES": 2, "FR": 2, "BE": 2, "CH": 2, "AT": 2, "SI": 2, "HR": 2,
    # 3: info + stats + whatif.
    "IE": 3, "GB": 3, "NL": 3, "DE": 3, "IT": 3, "PL": 3, "CZ": 3,
    "SK": 3, "HU": 3, "RO": 3, "BG": 3, "GR": 3,
}


def test_real_config_matches_the_interaction_table_exactly():
    from backend.util.paths import frontend_config_dir
    modes = json.loads((frontend_config_dir() / "countries.modes.json").read_text())
    countries = modes["countries"]
    assert set(countries.keys()) == set(EXPECTED_INTERACTION.keys()), \
        f"config/table cc set mismatch: {set(countries.keys()) ^ set(EXPECTED_INTERACTION.keys())}"
    for cc, expected in EXPECTED_INTERACTION.items():
        actual = countries[cc].get("interaction")
        assert actual == expected, f"{cc}: interaction={actual}, expected {expected}"


def test_real_config_has_no_leftover_stats_whatif_info_booleans():
    """The three old independent flags must be fully retired from the
    checked-in config file — interaction is the only source of truth."""
    from backend.util.paths import frontend_config_dir
    modes = json.loads((frontend_config_dir() / "countries.modes.json").read_text())
    for cc, info in modes["countries"].items():
        assert "stats" not in info, f"{cc}: leftover 'stats' key in countries.modes.json"
        assert "whatif" not in info, f"{cc}: leftover 'whatif' key in countries.modes.json"
        assert "info" not in info, f"{cc}: leftover 'info' key in countries.modes.json"
        assert "interaction" in info, f"{cc}: missing 'interaction' key"


# ---------------------------------------------------------------------------
# Cross-file pin + per-entry shape checks between countries.modes.json and
# country.reference.json, and a placeholder-substitution regression guard
# for common.texts.json's `templates`. See the `_doc` keys added to each of
# those two config files for the underlying design this pins.
# ---------------------------------------------------------------------------

def test_country_reference_ccs_match_interaction_at_least_1():
    """country.reference.json (the per-country legal/history research) must
    carry exactly the countries with interaction >= 1 (hasInfo, see
    backend/util/focus_config.py) — no reference data for a country the site
    never shows info for, and no info-eligible country missing its
    reference."""
    from backend.util.paths import frontend_config_dir
    modes = json.loads((frontend_config_dir() / "countries.modes.json").read_text())
    ref = json.loads((frontend_config_dir() / "country.reference.json").read_text())
    interaction_ge_1 = {cc for cc, a in modes["countries"].items() if a.get("interaction", 0) >= 1}
    assert set(ref.keys()) == interaction_ge_1, \
        f"country.reference.json vs interaction>=1 mismatch: {set(ref.keys()) ^ interaction_ge_1}"


# Interaction-0 countries (UA/BY/RU, display-only) carry only these three
# keys — no adjective/ctags/headline, since the site never shows them.
_KEYS_INTERACTION_0 = {"permissiveness", "interaction", "name"}
# Every interaction>=1 country carries this base set...
_BASE_KEYS_INTERACTION_GE_1 = {
    "permissiveness", "interaction", "adjective", "ctags", "in_nato", "name",
}
# ...plus this always-optional key (CZ/GB use it for a shorter display name)...
_OPTIONAL_KEYS = {"short_name"}
# ...plus EXACTLY ONE of these two headline shapes (see countries.modes.json's
# own `_doc.headline_shapes`: both are rendered by frontend/src/data.js's
# mergeFocusConfig / backend/util/focus_config.py's merge_focus_config, so
# neither is dead, but a country must pick one, not both, not neither).
_HEADLINE_SHAPES = [{"headline"}, {"headline_name", "headline_note"}]


def test_real_config_country_shape_matches_interaction_level():
    """Every country's key set matches what its interaction level requires,
    including picking exactly one headline shape (both shapes are
    live-rendered, so this only pins 'exactly one', not which one)."""
    from backend.util.paths import frontend_config_dir
    modes = json.loads((frontend_config_dir() / "countries.modes.json").read_text())
    for cc, attrs in modes["countries"].items():
        keys = set(attrs.keys())
        interaction = attrs.get("interaction", 0)
        if interaction == 0:
            assert keys == _KEYS_INTERACTION_0, \
                f"{cc} (interaction 0): expected exactly {_KEYS_INTERACTION_0}, got {keys}"
            continue
        assert _BASE_KEYS_INTERACTION_GE_1 <= keys, \
            f"{cc} (interaction {interaction}): missing base keys, has {keys}"
        extra = keys - _BASE_KEYS_INTERACTION_GE_1 - _OPTIONAL_KEYS
        matching_shapes = [shape for shape in _HEADLINE_SHAPES if shape <= extra]
        assert len(matching_shapes) == 1, \
            f"{cc}: expected exactly one headline shape among {extra}, matched {matching_shapes}"
        leftover = extra - matching_shapes[0]
        assert not leftover, f"{cc}: unrecognized extra key(s) {leftover}"


# The only placeholders frontend/src/data.js knows how to substitute in
# common.texts.json's `templates` strings — two syntaxes, two separate
# substitution passes (see common.texts.json's own `_doc.templates_placeholders`):
#   - `{name}`: mergeFocusConfig (data.js), resolved ONCE at config-merge
#     time from the country's headline_name.
#   - `{{date}}`/`{{end}}`/`{{acft}}`/`{{comb}}`/`{{dir}}`/`{{pos}}`:
#     fillHeadlineStats (data.js), resolved on every render from live
#     filter state ({{acft}} = prevented-aircraft share, added with the
#     aircraft-and-flights headline).
_KNOWN_TEMPLATE_PLACEHOLDERS = {
    "{name}",
    "{{date}}", "{{end}}", "{{acft}}", "{{comb}}", "{{dir}}", "{{pos}}",
    "{{flights_and_landings}}",
}
# Double-brace alternative tried first, so "{{date}}" isn't mis-split into
# nested single-brace tokens.
_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{\{[a-zA-Z_]+\}\}|\{[a-zA-Z_]+\}")


def test_templates_placeholders_are_all_in_the_known_substituted_set():
    """Every {placeholder} appearing in common.texts.json's `templates`
    strings must be one of the tokens data.js's two substitution passes
    actually fill — guards against a typo'd or orphaned placeholder that
    would render literally on the live site."""
    from backend.util.paths import frontend_config_dir
    texts = json.loads((frontend_config_dir() / "common.texts.json").read_text())
    templates = texts.get("templates", {})
    assert templates, "no templates object found in common.texts.json"
    found = set()
    for value in templates.values():
        found.update(_TEMPLATE_PLACEHOLDER_RE.findall(value))
    unknown = found - _KNOWN_TEMPLATE_PLACEHOLDERS
    assert not unknown, f"unrecognized template placeholder(s): {unknown}"
