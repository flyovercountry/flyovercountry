"""Download and filter raw ADS-B data from adsb.lol globe_history.

Downloads daily tarballs from GitHub releases, extracts trace_full_*.json.gz
per aircraft, filters to the US-military identity test (backend.util.mil
.is_relevant: the ADF7C0-AFFFFF military sub-range plus DB-evidenced
singles elsewhere in the A00000-AFFFFF US block), downsamples traces, and
writes parquet per date.

Idempotent and resumable: completed dates are marked with .done files
in data/build/ingest/ingest_state/. Safe to re-run with wider date ranges.

Output per date (data/build/ingest/date=YYYY-MM-DD/):
  aircraft.parquet    one row per aircraft seen that day
  trace.parquet       downsampled trace points (interval set by
                       --sample-sec at the CLI, default 10s; 0 = full
                       fidelity); manifest.json's sample_sec records the
                       value actually used for a given date
  manifest.json       row counts and metadata
"""
from __future__ import annotations

import gc
import gzip
import io
import json
import random
import re
import shutil
import tarfile
import time
from datetime import date as date_cls, timedelta, datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from backend.util.mil import is_relevant as _is_mil_relevant
from backend.util.paths import raw_dir
from backend.util import dimensions as _dimensions
from backend.util.log import log

try:
    import orjson as _orjson
    def _json_loads(b):
        return _orjson.loads(b)
except ImportError:
    def _json_loads(b):
        return json.loads(b)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.10 Safari/605.1.1"
RELEASES_CACHE_SEC = 6 * 3600
MIN_FREE_GB = 10.0
PROGRESS_SEC = 10.0
ERR_LOG_LIMIT = 50  # per-member parse errors logged individually before summarizing

_PROD_LINK_RE = re.compile(
    r"\[planes-readsb-(prod-0\w*)\s*\((\d+)\s*([MG])i?B\)\]"
    r"\(https://github\.com/adsblol/globe_history_(\d{4})/releases/tag/"
    r"v(\d{4}\.\d{2}\.\d{2})-planes-readsb-\1#assets\)"
)

PT_T, PT_LAT, PT_LON, PT_ALT = 0, 1, 2, 3
PT_GS, PT_TRK, PT_VR = 4, 5, 7  # index 6 ("flags") is unused, never read
PT_AUX, PT_SRC = 8, 9


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    log(f"{ts} {msg}")


