"""Reading and writing `/etc/snapserver.conf`, and the restart that follows.

What breaks when these fail: this is the write half of the network-preset
resource — the one endpoint on the appliance that edits a file under `/etc` and
then restarts a daemon whose restart **cuts the sound in every room**. Measured
2026-08-27, all four steps ran at 0 %: `_read_snapserver_conf`,
`_modify_config_content`, `_update_config_file` and `_restart_snapserver` are 68
of this module's 93 uncovered lines, and B1 closed with them named and untouched.

The read half already paid for its own defect (B1-1: the daemon nests streams
under `"server"`, so the merge collapsed to the file and the settings page
reported a preset snapserver was not running). The write half carries three
invariants nothing was holding:

* **the argv is the contract.** `sudo milo-deploy-update write-config <tmp> <dest>`
  is pinned verbatim by `/etc/sudoers.d/milo-backend`; the `milo` user cannot
  write `/etc` any other way, and a changed verb or argument order is a real
  permission denial on the unit and nowhere else.
* **a failed write must not reach the restart.** The file lands on disk *before*
  snapserver is asked to reload it, so an accepted write followed by a refused
  restart already leaves the new preset on disk with the old one playing. A
  write that failed and restarted anyway would cut every room for nothing.
* **32-bit is forced, whatever the caller asked for.** `sampleformat` is the
  pipeline's own format, not a preference.

Every subprocess spawn in this file is replaced, not observed: this checkout is
the appliance, `DEPLOY_UPDATE_CMD` is a real root-owned helper, and the systemd
manager here restarts the live snapserver. `snapserver_conf` is pointed at the
pytest tmpdir for the same reason.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config.constants import DEPLOY_UPDATE_CMD
from backend.core.multiroom.snapcast import SnapcastRequestError, SnapcastService

LIVE_CONF = """\
# Snapserver configuration
[stream]
default_source = Multiroom
buffer = 700
codec = flac
chunk_ms = 40
sampleformat = 48000:32:2
# chunk_ms = 10   ; documented alternative, not in force
source = meta:///Bluetooth/ROC/Spotify?name=Multiroom
source = alsa:///?name=Bluetooth&device=hw:1,1,1&idle_threshold=5000
source = alsa:///?name=ROC&device=hw:1,1,2&idle_threshold=5000

[http]
enabled = true
bind_to_address = 0.0.0.0
port = 1780
doc_root = /usr/share/snapserver/snapweb/

