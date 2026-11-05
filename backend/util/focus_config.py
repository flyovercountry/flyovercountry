"""Merge the split focus-config files into one dict.

Country config lives in three git-tracked files in data/in/config/frontend/
(served directly by frontend/serve.mjs, so edits are live with no export
step; see backend/util/paths.py frontend_config_dir()):

  - countries.modes.json    flat per-country map + permissiveness_levels legend text
  - common.texts.json       global copy + per-country templated strings + hashtags
  - country.reference.json  per-country research (refusal_analysis + citations) and
                            hand-written prose (general/counterfactual)

Backend consumers (export, boundary gen, the published-number checkers) call
``load_focus_config()`` and receive one dict with a per-country ``reference``
key. The frontend does the equivalent merge in JS (frontend/src/data.js);
keep the two in sync.

Every country, including the display-only ones (Ukraine/Belarus/Russia), is
one entry in the flat ``countries`` map keyed by cc, carrying a
``permissiveness`` level (0-2, matched to a fill colour + legend text in
``permissiveness_levels``) and an ordinal ``interaction`` field (0-3), where
``whatif`` implies ``stats`` implies ``info`` with no exceptions. The two are
orthogonal: never derive one from the other (Switzerland/Austria are
permissiveness 0 but interaction 2; Saudi/Kuwait are permissiveness 2 but
interaction 1). Display-only is simply interaction 0.

``has_info``/``has_stats``/``has_whatif`` derive the three booleans from
``interaction`` so no call site does its own ``>=`` comparison;
``merge_focus_config`` also stamps them onto every merged entry as
``info``/``stats``/``whatif`` for consumers that read those keys.
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.util.paths import frontend_config_dir as _default_config_dir

_DEFAULT_DIR = _default_config_dir()  # data/in/config/frontend/

MODES_FILE = "countries.modes.json"
TEXTS_FILE = "common.texts.json"
REFERENCE_FILE = "country.reference.json"


def has_info(entry: dict) -> bool:
    """Research text + its table-of-contents entries are available."""
    return (entry.get("interaction") or 0) >= 1


def has_stats(entry: dict) -> bool:
    """The per-country stats table/figures render."""
    return (entry.get("interaction") or 0) >= 2


def has_whatif(entry: dict) -> bool:
    """The What-If mechanism is available."""
    return (entry.get("interaction") or 0) >= 3


def _read(directory: Path, name: str) -> dict:
    with (directory / name).open(encoding="utf-8") as f:
        return json.load(f)


def merge_focus_config(modes: dict, texts: dict, reference: dict) -> dict:
    """Reconstruct the monolithic focus-config dict from the three parts."""
    templates = texts.get("templates") or {}
    hl_intro = templates.get("headline_intro", "")
    hl_outro = templates.get("headline_outro", "")
    closing = templates.get("closing", "")

    out = {
        "title": texts.get("title"),
        "description": texts.get("description"),
        "message": texts.get("message"),
        "footer": texts.get("footer"),
        "info": texts.get("info"),
        "hashtags": texts.get("hashtags"),
        "permissiveness_levels": modes.get("permissiveness_levels", {}),
        "countries": {},
    }
    for cc, attrs in modes["countries"].items():
        entry = dict(attrs)  # permissiveness/interaction + name/.../headline (static)
        # or headline_name+headline_note.
        entry["info"] = has_info(entry)
        entry["stats"] = has_stats(entry)
        entry["whatif"] = has_whatif(entry)
        # Static countries carry a full `headline`; templated ones carry
        # headline_name + headline_note, rendered into the shared intro/outro
        # template. `headline` is the full text, `headline_intro` the stats
        # sentence alone (parity with data.js's headlineIntro).
        if not entry.get("headline") and entry.get("headline_note") is not None \
                and entry.get("headline_name"):
            name = entry["headline_name"]
            note = entry["headline_note"]
            outro_resolved = hl_outro.replace("{name}", name) if hl_outro else ""
            entry["headline_intro"] = hl_intro.replace("{name}", name)
            entry["headline"] = (
                entry["headline_intro"]
                + ((" " + note) if note else "")
                + (("\n\n" + outro_resolved) if outro_resolved else ""))
            entry["closing"] = closing
        elif entry.get("headline"):
            entry["headline_intro"] = entry["headline"]
        r = reference.get(cc) or {}
        for key, val in (r.get("prose") or {}).items():
            entry[key] = val  # general/counterfactual/footnote
        if r.get("refusal_analysis") is not None:
            entry["reference"] = {
                "refusal_analysis": r["refusal_analysis"],
                "citations": r.get("citations", {}),
            }
        out["countries"][cc] = entry
    return out


def load_focus_config(config_dir=None) -> dict:
    """Load + merge the three config files from ``config_dir`` (default data/in/config/frontend/)."""
    directory = Path(config_dir) if config_dir else _DEFAULT_DIR
    return merge_focus_config(
        _read(directory, MODES_FILE),
        _read(directory, TEXTS_FILE),
        _read(directory, REFERENCE_FILE),
    )
