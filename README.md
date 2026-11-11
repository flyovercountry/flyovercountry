# Flyover Country

US military flights over Europe, mapped. A dockerized pipeline turns raw
ADS-B traces from adsb.lol into a static website: a map, per-country stats
and What-If counterfactuals. The ship artifact is one directory,
`data/out/`: the Vite build of `frontend/src/` plus the pipeline's exported
site data in `data/out/data/`. It is uploaded by hand to Cloudflare Pages;
nothing dynamic runs in production.

## What this project is

Aircraft broadcast their position, altitude and identity by radio (a system
called ADS-B), and volunteers around the world collect those broadcasts.
This project takes one community archive of them (adsb.lol), keeps only
aircraft identified as US military, and answers questions such as: which
countries' airspace did these aircraft cross, where did they land, and how
many flights would have been disrupted had a given country refused them
passage from a given day (the site's "What If?" mode)?

Everything is computed offline, ahead of time. The result is a folder of
static files with no server-side code, so the website can be hosted anywhere
that serves files.

The analysis can only see what aircraft transmit and what receivers hear.
Military aircraft sometimes switch their transponders off, and receiver
coverage is patchy in places, so every count on the site is a lower bound.
The methodology page explains this and the other limits in detail.

## How the pieces fit together

```
adsb.lol daily archives          reference lists (aircraft registries,
        |                        airports, country borders)
        v                                   |
  ./run ingest   --------------+            v
  download, keep US-military   |      ./run gen
  aircraft only, thin points   |      build lookup assets
        |                      |            |
        +----------+-----------+------------+
                   v
             ./run etl    eleven stages in four phases:
             preprocess -> timeline -> landing -> publish
                   |
                   v
        data/out/data/  (static JSON/GeoJSON)
                   +
        frontend/src/   (Svelte/Vite)  --- ./run serve --->  data/out/
                                                             (the site)
```

Where to read next:

- This file explains how to run and maintain the pipeline.
- The site's own Methodology page (source:
  `frontend/src/static/methodology.html`) explains what each stage does and
  why, in plain terms, and its Glossary defines the vocabulary. It is the
  best place to start if you want to understand the results rather than
  rebuild them.
- `data/in/external/README.md` explains where to download each source file.

## Requirements

Docker and docker compose on the host. Every command in this file is typed
in your own host shell; `./run` starts a container for everything,
including the test suites.

## Container users

No container runs as root except the WireGuard sidecar, which needs
`NET_ADMIN` to configure the `wg0` interface; its only mount is the
WireGuard config, read-only.

The other containers (`ingest`, `etl`, `frontend`, `playwright`) run as an
unprivileged user named `docker`, created at the invoking host user's
uid/gid: `./.rebuild` (sourced by `./run`) exports `DOCKER_UID`/`DOCKER_GID`
from `id -u`/`id -g`, `docker-compose.yml` passes them as build args
(default 1000), and each Dockerfile creates the user at those ids. The
containers write into bind mounts of this repo (`data/`, `frontend/`,
`test/`), so the ids must match the host owner or the tree accumulates
root-owned files. The rebuild hash tracks file inputs only: if your uid/gid
change, rebuild the images by hand.

`./run` also removes dead one-off containers (leftovers of interrupted
`docker compose run --rm`) on every invocation, matched by compose's
`oneoff` label.

## Quickstart

Run in order:

```
./run gen        # 1. build lookup assets (mil list, airports, raster, boundaries); minutes, one-time
./run ingest     # 2. download raw ADS-B data; hours; needs data/in/wg/wg0.conf
./run etl        # 3. run the pipeline, writing site data into data/out/data/; ~15 min for 50 days
./run serve      # 4. build the frontend into data/out/ and serve it at http://127.0.0.1:8150; runs until Ctrl-C
```

`./run <command>` is the single entry point. Every command takes
`-h`/`--help` and validates its arguments and preconditions before doing
anything. `./run all` runs steps 2-4 back to back; step 1 is a one-time
setup, and `./run gen mil` also runs automatically at the start of step 2.
`./run status` reports which stages are present and whether any output is
stale.

Before step 1, place the source files (table below). Before step 2, read
Network setup. After step 4, upload `data/out/` (Publish, below).

### Network setup

`ingest` is the only step that goes online. It pulls the adsb.lol daily
archives from GitHub, and GitHub logs the address of every download. So
that running this pipeline does not put your own address in those logs,
compose confines `ingest` to the network namespace of a WireGuard sidecar:
its default route and DNS go through `wg0`, and `ingest` refuses to start
outside that namespace. Put a standard WireGuard client config at
`data/in/wg/wg0.conf` (untracked); any commercial WireGuard provider or a
peer of your own works. Without a config the sidecar prints setup
instructions and exits.