def _fmt_dur(secs: float) -> str:
    secs = max(0, int(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


class _Eta:
    """Cross-day ETA bookkeeping for ingest progress lines.

    Per day: day_t0 marks the start of the download; the processing phase reuses
    it so a whole-day estimate spans download + computation. ETA_all projects
    remaining days from a blend of the current day's estimate (70%) and the
    average of completed days (30%)."""

    def __init__(self, n_days: int):
        self.n_days = n_days
        self.prior_day_secs: list[float] = []
        self.day_index = 0
        self.day_t0 = 0.0
        self.dl_total = 0       # bytes — this day's expected download size
        self.proc_total = 0     # aircraft to process this day
        self.proc_t0 = 0.0

    def start_day(self, dl_total_bytes: int):
        self.day_index += 1
        self.day_t0 = time.time()
        self.dl_total = dl_total_bytes
        self.proc_total = 0
        self.proc_t0 = 0.0

    def download_eta(self, day_done_bytes: int) -> str:
        elapsed = time.time() - self.day_t0
        rate = day_done_bytes / elapsed if elapsed > 0 else 0
        remaining = max(0, self.dl_total - day_done_bytes)
        return _fmt_dur(remaining / rate) if rate > 0 else "?"

    def start_proc(self, total: int):
        self.proc_total = total
        self.proc_t0 = time.time()

    def proc_eta(self, processed: int) -> tuple[str, str]:
        """(ETA_day, ETA_all) for the processing phase."""
        now = time.time()
        elapsed = now - self.proc_t0
        rate = processed / elapsed if elapsed > 0 else 0
        remaining = max(0, self.proc_total - processed)
        eta_day = remaining / rate if rate > 0 else 0
        cur_day_est = (now - self.day_t0) + eta_day   # download + computation
        if self.prior_day_secs:
            prior_avg = sum(self.prior_day_secs) / len(self.prior_day_secs)
            per_day = 0.7 * cur_day_est + 0.3 * prior_avg
        else:
            per_day = cur_day_est
        days_after = max(0, self.n_days - self.day_index)
        eta_all = eta_day + days_after * per_day
        return _fmt_dur(eta_day), _fmt_dur(eta_all)

    def finish_day(self):
        self.prior_day_secs.append(time.time() - self.day_t0)




# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _date_range(start: str, end: str) -> list[str]:
    d = date_cls.fromisoformat(start)
    d_end = date_cls.fromisoformat(end)
    out = []
    while d <= d_end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# State — idempotent resume
# ---------------------------------------------------------------------------

def _state_dir() -> Path:
    d = raw_dir() / "ingest_state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_done(date: str) -> bool:
    return (_state_dir() / f"{date}.done").exists()


def _mark_done(date: str, payload: dict):
    with open(_state_dir() / f"{date}.done", "w") as f:
        json.dump(payload, f)
    err = _state_dir() / f"{date}.err"
    if err.exists():
        err.unlink()


def _mark_err(date: str, msg: str):
    with open(_state_dir() / f"{date}.err", "w") as f:
        f.write(msg + "\n")


# ---------------------------------------------------------------------------
# RELEASES.md — index of available dates
# ---------------------------------------------------------------------------

def _cache_dir() -> Path:
    d = raw_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fetch_url(url: str) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _url_exists(url: str) -> bool:
    import urllib.request
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception as e:
        _log(f"  WARN: _url_exists({url}): {type(e).__name__}: {e}")
        return False


def _fetch_releases(year: int) -> str:
    cache = _cache_dir() / f"releases_{year}.md"
    if cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < RELEASES_CACHE_SEC:
            return cache.read_text()
    url = (f"https://raw.githubusercontent.com/adsblol/"
           f"globe_history_{year}/main/RELEASES.md")
    _log(f"fetching {url}")
    try:
        content = _fetch_url(url).decode("utf-8", errors="replace")
    except Exception as e:
        _log(f"  WARN: failed to fetch {url}: {e}")
        if cache.exists():
            return cache.read_text()
        return ""
    cache.write_text(content)
    return content


def _available_dates(years: set[int]) -> dict[str, tuple[str, int, int]]:
    """Return {date: (variant_slug, repo_year, size_bytes)} for best prod release."""
    best: dict = {}
    for year in sorted(years):
        md = _fetch_releases(year)
        for m in _PROD_LINK_RE.finditer(md):
            short_slug, size_s, unit, repo_year_s, dotdate = (
                m.group(1), m.group(2), m.group(3), m.group(4), m.group(5),
            )
            size = int(size_s) * (1 << 30 if unit == "G" else 1 << 20)
            repo_year = int(repo_year_s)
            iso = dotdate.replace(".", "-")
            slug = f"planes-readsb-{short_slug}"
            cur = best.get(iso)
            if cur is None or size > cur[0]:
                best[iso] = (size, slug, repo_year)
            elif size == cur[0] and slug == "planes-readsb-prod-0":
                best[iso] = (size, slug, repo_year)
    return {d: (slug, ry, size) for d, (size, slug, ry) in best.items()}


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _free_gb(path: Path) -> float:
    u = shutil.disk_usage(path)
    return u.free / (1 << 30)


def _download_one(url: str, dest: Path, label: str = "",
                  eta=None, day_done_before: int = 0):
    import urllib.request
    tmp = dest.with_suffix(dest.suffix + ".partial")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        expected = int(resp.headers.get("Content-Length", "0"))
        got = 0
        t0 = last_report = time.time()
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                now = time.time()
                if now - last_report >= PROGRESS_SEC:
                    rate = got / max(now - t0, 1e-3) / (1 << 20)
                    extra = (f" ETA_day: {eta.download_eta(day_done_before + got)}"
                             if eta is not None else "")
                    if expected:
                        pct = 100 * got / expected
                        _log(f"  {label}: {got >> 20}/{expected >> 20} MB "
                             f"({pct:.0f}%) @ {rate:.1f} MB/s{extra}")
                    else:
                        _log(f"  {label}: {got >> 20} MB @ {rate:.1f} MB/s{extra}")
                    last_report = now
        if expected and got != expected:
            raise IOError(f"size mismatch: got {got}, expected {expected}")
    tmp.rename(dest)


def _resolve_assets(year: int, tag: str) -> list[str]:
    base = (f"https://github.com/adsblol/globe_history_{year}"
            f"/releases/download/{tag}")
    split = [f"{tag}.tar.aa", f"{tag}.tar.ab"]
    if _url_exists(f"{base}/{split[0]}"):
        return split
    single = f"{tag}.tar"
    if _url_exists(f"{base}/{single}"):
        return [single]
    raise RuntimeError(f"no tar assets found for {tag}")


def _concat_halves(halves: list[Path], out: Path):
    total = sum(h.stat().st_size for h in halves)
    written = 0
    t0 = last_report = time.time()
    with open(out, "wb") as outf:
        for h in halves:
            with open(h, "rb") as inp:
                while True:
                    buf = inp.read(1 << 22)
                    if not buf:
                        break
                    outf.write(buf)
                    written += len(buf)
                    now = time.time()
                    if now - last_report >= PROGRESS_SEC:
                        pct = 100 * written / total if total else 0
                        _log(f"  concat: {written >> 30:.2f}/"
                             f"{total >> 30:.2f} GB ({pct:.0f}%)")
                        last_report = now


def _download_date(
    date: str, variant: str, repo_year: int, eta=None,
) -> Path:
    """Download tarball for one date. Returns path to combined tar."""
    dl_dir = raw_dir() / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)

    tag = f"v{date.replace('-', '.')}-{variant}"
    combined = dl_dir / f"{tag}.tar"
    if combined.exists() and combined.stat().st_size > 0:
        _log(f"  reusing {combined.name} "
             f"({combined.stat().st_size >> 20} MB)")
        return combined

    if _free_gb(dl_dir) < MIN_FREE_GB:
        raise RuntimeError(
            f"insufficient disk ({_free_gb(dl_dir):.1f} GB free)",
        )

    base_url = (f"https://github.com/adsblol/globe_history_{repo_year}"
                f"/releases/download/{tag}")
    filenames = _resolve_assets(repo_year, tag)
    paths = []
    day_done = 0
    for fname in filenames:
        dest = dl_dir / fname
        if dest.exists() and dest.stat().st_size > 0:
            _log(f"  {fname}: already have {dest.stat().st_size >> 20} MB")
            day_done += dest.stat().st_size
            paths.append(dest)
            continue
        backoff = 10
        for attempt in range(1, 6):
            try:
                t0 = time.time()
                _download_one(
                    f"{base_url}/{fname}", dest, label=fname,
                    eta=eta, day_done_before=day_done,
                )
                _log(f"  {fname}: {dest.stat().st_size >> 20} MB "
                     f"in {time.time() - t0:.0f}s")
                break
            except Exception as e:
                _log(f"  {fname} attempt {attempt}/5: {e}")
                if attempt == 5:
                    raise
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)
        day_done += dest.stat().st_size
        paths.append(dest)

    if len(paths) == 1 and paths[0] == combined:
        return combined
    if len(paths) == 1:
        paths[0].rename(combined)
        return combined

    _log(f"  concat {len(paths)} parts -> {combined.name}")
    _concat_halves(paths, combined)
    for h in paths:
        if h != combined and h.exists():
            h.unlink()
    return combined


