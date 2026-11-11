"""gen — asset generators for data/build/lookup/ (mil, airports, raster,
boundaries, roles_page). Each module is a standalone script (argparse +
__main__), run by path via subprocess (see backend.cli); export also
imports gen.airports.US_MIL_DUAL_USE directly."""
