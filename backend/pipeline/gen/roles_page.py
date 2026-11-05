"""Generate the aircraft-model to role-group reference page.

The role filter is meaningless to a reader who cannot see how a type code
becomes a role group, and nothing on the site explained it. This builds that
page.

GENERATED, NOT WRITTEN. The page is derived from data/in/config/roles.json
and from the assignment semantics in backend/util/roles.py. A hand-written
copy would drift from the pipeline the first time the taxonomy changed, and
the drift would be invisible — which is exactly the failure this file
exists to prevent. test/backend/test_roles_page.py checks render() and
render_json() against the live taxonomy on every run, so a roles.json edit
that changes their output fails the suite immediately, with no separate
regeneration step to forget.

`groups` is already documented in roles.json as the single source of truth
for the published UI group table, and export.py's role_catalog builder reads
it via taxonomy()['groups']. This reads the same place. There is no third
copy.

Regenerate with:
    ./run gen roles
"""
from __future__ import annotations

import html
import json

from backend.util.paths import frontend_config_dir, lookup_dir
from backend.util.roles import EXCLUDED_ROLES, ROTARY, taxonomy
from backend.util.log import log

# The three synthetic buckets. They are not in roles.json's `roles` map —
# they exist only as labels for aircraft that never resolved to a designator
# with roles. Kept here with the exact semantics roles_string() implements.
UNKNOWN_BUCKETS = [
    ("typecode_unknown",
     "The aircraft broadcast no type code at all, so there was nothing to "
     "look up."),
    ("type_unknown",
     "A type code was broadcast but could not be resolved to a known "
     "aircraft type."),
    ("role_unknown",
     "The type resolved fine, but its designator carries no roles in the "
     "taxonomy — so the aircraft is known and its job is not."),
]

# Fallback only: common.texts.json's footer.contact_url is the single
# source of truth (shared with the frontend's ContentPanel.svelte via
# getFocusConfig()). Used if that key is missing so this generator never
# hard-fails on a stale/incomplete config.
_CONTACT_URL_FALLBACK = (
    "https://cryptpad.fr/form/#/2/form/view/"
    "9rqPIEkeCMj6YcADThr2VIzaRop7Neg7+Zco+6yftKM/embed/"
)


def _contact_url() -> str:
    try:
        with (frontend_config_dir() / "common.texts.json").open(encoding="utf-8") as f:
            texts = json.load(f)
        url = (texts.get("footer") or {}).get("contact_url")
        if url:
            return url
    except (OSError, json.JSONDecodeError):
        pass
    return _CONTACT_URL_FALLBACK


CONTACT_URL = _contact_url()

# The same taxonomy is also served as data for the in-app roles view.
# The standalone HTML page stays as a no-JS fallback; this is what the
# reader tab renders, because that view interleaves LIVE aircraft counts
# and so cannot use a pre-rendered HTML blob. Both are written under
# lookup_dir() (data/build/lookup/) by main() below; export.py copies them
# into data/out/ alongside the frontend build.


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _role_index(tax: dict) -> dict[str, str]:
    """role slug -> group slug, from `groups` (the source of truth)."""
    out: dict[str, str] = {}
    for group in tax["groups"]:
        for role in group["roles"]:
            out[role] = group["slug"]
    return out


def _designators_by_role(tax: dict) -> dict[str, list[tuple[str, dict]]]:
    out: dict[str, list[tuple[str, dict]]] = {}
    for tc, d in sorted(tax["designators"].items()):
        for role in d.get("roles") or []:
            out.setdefault(role, []).append((tc, d))
    return out


