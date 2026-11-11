#!/bin/sh
set -e

# Install from the mounted lockfile; `npm ci` fails if package.json and
# package-lock.json disagree.
npm ci --ignore-scripts 2>&1

# Watch the mounted package.json for changes while running.
pkg_hash=$(sha256sum /app/package.json | cut -d' ' -f1)
(
  while true; do
    sleep 5
    h=$(sha256sum /app/package.json | cut -d' ' -f1)
    if [ "$h" != "$pkg_hash" ]; then
      echo "[frontend] package.json changed, installing dependencies..."
      npm ci --ignore-scripts 2>&1
      pkg_hash=$h
    fi
  done
) &

node frontend/watch-build.mjs &
exec node frontend/serve.mjs
