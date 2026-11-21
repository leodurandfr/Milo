"""
Tests for the satellite's boot-time registration with the main Milo.

What breaks when these fail: a literal `MILO_PRINCIPAL_IP` in the unit's env
file becomes inert again. That spelling exists for a LAN where mDNS does not
work, and a satellite set that way never appears as a pending speaker on the
server — with no error anywhere, because registration retries in silence
forever.

It also pins the way back where mDNS never answers: a router that filters
multicast leaves a restarted speaker looking for `milo.local` in vain, so the
addresses the server's heartbeat answer listed are kept on the card and tried
last. When that breaks, the speaker is silent after every restart on such a LAN.

The mocked boundary is `socket.getaddrinfo`, i.e. mDNS resolution, which cannot
run in CI. The server's port is a real listening socket on the loopback.
"""
import asyncio
import logging
import os
import socket
import types
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from aiohttp import web

from services import registration
from services.registration import MILO_PRINCIPAL_PORT, resolve_milo_principal


def _addrinfo(ip: str) -> list:
    """One getaddrinfo result tuple, in the shape the resolver indexes."""
    return [(2, 1, 6, "", (ip, MILO_PRINCIPAL_PORT))]


@pytest.mark.asyncio
async def test_literal_server_ip_is_used_without_resolving():
    """A literal IP in the env file reaches the app: no mDNS lookup is attempted.

    The defect this covers is not a wrong address but a lookup that should never
    happen — on such a LAN `milo.local` does not resolve at all.
    """
    with patch.dict(os.environ, {"MILO_PRINCIPAL_IP": "192.168.1.10"}, clear=True), \
         patch("services.registration.socket.getaddrinfo") as getaddrinfo:
        assert await resolve_milo_principal() == "192.168.1.10"
        getaddrinfo.assert_not_called()


@pytest.mark.asyncio
async def test_hostname_from_env_is_the_name_resolved():
    """The conversion stores the string "milo.local", not an IP.

    `milo-first-boot` writes the hostname on purpose, so the satellite survives
    the server moving between ethernet and WiFi — the resolver must accept it.
    """
    with patch.dict(os.environ, {"MILO_PRINCIPAL_IP": "milo.local"}, clear=True), \
         patch("services.registration.socket.getaddrinfo",
               return_value=_addrinfo("192.168.1.42")) as getaddrinfo:
        assert await resolve_milo_principal() == "192.168.1.42"
        assert getaddrinfo.call_args.args[0] == "milo.local"


@pytest.mark.asyncio
async def test_no_env_entry_falls_back_to_mdns():
    """A pi-gen-flashed satellite has no /var/lib/milo-client/env entry."""
    with patch.dict(os.environ, {}, clear=True), \
         patch("services.registration.socket.getaddrinfo",
               return_value=_addrinfo("192.168.1.42")) as getaddrinfo:
        assert await resolve_milo_principal() == "192.168.1.42"
        assert getaddrinfo.call_args.args[0] == "milo.local"


@pytest.mark.asyncio
async def test_unresolvable_target_names_itself_in_the_error():
    """The retry loop logs this message; it must say which address failed.

    A bare "Cannot resolve milo.local" on a unit installed with --server sends
    the operator hunting for an mDNS fault that is not the cause.
    """
    import socket as socket_module

    with patch.dict(os.environ, {"MILO_PRINCIPAL_IP": "milo-server.lan"}, clear=True), \
         patch("services.registration.socket.getaddrinfo",
               side_effect=socket_module.gaierror):
        with pytest.raises(RuntimeError, match="milo-server.lan"):
            await resolve_milo_principal()


def test_a_heartbeat_answer_keeps_the_servers_ipv4_addresses_in_order():
    """The watcher dials these in order when the stream's own address goes
    silent; an IPv6 entry would never match the launcher's IPv4 literal."""
    from services.registration import known_server_addresses, remember_server_addresses

    remember_server_addresses(["192.168.1.55", "fe80::1", "192.168.1.39"])
    assert known_server_addresses() == ("192.168.1.55", "192.168.1.39")


def _restart(monkeypatch):
    """What a new process of the app knows: nothing in memory, the card as it is."""
    monkeypatch.setattr(registration, "_server_addresses", None)
    monkeypatch.setattr(registration, "_stored", None)


def test_the_servers_addresses_outlive_a_restart(monkeypatch, server_addresses_file):
    """A restarted speaker on a LAN without mDNS has only these to find its
    server with: kept in memory alone, they were gone at the restart that needs them."""
    registration.remember_server_addresses(["192.168.1.55", "192.168.1.39"])
    _restart(monkeypatch)

    assert registration.known_server_addresses() == ("192.168.1.55", "192.168.1.39")
    # One per line: the launcher reads the same file with no JSON parser.
    assert server_addresses_file.read_text().split() == ["192.168.1.55", "192.168.1.39"]