[server]
threads = 4
"""


class SpawnedPastTheGuards(BaseException):
    """A real subprocess was about to run.

    BaseException-derived: `_update_config_file` and `_restart_snapserver` are
    both wrapped in `@handle_errors`, which catches `Exception` and returns the
    declared default — an ordinary error would leave the run green with a real
    `sudo` having been attempted.
    """


class FakeProc:
    """Stands in for the deploy wrapper, and records how it was invoked."""

    def __init__(self, returncode=0, stderr=b""):
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self):
        return b"", self._stderr


@pytest.fixture
def systemd():
    manager = MagicMock()
    manager.restart = AsyncMock(return_value=True)
    return manager


@pytest.fixture
def service(systemd, tmp_path, monkeypatch):
    """The service with its conf redirected and every spawn wired to explode."""

    def _never(*args, **kwargs):
        raise SpawnedPastTheGuards(f"a subprocess was spawned past the doubles: {args}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _never)
    svc = SnapcastService(systemd_manager=systemd)
    svc.snapserver_conf = tmp_path / "snapserver.conf"
    svc.snapserver_conf.write_text(LIVE_CONF)
    return svc


@pytest.fixture
def staging(tmp_path, monkeypatch):
    """Redirect the one hard-coded path in this module onto the pytest tmpdir.

    Carried on the path, not on the module: `snapcast.py` imports `aiofiles`
    plainly, so replacing its `open` outright would replace it for every other
    writer in the process. Everything but the staging file is delegated to the
    real primitive.
    """
    import aiofiles

    real_open = aiofiles.open
    staged = tmp_path / "snapserver.conf.tmp"

    def _open(path, *args, **kwargs):
        if str(path) == "/tmp/snapserver.conf.tmp":
            return real_open(staged, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("backend.core.multiroom.snapcast.aiofiles.open", _open)
    return staged


@pytest.fixture
def deploy(monkeypatch):
    """Replace the privileged write with a double that records its argv."""
    calls = []
    outcome = {"proc": FakeProc()}

    async def _exec(*args, **kwargs):
        calls.append(args)
        return outcome["proc"]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    return calls, outcome


class TestReadingTheConfFile:
    """The parser. Its `buffer` is the only place `buffer_ms` exists at all —
    snapserver's JSON-RPC does not expose it."""

    async def test_it_reads_the_stream_section_the_settings_page_shows(self, service):
        parsed = (await service._read_snapserver_conf())["parsed_config"]

        assert parsed["stream"]["buffer"] == "700"
        assert parsed["stream"]["codec"] == "flac"
        assert parsed["stream"]["chunk_ms"] == "40"
        assert parsed["stream"]["sampleformat"] == "48000:32:2"

    async def test_every_source_line_is_kept_not_just_the_last(self, service):
        """`[stream]` carries one `source =` per audio source — eleven on this
        appliance. Storing them as a plain key would keep one and lose the rest,
        which is the list `install/snapcast.sh` builds the whole ALSA loopback
        map from."""
        parsed = (await service._read_snapserver_conf())["parsed_config"]

        assert parsed["stream"]["sources"] == [
            "meta:///Bluetooth/ROC/Spotify?name=Multiroom",
            "alsa:///?name=Bluetooth&device=hw:1,1,1&idle_threshold=5000",
            "alsa:///?name=ROC&device=hw:1,1,2&idle_threshold=5000",
        ]

    async def test_a_value_containing_an_equals_sign_survives_intact(self, service):
        """Every `source` line is a URI with a query string. Splitting on every
        `=` instead of the first would raise on each of them, and the whole parse
        is fail-open — the settings page would silently show defaults."""
        service.snapserver_conf.write_text(
            "[stream]\nsource = alsa:///?name=CD&device=hw:1,1,7&idle_threshold=5000\n"
        )

        parsed = (await service._read_snapserver_conf())["parsed_config"]

        assert parsed["stream"]["sources"] == [
            "alsa:///?name=CD&device=hw:1,1,7&idle_threshold=5000"
        ]

    async def test_sections_are_kept_apart(self, service):
        """`[http]` and `[server]` hold keys with no overlap today, but the
        writer's whole guard is "am I inside [stream]" — a parser that flattened
        them would make the reader disagree with the writer about scope."""
        parsed = (await service._read_snapserver_conf())["parsed_config"]

        assert parsed["http"]["port"] == "1780"
        assert parsed["server"]["threads"] == "4"
        assert "port" not in parsed["stream"]

    async def test_a_commented_key_inside_a_section_is_not_a_setting(self, service):
        """`install/snapcast.sh` leaves documented alternatives in the file. Read
        as live values they would go straight into the settings page, and the
        page's own write path would then make them real."""
        parsed = (await service._read_snapserver_conf())["parsed_config"]

        assert parsed["stream"]["chunk_ms"] == "40"
        assert not any(k.startswith("#") for k in parsed["stream"])
        assert "" not in parsed

    async def test_a_key_before_any_section_is_ignored_not_fatal(self, service):
        """The parse is fail-open as a whole, so anything that raises inside it
        costs the *entire* file — `buffer_ms` included, which exists nowhere
        else. A hand-edited conf with a stray leading key must cost that key."""
        service.snapserver_conf.write_text("orphan = 1\n" + LIVE_CONF)

        parsed = (await service._read_snapserver_conf())["parsed_config"]

        assert parsed["stream"]["buffer"] == "700"
        assert "orphan" not in parsed

    async def test_a_fresh_image_without_the_file_is_not_an_error(self, service, caplog):
        """`GET /api/routing/snapcast/server-config` runs on every settings-page
        open. Reaching the fail-open catch instead of the guard turns each one
        into an ERROR line, which is what raises the UI's fault banner."""
        service.snapserver_conf.unlink()

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.snapcast"):
            assert await service._read_snapserver_conf() == {}

        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_the_raw_text_is_returned_alongside_the_parse(self, service):
        """`_update_config_file` rewrites the file it read; the parse alone
        cannot reproduce comments, ordering or the source list."""
        result = await service._read_snapserver_conf()

        assert result["raw_content"] == LIVE_CONF

    async def test_a_missing_file_is_an_empty_parse_not_a_failure(self, service):
        """A fresh image before `install/snapcast.sh` has run. The read is
        fail-open by design so the settings page loads with defaults."""
        service.snapserver_conf.unlink()

        assert await service._read_snapserver_conf() == {}


