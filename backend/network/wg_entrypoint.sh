#!/bin/bash
set -e

WG_CONF=/etc/wireguard/wg0.conf

if [ ! -r "$WG_CONF" ]; then
    cat >&2 <<'EOF'
wireguard sidecar: no config found at /etc/wireguard/wg0.conf

Expected host path: data/in/wg/wg0.conf

  * OFFLINE dev box (containers run, all internet egress fails): generate
    a peerless config — WireGuard comes up and blackholes all traffic
    (no tunnel, no leak):
        docker compose run --rm --entrypoint sh wireguard -c 'printf "[Interface]\nPrivateKey = %s\nAddress = 169.254.0.1/32\n" "$(wg genkey)"' > data/in/wg/wg0.conf

  * Production: put a real WireGuard config (with a Peer/Endpoint) at
    data/in/wg/wg0.conf.
EOF
    exit 1
fi

cp "$WG_CONF" /tmp/wg0.conf
sed -i '/^\[Interface\]/a Table = off' /tmp/wg0.conf

# Capture the config's own DNS server (if it has one) BEFORE deleting the
# line below — used to write /etc/resolv.conf once wg0 is up, further down.
WG_DNS=$(awk -F'=' '/^DNS/{gsub(/^[ \t]+|[ \t]+$/,"",$2); print $2; exit}' /tmp/wg0.conf)
sed -i '/^DNS/d' /tmp/wg0.conf

# Gateway and device from the same (first) default route line.
DEFAULT_ROUTE=$(ip route show default | head -n1)
DEFAULT_GW=$(printf '%s\n' "$DEFAULT_ROUTE" | awk '{print $3}')
DEFAULT_DEV=$(printf '%s\n' "$DEFAULT_ROUTE" | awk '{print $5}')

# Endpoint is "host:port" or "[ipv6]:port"; the loop below strips the
# brackets and drops the port after the last ':'.
ENDPOINTS=$(awk -F'=' '/^Endpoint/{gsub(/^[ \t]+|[ \t]+$/,"",$2); print $2}' /tmp/wg0.conf)

wg-quick up /tmp/wg0.conf

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
} > /etc/resolv.conf

echo "WireGuard tunnel up"
wget -q --spider -T 5 https://1.1.1.1 || echo "could not reach 1.1.1.1"

exec sleep infinity
