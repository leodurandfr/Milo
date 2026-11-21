"""A satellite follows a new server only in the minutes after a power cut (server_secret.py).

A reflashed server holds a new key, so every speaker that pinned the old one
refuses its update, remote access and reboot. Owner decision: the gesture that
moves a speaker is to unplug it and plug it back in. What breaks when these
fail: either a reflash strands every speaker until each is reflashed too, or a
host on the LAN replaces a speaker's server whenever it likes — which is what
the pin exists to prevent.

The outside world is the kernel's boot id and the pin's files, under tmp_path
(conftest's `boot_trace` and `secret_file`), and the monotonic clock where the
window's end is the question.
"""
import asyncio
import types
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from fastapi.testclient import TestClient

import main
import server_secret
from server_secret import SECRET_HEADER, ServerSecretRefused, admit, open_window_after_unclean_stop
from tests.conftest import ANOTHER_SERVER_SECRET, SERVER_SECRET
from tests.test_server_secret import TO_ROOT, _send

HAND_OVER = ("PUT", "/api/system/server-secret")
# What the reflashed server derives: neither the old server's secret nor ANOTHER's.
REFLASHED_SECRET = "c3" * 32


@pytest.fixture
def client():
    return TestClient(main.app, base_url="http://192.168.1.60:8001")


def _reboot(boot_trace, boot="7a0d5e41-boot-two"):
    """The kernel's new boot id, as a reboot (clean or not) leaves it."""
    (boot_trace / "boot_id").write_text(f"{boot}\n")


def _hand_over(client, secret):
    return client.put(HAND_OVER[1], headers={SECRET_HEADER: secret})


@contextmanager
def _quiet_lifespan():
    """The lifespan with what it starts left out: the server it registers with,
    the address it follows, the DSP it connects to."""
    with patch.object(main, "register_with_main_milo", new=AsyncMock()), \
            patch.object(main, "follow_server_address", new=AsyncMock()), \
            patch.object(main.equalizer_service, "start_connection_loop", new=MagicMock()), \
            patch.object(main.equalizer_service, "stop_connection_loop", new=AsyncMock()):
        yield


def test_after_a_power_cut_the_hand_over_follows_a_new_server(client, server):
    """The reflashed server's first hand-over is taken; the old server's secret
    is refused from then on: the speaker answers one server, the new one."""
    assert open_window_after_unclean_stop() is True

    followed = _hand_over(client, REFLASHED_SECRET)

    assert followed.status_code == 200, followed.text
    assert followed.json()["data"] == {"changed": True}
    assert server_secret.SECRET_FILE.read_text().strip() == REFLASHED_SECRET
    assert _send(client, ("POST", "/api/hardware/reboot"), SERVER_SECRET).status_code == 403


@pytest.mark.parametrize("route", [r for r in TO_ROOT if r != HAND_OVER],
                         ids=[f"{m} {p}" for m, p in TO_ROOT if (m, p) != HAND_OVER])
def test_only_the_hand_over_follows(client, server, route):
    """The window replaces a pin, it does not open the unit: a request to root
    from an unknown server is refused inside it like outside it."""
    open_window_after_unclean_stop()

    response = _send(client, route, REFLASHED_SECRET)

    assert response.status_code == 403, response.text
    assert server_secret.SECRET_FILE.read_text().strip() == SERVER_SECRET
    assert server_secret.accepts_new_server(), "a refused request closed the window"


@pytest.mark.asyncio
async def test_a_clean_stop_opens_no_window(server, boot_trace):
    """The app's own stop and start, across a reboot: the first start of a
    fresh unit follows (no stop at all came before it), the start after a
    stop systemd asked for does not."""
    with _quiet_lifespan():
        async with main.lifespan(main.app):
            first_start = server_secret.accepts_new_server()
        _reboot(boot_trace)
        async with main.lifespan(main.app):
            after_clean_stop = server_secret.accepts_new_server()

    assert (first_start, after_clean_stop) == (True, False)


@pytest.mark.asyncio
async def test_a_stop_whose_teardown_fails_still_opens_no_window(server, boot_trace):
    """A stop systemd asked for has begun when the teardown runs: a step of it
    that raises — or hangs until systemd kills the process — must not leave the
    next start reading a power cut, which is the direction that opens the unit."""
    with _quiet_lifespan(), \
            patch.object(main.equalizer_service, "stop_connection_loop",
                         new=AsyncMock(side_effect=[RuntimeError("DSP gone"), None])):
        with pytest.raises(RuntimeError):
            async with main.lifespan(main.app):
                pass
        _reboot(boot_trace)
        async with main.lifespan(main.app):
            after_failed_teardown = server_secret.accepts_new_server()

    assert after_failed_teardown is False


def test_a_start_that_cannot_record_its_boot_keeps_the_clean_stop(server, boot_trace, monkeypatch):
    """Consumed first, the marker of a clean stop would be gone when the boot
    could not be recorded: the next start on this boot — an app restart —
    would read a power cut and follow whoever hands it a secret."""
    (boot_trace / "clean-stop").write_text("clean\n")
    monkeypatch.setattr(server_secret, "BOOT_FILE", boot_trace / "missing" / "started-boot")
    assert open_window_after_unclean_stop() is False

    monkeypatch.setattr(server_secret, "BOOT_FILE", boot_trace / "started-boot")

    assert open_window_after_unclean_stop() is False
    assert not server_secret.accepts_new_server()