class TestRewritingTheStreamSection:
    """`_modify_config_content`: what actually changes on disk."""

    def test_the_wire_name_and_the_file_name_are_bridged(self, service):
        """The conf calls it `buffer`, the API calls it `buffer_ms`. They are one
        setting, and the mapping lives only here."""
        out = service._modify_config_content(LIVE_CONF, {"buffer_ms": 1500})

        assert "buffer = 1500" in out
        assert "buffer = 700" not in out

    def test_the_sample_format_is_forced_regardless_of_what_was_asked(self, service):
        """32-bit is the pipeline's format, not a user preference: CamillaDSP and
        every ALSA loopback leg are configured for it."""
        out = service._modify_config_content(LIVE_CONF, {"sampleformat": "44100:16:2"})

        assert "sampleformat = 48000:32:2" in out
        assert "44100:16:2" not in out

    def test_keys_outside_the_stream_section_are_left_alone(self, service):
        """`[http]` is what serves snapweb on 0.0.0.0:1780 and `[server]` sets the
        thread count. A writer that matched by key name anywhere in the file
        would rewrite them from a stream payload."""
        content = LIVE_CONF.replace("[server]\nthreads = 4", "[server]\ncodec = 4")

        out = service._modify_config_content(content, {"codec": "opus"})

        assert "[stream]" in out and "codec = opus" in out
        assert out.split("[server]")[1].strip() == "codec = 4"

    def test_a_commented_key_is_not_revived(self, service):
        """`install/snapcast.sh` leaves documentation lines in the file; turning
        one into a live setting would change behaviour nobody asked for."""
        content = LIVE_CONF.replace("buffer = 700", "# buffer = 700")

        out = service._modify_config_content(content, {"buffer_ms": 1500})

        assert "# buffer = 700" in out
        assert "buffer = 1500" not in out

    def test_everything_the_payload_does_not_name_is_preserved_byte_for_byte(self, service):
        """The eleven `source =` lines are the ALSA loopback map. Losing one
        removes a whole audio source from multiroom."""
        out = service._modify_config_content(LIVE_CONF, {"chunk_ms": 20})

        assert out.count("source = ") == LIVE_CONF.count("source = ")
        assert "default_source = Multiroom" in out
        assert out.splitlines()[0] == "# Snapserver configuration"

    def test_a_key_absent_from_the_file_is_silently_dropped(self, service):
        """Measured limit, recorded rather than fixed (B1-9).

        The writer only rewrites lines that already exist, so a setting with no
        line in `[stream]` is lost while `update_server_config` returns success
        and restarts snapserver anyway. Latent on this fleet only because
        `install/snapcast.sh` writes all four keys — an image that omitted one,
        or a fifth setting added to the payload, would fail exactly this way.
        """
        content = LIVE_CONF.replace("chunk_ms = 40\n", "")

        out = service._modify_config_content(content, {"chunk_ms": 20})

        live = [ln for ln in out.splitlines() if ln.strip().startswith("chunk_ms")]
        assert live == []


