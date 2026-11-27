"""The journal's retention: the helper, run for real, and the backend that drives it.

`milo-set-journal-retention` is granted to the backend through sudo with no
argument at all, so the days it reads on stdin are the one input reaching root
that sudo checks nothing of: whatever it lets through is written into a
journald drop-in, and journald restarted. A value it should have refused is a
journal kept for a period nobody offered, or capped at a size nobody measured;
a drop-in it writes under a name that sorts before `99-milo-journald.conf` is a
retention the page announces and journald never applies. Consumers: `PUT
/api/settings/log-retention` and the reconcile at each start
(`core/system/journal_retention.py`), and the logs page, which offers periods up
to `retention_days`.
"""

import asyncio
import json
import logging
import os
import pty
import stat
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, Mock

from backend.api.settings import create_settings_router
from backend.config import constants
from backend.core.settings import SettingsService
from backend.core.system import journal_retention as retention_module
from backend.core.system.journal_retention import (
    JournalRetentionError,
    JournalRetentionService,
    read_retention_in_force,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-set-journal-retention"
ROOTFS_JOURNALD = REPO_ROOT / "rootfs" / "etc" / "systemd" / "journald.conf.d" / "99-milo-journald.conf"
DROPIN_DIR_LINE = 'DROPIN_DIR="/etc/systemd/journald.conf.d"\n'


def _helper_assignment(name: str) -> str:
    """A `NAME="value"` line of the helper, read as bash would assign it."""
    lines = [
        line for line in HELPER.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"{name}=")
    ]
    assert len(lines) == 1, f"{name} assigned {len(lines)} times in the helper"
    return lines[0].split("=", 1)[1].strip('"')


# --------------------------------------------------------------------------- #
# The helper, under bash, against a drop-in directory of our own
# --------------------------------------------------------------------------- #

# Each stub appends its argv to the log and does nothing else: `chown root:root`
# needs root, and `systemctl restart systemd-journald` would restart the host's.
STUB = '#!/bin/sh\necho "$(basename "$0") $*" >> "$STUB_LOG"\n'


@pytest.fixture
def world(tmp_path):
    """A copy of the helper writing into tmp_path, with chown and systemctl stubbed.

    The directory line must be rewritten exactly once, or the run would touch
    the host's real /etc/systemd/journald.conf.d.
    """
    text = HELPER.read_text(encoding="utf-8")
    assert text.count(DROPIN_DIR_LINE) == 1, f"{DROPIN_DIR_LINE.strip()!r} not found exactly once"
    dropins = tmp_path / "journald.conf.d"
    dropins.mkdir()
    helper = tmp_path / "milo-set-journal-retention"
    helper.write_text(text.replace(DROPIN_DIR_LINE, f'DROPIN_DIR="{dropins}"\n'), encoding="utf-8")

    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name in ("chown", "systemctl"):
        (stubs / name).write_text(STUB)
        (stubs / name).chmod(0o755)
    return {
        "helper": helper,
        "dropins": dropins,
        "dropin": dropins / _helper_assignment("DROPIN_NAME"),
        "stubs": stubs,
        "log": tmp_path / "stub.log",
    }


def _run(world, stdin, args=(), timeout=30):
    result = subprocess.run(
        ["bash", str(world["helper"]), *args],
        **({"input": stdin} if isinstance(stdin, bytes) else {"stdin": stdin}),
        env={"PATH": f"{world['stubs']}:/usr/bin:/bin", "STUB_LOG": str(world["log"])},
        capture_output=True, timeout=timeout,
    )
    calls = world["log"].read_text().splitlines() if world["log"].exists() else []
    return result, calls


def _directory(world):
    """Every file in the drop-in directory and its bytes, temporary ones included."""
    return {p.name: p.read_bytes() for p in sorted(world["dropins"].iterdir())}


@pytest.mark.parametrize("days, cap", [(15, "160M"), (30, "300M")])
def test_a_longer_period_is_written_atomically_and_journald_restarted(world, days, cap):
    result, calls = _run(world, f"{days}\n".encode())

    assert result.returncode == 0, result.stderr
    text = world["dropin"].read_text()
    assert f"MaxRetentionSec={days}d\n" in text
    assert f"SystemMaxUse={cap}\n" in text
    # What the backend reads back is what the helper wrote: the reconcile and
    # the helper agree on the spelling.
    assert read_retention_in_force(world["dropin"]) == days
    assert stat.S_IMODE(world["dropin"].stat().st_mode) == 0o644
    # Nothing but the drop-in is left: no temporary file journald might read.
    assert list(_directory(world)) == [world["dropin"].name]
    # Owned by root before it is moved in place, then journald restarted, once.
    assert calls[0].startswith("chown root:root -- ")
    assert calls[1:] == ["systemctl restart systemd-journald"]