# ---------------------------------------------------------------------------
# Trace parsing
# ---------------------------------------------------------------------------

def _pt_alt_ft(pt):
    v = pt[PT_ALT]
    if v is None or v == "ground":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _pt_on_ground(pt):
    return pt[PT_ALT] == "ground"


def _downsample(trace: list, interval_sec: int) -> list:
    out = []
    last_bucket = None
    for pt in trace:
        bucket = int(pt[PT_T] // interval_sec)
        if bucket != last_bucket:
            out.append(pt)
            last_bucket = bucket
    return out


# ---------------------------------------------------------------------------
# Per-aircraft processing
# ---------------------------------------------------------------------------

def _process_aircraft(
    d: dict, sample_sec: int, all_icaos: bool = False,
) -> tuple[dict, list[dict]] | None:
    """Process one trace_full JSON payload. Returns (aircraft_row, traces)."""
    icao = d.get("icao", "")
    if not icao or len(icao) < 6:
        return None

    if not all_icaos and not _is_mil_relevant(icao):
        return None

    trace = d.get("trace") or []
    if not trace:
        return None

    base_ts = d.get("timestamp")

    aircraft_row = {
        "icao24": icao,
        "type_code": d.get("t"),
        "desc": d.get("desc"),
        "registration": d.get("r"),
        "dbflags": d.get("dbFlags"),
    }

    ds = _downsample(trace, sample_sec) if sample_sec > 0 else trace
    trace_rows: list[dict] = []
    for pt in ds:
        aux = (pt[PT_AUX]
               if len(pt) > PT_AUX and isinstance(pt[PT_AUX], dict)
               else {})
        trace_rows.append({
            "icao24": icao,
            "ts_s": int(base_ts + pt[PT_T]),
            "lat": pt[PT_LAT],
            "lon": pt[PT_LON],
            "alt_ft": _pt_alt_ft(pt),
            "on_ground": _pt_on_ground(pt),
            "gs_kt": pt[PT_GS],
            "track_deg": pt[PT_TRK],
            "vert_rate_fpm": pt[PT_VR],
            "source": pt[PT_SRC] if len(pt) > PT_SRC else None,
            "callsign": (aux.get("flight") or "").strip() or None,
            "squawk": (str(aux.get("squawk"))
                       if aux.get("squawk") else None),
            "category": aux.get("category"),
        })

    return aircraft_row, trace_rows


# ---------------------------------------------------------------------------
# Tarball processing
# ---------------------------------------------------------------------------

TRACE_BATCH_SIZE = 500_000


def _flush_trace_batch(rows: list[dict], path: Path):
    if not rows:
        return
    df = pd.DataFrame(rows)
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, str(path), compression="zstd", compression_level=6)


