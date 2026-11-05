"""Behavioral tests for backend/pipeline/gen/mil.py — the standalone script
that builds mil_icao24.txt from Mictronics + basic-ac-db source dumps. It
runs under an `if __name__ == "__main__"` guard and takes its inputs from
argparse (sys.argv), so it is loaded the same way test_gridspec.py loads
gen/raster.py — via importlib.util.spec_from_file_location — and tests
monkeypatch sys.argv before calling mod.main()."""
import gzip
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


def _load_mil():
    src = Path("backend/pipeline/gen/mil.py")
    spec = importlib.util.spec_from_file_location("gen_mil", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_mictronics(path, entries):
    """entries: dict icao24 -> f-flag string (or None to omit the f field)."""
    db = {}
    for icao24, f in entries.items():
        rec = {"r": "", "t": ""}
        if f is not None:
            rec["f"] = f
        db[icao24] = rec
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("aircrafts.json", json.dumps(db))


def _write_basic_ac(path, rows):
    """rows: list of dicts with icao/mil keys, one JSON object per line."""
    with gzip.open(path, "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


# --- _in_scope boundaries --------------------------------------------------

def test_in_scope_boundaries():
    mod = _load_mil()
    assert mod._in_scope("a00000")     # bottom of the broad US hex range
    assert mod._in_scope("adf7bf")     # one below the mil sub-range
    assert not mod._in_scope("adf7c0")  # mil sub-range lower bound: already covered by line 1
    assert not mod._in_scope("afffff")  # mil sub-range upper bound
    assert not mod._in_scope("b00000")  # outside the broad US range entirely
    assert not mod._in_scope("9fffff")  # below the broad US range entirely


# --- main() over synthetic fixtures ----------------------------------------

def test_main_merges_sources_with_provenance(tmp_path, monkeypatch):
    mod = _load_mil()

    mictronics_zip = tmp_path / "indexedDB.zip"
    _write_mictronics(mictronics_zip, {
        "a11cf8": "1",          # military per mictronics only
        "a33333": "0",          # explicitly non-military: excluded
        "a44444": "10",         # military (f starts with "1"), also in basic-ac below
    })

    basic_ac_gz = tmp_path / "basic-ac-db.json.gz"
    _write_basic_ac(basic_ac_gz, [
        {"icao": "a44444", "mil": True},   # overlap with mictronics
        {"icao": "a55555", "mil": True},   # basic-ac-db only
        {"icao": "a66666", "mil": False},  # explicitly non-military: excluded
    ])

    out = tmp_path / "out" / "mil_icao24.txt"
    argv = ["mil.py", str(out),
            "--mictronics", str(mictronics_zip),
            "--basic-ac", str(basic_ac_gz)]
    monkeypatch.setattr("sys.argv", argv)
    mod.main()

    lines = out.read_text().splitlines()
    assert lines[0] == "adf7c0-afffff, US military hex prefixes"

    body = dict(line.split(", ", 1) for line in lines[1:])
    assert set(body) == {"a11cf8", "a44444", "a55555"}
    assert body["a11cf8"] == "mictronics military flag"
    assert body["a55555"] == "basic-ac-db mil=true"
    # union of both sources' provenance, "; "-joined
    assert body["a44444"] == "mictronics military flag; basic-ac-db mil=true"


def test_main_missing_source_warns_and_continues(tmp_path, monkeypatch, capsys):
    mod = _load_mil()

    basic_ac_gz = tmp_path / "basic-ac-db.json.gz"
    _write_basic_ac(basic_ac_gz, [{"icao": "a55555", "mil": True}])

    missing_mictronics = tmp_path / "does-not-exist.zip"
    out = tmp_path / "out" / "mil_icao24.txt"
    argv = ["mil.py", str(out),
            "--mictronics", str(missing_mictronics),
            "--basic-ac", str(basic_ac_gz)]
    monkeypatch.setattr("sys.argv", argv)
    mod.main()

    err = capsys.readouterr().err
    assert "mictronics source missing" in err

    lines = out.read_text().splitlines()
    assert lines[0] == "adf7c0-afffff, US military hex prefixes"
    assert lines[1] == "a55555, basic-ac-db mil=true"


def test_main_corrupt_zip_warns_and_skips(tmp_path, monkeypatch, capsys):
    """A corrupt/truncated mictronics zip must degrade like an absent
    source (warn, keep going) rather than raise — see the try/except
    around scan() in main()."""
    mod = _load_mil()

    corrupt_zip = tmp_path / "indexedDB.zip"
    corrupt_zip.write_bytes(b"not actually a zip file")

    basic_ac_gz = tmp_path / "basic-ac-db.json.gz"
    _write_basic_ac(basic_ac_gz, [{"icao": "a55555", "mil": True}])

    out = tmp_path / "out" / "mil_icao24.txt"
    argv = ["mil.py", str(out),
            "--mictronics", str(corrupt_zip),
            "--basic-ac", str(basic_ac_gz)]
    monkeypatch.setattr("sys.argv", argv)
    mod.main()   # must not raise

    err = capsys.readouterr().err
    assert "mictronics source unreadable" in err

    lines = out.read_text().splitlines()
    assert lines[0] == "adf7c0-afffff, US military hex prefixes"
    assert lines[1] == "a55555, basic-ac-db mil=true"