class TestTheWriteToEtc:
    """`_update_config_file`: the one privileged write on this path."""

    async def test_the_deploy_wrapper_is_called_with_the_argv_sudoers_pins(
        self, service, deploy, staging
    ):
        """Invariant 1. The `milo` user's only route to `/etc/snapserver.conf` is
        `NOPASSWD` on this exact command; a changed verb or a reordered pair is a
        permission denial that reproduces on a unit and nowhere else."""
        calls, _ = deploy

        assert await service._update_config_file({"buffer_ms": 1500}) is True

        assert len(calls) == 1
        assert calls[0] == (
            "sudo", DEPLOY_UPDATE_CMD,
            "write-config", "/tmp/snapserver.conf.tmp", str(service.snapserver_conf),
        )

    async def test_the_staged_file_carries_the_modified_content(
        self, service, deploy, staging
    ):
        """The wrapper copies the staging file verbatim, so the whole edit has to
        be in it. A deploy of the file as it was read succeeds, restarts
        snapserver, and changes nothing — with the settings page reporting the
        new preset."""
        calls, _ = deploy

        assert await service._update_config_file({"buffer_ms": 180, "codec": "pcm"}) is True

        staged = staging.read_text()
        assert "buffer = 180" in staged
        assert "codec = pcm" in staged
        assert calls[0][3] == "/tmp/snapserver.conf.tmp"

    async def test_a_refused_deploy_is_a_failure_and_says_why(
        self, service, deploy, staging, caplog
    ):
        """The wrapper refuses a destination outside its whitelist. Reporting
        success would have the caller restart snapserver for a file that never
        changed — every room silent, nothing different afterwards."""
        calls, outcome = deploy
        outcome["proc"] = FakeProc(returncode=1, stderr=b"refused: destination not allowed")

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.snapcast"):
            assert await service._update_config_file({"buffer_ms": 1500}) is False

        assert any("destination not allowed" in r.message for r in caplog.records)

    async def test_a_missing_conf_file_is_refused_before_anything_is_staged(
        self, service, deploy, staging, caplog
    ):
        """Writing a file the appliance does not have would create one snapserver
        never reads, from a template that is not the installer's."""
        service.snapserver_conf.unlink()
        calls, _ = deploy

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.snapcast"):
            assert await service._update_config_file({"buffer_ms": 1500}) is False

        assert calls == []
        assert not staging.exists()
        assert any("not found" in r.message for r in caplog.records)


class TestTheRestart:
    """`_restart_snapserver`: the step that cuts the sound in every room."""

    async def test_it_settles_before_probing_and_only_then_reports_success(
        self, service, systemd, monkeypatch
    ):
        """The old instance's socket answers `Server.GetRPCVersion` while it is
        going down, so probing immediately would report the *old* daemon as the
        restarted one — the settings page then shows a preset that is not
        running, which is the exact confusion B1-1 was."""
        order = []
        waits = []

        async def _sleep(delay, *a, **k):
            waits.append(delay)
            order.append("settle")

        systemd.restart = AsyncMock(side_effect=lambda unit: order.append("restart") or True)
        service.is_available = AsyncMock(side_effect=lambda: order.append("probe") or True)
        monkeypatch.setattr(asyncio, "sleep", _sleep)

        assert await service._restart_snapserver() is True

        assert order == ["restart", "settle", "probe"]
        assert waits == [3]

    async def test_the_unit_restarted_is_the_multiroom_one(self, service, systemd, monkeypatch):
        """`milo-snapserver-multiroom.service` has no `WantedBy`: its lifecycle
        belongs solely to `AudioRoutingService._sync_snapcast_state`, and its name
        is what `/etc/sudoers.d/milo-backend` grants."""
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        service.is_available = AsyncMock(return_value=True)

        await service._restart_snapserver()

        systemd.restart.assert_awaited_once_with("milo-snapserver-multiroom.service")

    async def test_a_refused_restart_never_reaches_the_probe(
        self, service, systemd, monkeypatch
    ):
        """systemd refusing means the old daemon is still playing. Probing would
        find it, answer "available", and report a restart that did not happen."""
        systemd.restart = AsyncMock(return_value=False)
        service.is_available = AsyncMock(return_value=True)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        assert await service._restart_snapserver() is False
        service.is_available.assert_not_called()

    async def test_a_daemon_that_never_comes_back_is_reported_not_claimed(
        self, service, systemd, monkeypatch, caplog
    ):
        """Every room is silent at this point. Returning True would end the
        request with a green banner over a fleet with no server."""
        clock = {"t": 500.0}
        service.is_available = AsyncMock(return_value=False)

        async def _sleep(delay, *a, **k):
            clock["t"] += delay

        monkeypatch.setattr(asyncio, "sleep", _sleep)
        monkeypatch.setattr("backend.core.multiroom.snapcast.time.monotonic", lambda: clock["t"])

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.snapcast"):
            assert await service._restart_snapserver() is False

        assert any("API not available" in r.message for r in caplog.records)


