#!/bin/bash
# Sidecar healthcheck. Healthy means "egress is in the mode this sidecar was
# started in", not that the tunnel has connectivity:
#
#   tunneled  wg0 is the default route AND the direct marker is absent
#   direct    the direct marker is present AND wg0 is NOT the default route
#
# Both conditions are checked in each mode so that a sidecar can never pass
# as healthy in the wrong mode (e.g. a stale direct marker on a tunneled
# start, or a tunneled route under a direct start).
FLY_DIRECT_MARKER="${FLY_DIRECT_MARKER:-/run/fly-net-direct}"

if ip route show default | grep -q '\bwg0\b'; then
    via_wg0=1
else
    via_wg0=0
fi
if [ -e "$FLY_DIRECT_MARKER" ]; then
    marker=1
else
    marker=0
fi

if [ "${FLY_NET_DIRECT:-0}" = "1" ]; then
    [ "$marker" = 1 ] && [ "$via_wg0" = 0 ]
else
    [ "$marker" = 0 ] && [ "$via_wg0" = 1 ]
fi
