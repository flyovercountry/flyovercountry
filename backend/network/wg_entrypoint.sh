#!/bin/bash
# WireGuard sidecar entrypoint. The ingest container shares this container's
# network namespace, so whatever egress is set up here is the only egress
# ingest has.
#
# Two modes, chosen by FLY_NET_DIRECT (set by `./run ingest --direct`):
#
#   tunneled (default)  bring up wg0 from /etc/wireguard/wg0.conf and make
#                       it the default route and DNS path. Ingest's traffic
#                       leaves through the tunnel; GitHub sees the peer's
#                       address, not this machine's.
#   direct (=1 only)    touch nothing: no config needed, no wg0, no route or
#                       DNS changes. Ingest's traffic leaves from this machine
#                       directly, and GitHub logs this machine's address.
#
# The direct marker file is what healthcheck.sh uses to tell the modes apart,
# so a sidecar can never report healthy in the mode it was not started in.
#
# Paths are overridable by environment so the script can be exercised by
# the test suite with shimmed `ip`/`wg-quick` binaries and a temp directory.
set -e

WG_CONF="${WG_CONF:-/etc/wireguard/wg0.conf}"
FLY_RESOLV_CONF="${FLY_RESOLV_CONF:-/etc/resolv.conf}"
FLY_DIRECT_MARKER="${FLY_DIRECT_MARKER:-/run/fly-net-direct}"
FLY_WG_WORK="${FLY_WG_WORK:-/tmp}"

if [ "${FLY_NET_DIRECT:-0}" = "1" ]; then
    cat >&2 <<'EOF'
wireguard sidecar: DIRECT MODE. No tunnel is configured; ingest will
download from GitHub with this machine's own address, which GitHub logs.
This lasts only for the current `./run ingest --direct` invocation.
EOF
    : > "$FLY_DIRECT_MARKER"
    exec sleep infinity
fi

rm -f "$FLY_DIRECT_MARKER"

if [ ! -r "$WG_CONF" ]; then
    cat >&2 <<'EOF'
wireguard sidecar: no config found at /etc/wireguard/wg0.conf

Expected host path: data/in/wg/wg0.conf

  * OFFLINE dev box (containers run, all internet egress fails): generate
    a peerless config — WireGuard comes up and blackholes all traffic
    (no tunnel, no leak):
        docker compose run --rm --entrypoint sh wireguard -c 'printf "[Interface]\nPrivateKey = %s\nAddress = 169.254.0.1/32\n" "$(wg genkey)"' > data/in/wg/wg0.conf

  * To download: put a real WireGuard config (with a Peer/Endpoint) at
    data/in/wg/wg0.conf. Any WireGuard provider or your own peer works.

  * To download WITHOUT a tunnel, exposing this machine's address to
    GitHub: `./run ingest --direct`.
EOF
    exit 1
fi

cp "$WG_CONF" "$FLY_WG_WORK/wg0.conf"
sed -i '/^\[Interface\]/a Table = off' "$FLY_WG_WORK/wg0.conf"

# Capture the config's own DNS server (if it has one) BEFORE deleting the
# line below — used to write resolv.conf once wg0 is up, further down.
WG_DNS=$(awk -F'=' '/^DNS/{gsub(/^[ \t]+|[ \t]+$/,"",$2); print $2; exit}' "$FLY_WG_WORK/wg0.conf")
sed -i '/^DNS/d' "$FLY_WG_WORK/wg0.conf"

# Gateway and device from the same (first) default route line.
DEFAULT_ROUTE=$(ip route show default | head -n1)
DEFAULT_GW=$(printf '%s\n' "$DEFAULT_ROUTE" | awk '{print $3}')
DEFAULT_DEV=$(printf '%s\n' "$DEFAULT_ROUTE" | awk '{print $5}')

# Endpoint is "host:port" or "[ipv6]:port"; the loop below strips the
# brackets and drops the port after the last ':'.
ENDPOINTS=$(awk -F'=' '/^Endpoint/{gsub(/^[ \t]+|[ \t]+$/,"",$2); print $2}' "$FLY_WG_WORK/wg0.conf")

wg-quick up "$FLY_WG_WORK/wg0.conf"

for ep in $ENDPOINTS; do
    ep="$(printf '%s' "$ep" | tr -d '[]')"
    host="${ep%:*}"
    ip route add "$host/32" via "$DEFAULT_GW" dev "$DEFAULT_DEV"
done
ip route del default
ip route add default dev wg0

# DNS through the tunnel too: this image has no resolvconf, so without
# this the container keeps Docker's embedded resolver and every hostname
# lookup leaks outside wg0. Comma-separated DNS entries each become a
# nameserver line; 9.9.9.9 is the fallback.
{
    for ns in $(printf '%s' "${WG_DNS:-9.9.9.9}" | tr ',' ' '); do
        echo "nameserver $ns"
    done
} > "$FLY_RESOLV_CONF"

echo "WireGuard tunnel up"
wget -q --spider -T 5 https://1.1.1.1 || echo "could not reach 1.1.1.1"

exec sleep infinity