def test_a_restart_on_the_same_boot_opens_no_window(server):
    """A crash of the app restarts it on the same boot with no marker: no
    power cut, so nothing to follow — else a request that crashes the app
    would open the window."""
    assert open_window_after_unclean_stop() is True

    assert open_window_after_unclean_stop() is False
    assert not server_secret.accepts_new_server()


def test_the_window_closes_after_its_minutes(client, server, monkeypatch):
    open_window_after_unclean_stop()
    later = server_secret.time.monotonic() + server_secret.READOPTION_WINDOW_S + 1
    monkeypatch.setattr(server_secret.time, "monotonic", lambda: later)

    response = _hand_over(client, REFLASHED_SECRET)

    assert response.status_code == 403
    assert server_secret.SECRET_FILE.read_text().strip() == SERVER_SECRET


def test_its_own_server_closes_the_window(client, server):
    """After a power cut the speaker's server, not reflashed, admits it again
    within seconds: from then on nothing is left to follow, and the window is
    no longer a door for whoever comes next."""
    open_window_after_unclean_stop()

    assert _hand_over(client, SERVER_SECRET).json()["data"] == {"changed": False}
    assert _hand_over(client, REFLASHED_SECRET).status_code == 403


def test_a_unit_that_holds_no_pin_opens_no_window(client, secret_file):
    """A fresh card's first start follows no clean stop either. Open there,
    the window would have the heartbeat ask the server for its secret, and a
    speaker merely waiting to be configured would pin it before anyone
    configured it. Its first pin stays the first contact, and the one after
    it is refused."""
    assert open_window_after_unclean_stop() is False
    assert not server_secret.accepts_new_server()

    assert _hand_over(client, SERVER_SECRET).json()["data"] == {"changed": True}
    assert _hand_over(client, REFLASHED_SECRET).status_code == 403


@pytest.mark.asyncio
async def test_two_new_servers_in_one_window_pin_one(server):
    open_window_after_unclean_stop()

    results = await asyncio.gather(
        admit(REFLASHED_SECRET, follow=True), admit(ANOTHER_SERVER_SECRET, follow=True),
        return_exceptions=True,
    )

    pinned = server_secret.SECRET_FILE.read_text().strip()
    assert pinned in (REFLASHED_SECRET, ANOTHER_SERVER_SECRET)
    loser = results[1] if pinned == REFLASHED_SECRET else results[0]
    assert isinstance(loser, ServerSecretRefused)


def test_a_pin_a_power_cut_corrupted_is_replaced_in_the_window(client, secret_file):
    """Outside the window an unreadable pin refuses everything; inside it the
    gesture that follows a new server is also the one that repairs it."""
    secret_file.write_bytes(b"\xff\xfe torn")
    open_window_after_unclean_stop()

    response = _hand_over(client, SERVER_SECRET)

    assert response.status_code == 200, response.text
    assert secret_file.read_text().strip() == SERVER_SECRET


def test_a_boot_it_cannot_read_opens_no_window(server, boot_trace):
    """Fails closed: with no boot id, a crash could not be told from a power cut."""
    (boot_trace / "boot_id").unlink()

    assert open_window_after_unclean_stop() is False


@pytest.mark.asyncio
async def test_the_heartbeat_says_while_the_window_is_open(server, monkeypatch, tmp_path):
    """The server hands its secret again to a speaker it knows refuses it only
    when the heartbeat says the speaker would follow it: said wrong, the gesture
    moves nothing, or every heartbeat of a foreign speaker is a hand-over."""
    from services import registration

    received = asyncio.Queue()

    async def register(request):
        await received.put(await request.json())
        return web.json_response({"status": "success", "server_addresses": []})

    app = web.Application()
    app.router.add_post(registration.REGISTER_ENDPOINT, register)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()

    net = tmp_path / "net" / "eth0"
    net.mkdir(parents=True)
    (net / "address").write_text("d8:3a:dd:68:e7:e4\n")
    monkeypatch.setenv("MILO_PRINCIPAL_IP", "127.0.0.1")
    monkeypatch.setattr(registration, "MILO_PRINCIPAL_PORT", runner.addresses[0][1])
    monkeypatch.setattr(registration, "SYSFS_NET", str(tmp_path / "net"))
    monkeypatch.setattr(registration, "HARDWARE_FILE", str(tmp_path / "hardware.json"))
    monkeypatch.setattr(registration, "IDENTITY_FILE", str(tmp_path / "identity.json"))
    monkeypatch.setattr(registration, "ASOUND_CARDS_FILE", str(tmp_path / "cards"))
    # The loop's clock: each wait (the 5 s settle, the 15 s heartbeat) lasts
    # until the test lets one heartbeat through.
    ticks = asyncio.Queue()

    async def next_tick(_seconds):
        await ticks.get()

    monkeypatch.setattr(registration, "asyncio", types.SimpleNamespace(
        sleep=next_tick, get_running_loop=asyncio.get_running_loop,
    ))

    open_window_after_unclean_stop()
    task = asyncio.create_task(registration.register_with_main_milo())
    try:
        ticks.put_nowait(None)
        in_window = await asyncio.wait_for(received.get(), timeout=10)
        await admit(SERVER_SECRET, follow=True)  # its own server's hand-over
        ticks.put_nowait(None)
        after = await asyncio.wait_for(received.get(), timeout=10)
    finally:
        task.cancel()
        await runner.cleanup()

    assert in_window["accepts_new_server"] is True
    assert after["accepts_new_server"] is False