def test_the_period_in_force_restarts_nothing(world):
    """Called at every start that disagrees, and from the page: a period already
    in force must not restart journald for nothing."""
    _run(world, b"30\n")
    written = _directory(world)
    world["log"].unlink()

    result, calls = _run(world, b"30\n")

    assert result.returncode == 0, result.stderr
    assert b"unchanged" in result.stdout
    assert calls == []
    assert _directory(world) == written


def test_seven_days_removes_the_dropin_and_seven_again_does_nothing(world):
    """Seven days is the rootfs file's, declared once: the helper's drop-in goes."""
    _run(world, b"15\n")
    world["log"].unlink()

    result, calls = _run(world, b"7\n")
    assert result.returncode == 0, result.stderr
    assert _directory(world) == {}
    assert calls == ["systemctl restart systemd-journald"]
    assert read_retention_in_force(world["dropin"]) == 7

    world["log"].unlink()
    result, calls = _run(world, b"7")
    assert result.returncode == 0, result.stderr
    assert calls == []


@pytest.mark.parametrize("before, asked", [
    (None, b"15\n"),
    (b"15\n", b"30\n"),
    (b"30\n", b"7\n"),
])
def test_a_journald_that_does_not_restart_leaves_the_directory_as_it_was(world, before, asked):
    """Left changed, the drop-in would read "unchanged" to the next call, which
    would then restart nothing: the setting and the file agreeing over a
    journald still on the old caps, until a reboot. Put back, the retry makes
    the change and restarts journald."""
    if before is not None:
        _run(world, before)
    previous = _directory(world)
    systemctl = world["stubs"] / "systemctl"
    working = systemctl.read_text()
    systemctl.write_text(working + "exit 1\n")
    if world["log"].exists():
        world["log"].unlink()

    result, calls = _run(world, asked)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert calls[-1:] == ["systemctl restart systemd-journald"]
    assert _directory(world) == previous

    systemctl.write_text(working)
    world["log"].unlink()
    result, calls = _run(world, asked)
    assert result.returncode == 0, result.stderr
    assert calls[-1:] == ["systemctl restart systemd-journald"]


def test_a_closed_stdin_is_refused_rather_than_waited_on(world):
    """sudo never hands one over, a root shell can: bash would give fd 0 to the
    read's own pipe, and the helper would wait on itself for good."""
    result = subprocess.run(
        ["sh", "-c", 'exec bash "$0" <&-', str(world["helper"])],
        env={"PATH": f"{world['stubs']}:/usr/bin:/bin", "STUB_LOG": str(world["log"])},
        capture_output=True, timeout=5,
    )

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert not world["log"].exists()
    assert _directory(world) == {}


@pytest.mark.parametrize("stdin", [
    b"",
    b"\n",
    b"07\n",
    b" 7\n",
    b"7 \n",
    b"7d\n",
    b"8\n",
    b"1\n",
    b"150\n",
    b"300\n",
    b"-7\n",
    b"7\r\n",
    b"7\n\n",
    b"15\nx",
    b"30\n7\n",
    b"7\x00",
    b"7\x0015\n",
    b"15" + b"\n" * 40,
    b"\xef\xbb\xbf7\n",
])
def test_a_value_other_than_seven_fifteen_or_thirty_touches_nothing(world, stdin):
    """Refused with exit 2 before anything is written, removed or restarted —
    a drop-in already in place stays as it was."""
    _run(world, b"15\n")
    before = _directory(world)
    world["log"].unlink()

    result, calls = _run(world, stdin)

    assert result.returncode == 2, (stdin, result.stdout, result.stderr)
    assert result.stderr.startswith(b"ERROR:")
    assert calls == []
    assert _directory(world) == before


@pytest.mark.parametrize("args", [("15",), ("7",), ("",), ("--help",)])
def test_an_argument_is_refused_whatever_it_says(world, args):
    """sudo grants the helper with none; one given here is a caller that bypassed the route."""
    _run(world, b"15\n")
    before = _directory(world)
    world["log"].unlink()

    result, calls = _run(world, b"30\n", args=args)

    assert result.returncode == 1
    assert calls == []
    assert _directory(world) == before


def test_a_terminal_on_stdin_is_refused_rather_than_waited_on(world):
    """`sudo milo-set-journal-retention` typed by hand: the read would hold the session open for good."""
    primary, secondary = pty.openpty()
    try:
        result, calls = _run(world, secondary, timeout=5)
    finally:
        os.close(primary)
        os.close(secondary)

    assert result.returncode == 1, (result.stdout, result.stderr)
    assert calls == []
    assert _directory(world) == {}