def _render_group(group: dict, roles: dict, by_role: dict) -> list[str]:
    parts: list[str] = []
    default_on = "on by default" if group.get("on") else "off by default"
    parts.append(f'<section class="role-group" id="group-{_e(group["slug"])}">')
    parts.append(
        f'<h3>{_e(group["label"])} '
        f'<span class="group-flag">({_e(default_on)})</span></h3>')

    for role_slug in group["roles"]:
        role = roles.get(role_slug)
        if role is None:
            # A group member with no entry in `roles` is one of the three
            # synthetic buckets; those are documented in their own section.
            bucket = dict(UNKNOWN_BUCKETS).get(role_slug)
            if bucket:
                parts.append(
                    f'<div class="role"><h4>{_e(role_slug)} '
                    f'<span class="synthetic">synthetic bucket</span></h4>'
                    f'<p class="role-desc">{_e(bucket)}</p></div>')
            continue

        parts.append(f'<div class="role" id="role-{_e(role_slug)}">')
        parts.append(f'<h4>{_e(role["label"])} '
                     f'<code>{_e(role_slug)}</code></h4>')
        if role.get("desc"):
            parts.append(f'<p class="role-desc">{_e(role["desc"])}</p>')

        entries = by_role.get(role_slug, [])
        if entries:
            parts.append('<table class="designators">')
            parts.append("<thead><tr><th>Type code</th><th>Aircraft</th>"
                         "<th>Also mapped to</th></tr></thead><tbody>")
            for tc, d in entries:
                others = [r for r in (d.get("roles") or []) if r != role_slug]
                other_txt = ", ".join(_e(o) for o in others) if others else "&mdash;"
                variant = ""
                if d.get("desc_roles"):
                    variant = (' <span class="has-variants" '
                               'title="Narrowed further by description">'
                               "variant rules</span>")
                parts.append(
                    f"<tr><td><code>{_e(tc)}</code>{variant}</td>"
                    f'<td>{_e(d.get("label", ""))}</td>'
                    f'<td class="other-roles">{other_txt}</td></tr>')
            parts.append("</tbody></table>")
        else:
            parts.append('<p class="no-types">No type codes map to this '
                         "role in the current taxonomy.</p>")
        parts.append("</div>")

    parts.append("</section>")
    return parts


def _render_variants(tax: dict) -> list[str]:
    """Designators whose roles are narrowed by the description string.

    ORDER IS SEMANTIC: roles_of_desc() matches patterns as substrings against
    an upper-cased description and takes the FIRST match, so the list must be
    shown in its taxonomy order, not sorted.
    """
    parts = [
        '<h2 id="variants">Variant narrowing</h2>',
        "<p>Some type codes cover a family of aircraft doing quite different "
        "jobs &mdash; a C-130 airframe may be a tanker, a gunship or a "
        "search-and-rescue platform. Where the aircraft&rsquo;s description "
        "string identifies the variant, it narrows the roles to just those "
        "listed below, replacing the designator&rsquo;s full set.</p>",
        "<p class='order-note'><strong>Order matters.</strong> Patterns are "
        "matched as substrings against the upper-cased description and the "
        "<em>first</em> match wins, so these are listed in taxonomy order, "
        "not alphabetically. If no pattern matches, the designator&rsquo;s "
        "full role set applies.</p>",
    ]
    any_rows = False
    parts.append('<table class="variants">')
    parts.append("<thead><tr><th>Type code</th><th>If description "
                 "contains</th><th>Roles become</th></tr></thead><tbody>")
    for tc, d in sorted(tax["designators"].items()):
        rules = d.get("desc_roles")
        if not rules:
            continue
        any_rows = True
        for i, (pattern, roles) in enumerate(rules):
            first = f'<code>{_e(tc)}</code>' if i == 0 else ""
            parts.append(
                f'<tr><td>{first}</td><td><code>{_e(pattern)}</code></td>'
                f'<td>{_e(", ".join(roles))}</td></tr>')
    parts.append("</tbody></table>")
    if not any_rows:
        parts = parts[:1] + ["<p>No designator uses variant narrowing.</p>"]
    return parts