_TRACE_HEX_RE = re.compile(r"trace_full_([0-9a-fA-F]{6})\.json")


def _hex_from_member(name: str) -> str | None:
    """Extract icao24 hex from a tar member filename like trace_full_XXXXXX.json.gz."""
    m = _TRACE_HEX_RE.search(name)
    return m.group(1).lower() if m else None


def _process_tarball(
    tar_path: Path, date: str, sample_sec: int, all_icaos: bool = False,
    eta=None,
) -> dict:
    out_dir = raw_dir() / f"date={date}"
    out_dir.mkdir(parents=True, exist_ok=True)
    batch_dir = out_dir / "_trace_batches"
    if batch_dir.exists():
        shutil.rmtree(batch_dir)
    batch_dir.mkdir()

    out_aircraft: list[dict] = []
    trace_buf: list[dict] = []
    kept = errored = 0
    line_counts: dict[str, int] = {}
    total_pts = 0
    batch_idx = 0

    # Open the tar seekably so aircraft can be processed in a random order (keeps
    # per-slice work even) and the total is known up front for the ETA.
    with tarfile.open(tar_path, mode="r:") as tf:
        scanned = skipped = 0
        work = []
        for member in tf:
            if not member.isfile() or "trace_full_" not in member.name:
                continue
            scanned += 1
            icao_from_name = _hex_from_member(member.name)
            if not all_icaos and icao_from_name and not _is_mil_relevant(icao_from_name):
                skipped += 1
                continue
            work.append(member)
            if scanned % 100000 == 0:
                _log(f"  indexing... {scanned} members")
        random.shuffle(work)
        total = len(work)
        if eta is not None:
            eta.start_proc(total)
        _log(f"  {scanned} members, {skipped} filtered — processing {total} "
             f"aircraft in random order")

        t0 = time.time()
        processed = 0
        for member in work:
            processed += 1
            fh = tf.extractfile(member)
            if fh is None:
                continue
            try:
                raw = fh.read()
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
                    payload = gz.read()
                d = _json_loads(payload)
                trace_len = len(d.get("trace") or [])
                icao = (d.get("icao") or "").strip().lower()
                if icao and trace_len:
                    line_counts[icao] = trace_len
                result = _process_aircraft(d, sample_sec, all_icaos)
                if result is not None:
                    aircraft_row, trace_rows = result
                    out_aircraft.append(aircraft_row)
                    trace_buf.extend(trace_rows)
                    kept += 1
                    if len(trace_buf) >= TRACE_BATCH_SIZE:
                        _flush_trace_batch(
                            trace_buf,
                            batch_dir / f"batch_{batch_idx:04d}.parquet",
                        )
                        total_pts += len(trace_buf)
                        trace_buf.clear()
                        batch_idx += 1
                        gc.collect()
            except Exception as e:
                errored += 1
                if errored <= ERR_LOG_LIMIT:
                    icao_err = _hex_from_member(member.name) or member.name
                    _log(f"  ERROR {icao_err}: {type(e).__name__}: {e}")
                elif errored == ERR_LOG_LIMIT + 1:
                    _log(f"  ERROR: suppressing further per-member error logs "
                         f"after {ERR_LOG_LIMIT} (final count in the done: line)")
            if processed % 500 == 0:
                elapsed = time.time() - t0
                pts_so_far = total_pts + len(trace_buf)
                extra = ""
                if eta is not None:
                    eta_day, eta_all = eta.proc_eta(processed)
                    extra = f" ETA_day: {eta_day} ETA_all: {eta_all}"
                _log(f"  {processed}/{total} "
                     f"({processed / max(elapsed, 1e-3):.0f}/s) "
                     f"kept={kept} aircraft={len(out_aircraft)} "
                     f"pts={pts_so_far}{extra}")

    if trace_buf:
        _flush_trace_batch(
            trace_buf, batch_dir / f"batch_{batch_idx:04d}.parquet",
        )
        total_pts += len(trace_buf)
        trace_buf.clear()
        gc.collect()

    elapsed = time.time() - t0
    _log(f"  done: {total} aircraft, {kept} kept, "
         f"{errored} errors, {elapsed:.0f}s")
    return {
        "aircraft": out_aircraft,
        "trace_batch_dir": batch_dir,
        "trace_count": total_pts,
        "line_counts": line_counts,
        "scanned": scanned,
        "skipped": skipped,
        "kept": kept,
        "errored": errored,
        "process_sec": elapsed,
    }


