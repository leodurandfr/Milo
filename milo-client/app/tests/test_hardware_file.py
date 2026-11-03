"""
hardware.json: the setup wizard's route writes it, five readers parse it.

What breaks when these fail: a reader that no longer finds the audio card. The
two unit-level one-liners (CamillaDSP, snapclient) then read `none` and exit 0,
so the room goes silent with no error; registration sends
`hardware_configured=false` on the next heartbeat, which the server takes for a
reinstall and answers by unregistering the satellite (name, zone, EQ, trim and
delay erased); and the server's volume-control switch answers 400. The backend
contract cannot see any of it — it excludes `GET /api/hardware` as a file's
shape, not a handler's — so this is the test that holds the writer and its
readers to one spelling.

Nothing is mocked but the file's location: the real route writes it, and every
reader, the shell ones included, is run as shipped against what it wrote.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import create_hardware_router
from services import registration

CLIENT_ROOT = Path(__file__).resolve().parents[2]
CAMILLADSP_UNIT = CLIENT_ROOT / "system" / "milo-client-camilladsp.service"
SNAPCLIENT_LAUNCHER = CLIENT_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-client-snapclient-launcher"
APPLY_HARDWARE = CLIENT_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-client-apply-hardware"
HARDWARE_HELPERS = CLIENT_ROOT / "rootfs" / "usr" / "local" / "lib" / "milo" / "hardware-helpers.sh"

# `python3 [-flags] -c "<code>" <argv...> < <file>`, the shape both units use to
# read the card id; the flags are optional so an `-I` added later still matches.
_ONE_LINER = re.compile(
    r'python3(?:\s+-\w+)*\s+-c\s*\\?\s*"(?P<code>[^"]+)"\s*\\?\s*(?P<argv>[^<]+?)\s*<\s*(?P<path>\S+)'
)


def _one_liner(path: Path) -> tuple[str, list[str], str]:
    """The card-id one-liner of a unit file or launcher: (code, argv, input path)."""
    match = _ONE_LINER.search(path.read_text())
    assert match, f"no hardware.json one-liner found in {path}"
    code, argv = match["code"], match["argv"].split()
    assert code.strip() and argv == ["audio", "id", "none"], (path, code, argv)
    return code, argv, match["path"].strip('"')


def _shell_hardware_file(path: Path) -> str:
    match = re.search(r'^HARDWARE_FILE="([^"]+)"', path.read_text(), re.MULTILINE)
    assert match, f"no HARDWARE_FILE= declaration in {path}"
    return match[1]


# volume_control False on purpose: True is the readers' default and would hide a rename.
CARD = {"audio_id": "hifiberry-dacplus-std", "overlay": "hifiberry-dacplus-std", "volume_control": False}
NO_CARD = {"audio_id": "none"}


@pytest.mark.parametrize("body, registered", [
    (CARD, {"audio_id": "hifiberry-dacplus-std", "hardware_configured": True, "volume_control": False}),
    (NO_CARD, {"audio_id": "none", "hardware_configured": False, "volume_control": True}),
], ids=["card", "no-card"])
def test_what_the_wizard_writes_is_what_its_readers_read(body, registered, tmp_path, monkeypatch):
    hardware_file = tmp_path / "hardware.json"
    monkeypatch.setattr("services.registration.HARDWARE_FILE", str(hardware_file))
    app = FastAPI()
    app.include_router(create_hardware_router())
    client = TestClient(app)

    assert client.put("/api/hardware/audio", json=body).status_code == 200
    audio_id = body["audio_id"]

    assert registration._read_hardware_config() == registered

    for unit in (CAMILLADSP_UNIT, SNAPCLIENT_LAUNCHER):
        code, argv, _ = _one_liner(unit)
        with hardware_file.open() as stdin:
            out = subprocess.run([sys.executable, "-c", code, *argv], stdin=stdin,
                                 capture_output=True, text=True, check=True)
        assert out.stdout.strip() == audio_id, unit.name

    if audio_id != "none":
        out = subprocess.run(
            ["bash", "-c", f'HARDWARE_FILE="{hardware_file}"; source "{HARDWARE_HELPERS}"; '
                           'read_json audio.overlay'],
            capture_output=True, text=True, check=True)
        assert out.stdout.strip() == body["overlay"]

    # backend/api/multiroom.py reads the card from this answer, by the same keys.
    assert client.get("/api/hardware").json()["audio"]["id"] == audio_id


def test_every_reader_opens_the_file_the_wizard_writes():
    """The shell readers restate the path; they must name the one the app declares."""
    _, _, unit_path = _one_liner(CAMILLADSP_UNIT)
    assert unit_path == registration.HARDWARE_FILE
    for script in (SNAPCLIENT_LAUNCHER, APPLY_HARDWARE):
        assert _shell_hardware_file(script) == registration.HARDWARE_FILE, script.name