def test_the_dropin_sorts_after_the_rootfs_file_it_overrides():
    """journald reads drop-ins in lexical order of their names, the last value
    of a key winning: a name that sorts first is a retention never applied.
    `99-milo-journald-retention.conf` would be one (`-` sorts before `.`)."""
    ours = _helper_assignment("DROPIN_NAME")
    assert ours.endswith(".conf")
    assert ROOTFS_JOURNALD.is_file()
    assert sorted([ours, ROOTFS_JOURNALD.name])[-1] == ours
    assert ours.encode() > ROOTFS_JOURNALD.name.encode()


def test_the_backend_reads_the_dropin_the_helper_writes():
    """The reconcile reads the file the helper owns: two spellings of the path
    and the reconcile calls the helper at every start, forever."""
    assert str(constants.JOURNAL_RETENTION_DROPIN) == (
        f'{_helper_assignment("DROPIN_DIR")}/{_helper_assignment("DROPIN_NAME")}'
    )


def test_the_rootfs_file_declares_the_seven_days_the_helper_leaves_to_it():
    """Seven days is no drop-in at all: the rootfs file must say seven."""
    assert read_retention_in_force(ROOTFS_JOURNALD) == retention_module.RETENTION_WITHOUT_DROPIN_DAYS


# --------------------------------------------------------------------------- #
# The backend: the helper behind sudo is the outside world
# --------------------------------------------------------------------------- #

class _FakeHelper:
    """`sudo -n milo-set-journal-retention` as the backend reaches it."""

    def __init__(self, returncode=0, stderr=b"", missing=False, hang=False):
        self.calls = []
        self.returncode = returncode
        self.stderr = stderr
        self.missing = missing
        self.hang = hang

    async def __call__(self, *argv, **kwargs):
        if self.missing:
            raise FileNotFoundError(argv[0])
        helper = self

        class _Process:
            returncode = None

            async def communicate(self, data=None):
                helper.calls.append((argv, data))
                if helper.hang:
                    await asyncio.Event().wait()
                self.returncode = helper.returncode
                return b"journal retention: done\n", helper.stderr

            def kill(self):
                pass

            async def wait(self):
                return self.returncode

        return _Process()


@pytest.fixture
def settings(tmp_path):
    service = SettingsService()
    service.settings_file = str(tmp_path / "settings.json")
    return service


def _service(monkeypatch, settings, dropin, helper):
    monkeypatch.setattr(retention_module.asyncio, "create_subprocess_exec", helper)
    return JournalRetentionService(settings, dropin=dropin)


async def test_the_reconcile_calls_nothing_when_the_dropin_agrees(monkeypatch, settings, tmp_path):
    """No drop-in is seven days; a drop-in saying fifteen is fifteen."""
    helper = _FakeHelper()
    dropin = tmp_path / "retention.conf"

    await _service(monkeypatch, settings, dropin, helper).reconcile()

    dropin.write_text("[Journal]\nMaxRetentionSec=15d\nSystemMaxUse=160M\n")
    await settings.set_setting("system.log_retention_days", 15)
    await _service(monkeypatch, settings, dropin, helper).reconcile()

    assert helper.calls == []


@pytest.mark.parametrize("dropin_text, setting", [
    (None, 30),
    ("[Journal]\nMaxRetentionSec=30d\n", 7),
    ("[Journal]\nMaxRetentionSec=15d\n", 30),
    ("[Journal]\nSystemMaxUse=160M\n", 15),
])
async def test_the_reconcile_applies_the_setting_when_the_dropin_disagrees(
    monkeypatch, settings, tmp_path, dropin_text, setting,
):
    """Once, through sudo -n, the days on stdin and no argument (the grant is `""`)."""
    helper = _FakeHelper()
    dropin = tmp_path / "retention.conf"
    if dropin_text is not None:
        dropin.write_text(dropin_text)
    await settings.set_setting("system.log_retention_days", setting)

    await _service(monkeypatch, settings, dropin, helper).reconcile()

    assert helper.calls == [(("sudo", "-n", constants.SET_JOURNAL_RETENTION_CMD), f"{setting}\n".encode())]


@pytest.mark.parametrize("helper", [
    _FakeHelper(missing=True),
    _FakeHelper(returncode=1, stderr=b"sudo: a password is required"),
])
async def test_the_reconcile_fails_open_with_one_warning(monkeypatch, settings, tmp_path, caplog, helper):
    """A dev host has no helper and no grant: the backend starts all the same."""
    await settings.set_setting("system.log_retention_days", 30)
    service = _service(monkeypatch, settings, tmp_path / "retention.conf", helper)

    with caplog.at_level(logging.INFO, logger=retention_module.logger.name):
        await service.reconcile()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert [r.levelno for r in warnings] == [logging.WARNING]


