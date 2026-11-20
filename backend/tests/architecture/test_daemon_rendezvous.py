"""Structural guardrail: where a source meets its daemon is spelled the same on both sides.

Three daemons are reached at a place both sides must name, and each side names
it on its own: shairport-sync writes its session pipe where
`rootfs/etc/shairport-sync.conf` says, the Tidal daemon opens its controller
socket where `rootfs/usr/local/bin/milo-tidal-connect` says, and qobuz-proxy
listens on the port `provisioning/qobuz-proxy.sh` writes into its config. The
backend spells each again. Moved on one side only, nothing fails anywhere: the
source waits on a pipe nobody writes, or a socket nobody opened, or polls a
port nobody listens on — on the appliance the source is simply silent.

The backend side is read off the instance `dependencies.py` builds, never off a
constant: a creator passing its own `config=` would override the source's
default, and that is the value the unit actually uses.

Doctrine note (same as `test_source_service_units.py`): every extractor asserts
it found something first, so a reworded file fails loudly instead of passing on
an empty match.
"""
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from backend import dependencies
from backend.api import qobuz_account

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def built(monkeypatch):
    """A source as `initialize_services` creates it, its collaborators None."""
    monkeypatch.setattr(dependencies, "get_service", lambda name: None)
    return dependencies._create_service


def _one(pattern: str, text: str, where: str) -> str:
    found = re.findall(pattern, text, re.DOTALL)
    assert len(found) == 1, f"{where}: expected one match of {pattern!r}, found {found}"
    return found[0]


def shairport_pipe() -> str:
    """`metadata.pipe_name` in the shairport-sync.conf the image installs."""
    conf = (REPO_ROOT / "rootfs/etc/shairport-sync.conf").read_text()
    block = _one(r"\nmetadata\s*=\s*\{(.*?)\};", conf, "shairport-sync.conf")
    return _one(r'pipe_name\s*=\s*"([^"]+)"', block, "shairport-sync.conf metadata block")


def tidal_socket() -> str:
    """The controller socket milo-tidal-connect hands the Tidal daemon."""
    launcher = (REPO_ROOT / "rootfs/usr/local/bin/milo-tidal-connect").read_text()
    return _one(
        r'--controller-unix-socket-path\s+"\$\{TIDAL_CONTROLLER_SOCKET:-([^}"]+)\}"',
        launcher, "milo-tidal-connect",
    )


def qobuz_port() -> int:
    """`server.http_port` in the config.yaml provisioning writes for qobuz-proxy."""
    script = (REPO_ROOT / "provisioning/qobuz-proxy.sh").read_text()
    server = _one(r"\nserver:\n((?:[ ]+.*\n)+)", script, "qobuz-proxy.sh config")
    return int(_one(r"http_port:\s*(\d+)", server, "qobuz-proxy.sh server block"))


def test_airplay_reads_the_pipe_shairport_sync_writes(built):
    pipe = shairport_pipe()
    assert pipe.startswith("/"), pipe
    assert built("airplay_source")._metadata_pipe == pipe


def test_tidal_talks_on_the_socket_the_daemon_opens(built):
    socket = tidal_socket()
    assert socket.startswith("/"), socket
    assert built("tidal_source")._socket_path == socket


def test_no_unit_moves_the_tidal_socket_behind_the_launchers_back():
    """The launcher's path is a default: an `Environment=` in a unit would
    win over it, and the check above would be comparing the wrong spelling."""
    units = sorted((REPO_ROOT / "system").glob("*.service"))
    assert len(units) >= 15, f"only {units} under system/ — the glob is broken"
    setting = [u.name for u in units if "TIDAL_CONTROLLER_SOCKET" in u.read_text()]
    assert not setting, f"{setting} set TIDAL_CONTROLLER_SOCKET"


def test_qobuz_reaches_the_port_qobuz_proxy_listens_on(built):
    port = qobuz_port()
    status = urlsplit(built("qobuz_source")._status_url)
    assert status.port == port, status
    # The account routes reach the same sidecar (the logout over loopback, the
    # login URL handed to the browser), both built from the port qobuz.source
    # declares.
    assert urlsplit(qobuz_account.QOBUZ_PROXY_INTERNAL).port == port