If you do not mind GitHub logging your address, `./run ingest --direct`
runs that one invocation with no tunnel: the sidecar configures nothing
and needs no config. The flag must come first (`./run ingest --direct
downsample ...` works). It is not remembered: the sidecar is recreated in
the requested mode on every `./run ingest`, a direct sidecar is removed
when the command ends, and the ingest CLI itself has no way to opt out of
the sidecar. Both `run` and `ingest` print a warning in direct mode.

The maintenance sub-commands (`downsample`, `merge-batches`) do no
networking but share the service, so they need the sidecar healthy too.
The ingest CLI's own options (`--start/--end/--sample-sec/--all`) are not
reachable through `./run ingest`; invoke the container directly:
`docker compose run --rm ingest run ingest --start ... --end ...`.

## What `./run etl` runs

A few terms used below (the methodology Glossary has the full list):

- **Trace**: the time-ordered position reports (a "point" each) received
  from one aircraft.
- **Blackout**: a stretch longer than 15 minutes with no reports from an
  airborne aircraft, because it was out of receiver range or its transponder
  was off. The pipeline has to decide whether the aircraft landed inside it
  (the flight is "split" in two) or flew on (the flight is "joined").
- **Theater**: the Middle East region (plus a narrow Balkans test) used by
  the theater filter to pick out flights tied to that region.
- **Parquet**: the columnar file format used for every intermediate table
  between stages.

Eleven stages, always in this order. With no arguments every stage runs;
pass a phase or stage names to run a subset (`./run etl split flight`).

Phase `preprocess`:

1. `types`: resolve an ICAO type code, roles and relevance for every
   aircraft (non-US-military aircraft are dropped). Flags rotary-wing and
   non-aircraft types as `excluded`; every later stage filters them out.
2. `denoise`: flag position reports that disagree with the aircraft's own
   local track, or that imply impossible speed to every neighbour, into
   `exclusions.parquet`; every later raw-trace read drops them.

Phase `timeline`:

3. `partition`: per-aircraft OFF/GROUND/AIR profile, partitioned into
   intervals. `partition`, `denoise` and `edge` read traces in bounded
   batches of aircraft; `flight` still loads the whole crop at once, so
   watch memory on constrained hosts.
4. `edge`: the flight state at each blackout's two edges.
5. `split`: the split-vs-join verdict for each blackout.
6. `flight`: assemble airborne stretches into flights; flights with too few
   usable trace points are dropped and recorded in `flight_dropped.parquet`.

Phase `landing`:

7. `train`: train the landing-attribution model; writes
   `attribution_model.pkl`. Set `TRAIN_USE_CACHED=1` to reuse the cached
   artifact.
8. `attribute`: attribute each flight to the countries it crosses (from the
   full-resolution raw points) and run the landing engine, which writes
   `landings.parquet` (one event per ground stay, with an evidence tier)
   and the per-aircraft `timeline.parquet`. Flight origin/destination
   inherit from the same table.
9. `theater`: per-flight theater-relevance columns.

Phase `publish`:

10. `trace`: an RDP-decimated polyline per flight, for rendering only.
11. `export`: render the parquets into static JSON/GeoJSON in
    `data/out/data/`, including the landings layer (`landings.geojson`,
    `airports_meta.json`). Routes are re-simplified here (finer for
    desktop, coarser for mobile). Missing input parquets make export log
    and exit 0; the precondition gate in `./run` is what stops an
    out-of-order run.