class TestWaitingForTheDaemon:
    """`wait_until_available`: a wall-clock deadline, not a probe count."""

    async def test_the_wait_is_capped_in_wall_clock_not_in_attempts(
        self, service, monkeypatch
    ):
        """Each probe carries its own 3 s request timeout, so a loop counting
        attempts would wait several times the timeout against a hung snapserver —
        with the whole fleet already silent and an HTTP request held open."""
        clock = {"t": 1000.0}
        probes = []

        async def _probe():
            probes.append(clock["t"])
            clock["t"] += 3.0  # the request timeout a hung daemon costs
            return False

        async def _sleep(delay, *a, **k):
            clock["t"] += delay

        service.is_available = _probe
        monkeypatch.setattr("backend.core.multiroom.snapcast.time.monotonic", lambda: clock["t"])
        monkeypatch.setattr(asyncio, "sleep", _sleep)

        assert await service.wait_until_available(timeout=10.0) is False

        assert clock["t"] - 1000.0 <= 13.0, "the wait ran past its deadline plus one probe"
        assert len(probes) == 3

    async def test_it_returns_as_soon_as_the_daemon_answers(self, service, monkeypatch):
        answers = [False, True]
        service.is_available = AsyncMock(side_effect=answers)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        assert await service.wait_until_available(timeout=10.0) is True
        assert service.is_available.await_count == 2

    async def test_a_probe_failure_is_not_availability(self, service, caplog):
        """`_request` raises a typed error on every transport failure so
        `is_available` can tell an unreachable daemon from one that answered an
        empty result — a bare `bool(result)` on a swallowed {} could not.

        The catch is the typed error alone, deliberately: anything else escaping
        here is a fault in Milō, not in snapserver, and must not read as "the
        daemon is down".
        """
        service._request = AsyncMock(return_value={})
        assert await service.is_available() is True, (
            "an empty result is a live daemon, not an unavailable one"
        )

        service._request = AsyncMock(side_effect=SnapcastRequestError("refused"))

        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.snapcast"):
            assert await service.is_available() is False

        assert any("availability check failed" in r.message for r in caplog.records)

        service._request = AsyncMock(side_effect=TypeError("a bug in Milō"))
        with pytest.raises(TypeError):
            await service.is_available()


class TestTheWholeUpdate:
    """`update_server_config`: validate, force, write, restart — in that order."""

    async def test_a_rejected_body_writes_nothing_and_restarts_nothing(
        self, service, deploy, systemd
    ):
        """An unknown key means a body in the wrong shape; accepting it wrote
        nothing, returned success and restarted snapserver anyway — the only way
        this endpoint can lie."""
        calls, _ = deploy

        assert await service.update_server_config({"buffer_ms": 700, "bogus": 1}) is False

        assert calls == []
        systemd.restart.assert_not_called()

    async def test_a_write_that_failed_never_restarts_the_server(self, service, systemd):
        """The restart cuts every room. Doing it after a failed write costs the
        silence and changes nothing."""
        service._update_config_file = AsyncMock(return_value=False)

        assert await service.update_server_config({"buffer_ms": 700}) is False

        systemd.restart.assert_not_called()

    async def test_the_sample_format_is_pinned_before_the_file_is_touched(self, service):
        """The caller's value never reaches the writer, whatever it sent."""
        seen = {}
        service._update_config_file = AsyncMock(side_effect=lambda cfg: seen.update(cfg) or True)
        service._restart_snapserver = AsyncMock(return_value=True)

        assert await service.update_server_config(
            {"buffer_ms": 700, "sampleformat": "44100:16:2"}
        ) is True

        assert seen["sampleformat"] == "48000:32:2"

    async def test_the_answer_is_the_restarts_answer(self, service):
        """A write that landed with a daemon that did not come back is exactly
        the state where the settings page must not confirm the new preset: the
        file on disk and the daemon playing now disagree."""
        service._update_config_file = AsyncMock(return_value=True)
        service._restart_snapserver = AsyncMock(return_value=False)

        assert await service.update_server_config({"buffer_ms": 700}) is False