# ---------------------------------------------------------------------------
# Parquet output
# ---------------------------------------------------------------------------

def _write_parquet(rows: list[dict], path: Path):
    tmp = path.with_suffix(".parquet.tmp")
    if not rows:
        table = pa.table({"__empty__": pa.array([], type=pa.bool_())})
    else:
        df = pd.DataFrame(rows)
        table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, str(tmp), compression="zstd", compression_level=6)
    tmp.rename(path)
    return path.stat().st_size


def _merge_trace_batches(batch_dir: Path, dest: Path):
    batches = sorted(batch_dir.glob("batch_*.parquet"))
    if not batches:
        table = pa.table({"__empty__": pa.array([], type=pa.bool_())})
        pq.write_table(table, str(dest))
    else:
        raw_schema = pq.ParquetFile(str(batches[0])).schema_arrow
        columns = [f.name for f in raw_schema if f.name != "date"]
        schema = pa.schema([f for f in raw_schema if f.name != "date"])
        tmp = dest.with_suffix(".parquet.tmp")
        writer = pq.ParquetWriter(str(tmp), schema, compression="zstd",
                                  compression_level=6)
        for b in batches:
            pf = pq.ParquetFile(str(b))
            tbl = pf.read(columns=columns)
            tbl = tbl.cast(schema)
            writer.write_table(tbl)
            del pf, tbl
        writer.close()
        tmp.rename(dest)
    shutil.rmtree(batch_dir)
    return dest.stat().st_size