def test_an_unchanged_answer_is_not_written_again(monkeypatch, server_addresses_file):
    """A heartbeat answers every 15 s: a write at each would wear the card out
    for nothing. Each write replaces the file (a new inode), so an unchanged
    inode is a write that did not happen."""
    answer = ["192.168.1.55", "192.168.1.39"]
    registration.remember_server_addresses(answer)
    first = server_addresses_file.stat().st_ino

    registration.remember_server_addresses(answer)
    _restart(monkeypatch)
    registration.remember_server_addresses(answer)
    assert server_addresses_file.stat().st_ino == first

    registration.remember_server_addresses(["192.168.1.39"])
    assert server_addresses_file.stat().st_ino != first
    assert server_addresses_file.read_text().split() == ["192.168.1.39"]


def test_a_card_that_cannot_hold_them_keeps_them_for_this_run(monkeypatch, server_addresses_file):
    """A full card costs the next restart its way back, never this run's."""
    monkeypatch.setattr(registration, "SERVER_ADDRESSES_FILE", server_addresses_file / "absent-dir" / "x")
    registration.remember_server_addresses(["192.168.1.55"])
    assert registration.known_server_addresses() == ("192.168.1.55",)


def test_a_card_that_stays_full_is_reported_once(monkeypatch, caplog):
    """Every heartbeat answer tries the write again, every 15 s: a warning at
    each would bury the journal for as long as the card stays full."""
    card = {"full": True, "tries": 0}
    replace = os.replace

    def _replace(src, dst):
        card["tries"] += 1
        if card["full"]:
            raise OSError(28, "No space left on device")
        replace(src, dst)

    monkeypatch.setattr("atomic_file.os.replace", _replace)
    caplog.set_level(logging.DEBUG, logger=registration.logger.name)

    def warnings():
        return [r for r in caplog.records if r.levelno >= logging.WARNING]

    for addresses in (["192.168.1.55"], ["192.168.1.39"], ["192.168.1.55"]):
        registration.remember_server_addresses(addresses)
    assert card["tries"] == 3, "a failed write is not tried again at the next answer"
    assert len(warnings()) == 1

    card["full"] = False
    registration.remember_server_addresses(["192.168.1.39"])
    card["full"] = True
    registration.remember_server_addresses(["192.168.1.55"])
    assert len(warnings()) == 2, "a new outage, after a write that succeeded, is reported again"


@contextmanager
def _server_listening():
    """The main Milo's port, accepting on 127.0.0.1 only: 127.0.0.2 refuses it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with patch.object(registration, "MILO_PRINCIPAL_PORT", listener.getsockname()[1]):
            yield


ANSWERS, SILENT = "127.0.0.1", "127.0.0.2"


@contextmanager
def _no_mdns():
    with patch.dict(os.environ, {"MILO_PRINCIPAL_IP": "milo.local"}, clear=True), \
         patch("services.registration.socket.getaddrinfo",
               side_effect=socket.gaierror("Name or service not known")):
        yield


@pytest.mark.asyncio
async def test_without_mdns_the_first_known_address_that_answers_is_the_server(server_addresses_file):
    """The order is the server's (wired first), but a silent address is passed
    over: a server whose cable went answers on its WiFi."""
    server_addresses_file.write_text(f"{SILENT}\n{ANSWERS}\n")
    with _server_listening(), _no_mdns():
        assert await resolve_milo_principal() == ANSWERS


@pytest.mark.asyncio
@pytest.mark.parametrize("listed", [("127.0.0.1", "127.0.0.3"), ("127.0.0.3", "127.0.0.1")])
async def test_without_mdns_the_wired_address_comes_first_when_both_answer(
        server_addresses_file, listed):
    """Both answering, the first listed is taken: the owner's Ethernet first."""
    server_addresses_file.write_text("".join(f"{address}\n" for address in listed))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("0.0.0.0", 0))
        listener.listen()
        with patch.object(registration, "MILO_PRINCIPAL_PORT", listener.getsockname()[1]), _no_mdns():
            assert await resolve_milo_principal() == listed[0]


@pytest.mark.asyncio
async def test_without_mdns_and_no_known_address_answering_nothing_is_found(server_addresses_file):
    """A server that is off is not found at an address it no longer answers:
    the watcher reads a found address as a move and restarts the room."""
    server_addresses_file.write_text(f"{SILENT}\n")
    with _server_listening(), _no_mdns():
        with pytest.raises(RuntimeError, match="milo.local"):
            await resolve_milo_principal()


