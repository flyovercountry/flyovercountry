"""The generated roles page must match the live taxonomy.

This is the mechanism that makes the page trustworthy. Without it, the page
is just a copy of roles.json that happened to be accurate once — and the
first taxonomy edit would silently make the published documentation describe
a pipeline that no longer exists. render() and render_json() are exercised
directly against the live taxonomy() here, rather than against a checked-in
file, because there is no checked-in file any more: `./run gen roles` writes
straight into data/build/lookup/, and export.py copies it from there into
data/out/. See test/backend/test_etl_export.py for that copy step.
"""
from __future__ import annotations

import html

from backend.pipeline.gen.roles_page import render, render_json
from backend.util.roles import EXCLUDED_ROLES, ROTARY, taxonomy


def _page() -> str:
    return render()


def test_every_designator_appears():
    """The full index must not silently truncate."""
    page = _page()
    for tc in taxonomy()["designators"]:
        assert f"<code>{tc}</code>" in page, f"type code {tc} missing from page"


def test_every_role_and_group_appears():
    tax = taxonomy()
    page = _page()
    # Labels are HTML-escaped in the page (several contain '&'), so compare
    # against the escaped form — asserting on the raw text would fail for
    # exactly the labels that most need escaping.
    for slug, role in tax["roles"].items():
        assert slug in page, f"role {slug} missing from page"
        label = html.escape(role["label"], quote=True)
        assert label in page, f"role label {role['label']!r} missing"
    for group in tax["groups"]:
        label = html.escape(group["label"], quote=True)
        assert label in page, f"group {group['label']!r} missing"


def _variants_table(page: str) -> str:
    """Just the variant-narrowing table.

    Scoping matters: type codes and description patterns also appear in the
    per-role tables, the full index and several prose descriptions, so a
    whole-page search finds the wrong occurrence and the ordering assertion
    below becomes meaningless.
    """
    start = page.find('<table class="variants">')
    assert start != -1, "variants table missing from page"
    end = page.find("</table>", start)
    assert end != -1, "variants table not closed"
    return page[start:end]


def test_variant_rules_keep_taxonomy_order():
    """roles_of_desc() takes the FIRST matching pattern, so display order is
    semantic, not cosmetic. A sorted variant table would misdescribe the
    pipeline for any designator whose rules are not already in sorted order.
    """
    table = _variants_table(_page())
    for tc, d in taxonomy()["designators"].items():
        rules = d.get("desc_roles") or []
        if len(rules) < 2:
            continue
        # The type code labels only the first row of its run, so the run
        # starts there and ends at the next designator's code.
        start = table.find(f"<code>{tc}</code>")
        assert start != -1, f"{tc}: missing from the variants table"
        cursor = start
        for pattern, _roles in rules:
            idx = table.find(f"<code>{pattern}</code>", cursor)
            assert idx != -1, (
                f"{tc}: variant pattern {pattern!r} missing, or rendered out "
                "of taxonomy order — first-match semantics would be "
                "misrepresented")
            cursor = idx + 1


def test_states_the_exclusions():
    """A reader looking for helicopters must learn they were removed by
    design rather than concluding the data is empty."""
    page = _page()
    for role in sorted(ROTARY | EXCLUDED_ROLES):
        assert role in page, f"excluded role {role} not explained on the page"
    assert "helicopters" in page.lower()


def test_carries_the_uncertainty_disclaimer_and_contact():
    page = _page()
    assert "AI assistance" in page
    assert "very uncertain" in page
    assert "public documentation" in page
    assert "cryptpad.fr/form" in page, "no contact-form link"
    # Prominent, not buried: it must precede the group listings.
    assert page.index("very uncertain") < page.index('id="groups"')


def test_explains_the_unknown_buckets():
    page = _page()
    for bucket in ("typecode_unknown", "type_unknown", "role_unknown"):
        assert bucket in page, f"{bucket} not explained"


def test_no_unescaped_config_text_breaks_the_markup():
    """Labels and descriptions come from config; a stray angle bracket would
    produce broken markup rather than visible text."""
    page = _page()
    tax = taxonomy()
    for d in tax["designators"].values():
        label = d.get("label", "")
        if "<" in label or "&" in label:
            assert label not in page, (
                f"designator label {label!r} appears unescaped")
    # Tag soup check: every opened table is closed.
    assert page.count("<table") == page.count("</table>")
    assert page.count("<tbody>") == page.count("</tbody>")
    assert page.count("<section") == page.count("</section>")


# --- ROLESINLINE: the in-app view's data artifact ------------------------

def _roles_json():
    return render_json()


def test_roles_json_covers_every_designator_and_group():
    tax = taxonomy()
    data = _roles_json()
    assert {d["code"] for d in data["designators"]} == set(tax["designators"])
    assert [g["slug"] for g in data["groups"]] == [g["slug"] for g in tax["groups"]], \
        "group ORDER must match the taxonomy — it is the published UI order"


def test_roles_json_preserves_variant_order():
    """First-match semantics again: the in-app view lists these in order, so
    the data must carry them in order."""
    tax = taxonomy()
    by_code = {d["code"]: d for d in _roles_json()["designators"]}
    for tc, d in tax["designators"].items():
        expected = [p for p, _ in (d.get("desc_roles") or [])]
        actual = [v["pattern"] for v in by_code[tc]["variants"]]
        assert actual == expected, f"{tc}: variant order differs from taxonomy"


def test_roles_json_excluded_roles_are_labelled():
    data = _roles_json()
    assert {e["slug"] for e in data["excluded"]} == set(EXCLUDED_ROLES)
    for e in data["excluded"]:
        assert e["label"], f"{e['slug']} has no label"
        assert e["why"] in ("Rotary-wing", "Not an aircraft")