Every landing figure on the site (the stats tables' Ground and Landings
columns, the landings circles, each flight's origin/destination) derives
from `data/build/etl/landings.parquet`.

## Maintenance

| Command | When |
|---|---|
| `./run ingest merge-batches` | Recover after an `ingest` crash left partial batches |
| `./run ingest downsample [--interval N --start/--end YYYY-MM-DD --backup-dir D]` | Thin stored raw traces. Disk usage goes up until you verify the result and delete the backup dir (default `data/build/ingest_full/`) by hand |
| `./run clean` | Remove `data/build/lookup/` and `data/out/data/`; keeps ingest and etl output, external sources and config |

Only `serve` runs forever. If another command hangs well past its expected
time, run it under `tmux` with `| tee` so an OOM kill leaves output to
inspect.

## Source data

All source files are placed by hand. Download mechanics for every source
are in [data/in/external/README.md](data/in/external/README.md), and the
`gen` commands print the same instructions when a required input is
missing.

| Input | What it is | Where | Place at |
|---|---|---|---|
| Aircraft database | adsb.lol registry: ICAO24 to type code, registration, description | https://www.adsb.lol/data/ (`aircraft-database-complete-YYYY-MM.csv`) | `data/in/external/aircraft/` (newest by name is used) |
| OurAirports | World airfields: coordinates, type, ident | https://davidmegginson.github.io/ourairports-data/airports.csv | `data/in/external/airports.csv` |
| Natural Earth 10m admin-0 | Country boundary shapefiles | https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip | unzip into `data/in/external/ne_10m_admin_0_countries/` |
| Mictronics aircraft DB | Registry with per-hex military flags; second type source | https://www.mictronics.de/aircraft-database/indexedDB_old.php (browser download) | `data/in/external/aircraft/mictronics_indexedDB.zip` |

Optional inputs, each used when present:

| Input | What it is | Where | Place at |
|---|---|---|---|
| basic-ac-db | ADS-B Exchange registry; second military-flag source (the mil list is the union of both) and a type source | https://downloads.adsbexchange.com/downloads/basic-ac-db.json.gz | `data/in/external/aircraft/basic-ac-db.json.gz` |
| plane-alert-db | Community registry of aircraft of interest; a type source | https://raw.githubusercontent.com/sdr-enthusiasts/plane-alert-db/main/plane-alert-db.csv | `data/in/external/aircraft/plane-alert-db.csv` |
| ICAO DOC 8643 | Official type designators; validates type codes and recognises rotorcraft | POST https://www4.icao.int/doc8643/External/AircraftTypes | `data/in/external/aircraft/doc8643_aircraft_types.json` |
| OSM military airports | OpenStreetMap Overpass export; promotes fields to `military_base` in `gen airports`, which otherwise uses its hand-curated lists only | https://overpass-api.de/ | `data/in/external/osm_military_airports.csv` |

### What `./run gen` builds

| Target | Reads | Writes (`data/build/lookup/`) | Used by |
|---|---|---|---|
| `mil` | Mictronics zip + basic-ac-db | `mil_icao24.txt` | `ingest` (filters traffic to US-military addresses) and the `types` relevance test |
| `airports` | OurAirports CSV, OSM cross-reference (optional) | `airports.pkl` | `attribute`: landing attribution (`backend/pipeline/gen/airports.py`, `/methodology.html#landings`) |
| `raster` | Natural Earth shapefile | `airspace/` (georaster + ISO2 lookups) | `attribute`: point-to-country attribution |
| `boundaries` | Natural Earth shapefile + `countries.modes.json` | `boundaries.geojson` | `export` copies it into the site data as the map's country outlines |
| `roles` | `data/in/config/roles.json` | `roles.html`, `roles.reference.json` | `export` copies them into `data/out/` |

The `types` stage reads the four aircraft registries (adsb.lol CSV,
Mictronics, plane-alert-db, basic-ac-db), DOC 8643 and
`data/in/config/type_overrides.json` directly, layered as described in
`/methodology.html#phase-preprocess`.

## Updating the frontend

While `./run serve` runs, `frontend/watch-build.mjs` polls `frontend/src/`
and runs a full `vite build` on any change (a few seconds). `package.json`
is watched too: a change runs `npm ci`, which fails if
`package-lock.json` disagrees, so regenerate the lock (Dependencies) when
you add or bump a dependency.

`serve.mjs` reads `data/out/` on every request, so a re-run of
`./run etl export` is visible immediately. Hand edits to
`data/in/config/frontend/` are copied into `data/out/data/` by every
build (`frontend/copy-data.mjs`), so after a config-only edit run
`./run serve` again.

### Contact and repository links

Two links are per deployment and come from untracked, gitignored files in
`data/in/config/frontend/`, each with the same two keys:

```
contact.json   { "text": "Contact us",    "link": "mailto:someone@example.org" }
repo.json      { "text": "Fork the repo", "link": "https://github.com/you/your-fork" }
```

`link` is required and must be a `mailto:` or `https:` URL. `text` is the
button label, defaulting to the value shown above; it is ellipsized past
roughly twenty characters. For a Signal contact, use the username link
copied from the app (Settings, your profile, the username's QR code or
link, Share); it cannot be derived from the username.

Both files are copied into `data/out/data/` by the frontend build like the
rest of that directory, so their contents are public by design. They
drive: the two chips in the sidebar footer; the "Send a correction" link
on the roles page and in the in-app roles view (contact); and the "source
code is public" sentence on the methodology page (repo), which the build
fills in from `repo.json` via `frontend/apply-links.mjs`.

If a file is missing or invalid, everything it drives is simply absent:
no button, no link, no sentence, no placeholder. The rules live in
`frontend/src/lib/links.js`, with build-time copies in
`frontend/apply-links.mjs` and `backend/pipeline/gen/roles_page.py`;
change them together.

After editing either file: `./run serve` rebuilds the site, which copies
both files into `data/out/data/` and fills in the methodology link. After
editing `contact.json` also run `./run gen roles` first, so the roles page
carries the new link.

## Publish to Cloudflare Pages

`data/out/` and nothing else ships. `./run serve` (or a bare `vite build`)
compiles `frontend/src/` into it, copies `_headers` and `_redirects` from
`frontend/src/static/` and the config JSONs from `data/in/config/frontend/`;
`./run etl export` has already written the rest of `data/out/data/`. There
are no pre-built `.gz` files: the local server gzips on the fly and sets
the same security headers as `_headers`; Pages does its own compression.

Upload: build, then Cloudflare dashboard > Pages > Upload assets > drag
`data/out/`. The repo contains no accounts, tokens, domains or analytics;
the site's only external requests are unpkg (maplibre) and cartocdn (map
tiles), both allowed in the `_headers` CSP.