class TestReadingTheClientList:
    """`_parse_clients`: which snapserver entries become speakers in the UI."""

    @staticmethod
    def _status(clients):
        return {"server": {"groups": [{"clients": clients}]}}

    @staticmethod
    def _entry(*, client_id, name, ip, host="milo-client-x", connected=True, age=0.0):
        import time as _t

        return {
            "id": client_id,
            "connected": connected,
            "config": {"name": name, "volume": {"percent": 100, "muted": False}},
            "host": {"name": host, "ip": f"::ffff:{ip}", "mac": client_id},
            "lastSeen": {"sec": _t.time() - age},
        }

    def test_a_disconnected_entry_is_absent_not_flagged(self, service):
        """Absence is how `_process_disconnected_clients` detects a departure.
        Returning it with a flag would keep it in every caller's list."""
        parsed = service._parse_clients(self._status([
            self._entry(client_id="aa:bb:cc:dd:ee:01", name="Canapé", ip="192.168.1.153",
                        connected=False),
        ]))

        assert parsed == []

    def test_a_disconnected_entry_is_dropped_before_it_can_cost_the_whole_list(
        self, service
    ):
        """The early `continue` is not redundant with the freshness rule below it.

        `compute_mac_id` raises for an entry announcing no usable id, and this
        parse has no catch of its own — the failure surfaces as `get_clients`'
        fail-open [], i.e. as "every speaker vanished". Skipping a disconnected
        entry before the identity is computed is what keeps one stale record in
        snapserver from emptying the room list of the clients that are playing.
        """
        parsed = service._parse_clients(self._status([
            self._entry(client_id="", name="Ghost", ip="192.168.1.99", connected=False),
            self._entry(client_id="aa:bb:cc:dd:ee:01", name="Canapé", ip="192.168.1.153",
                        age=1.0),
        ]))

        assert [c["mac_id"] for c in parsed] == ["aa:bb:cc:dd:ee:01"]

    def test_snapservers_own_web_client_is_not_a_speaker(self, service):
        """snapweb is served on 0.0.0.0:1780 and registers as a client whenever
        anyone on the LAN opens it. Admitting it would put a phantom speaker in
        the room list — and every admission path would then push volume, EQ and
        buffer config at a browser tab."""
        parsed = service._parse_clients(self._status([
            self._entry(client_id="ff:ff:ff:ff:ff:01", name="Snapweb client",
                        ip="192.168.1.20"),
        ]))

        assert parsed == []

    def test_a_stale_local_entry_is_skipped_and_named(self, service, monkeypatch, caplog):
        """A loopback client whose id is not this host's MAC is the previous
        snapclient still registered after a hostID change. Admitting it creates a
        second "local" identity, and the local client is the one that owns
        equalizer.json — the wrong one would be driven over HTTP instead."""
        monkeypatch.setattr(
            "backend.core.multiroom.identity.get_local_mac", lambda: "aa:bb:cc:dd:ee:99"
        )

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.snapcast"):
            parsed = service._parse_clients(self._status([
                self._entry(client_id="00:11:22:33:44:55", name="Milō", ip="127.0.0.1"),
            ]))

        assert parsed == []
        assert any("stale local client" in r.message for r in caplog.records)

    def test_the_local_client_is_online_however_old_its_last_seen_is(
        self, service, monkeypatch
    ):
        """It plays through the loopback and does not stamp lastSeen the way a
        network client does. Applying the freshness rule to it would drop this
        appliance's own speaker out of its own UI after 20 seconds."""
        monkeypatch.setattr(
            "backend.core.multiroom.identity.get_local_mac", lambda: "aa:bb:cc:dd:ee:99"
        )

        parsed = service._parse_clients(self._status([
            self._entry(client_id="aa:bb:cc:dd:ee:99", name="Milō", ip="127.0.0.1",
                        age=3600.0),
        ]))

        assert [c["mac_id"] for c in parsed] == ["aa:bb:cc:dd:ee:99"]

    def test_a_remote_client_that_stopped_stamping_is_dropped(self, service):
        """snapserver keeps `connected: true` forever for a satellite that
        vanished without a TCP FIN — a power cut or a Wi-Fi drop. lastSeen is the
        only evidence, and this is where it is read."""
        parsed = service._parse_clients(self._status([
            self._entry(client_id="aa:bb:cc:dd:ee:01", name="Canapé", ip="192.168.1.153",
                        age=service.LAST_SEEN_FRESHNESS_S + 5),
        ]))

        assert parsed == []

    def test_a_recently_seen_remote_client_is_kept(self, service):
        """The other half of the same rule: a healthy client stamps ~1×/s."""
        parsed = service._parse_clients(self._status([
            self._entry(client_id="aa:bb:cc:dd:ee:01", name="Canapé", ip="192.168.1.153",
                        age=1.0),
        ]))

        assert [c["ip"] for c in parsed] == ["192.168.1.153"]


