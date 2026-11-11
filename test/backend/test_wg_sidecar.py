"""Behavioral tests for the WireGuard sidecar scripts
(backend/network/wg_entrypoint.sh and healthcheck.sh).

The scripts are run with bash under a PATH of shim binaries (`ip`,
`wg-quick`, `wget`, `sleep`) that record their invocations instead of
touching the network, and with every absolute path overridden into a temp
directory. No root, no NET_ADMIN, no image rebuild: backend/ is a read-only
bind mount in the etl container the suite runs in."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

NETWORK = Path(__file__).resolve().parents[2] / "backend" / "network"
ENTRYPOINT = NETWORK / "wg_entrypoint.sh"
HEALTHCHECK = NETWORK / "healthcheck.sh"

CONF_WITH_PEER = """[Interface]
PrivateKey = cEI5pQyBIXg1h5tM4F+LEt7P0wGL6cQpPpLG6fXqQ0A=
Address = 10.0.0.2/32
DNS = 10.0.0.1

[Peer]
PublicKey = J7m6n0Z8l2hXz3kq6zP8n2nQ3b6GZ0R4ZdqSTv1hQ1c=
AllowedIPs = 0.0.0.0/0
Endpoint = 203.0.113.9:51820
"""


@pytest.fixture
def sandbox(tmp_path):
    """Shim bin dir + path overrides. `ip route show default` reports via
    eth0 unless the test writes a different stub route."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.log"
    route = tmp_path / "default_route"
    route.write_text("default via 172.18.0.1 dev eth0\n")

    def shim(name, body):
        p = bindir / name
        p.write_text("#!/bin/bash\n"
                     f"echo \"{name} $*\" >> \"$CALLS\"\n" + body)
        p.chmod(0o755)

    shim("ip", 'if [ "$1 $2 $3" = "route show default" ]; then cat "$ROUTE"; fi\nexit 0\n')
    shim("wg-quick", "exit 0\n")
    shim("wget", "exit 0\n")
    # `exec sleep infinity` is the scripts' steady state: make it return
    # so the test sees a clean exit instead of a hang.
    shim("sleep", "exit 0\n")

    # The script copies the mounted config into a working dir before
    # editing it (the mount is read-only in the container), so the two
    # must be distinct here as well.
    work = tmp_path / "work"
    work.mkdir()
    env = dict(os.environ)
    env.update({
        "PATH": f"{bindir}:{env['PATH']}",
        "CALLS": str(calls),
        "ROUTE": str(route),
        "WG_CONF": str(tmp_path / "wg0.conf"),
        "FLY_RESOLV_CONF": str(tmp_path / "resolv.conf"),
        "FLY_DIRECT_MARKER": str(tmp_path / "direct.marker"),
        "FLY_WG_WORK": str(work),
    })
    env.pop("FLY_NET_DIRECT", None)
    return {"env": env, "calls": calls, "route": route, "tmp": tmp_path}


def _run(script, sb, **extra_env):
    env = dict(sb["env"])
    env.update(extra_env)
    return subprocess.run(["bash", str(script)], env=env,
                          capture_output=True, text=True)


def _calls(sb):
    return sb["calls"].read_text() if sb["calls"].exists() else ""


# --- entrypoint: tunneled mode -------------------------------------------

def test_tunneled_without_config_fails_with_instructions(sandbox):
    r = _run(ENTRYPOINT, sandbox)
    assert r.returncode == 1
    assert "no config found" in r.stderr
    assert "./run ingest --direct" in r.stderr
    assert "wg-quick" not in _calls(sandbox)
    assert not (sandbox["tmp"] / "direct.marker").exists()