## Dependencies

Both ecosystems are lockfile-pinned and hash-verified, so an image rebuild
installs the same bytes on any machine. A dependency bump can move pipeline
artifact bytes; after one, rebuild and re-run `./run test backend`,
`./run gen` and `./run etl export` before trusting the new environment.

| Lockfile | Ecosystem | Regenerate |
|---|---|---|
| `requirements.lock` | Python (`etl`, `ingest` images) | `docker compose run --rm --entrypoint sh etl -c "pip install --quiet pip-tools && PATH=\$HOME/.local/bin:\$PATH pip-compile --generate-hashes --extra dev --extra geo --output-file - pyproject.toml" > requirements.lock` |
| `package-lock.json` | npm (`frontend`, `playwright` images) | `docker compose run --rm -v "$(pwd):/host" -w /host --entrypoint npm frontend install --ignore-scripts --package-lock-only` |

`requirements.lock` installs in `--require-hashes` mode (`-r`, not `-c`),
followed by `pip install --no-deps .` for the project itself.
`pyproject.toml` keeps its `>=` floors; do not also pin versions there.
`package-lock.json` installs with `npm ci`, which enforces the lock's
integrity digests and fails if `package.json` disagrees.

## Testing

```
./run test                     both suites: backend, then frontend
./run test backend [NAME...]   pytest, in the etl container
./run test frontend [NAME...]  Playwright, in the playwright container
```

`test/backend/` is the pytest suite. `test/frontend/` is the Playwright
suite, one spec per feature with shared helpers in `helpers.js`;
`playwright.config.js` defines the device projects (desktop Chromium,
Pixel 7 Chromium, iPhone 13 WebKit, iPhone 8 and iPhone SE WebKit with
font-zoom variants) and routes each spec to the projects it applies to.
It runs against the `frontend` compose service, so start `./run serve`
first. `NAME...` filters either suite; see `--help` for the matching rules.

## Repo map

```
run                 single host entry point (docker dispatcher)
.rebuild            image build-if-needed helper (sourced by run)
backend/            the Python package
  cli.py            fly CLI entry point (run/gen/status/clean)
  util/             shared config/artifact readers and pure helpers
    paths.py dimensions.py roles.py mil.py focus_config.py log.py
    geo/            raster.py haversine.py airport_snap.py
  pipeline/
    gen/            mil airports raster boundaries roles_page
    ingest/         ingest.py downsample.py
    etl/            run.py loader.py constants.py grounding.py
      preprocess/   types denoise
      timeline/     partition edge split flight
      landing/      train attribute theater calibrate engine footprint merge naming
      publish/      trace export
  network/          entrypoint.sh, Dockerfile.wireguard, wg_entrypoint.sh
frontend/
  src/              Svelte/Vite source: index.html, app.js and the other
                     modules, style.css, tailwind.css, reset.css, lib/
                     (components/, stores/, i18n.js, mount.js), messages/,
                     static/ (_headers, _redirects, methodology.html,
                     map-init.js, roles.css, images, vendor/; copied
                     verbatim into data/out/)
  watch-build.mjs   dev loop: full vite build on every source change
  copy-data.mjs     copies data/in/config/frontend/ into data/out/data/
  apply-links.mjs   fills the repo link into the built methodology page
  serve.mjs         static server for data/out/ (gzip, security headers)
test/
  backend/          pytest suite
  frontend/         Playwright suite
data/               gitignored except in/config/ and the READMEs under in/external/
  in/external/      source files, placed by hand
  in/config/        dimensions.json roles.json type_overrides.json (tracked)
    frontend/       common.texts.json countries.modes.json country.reference.json (tracked)
                    contact.json repo.json (untracked; see Contact and repository links)
  in/wg/            WireGuard config (untracked)
  build/            regenerable working state: ingest/, lookup/, etl/, .build/
  out/              the ship artifact: the frontend build plus out/data/
```