class TestTheCommandsAndQueries:
    """The thin JSON-RPC calls, where the method name and the body are the contract."""

    async def test_muting_a_client_preserves_the_level_it_is_muted_at(self, service):
        """Snapserver has one call for both, so a mute that forgot the percent
        would set it to snapserver's default — and unmuting later would come back
        at the wrong level, on a stage that is supposed to be a passthrough."""
        service._request = AsyncMock(return_value={})

        assert await service.set_mute("aa:bb:cc:dd:ee:01", True, volume=100) is True

        service._request.assert_awaited_once_with("Client.SetVolume", {
            "id": "aa:bb:cc:dd:ee:01",
            "volume": {"percent": 100, "muted": True},
        })

    async def test_a_mute_that_fails_is_reported_as_false_not_raised(self, service):
        """Its callers are admission paths that must keep going for the other
        clients; the boolean is what decides whether this one is retried."""
        service._request = AsyncMock(side_effect=SnapcastRequestError("unreachable"))

        assert await service.set_mute("aa:bb:cc:dd:ee:01", True) is False

    async def test_the_client_list_comes_from_the_server_status(self, service):
        """One RPC, then the same parse the reconcile sweep uses — so both agree
        on which clients are live."""
        status = {"server": {"groups": []}}
        service._request = AsyncMock(return_value=status)
        service.extract_clients = MagicMock(return_value=["parsed"])

        assert await service.get_clients() == ["parsed"]

        service._request.assert_awaited_once_with("Server.GetStatus")
        service.extract_clients.assert_called_once_with(status)

    async def test_an_unreachable_server_flattens_to_an_empty_list(self, service):
        """Fail-open by design, and the reason `extract_clients` is public: a
        caller that would read [] as "every client vanished" has to fetch the
        status itself."""
        service._request = AsyncMock(side_effect=SnapcastRequestError("down"))

        assert await service.get_clients() == []

    async def test_the_status_query_is_fail_open_too(self, service):
        """Its callers guard on an empty dict; propagating would take down the
        connect sweep and the reconcile loop with it."""
        service._request = AsyncMock(side_effect=SnapcastRequestError("down"))

        assert await service.get_server_status() == {}


class ReachedTheLiveSnapserver(BaseException):
    """A real HTTP session to snapserver's control port was being built.

    BaseException-derived: `_request` converts every `Exception` into a
    `SnapcastRequestError`, and each of its callers is fail-open — an ordinary
    error would be absorbed twice over and the run would stay green with
    `http://localhost:1780/jsonrpc` actually dialled. snapserver answers there
    on this machine.
    """