def render(tax: dict | None = None) -> str:
    tax = tax or taxonomy()
    roles = tax["roles"]
    groups = tax["groups"]
    by_role = _designators_by_role(tax)
    role_to_group = _role_index(tax)

    n_designators = len(tax["designators"])
    n_roles = len(roles)

    p: list[str] = []
    p.append("<!DOCTYPE html>")
    p.append('<html lang="en">')
    p.append("<head>")
    p.append('<meta charset="utf-8">')
    p.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    p.append("<title>Aircraft roles &mdash; Flyover Country</title>")
    p.append('<link rel="icon" type="image/png" href="logo.png">')
    p.append('<link rel="stylesheet" href="roles.css">')
    p.append("</head>")
    p.append("<body>")
    p.append('<main class="roles-page">')

    p.append("<h1>How an aircraft becomes a role group</h1>")
    p.append(
        "<p class='lead'>The role filter groups aircraft by what they are "
        "for. This page shows exactly how that mapping is made, for every "
        f"one of the {n_designators} type codes and {n_roles} roles in the "
        "taxonomy. It is generated from the same configuration file the "
        "data pipeline reads, so it cannot describe anything the pipeline "
        "does not do.</p>")

    # ---- The disclaimer. Prominent, not buried. ----
    p.append('<div class="disclaimer" role="note">')
    p.append("<h2>Please correct this</h2>")
    p.append(
        "<p><strong>This taxonomy was assembled with AI assistance, and the "
        "authors are very uncertain about it.</strong> Role assignments for "
        "military aircraft are a matter of doctrine, variant and operator, "
        "and a single type code frequently covers airframes doing quite "
        "different jobs. We expect errors here.</p>")
    p.append(
        "<p>Corrections from subject-matter experts are actively wanted &mdash; "
        "ideally with a reference to public documentation we can cite, so the "
        "correction can be verified rather than simply believed. "
        f'<a href="{_e(CONTACT_URL)}" target="_blank" rel="noopener">'
        "Send a correction</a>.</p>")
    p.append("</div>")

    # ---- How the assignment actually works ----
    p.append('<h2 id="how">How the assignment works</h2>')
    p.append("<ol class='how-list'>")
    p.append(
        "<li>The aircraft&rsquo;s broadcast type code is stripped to letters "
        "and digits and upper-cased.</li>")
    p.append(
        "<li>That code is looked up in the designator table. <strong>There is "
        "no fallback and no guessing</strong>: a code that is not in the "
        "table yields no roles at all, rather than a default.</li>")
    p.append(
        "<li>If the designator has variant rules and the aircraft&rsquo;s "
        "description matches one, the roles are narrowed to that rule&rsquo;s "
        "list (see <a href='#variants'>Variant narrowing</a>).</li>")
    p.append(
        "<li>An aircraft matches a filter when its roles intersect the "
        "selected ones. Aircraft with several roles therefore appear under "
        "several groups &mdash; the roles are what the airframe "
        "<em>could</em> be doing, not a claim about a particular flight.</li>")
    p.append("</ol>")

    p.append(
        "<p class='caveat'><strong>What this is not.</strong> A role is a "
        "property of the aircraft type, not an observation of the mission. "
        "Nothing here asserts what any individual flight was carrying or "
        "doing.</p>")

    # ---- Excluded from the dataset entirely ----
    #
    # These roles exist in roles.json but belong to no group, precisely
    # because aircraft carrying them never reach the UI. They still get
    # named here with their labels and their type codes: a reader looking
    # for helicopters deserves to see "Rotary lift" and the airframes it
    # covers, not just a bare slug.
    p.append('<h2 id="excluded">Excluded from the data entirely</h2>')
    p.append(
        "<p>Aircraft whose type could fill any of the roles below are dropped "
        "from the pipeline before anything else happens, so they appear in no "
        "map, count or export. The exclusion is deliberately conservative: if "
        "<em>any</em> role the type could fill is in this set, the aircraft "
        "is excluded.</p>"
        "<p>If you are looking for helicopters and finding none, this is "
        "why &mdash; not an absence in the data.</p>")
    p.append('<table class="excluded-roles">')
    p.append("<thead><tr><th>Role</th><th>Why excluded</th>"
             "<th>Type codes</th></tr></thead><tbody>")
    for role_slug in sorted(EXCLUDED_ROLES):
        role = roles.get(role_slug, {})
        label = role.get("label", role_slug)
        why = "Rotary-wing" if role_slug in ROTARY else "Not an aircraft"
        tcs = [tc for tc, _ in by_role.get(role_slug, [])]
        tc_txt = (", ".join(f"<code>{_e(t)}</code>" for t in tcs)
                  if tcs else "&mdash;")
        p.append(
            f'<tr><td>{_e(label)} <code>{_e(role_slug)}</code></td>'
            f"<td>{_e(why)}</td><td>{tc_txt}</td></tr>")
    p.append("</tbody></table>")

    # Any remaining role that is in neither a group nor the exclusion set
    # would be invisible on this page while still being live in the
    # pipeline — surface it rather than letting it hide.
    orphans = sorted(set(roles) - set(role_to_group) - set(EXCLUDED_ROLES))
    if orphans:
        p.append('<p class="caveat"><strong>Ungrouped roles.</strong> These '
                 "are defined in the taxonomy but belong to no filter group "
                 "and are not excluded, so they can be assigned but never "
                 "filtered on: "
                 + ", ".join(f"<code>{_e(o)}</code>" for o in orphans)
                 + ".</p>")

    # ---- Unknown buckets ----
    p.append('<h2 id="unknown">When the role is unknown</h2>')
    p.append("<p>Three synthetic buckets stand in when the lookup does not "
             "produce roles. They are not roles in the taxonomy; they exist "
             "so that an unresolved aircraft is visibly unresolved rather "
             "than silently dropped or lumped in with a real category.</p>")
    p.append("<dl class='buckets'>")
    for slug, meaning in UNKNOWN_BUCKETS:
        p.append(f"<dt><code>{_e(slug)}</code></dt><dd>{_e(meaning)}</dd>")
    p.append("</dl>")

    # ---- Group index ----
    p.append('<h2 id="groups">The groups</h2>')
    p.append('<ul class="group-index">')
    for g in groups:
        n = len([r for r in g["roles"] if r in roles])
        p.append(
            f'<li><a href="#group-{_e(g["slug"])}">{_e(g["label"])}</a> '
            f'<span class="count">{n} role{"s" if n != 1 else ""}</span></li>')
    p.append("</ul>")

    for g in groups:
        p.extend(_render_group(g, roles, by_role))

    p.extend(_render_variants(tax))

    # ---- Full designator index ----
    p.append('<h2 id="index">Every type code</h2>')
    p.append('<table class="all-designators">')
    p.append("<thead><tr><th>Type code</th><th>Aircraft</th>"
             "<th>Roles</th><th>Groups</th></tr></thead><tbody>")
    for tc, d in sorted(tax["designators"].items()):
        rs = d.get("roles") or []
        gs = sorted({role_to_group.get(r, "?") for r in rs})
        p.append(
            f"<tr><td><code>{_e(tc)}</code></td>"
            f'<td>{_e(d.get("label", ""))}</td>'
            f'<td>{_e(", ".join(rs)) or "&mdash;"}</td>'
            f'<td>{_e(", ".join(gs)) or "&mdash;"}</td></tr>')
    p.append("</tbody></table>")

    p.append('<p class="regen-note">This page is generated from '
             "<code>data/in/config/roles.json</code> by "
             "<code>backend/pipeline/gen/roles_page.py</code>. A change to "
             "the taxonomy that is not reflected here fails the test suite.</p>")

    p.append("</main>")
    p.append("</body>")
    p.append("</html>")
    return "\n".join(p) + "\n"


