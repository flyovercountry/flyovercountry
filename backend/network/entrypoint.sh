#!/bin/sh
set -e

if [ -d /app/data ] && [ "$(id -u)" = "0" ]; then
    DATA_UID=$(stat -c %u /app/data)
    DATA_GID=$(stat -c %g /app/data)
    if [ "$DATA_UID" != "0" ]; then
        exec setpriv --reuid="$DATA_UID" --regid="$DATA_GID" --clear-groups fly "$@"
    fi
fi

exec fly "$@"