class TestTheTransport:
    """`_request`: the error taxonomy every fail-open caller above depends on."""

    @pytest.fixture(autouse=True)
    def never_the_real_control_port(self, monkeypatch):
        import aiohttp

        class _Forbidden:
            def __init__(self, *a, **k):
                raise ReachedTheLiveSnapserver("a real session to snapserver was opening")

        monkeypatch.setattr(aiohttp, "ClientSession", _Forbidden)

    @staticmethod
    def _session(monkeypatch, *, status=200, payload=None, delay=0.0):
        import aiohttp

        captured = {}

        class _Response:
            def __init__(self):
                self.status = status

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def json(self):
                return payload if payload is not None else {"result": {}}

        class _Session:
            def __init__(self, *a, **k):
                captured["timeout"] = k.get("timeout")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def post(self, url, json=None):
                captured["url"] = url
                captured["body"] = json
                return _Response()

        monkeypatch.setattr(aiohttp, "ClientSession", _Session)
        return captured

    async def test_the_request_is_json_rpc_with_a_fresh_id_each_time(
        self, service, monkeypatch
    ):
        """snapserver matches replies to requests by id, and this service shares
        the daemon with snapweb; a frozen id makes two calls indistinguishable."""
        captured = self._session(monkeypatch)

        await service._request("Server.GetStatus")
        first = captured["body"]["id"]
        await service._request("Client.SetVolume", {"id": "x"})

        assert captured["url"] == service.base_url
        assert captured["body"]["jsonrpc"] == "2.0"
        assert captured["body"]["method"] == "Client.SetVolume"
        assert captured["body"]["params"] == {"id": "x"}
        assert captured["body"]["id"] == first + 1

    async def test_a_call_without_parameters_sends_no_params_key(
        self, service, monkeypatch
    ):
        """`Server.GetStatus` and `Server.GetRPCVersion` take none, and snapserver
        rejects a null params on some builds."""
        captured = self._session(monkeypatch)

        await service._request("Server.GetStatus")

        assert "params" not in captured["body"]

    async def test_the_request_is_bounded_so_a_hung_daemon_cannot_hold_a_caller(
        self, service, monkeypatch
    ):
        """`is_available` is polled in a loop whose deadline assumes each probe
        costs at most this; without it, one hung call holds the settings request,
        the routing transition, and the reconcile sweep."""
        captured = self._session(monkeypatch)

        await service._request("Server.GetStatus")

        assert captured["timeout"].total == 3

    async def test_a_non_200_is_a_typed_failure_not_an_empty_result(
        self, service, monkeypatch
    ):
        """The distinction the whole module rests on: a fail-open caller must be
        able to tell "snapserver refused" from "snapserver has no clients"."""
        self._session(monkeypatch, status=503)

        with pytest.raises(SnapcastRequestError) as exc:
            await service._request("Server.GetStatus")

        assert "503" in str(exc.value)

    async def test_a_json_rpc_error_reply_is_a_failure_even_at_200(
        self, service, monkeypatch
    ):
        """snapserver answers 200 with an `error` member for an unknown method or
        an unknown client id. Reading `result` would give {} — success."""
        self._session(monkeypatch, payload={"error": {"code": -32601, "message": "unknown"}})

        with pytest.raises(SnapcastRequestError) as exc:
            await service._request("Client.SetVolume", {"id": "gone"})

        assert "unknown" in str(exc.value)

    async def test_a_transport_failure_is_converted_and_keeps_its_cause(
        self, service, monkeypatch
    ):
        """Callers catch one type. Letting an `OSError` through would escape every
        `except SnapcastRequestError` in the module."""
        import aiohttp

        class _Refusing:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                raise OSError("connection refused")

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(aiohttp, "ClientSession", _Refusing)

        with pytest.raises(SnapcastRequestError) as exc:
            await service._request("Server.GetStatus")

        assert isinstance(exc.value.__cause__, OSError)
        assert "OSError" in str(exc.value)

    async def test_an_empty_result_is_returned_not_treated_as_a_failure(
        self, service, monkeypatch
    ):
        """`Client.SetVolume` answers with an empty result on success."""
        self._session(monkeypatch, payload={"result": {}})

        assert await service._request("Client.SetVolume", {"id": "x"}) == {}

    async def test_a_slow_answer_is_recorded_because_it_is_the_desync_symptom(
        self, service, monkeypatch, caplog
    ):
        """A snapserver that takes half a second to answer on loopback is CPU
        starvation, which is what desynchronises the fleet — and it leaves no
        other trace: the RPC still succeeds, so nothing else in the stack notices.
        """
        self._session(monkeypatch)
        ticks = iter([1000.0, 1000.8])
        monkeypatch.setattr("backend.core.multiroom.snapcast.time.time", lambda: next(ticks))

        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.snapcast"):
            await service._request("Server.GetStatus")

        assert any("SNAPCAST_SLOW" in r.message and "800ms" in r.message
                   for r in caplog.records)