def render_json(tax: dict | None = None) -> dict:
    """The taxonomy as structured data for the in-app view.

    Deliberately the SAME derivation as render() above — groups from
    `groups`, roles from `roles`, designators with their ordered desc_roles
    — so the page and the tab cannot disagree about what the pipeline does.
    Order is preserved everywhere it is semantic (group order, and
    desc_roles' first-match order).
    """
    tax = tax or taxonomy()
    roles = tax["roles"]
    by_role = _designators_by_role(tax)
    role_to_group = _role_index(tax)

    out: dict = {
        "groups": [],
        "unknown_buckets": [{"slug": s, "meaning": m} for s, m in UNKNOWN_BUCKETS],
        "excluded": [],
        "designators": [],
    }

    for group in tax["groups"]:
        g = {"slug": group["slug"], "label": group["label"],
             "default_on": bool(group.get("on")), "roles": []}
        for role_slug in group["roles"]:
            role = roles.get(role_slug)
            if role is None:
                continue          # synthetic bucket, described separately
            g["roles"].append({
                "slug": role_slug,
                "label": role["label"],
                "desc": role.get("desc", ""),
                "codes": [tc for tc, _ in by_role.get(role_slug, [])],
            })
        out["groups"].append(g)

    for role_slug in sorted(EXCLUDED_ROLES):
        role = roles.get(role_slug, {})
        out["excluded"].append({
            "slug": role_slug,
            "label": role.get("label", role_slug),
            "why": "Rotary-wing" if role_slug in ROTARY else "Not an aircraft",
            "codes": [tc for tc, _ in by_role.get(role_slug, [])],
        })

    for tc, d in sorted(tax["designators"].items()):
        rs = list(d.get("roles") or [])
        out["designators"].append({
            "code": tc,
            "label": d.get("label", ""),
            "roles": rs,
            "groups": sorted({role_to_group[r] for r in rs if r in role_to_group}),
            # ORDER IS SEMANTIC: roles_of_desc takes the FIRST match.
            "variants": [{"pattern": pat, "roles": list(vr)}
                         for pat, vr in (d.get("desc_roles") or [])],
        })

    return out


def main() -> None:
    # FLY_DATA_DIR-dependent, so resolved here rather than at import time —
    # a module-level call would freeze in whatever value was set (or unset)
    # when this module first got imported.
    out_dir = lookup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "roles.html"
    out_path.write_text(render(), encoding="utf-8")
    log(f"wrote {out_path}")

    json_path = out_dir / "roles.reference.json"
    json_path.write_text(
        json.dumps(render_json(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    log(f"wrote {json_path}")


if __name__ == "__main__":
    main()