def _flush_day(results: dict, date: str, sample_sec: int) -> dict:
    out = raw_dir() / f"date={date}"
    out.mkdir(parents=True, exist_ok=True)
    sizes = {}
    sizes["aircraft.parquet"] = _write_parquet(
        results["aircraft"], out / "aircraft.parquet",
    )
    # Bbox-geometry provenance stamp for this date's output. Its only reader
    # was the now-removed segments.parquet backfill routine; kept as cheap,
    # harmless on-disk provenance even though nothing currently reads it.
    _write_stamp(out, _bbox_stamp_value())
    sizes["trace.parquet"] = _merge_trace_batches(
        results["trace_batch_dir"], out / "trace.parquet",
    )
    manifest = {
        "date": date,
        "source": "adsb.lol",
        "sample_sec": sample_sec,
        "aircraft": len(results["aircraft"]),
        "traces": results["trace_count"],
        "icao24s": len(set(
            r["icao24"] for r in results["aircraft"]
        )),
        "scanned": results["scanned"],
        "skipped": results["skipped"],
        "kept": results["kept"],
        "errored": results["errored"],
    }
    with open(out / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    line_counts = results.get("line_counts")
    if line_counts:
        with open(out / "line_counts.json", "w") as f:
            json.dump(line_counts, f, separators=(",", ":"))
    return manifest


# ---------------------------------------------------------------------------
# Merge leftover batches (recovery after interrupted ingest)
# ---------------------------------------------------------------------------

def run_merge_batches():
    raw = raw_dir()
    date_dirs = sorted(raw.glob("date=*"))
    merged = skipped = 0
    for dd in date_dirs:
        batch_dir = dd / "_trace_batches"
        trace_path = dd / "trace.parquet"
        if not batch_dir.exists():
            continue
        if trace_path.exists():
            _log(f"  {dd.name}: trace.parquet exists, skipping")
            skipped += 1
            continue
        n_batches = len(list(batch_dir.glob("batch_*.parquet")))
        _log(f"  {dd.name}: merging {n_batches} batches...")
        size = _merge_trace_batches(batch_dir, trace_path)
        date = dd.name.removeprefix("date=")
        # aircraft.parquet may be absent (interrupted run) or, on dates
        # ingested before segments.parquet was retired, sit next to a
        # (now-ignored) segments.parquet — tolerate either on-disk shape
        # and never touch segments.parquet.
        aircraft_path = dd / "aircraft.parquet"
        n_aircraft = (pq.read_metadata(str(aircraft_path)).num_rows
                      if aircraft_path.exists() else 0)
        n_traces = pq.read_metadata(str(trace_path)).num_rows
        _mark_done(date, {
            "date": date, "aircraft": n_aircraft, "traces": n_traces,
            "merged_from_batches": n_batches,
        })
        _log(f"  {dd.name}: {n_traces} trace points, {size >> 20} MB")
        merged += 1
    _log(f"merge-batches: {merged} merged, {skipped} skipped")


# ---------------------------------------------------------------------------
# Bbox-geometry provenance stamp, written per date in _flush_day so the bbox
# in effect for an ingest is recoverable later. Nothing reads it back.
# ---------------------------------------------------------------------------

_BBOX_STAMP = ".bboxes_stamp"


def _bbox_stamp_value() -> str:
    """Canonical, human-readable representation of bbox coordinates."""
    return json.dumps(
        [[b["lat_south"], b["lat_north"], b["lon_west"], b["lon_east"]]
         for b in _dimensions.bboxes()],
        separators=(",", ":"),
    )


def _write_stamp(date_dir: Path, value: str):
    (date_dir / _BBOX_STAMP).write_text(value + "\n")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_ingest(start: str, end: str, *, sample_sec: int = 0, all_icaos: bool = False):
    t0_total = time.time()
    target = _date_range(start, end)
    _log(f"ingest: {len(target)} days ({start} to {end})")

    target_years = {int(d[:4]) for d in target}
    current_year = date_cls.today().year
    years = {
        y for y in (target_years | {y + 1 for y in target_years})
        if y <= current_year
    }
    available = _available_dates(years)
    _log(f"ingest: {len(available)} dates in release index")



    to_do = []
    skipped = done = missing = 0
    for date in target:
        if _is_done(date):
            done += 1
            continue
        if date not in available:
            _log(f"  {date}: not in releases index")
            missing += 1
            continue
        to_do.append(date)

    _log(f"ingest: done={done} missing={missing} to_do={len(to_do)}")
    if not to_do:
        _log("ingest: nothing to do")
        return

    eta = _Eta(len(to_do))
    n_ok = n_err = 0
    for i, date in enumerate(to_do, 1):
        variant, repo_year, size_bytes = available[date]
        eta.start_day(size_bytes)
        _log(f"[{i}/{len(to_do)}] {date} ({variant}, {size_bytes >> 20} MB)")
        tar_path = None
        try:
            tar_path = _download_date(date, variant, repo_year, eta)
            results = _process_tarball(tar_path, date, sample_sec, all_icaos, eta)
            manifest = _flush_day(results, date, sample_sec)
            _mark_done(date, manifest)
            _log(f"  {date}: {manifest['aircraft']} aircraft, "
                 f"{manifest['traces']} trace points")
            n_ok += 1
            eta.finish_day()
            # delete the tarball only on success — a processing failure
            # keeps it so the retry reuses it (_download_date's existing
            # reuse branch) instead of re-downloading gigabytes
            if tar_path and tar_path.exists():
                tar_path.unlink()
        except Exception as e:
            _log(f"  {date}: ERROR — {type(e).__name__}: {e}")
            if tar_path and tar_path.exists():
                _log(f"  {date}: keeping {tar_path.name} for retry reuse")
            _mark_err(date, f"{type(e).__name__}: {e}")
            n_err += 1
        finally:
            gc.collect()

    elapsed = time.time() - t0_total
    _log(f"ingest: complete — ok={n_ok} err={n_err} "
         f"({elapsed / 60:.0f} min)")