async def test_the_boot_does_not_wait_on_the_reconcile(monkeypatch, settings, tmp_path):
    """journald's restart can take seconds on a card: `initialize` returns at
    once, and the teardown cancels a reconcile still waiting."""
    helper = _FakeHelper(hang=True)
    await settings.set_setting("system.log_retention_days", 15)
    service = _service(monkeypatch, settings, tmp_path / "retention.conf", helper)

    assert await asyncio.wait_for(service.initialize(), 1) is True
    for _ in range(50):
        if helper.calls:
            break
        await asyncio.sleep(0.01)
    assert len(helper.calls) == 1

    await asyncio.wait_for(service.cleanup(), 1)


async def test_apply_raises_when_the_helper_refuses(monkeypatch, settings, tmp_path):
    helper = _FakeHelper(returncode=2, stderr=b"ERROR: the number of days must be 7, 15 or 30")
    with pytest.raises(JournalRetentionError, match="7, 15 or 30"):
        await _service(monkeypatch, settings, tmp_path / "r.conf", helper).apply(15)


# --------------------------------------------------------------------------- #
# The setting: one of three values, applied before it is stored
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("stored", [8, 0, -7, "15", 15.0, True, None, [30], {"days": 30}])
async def test_a_stored_value_outside_the_three_reads_as_the_default(settings, stored):
    """As a theme outside the three is: the default, never a value the helper refuses."""
    Path(settings.settings_file).write_text(json.dumps({
        "schema_version": SettingsService.SCHEMA_VERSION,
        "system": {"log_retention_days": stored},
    }))

    assert await settings.get_setting("system.log_retention_days") == settings.defaults["system"]["log_retention_days"]


async def test_each_offered_value_is_stored_as_written(settings):
    for days in sorted(constants.ALLOWED_LOG_RETENTION_DAYS):
        assert await settings.set_setting("system.log_retention_days", days)
        settings.invalidate_cache()
        assert await settings.get_setting("system.log_retention_days") == days


@pytest.fixture
def route(settings):
    state_machine = Mock(broadcast=AsyncMock(), get_source=Mock(return_value=None))
    retention = Mock(apply=AsyncMock())
    app = FastAPI()
    app.include_router(
        create_settings_router(
            volume_service=Mock(),
            state_machine=state_machine,
            screen_controller=Mock(),
            systemd_manager=Mock(),
            routing_service=Mock(),
            hardware_service=Mock(),
            settings_service=settings,
            multiroom_equalizer_service=Mock(),
            mac_link_calibration=Mock(),
            journal_retention_service=retention,
        ),
        prefix="/api/settings",
    )
    return TestClient(app), retention, state_machine


@pytest.mark.parametrize("body", [
    {"log_retention_days": 8},
    {"log_retention_days": "15"},
    {"log_retention_days": 0},
    {"log_retention_days": None},
    {"log_retention_days": True},
    {},
    {"log_retention_days": 15, "unit": "d"},
])
def test_the_route_refuses_any_other_value(route, settings, body):
    client, retention, _ = route

    response = client.put("/api/settings/log-retention", json=body)

    assert response.status_code == 422, response.text
    retention.apply.assert_not_awaited()


def test_the_route_applies_then_stores_then_announces(route, settings):
    client, retention, state_machine = route

    response = client.put("/api/settings/log-retention", json={"log_retention_days": 30})

    assert response.status_code == 200, response.text
    retention.apply.assert_awaited_once_with(30)
    assert asyncio.run(settings.get_setting("system.log_retention_days")) == 30
    event = state_machine.broadcast.await_args.args[0]
    assert (event.CATEGORY, event.TYPE) == ("settings", "log_retention_changed")
    assert event.config.log_retention_days == 30


def test_a_retention_the_helper_did_not_take_is_not_stored(route, settings):
    """Stored anyway, the page would offer thirty days of a journal kept for fifteen."""
    client, retention, state_machine = route
    asyncio.run(settings.set_setting("system.log_retention_days", 15))
    retention.apply.side_effect = JournalRetentionError("milo-set-journal-retention failed (rc=1)")

    response = client.put("/api/settings/log-retention", json={"log_retention_days": 30})

    assert response.status_code == 500
    settings.invalidate_cache()
    assert asyncio.run(settings.get_setting("system.log_retention_days")) == 15
    state_machine.broadcast.assert_not_awaited()


def test_the_helper_is_executable():
    """A helper sudo is granted but cannot exec is a 500 on every change."""
    assert os.access(HELPER, os.X_OK)
    assert HELPER.read_text(encoding="utf-8").startswith("#!/bin/bash\n")
