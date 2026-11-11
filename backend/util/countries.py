"""Shared two-letter country-code alias map.

OurAirports/OSM ISO country codes occasionally diverge from the codes the
rest of the pipeline (config, boundaries, published data) uses. This is
the single normalization point for that divergence; gen/airports.py,
gen/raster.py, and publish/export.py all import it rather than keeping
their own copies.
"""

COUNTRY_ALIASES = {"UK": "GB", "XK": "XK"}


def normalize_country(code: str) -> str:
    """Upper-cased, alias-resolved two-letter country code."""
    c = code.strip().upper()
    return COUNTRY_ALIASES.get(c, c)
