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

import pytest

import backend.pipeline.gen.roles_page as roles_page
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


def test_carries_the_uncertainty_disclaimer():
    page = _page()
    assert "AI assistance" in page
    assert "very uncertain" in page
    assert "public documentation" in page
    # Prominent, not buried: it must precede the group listings.
    assert page.index("very uncertain") < page.index('id="groups"')


# ---------------------------------------------------------------------------
# Contact details come from the untracked data/in/config/frontend/contact.json
# (mirrors frontend/src/lib/contact.js). The generator must render a link or
# nothing — never a placeholder and never a hardcoded address.
# ---------------------------------------------------------------------------

def _with_contact(tmp_path, monkeypatch, body: str | None):
    cfg = tmp_path / "frontend"
    cfg.mkdir()
    if body is not None:
        (cfg / "contact.json").write_text(body, encoding="utf-8")
    monkeypatch.setattr(roles_page, "frontend_config_dir", lambda: cfg)


def test_contact_renders_configured_text_and_link(tmp_path, monkeypatch):
    _with_contact(tmp_path, monkeypatch,
                  '{"text": "Write to us", "link": "mailto:someone@example.org"}')
    page = _page()
    assert ('<a href="mailto:someone@example.org" target="_blank" '
            'rel="noopener">Write to us</a> to send a correction.') in page


def test_contact_text_defaults_to_contact_us(tmp_path, monkeypatch):
    _with_contact(tmp_path, monkeypatch,
                  '{"link": "https://signal.me/#eu/abc"}')
    page = _page()
    assert ('<a href="https://signal.me/#eu/abc" target="_blank" '
            'rel="noopener">Contact us</a>') in page


@pytest.mark.parametrize("body", [
    None,                                   # file absent
    "not json",                             # unreadable
    '{"text": "Contact us"}',               # no link
    '{"link": "javascript:alert(1)"}',      # disallowed scheme
    '{"link": "http://example.org/"}',      # plain http not allowed
    '{"link": "", "text": "x"}',            # empty link
])
def test_contact_missing_or_invalid_renders_nothing(tmp_path, monkeypatch, body):
    _with_contact(tmp_path, monkeypatch, body)
    page = _page()
    assert "send a correction" not in page
    assert "mailto:" not in page and "signal.me" not in page
    assert "javascript:" not in page and "cryptpad" not in page


def test_contact_text_is_escaped(tmp_path, monkeypatch):
    _with_contact(tmp_path, monkeypatch,
                  '{"text": "<b>x</b>", "link": "https://example.org/?a=1&b=2"}')
    page = _page()
    assert "&lt;b&gt;x&lt;/b&gt;" in page
    assert "<b>x</b>" not in page