def test_tunneled_brings_up_wg0_and_pins_default_route(sandbox):
    (sandbox["tmp"] / "wg0.conf").write_text(CONF_WITH_PEER)
    r = _run(ENTRYPOINT, sandbox)
    assert r.returncode == 0, r.stderr
    calls = _calls(sandbox)
    assert "wg-quick up" in calls
    # endpoint pinned to the real gateway before the default route moves
    assert "ip route add 203.0.113.9/32 via 172.18.0.1 dev eth0" in calls
    assert "ip route del default" in calls
    assert "ip route add default dev wg0" in calls
    assert calls.index("route add 203.0.113.9/32") < calls.index("route del default")
    # DNS from the config, written to the (overridden) resolv.conf
    assert (sandbox["tmp"] / "resolv.conf").read_text() == "nameserver 10.0.0.1\n"
    # Table = off injected into the working copy, DNS line stripped; the
    # mounted original is untouched
    work = (sandbox["tmp"] / "work" / "wg0.conf").read_text()
    assert "Table = off" in work and "DNS" not in work
    assert (sandbox["tmp"] / "wg0.conf").read_text() == CONF_WITH_PEER
    assert not (sandbox["tmp"] / "direct.marker").exists()


@pytest.mark.parametrize("value", ["0", "", "yes", "true", "2"])
def test_only_exact_1_selects_direct_mode(sandbox, value):
    """Any value other than the literal `1` is tunneled mode: with no
    config present that means the setup message and exit 1."""
    r = _run(ENTRYPOINT, sandbox, FLY_NET_DIRECT=value)
    assert r.returncode == 1
    assert "no config found" in r.stderr
    assert not (sandbox["tmp"] / "direct.marker").exists()


def test_tunneled_start_clears_stale_direct_marker(sandbox):
    (sandbox["tmp"] / "direct.marker").write_text("")
    (sandbox["tmp"] / "wg0.conf").write_text(CONF_WITH_PEER)
    r = _run(ENTRYPOINT, sandbox)
    assert r.returncode == 0, r.stderr
    assert not (sandbox["tmp"] / "direct.marker").exists()


# --- entrypoint: direct mode ----------------------------------------------

def test_direct_touches_nothing_and_writes_marker(sandbox):
    r = _run(ENTRYPOINT, sandbox, FLY_NET_DIRECT="1")
    assert r.returncode == 0, r.stderr
    assert "DIRECT MODE" in r.stderr
    calls = _calls(sandbox)
    assert "wg-quick" not in calls
    assert "ip route" not in calls
    assert not (sandbox["tmp"] / "resolv.conf").exists()
    assert (sandbox["tmp"] / "direct.marker").exists()


def test_direct_needs_no_config_even_when_one_exists(sandbox):
    """A config on disk must not be brought up in direct mode."""
    (sandbox["tmp"] / "wg0.conf").write_text(CONF_WITH_PEER)
    r = _run(ENTRYPOINT, sandbox, FLY_NET_DIRECT="1")
    assert r.returncode == 0, r.stderr
    assert "wg-quick" not in _calls(sandbox)


# --- healthcheck: mode x state --------------------------------------------

def _health(sandbox, *, mode, via_wg0, marker):
    sandbox["route"].write_text(
        "default dev wg0 scope link\n" if via_wg0
        else "default via 172.18.0.1 dev eth0\n")
    m = sandbox["tmp"] / "direct.marker"
    if marker:
        m.write_text("")
    elif m.exists():
        m.unlink()
    return _run(HEALTHCHECK, sandbox, FLY_NET_DIRECT=mode).returncode


@pytest.mark.parametrize("via_wg0,marker,expected", [
    (True, False, 0),   # the one healthy tunneled state
    (False, False, 1),  # tunnel not up
    (True, True, 1),    # stale direct marker under a tunnel
    (False, True, 1),   # direct state under a tunneled start
])
def test_healthcheck_tunneled(sandbox, via_wg0, marker, expected):
    assert _health(sandbox, mode="0", via_wg0=via_wg0, marker=marker) == expected


@pytest.mark.parametrize("via_wg0,marker,expected", [
    (False, True, 0),   # the one healthy direct state
    (False, False, 1),  # entrypoint has not written the marker
    (True, True, 1),    # a tunnel route under a direct start
    (True, False, 1),   # tunneled state under a direct start
])
def test_healthcheck_direct(sandbox, via_wg0, marker, expected):
    assert _health(sandbox, mode="1", via_wg0=via_wg0, marker=marker) == expected