@pytest.mark.asyncio
async def test_a_name_that_resolves_is_preferred_to_the_known_addresses(server_addresses_file):
    """The known addresses come last: where mDNS answers, it decides, as it
    always did — the watcher's Ethernet-first rule reads its answer."""
    server_addresses_file.write_text(f"{ANSWERS}\n")
    with _server_listening(), \
         patch.dict(os.environ, {"MILO_PRINCIPAL_IP": "milo.local"}, clear=True), \
         patch("services.registration.socket.getaddrinfo", return_value=_addrinfo("192.168.1.42")):
        assert await resolve_milo_principal() == "192.168.1.42"


@pytest.mark.asyncio
async def test_a_restarted_speaker_without_mdns_registers_at_its_last_known_address(
        monkeypatch, tmp_path, server_addresses_file):
    """The defect itself: a speaker restarted on a LAN that filters multicast
    looked for milo.local forever and never reached its server again."""
    received = asyncio.Queue()

    async def register(request):
        await received.put(await request.json())
        return web.json_response({"status": "success", "server_addresses": [ANSWERS]})

    app = web.Application()
    app.router.add_post(registration.REGISTER_ENDPOINT, register)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, ANSWERS, 0).start()

    server_addresses_file.write_text(f"{SILENT}\n{ANSWERS}\n")  # what the last run kept
    net = tmp_path / "net" / "eth0"
    net.mkdir(parents=True)
    (net / "address").write_text("d8:3a:dd:68:e7:e4\n")
    monkeypatch.setattr(registration, "MILO_PRINCIPAL_PORT", runner.addresses[0][1])
    monkeypatch.setattr(registration, "SYSFS_NET", str(tmp_path / "net"))
    monkeypatch.setattr(registration, "HARDWARE_FILE", str(tmp_path / "hardware.json"))
    monkeypatch.setattr(registration, "IDENTITY_FILE", str(tmp_path / "identity.json"))
    monkeypatch.setattr(registration, "ASOUND_CARDS_FILE", str(tmp_path / "cards"))

    async def no_wait(_seconds):
        await asyncio.sleep(0.01)

    monkeypatch.setattr(registration, "asyncio", types.SimpleNamespace(
        **{**{name: getattr(asyncio, name) for name in asyncio.__all__}, "sleep": no_wait},
    ))
    with _no_mdns():
        task = asyncio.create_task(registration.register_with_main_milo())
        try:
            heartbeat = await asyncio.wait_for(received.get(), timeout=10)
            await asyncio.wait_for(received.get(), timeout=10)  # the answer was taken in
        finally:
            task.cancel()
            await runner.cleanup()

    assert heartbeat["mac_id"] == "d8:3a:dd:68:e7:e4"
    assert server_addresses_file.read_text().split() == [ANSWERS], \
        "the answer did not replace the addresses kept on the card"


# /proc/asound/cards as a satellite with a HAT and the image's loopbacks prints
# it: the id padded inside its brackets, a continuation line under each card.
_ASOUND_CARDS = """\
 0 [sndrpihifiberry]: HifiberryDacp - snd_rpi_hifiberry_dacplus
                      snd_rpi_hifiberry_dacplus
 1 [Loopback       ]: Loopback - Loopback
                      Loopback 1
"""


def test_the_alsa_card_ids_are_read_from_proc_asound(tmp_path):
    """The server tells a seated board from a missing one by these ids alone; a
    padded id read with its spaces would never match the one it expects."""
    from services import registration

    cards = tmp_path / "cards"
    cards.write_text(_ASOUND_CARDS)
    with patch.object(registration, "ASOUND_CARDS_FILE", str(cards)):
        assert registration.read_alsa_cards() == ["sndrpihifiberry", "Loopback"]


def test_a_unit_with_no_card_reports_an_empty_list(tmp_path):
    """No card is an answer — the board is not seen — unlike an unreadable file."""
    from services import registration

    cards = tmp_path / "cards"
    cards.write_text("--- no soundcards ---\n")
    with patch.object(registration, "ASOUND_CARDS_FILE", str(cards)):
        assert registration.read_alsa_cards() == []


def test_an_unreadable_card_list_is_none(tmp_path):
    """None tells the server it knows nothing, so it reports no missing board."""
    from services import registration

    with patch.object(registration, "ASOUND_CARDS_FILE", str(tmp_path / "absent")):
        assert registration.read_alsa_cards() is None
