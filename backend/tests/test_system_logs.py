"""`LogService` — the logs page reads three places, groups, filters, redacts.

What breaks when these fail:

* **grouping** — a line repeated forty times pushes the one line that matters
  off the page, and a count or a first/last occurrence that is wrong sends the
  owner looking at the wrong hour;
* **the filters** — a severity, a service or a text that does not narrow is a
  page the owner scrolls by hand, on a phone;
* **redaction** — the API is not authenticated and the page is meant to stay
  publishable: a network name, a share name, a public address or a URL query
  in a line that leaves is the household's data on a screenshot;
* **the context** — "Copy for correction" sends the lines around an event and
  the source at that moment; the wrong neighbors, or a present state passed
  off as the past one, is a correction aimed at the wrong fault.

The outside world is faked where it is met: journalctl's subprocess (a journald
that answers `-u`, a field match, `-p`, `--since`, `--until`, `-n` and `-g` as
systemd 257 does, in JSON), `vcgencmd`, errors.log and its rotation on
`tmp_path`, NetworkManager's known networks, the share file and the registry.
The Redactor is the one production builds, `DiagnosticService.build_redactor`,
from those inputs.
"""
import asyncio
import fnmatch
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.core.log_handler import errors_log_formatter
from backend.core.multiroom.models import Client
from backend.core.system.diagnostic import probes
from backend.core.system.diagnostic import service as diagnostic_module
from backend.core.system.diagnostic.service import DiagnosticService
from backend.core.system.logs import journal as journal_module
from backend.core.system.logs.service import LogService

SSID = "Livebox-Maison"
SHARE_ID = "nas-leo-d7992dfe"
SHARE_NAME = "NAS de Leo"
SPEAKER_NAME = "Cuisine"
PUBLIC_IP = "88.170.12.34"

NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture(autouse=True)
def _now():
    """Each test's lines are written relative to when it runs: the service
    reads its window back from the real clock, and a suite runs for minutes."""
    global NOW
    NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _ago(minutes: float) -> datetime:
    return NOW - timedelta(minutes=minutes)


# --------------------------------------------------------------------------- #
# Stand-ins for the outside world
# --------------------------------------------------------------------------- #

class _Network:
    async def known_ssids(self):
        return {SSID}


class _Registry:
    def __init__(self, clients):
        self._clients = {c.mac_id: c for c in clients}

    def get_all_clients(self):
        return self._clients

    def get_all_zones(self):
        return {}


class _Version:
    async def running_build(self):
        return {"version": "v0.3.0-12-g1c45d1d2", "commit": "1c45d1d2" + "0" * 32}


class _StateMachine:
    def get_current_state(self):
        return {"source": "spotify", "service": "active", "session": {"phase": "paused"}}


_CURSORS = iter(range(1, 1 << 30))


def _entry(at: datetime, unit: str, message: str, *, priority: int = 6, pid: str = "100"):
    micros = int(at.timestamp()) * 1_000_000 + at.microsecond
    return {
        "__CURSOR": f"s=fake;i={next(_CURSORS):x}",
        "__REALTIME_TIMESTAMP": str(micros),
        "_SYSTEMD_UNIT": unit,
        "_PID": pid,
        "_TRANSPORT": "stdout",
        "PRIORITY": str(priority),
        "MESSAGE": message,
    }


def _kernel(at: datetime, message: str, *, priority: int = 4, identifier: str = "kernel"):
    """A line the kernel ring holds: no unit, no process, its own transport."""
    micros = int(at.timestamp()) * 1_000_000 + at.microsecond
    return {
        "__CURSOR": f"s=fake;i={next(_CURSORS):x}",
        "__REALTIME_TIMESTAMP": str(micros),
        "_TRANSPORT": "kernel",
        "SYSLOG_IDENTIFIER": identifier,
        "PRIORITY": str(priority),
        "MESSAGE": message,
    }


_PRIORITIES = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")


def _priority_range(arg: str):
    """journalctl's `-p`: one level and every level above it, or `A..B`."""
    def rank(name):
        return int(name) if name.isdigit() else _PRIORITIES.index(name)
    if ".." in arg:
        low, high = arg.split("..")
        return rank(low), rank(high)
    return 0, rank(arg)


class _FakeProcess:
    def __init__(self, lines, returncode):
        self._lines = list(lines)
        self.returncode = None
        self._exit = returncode
        self.stdout = self

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        self.returncode = self._exit
        return b""

    def kill(self):
        self.returncode = -9

    async def wait(self):
        if self.returncode is None:
            self.returncode = self._exit
        return self.returncode


def _fake_journald(monkeypatch, entries, *, missing=False):
    """journalctl -o json over `entries`, as systemd 257 answers it: oldest
    first; `-n N` the newest N; `-g` a match on MESSAGE, newest first with
    `-n`, exit 1 on no match. `missing`: journalctl is not installed."""
    calls = []

    def stamp(arg):
        return float(arg.lstrip("@"))

    async def create_subprocess_exec(*args, **kwargs):
        calls.append(list(args))
        if missing:
            raise FileNotFoundError(2, "No such file or directory", "journalctl")
        assert args[0] == "journalctl" and "json" in args
        units, matches, since, until, newest, grep, priority = [], [], None, None, None, None, None
        it = iter(args[1:])
        for arg in it:
            if arg == "-u":
                units.append(next(it))
            elif arg == "-p":
                priority = _priority_range(next(it))
            elif arg == "--since":
                since = stamp(next(it))
            elif arg == "--until":
                until = stamp(next(it))
            elif arg == "-n":
                newest = int(next(it))
            elif arg == "-g":
                grep = next(it)
            elif re.fullmatch(r"[A-Z_]+=.*", arg):
                matches.append(arg.split("=", 1))
        # Never unrestricted: Milō's units, the system services the page
        # names, or the kernel's transport.
        assert units or matches, args
        # Outside Milō's own units, nothing below warning is ever asked: the
        # WiFi daemons write the SSID of every network in their info lines.
        if matches or any(not u.startswith("milo-") for u in units):
            assert priority is not None and priority[1] <= _PRIORITIES.index("warning"), args

        def unit_of(e):
            # -u matches a unit's own lines and systemd's lines about it.
            return [e.get("_SYSTEMD_UNIT") or "", e.get("UNIT") or ""]

        kept = [
            e for e in sorted(entries, key=lambda e: int(e["__REALTIME_TIMESTAMP"]))
            if (not units or any(fnmatch.fnmatch(name, u) for name in unit_of(e) for u in units))
            and all(e.get(field) == value for field, value in matches)
            and (priority is None or priority[0] <= int(e["PRIORITY"]) <= priority[1])
            and (since is None or int(e["__REALTIME_TIMESTAMP"]) / 1e6 >= since)
            and (until is None or int(e["__REALTIME_TIMESTAMP"]) / 1e6 <= until)
            # PCRE's smart case: an all-lowercase pattern matches any case.
            and (grep is None or re.search(grep, e["MESSAGE"], 0 if grep != grep.lower() else re.I))
        ]
        if newest is not None:
            kept = kept[-newest:]
            if grep is not None:
                kept.reverse()
        code = 1 if grep is not None and not kept else 0
        return _FakeProcess([json.dumps(e).encode() + b"\n" for e in kept], code)

    monkeypatch.setattr(journal_module, "create_subprocess_exec", create_subprocess_exec)
    return calls


def _errors_log_line(at: datetime, level: str, logger: str, message: str) -> str:
    """A line as the backend's file handler writes it: production's formatter."""
    record = logging.LogRecord(logger, getattr(logging, level), __file__, 0, message, None, None)
    record.created = at.timestamp()
    record.msecs = at.microsecond // 1000
    return errors_log_formatter().format(record) + "\n"


@pytest.fixture
def unit(monkeypatch, tmp_path):
    """A unit: its share file, its known network, one speaker that is off,
    errors.log with one rotation, and a LogService built as production does."""
    data_file = tmp_path / "music_library_data.json"
    data_file.write_text(json.dumps({
        "shares": [{"id": SHARE_ID, "name": SHARE_NAME}], "known_usb": {},
    }))
    monkeypatch.setattr(diagnostic_module, "MUSIC_LIBRARY_DATA_FILE", data_file)

    async def firmware(args, timeout=probes.DEFAULT_TIMEOUT):
        # No Bluetooth device known; the Pi's firmware answers the throttling
        # word it holds (`throttled`, a fixture a test may set).
        asked.append(list(args))
        if args[:2] == ["vcgencmd", "get_throttled"]:
            return throttled["answer"]
        return None
    asked, throttled = [], {"answer": "throttled=0x0\n"}
    monkeypatch.setattr(probes, "run", firmware)

    speaker = Client(mac_id="d8:3a:dd:68:e7:e4", name=SPEAKER_NAME, ip="192.168.1.50")
    speaker.online = False
    diagnostic = DiagnosticService(network_service=_Network(), registry_service=_Registry([speaker]))

    errors_log = tmp_path / "errors.log"
    service = LogService(
        diagnostic_service=diagnostic,
        version_service=_Version(),
        state_machine=_StateMachine(),
        error_log=errors_log,
    )
    service.firmware = SimpleNamespace(asked=asked, throttled=throttled)
    service.speaker_blocks = None
    return service, errors_log


def _write_errors_logs(errors_log, *, rotated, current):
    (errors_log.parent / "errors.log.1").write_text("".join(rotated))
    errors_log.write_text("".join(current))


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #

async def test_repeated_lines_are_one_group_across_a_rotation_and_the_journal(unit, monkeypatch):
    """Three occurrences of one share warning (two in the rotation, one in the
    current file) and four admission retries that differ only by their
    numbers: two groups, counted, with the first and last occurrence."""
    service, errors_log = unit
    share = "Share {} stopped answering ({} consecutive probes); hiding it until it comes back"
    _write_errors_logs(
        errors_log,
        rotated=[
            _errors_log_line(_ago(600), "WARNING", "source.music_library.shares", share.format(SHARE_ID, 3)),
            _errors_log_line(_ago(500), "WARNING", "source.music_library.shares", share.format(SHARE_ID, 4)),
        ],
        current=[
            _errors_log_line(_ago(400), "WARNING", "source.music_library.shares", share.format(SHARE_ID, 7)),
        ],
    )
    retries = [_ago(30), _ago(20.5), _ago(10.25), _ago(5)]
    _fake_journald(monkeypatch, [
        _entry(at, "milo-backend.service",
               f"WARNING:backend.core.multiroom.websocket:SYNC_RECONNECT: d8:3a:dd:68:e7:e4 "
               f"retrying in {n}.0s after {n * 1000 + 7} ms")
        for n, at in enumerate(retries, start=1)
    ] + [_entry(_ago(3), "milo-snapserver-multiroom.service", "[Info] (Server) onStateChanged (Radio): playing")])

    page = await service.read(since=timedelta(days=1))

    by_count = {g.count: g for g in page.groups}
    assert set(by_count) >= {3, 4}, [(g.count, g.template) for g in page.groups]
    shares = by_count[3]
    assert (shares.first_at, shares.last_at) == (_ago(600), _ago(400))
    assert shares.service == "milo-backend.service" and shares.level == "warning"
    admission = by_count[4]
    assert (admission.first_at, admission.last_at) == (retries[0], retries[-1])
    assert "retrying in 4.0s" in admission.sample  # the newest occurrence, whole
    assert "#" in admission.template and "4.0" not in admission.template
    # Newest last occurrence first.
    assert [g.last_at for g in page.groups] == sorted((g.last_at for g in page.groups), reverse=True)


async def test_a_record_in_errors_log_and_the_journal_counts_once(unit, monkeypatch):
    """The backend writes a WARNING to both: one event, not two."""
    service, errors_log = unit
    at = _ago(15)
    message = "Transition failed: Failed to start radio"
    _write_errors_logs(errors_log, rotated=[], current=[
        _errors_log_line(at, "WARNING", "backend.core.state", message),
    ])
    _fake_journald(monkeypatch, [
        _entry(at + timedelta(milliseconds=1), "milo-backend.service", f"WARNING:backend.core.state:{message}"),
    ])

    page = await service.read(since=timedelta(hours=1))

    matching = [g for g in page.groups if "Transition failed" in g.sample]
    assert [g.count for g in matching] == [1], [(g.service, g.count) for g in page.groups]


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #

async def test_level_unit_and_text_each_narrow_the_page(unit, monkeypatch):
    """Each line as its producer writes it now: the backend's stderr with its
    syslog prefix (PRIORITY 3 for an ERROR), snapserver through syslog
    (PRIORITY 4), go-librespot's logrus at PRIORITY 6 whatever its level."""
    service, errors_log = unit
    _write_errors_logs(errors_log, rotated=[], current=[])
    _fake_journald(monkeypatch, [
        _entry(_ago(9), "milo-backend.service", "INFO:source.radio:Starting radio"),
        _entry(_ago(8), "milo-backend.service", "ERROR:backend.shared.mpv:mpv IPC connect refused", priority=3),
        _entry(_ago(7), "milo-snapserver-multiroom.service", "(Stream) Radio: buffer underrun", priority=4),
        _entry(_ago(6), "milo-spotify.service", "level=info msg=\"track changed\""),
    ])

    everything = await service.read(since=timedelta(hours=1))
    assert len(everything.groups) == 4
    assert set(everything.services) == {
        "milo-backend.service", "milo-snapserver-multiroom.service", "milo-spotify.service",
    }

    warnings = await service.read(since=timedelta(hours=1), level="warning")
    assert sorted(g.level for g in warnings.groups) == ["error", "warning"]

    snapserver = await service.read(since=timedelta(hours=1), service="milo-snapserver-multiroom.service")
    assert [g.service for g in snapserver.groups] == ["milo-snapserver-multiroom.service"]

    text = await service.read(since=timedelta(hours=1), q="IPC CONNECT")
    assert [g.logger for g in text.groups] == ["backend.shared.mpv"]

    window = await service.read(since=timedelta(minutes=7, seconds=30))
    assert {g.last_at for g in window.groups} == {_ago(7), _ago(6)}


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #

async def test_no_network_share_speaker_public_address_or_query_leaves(unit, monkeypatch):
    service, errors_log = unit
    _write_errors_logs(errors_log, rotated=[], current=[
        _errors_log_line(_ago(50), "WARNING", "source.music_library.shares",
                         f"Share {SHARE_ID} stopped answering (3 consecutive probes)"),
        _errors_log_line(_ago(49), "WARNING", "source.music_library.shares",
                         f"Mounting '{SHARE_NAME}' failed"),
    ])
    _fake_journald(monkeypatch, [
        _entry(_ago(40), "milo-backend.service",
               f"INFO:backend.core.network.service:Connected to WiFi network: {SSID}"),
        _entry(_ago(39), "milo-backend.service",
               f"INFO:backend.core.multiroom.websocket:Speaker {SPEAKER_NAME} admitted"),
        _entry(_ago(38), "milo-backend.service",
               f"WARNING:backend.shared.mpv:Loading stream: http://{PUBLIC_IP}:8000/live.mp3?u=leo&t=5f3c2a&s=91ab"),
    ])

    page = await service.read(since=timedelta(hours=2))

    wire = json.dumps(page.model_dump(mode="json"))
    for secret in (SSID, SHARE_ID, SHARE_NAME, SPEAKER_NAME, PUBLIC_IP, "u=leo", "t=5f3c2a"):
        assert secret not in wire, secret
    # Replaced by the report's own labels, so the page still reads.
    assert "wifi-1" in wire and "share-1" in wire and "client-1" in wire
    # A search is matched on the redacted text: it tells nobody a name exists.
    assert (await service.read(since=timedelta(hours=2), q=SSID)).groups == []
    # The speaker the registry shows off is said, not silent.
    assert [(n.source, n.reason) for n in page.not_collected] == [
        ("speaker client-1", "offline in the registry, not asked"),
    ]


# --------------------------------------------------------------------------- #
# Context, for "Copy for correction"
# --------------------------------------------------------------------------- #

async def test_the_context_is_twenty_lines_each_side_with_the_source_then(unit, monkeypatch):
    service, errors_log = unit
    _write_errors_logs(errors_log, rotated=[], current=[])
    event_at = _ago(30)
    entries = [
        _entry(_ago(120), "milo-backend.service", "INFO:backend.core.state:Transition completed: spotify"),
        _entry(_ago(90), "milo-backend.service", "INFO:backend.core.state:Transition completed: radio"),
        _entry(event_at, "milo-backend.service",
               f"WARNING:backend.shared.mpv:stream stalled on {SSID}-relay"),
    ]
    entries += [
        _entry(event_at + timedelta(seconds=s), "milo-snapserver-multiroom.service", f"[Info] tick {s}")
        for s in range(-60, 61) if s
    ]
    _fake_journald(monkeypatch, entries)

    context = await service.context(at=event_at, service="milo-backend.service")

    assert len(context.lines) == 2 * service.CONTEXT_LINES + 1
    event = context.lines[context.event_index]
    assert event.at == event_at and "stream stalled" in event.message
    assert context.event_index == service.CONTEXT_LINES
    assert SSID not in json.dumps(context.model_dump(mode="json"))
    assert (context.source_state.basis, context.source_state.source) == ("logged_transition", "radio")
    assert context.sample == event.message


async def test_a_record_with_a_traceback_is_one_line_of_its_context(unit, monkeypatch):
    """journald stamps a traceback's lines microseconds after its record: the
    read that ends at the event holds the first line alone, the one that
    starts at it holds the record whole. The event is marked once, with its
    traceback, never a bare copy beside the whole one."""
    service, errors_log = unit
    _write_errors_logs(errors_log, rotated=[], current=[])
    event_at = _ago(30)
    traceback = [
        "Traceback (most recent call last):",
        '  File "/home/milo/milo/backend/shared/mpv.py", line 3, in load',
        "ValueError: bad",
    ]
    entries = [
        _entry(event_at - timedelta(seconds=1), "milo-backend.service", "INFO:source.radio:before"),
        _entry(event_at, "milo-backend.service", "ERROR:backend.shared.mpv:boom", pid="200"),
        *(
            _entry(event_at + timedelta(microseconds=10 * (i + 1)), "milo-backend.service", text, pid="200")
            for i, text in enumerate(traceback)
        ),
        _entry(event_at + timedelta(seconds=1), "milo-backend.service", "INFO:source.radio:after"),
    ]
    _fake_journald(monkeypatch, entries)

    context = await service.context(at=event_at, service="milo-backend.service")

    messages = [line.message for line in context.lines]
    assert [m.split("\n", 1)[0] for m in messages] == ["before", "boom", "after"], messages
    assert context.lines[context.event_index].message == "\n".join(["boom", *traceback])
    assert context.sample == context.lines[context.event_index].message


async def test_after_a_failed_transition_the_source_then_is_the_one_it_left_selected(unit, monkeypatch):
    """A failed start leaves its target selected, its service failed: the
    source that played before it is not the one at the moment."""
    service, errors_log = unit
    _write_errors_logs(errors_log, rotated=[], current=[])
    event_at = _ago(10)
    _fake_journald(monkeypatch, [
        _entry(_ago(60), "milo-backend.service", "INFO:backend.core.state:Transition completed: spotify"),
        _entry(_ago(55), "milo-backend.service",
               "WARNING:backend.core.state:Transition failed: tidal: Failed to start tidal"),
        _entry(event_at, "milo-tidal.service", "connection refused"),
    ])

    context = await service.context(at=event_at, service="milo-tidal.service")

    state = context.source_state
    assert (state.basis, state.source, state.at) == ("failed_transition", "tidal", _ago(55))


async def test_an_errors_log_line_keeps_its_moment_across_a_time_zone_change(unit, monkeypatch):
    """The setup wizard sets the time zone and the next boot reads every line
    written before it: each must still name its own moment, or the journal's
    copy of it no longer matches and the event shows twice."""
    service, errors_log = unit
    at = _ago(20)
    message = "Transition failed: radio: Failed to start radio"
    saved = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "UTC"
        time.tzset()
        line = _errors_log_line(at, "WARNING", "backend.core.state", message)
        os.environ["TZ"] = "Europe/Paris"
        time.tzset()
        _write_errors_logs(errors_log, rotated=[], current=[line])
        _fake_journald(monkeypatch, [
            _entry(at + timedelta(milliseconds=1), "milo-backend.service", f"WARNING:backend.core.state:{message}"),
        ])
        page = await service.read(since=timedelta(days=1))
    finally:
        if saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved
        time.tzset()

    assert [(g.count, g.last_at) for g in page.groups] == [(1, at + timedelta(milliseconds=1))]


async def test_with_no_journal_the_source_is_the_current_one_and_says_so(unit, monkeypatch):
    """journalctl missing: the journal is "not collected" with its reason,
    errors.log still answers, and the source state is labeled current."""
    service, errors_log = unit
    at = _ago(5)
    _write_errors_logs(errors_log, rotated=[], current=[
        _errors_log_line(at, "ERROR", "frontend", "[AudioPlayer] render failed"),
    ])
    _fake_journald(monkeypatch, [], missing=True)

    page = await service.read(since=timedelta(hours=1))
    assert [g.logger for g in page.groups] == ["frontend"]
    # Every read failed the one way: said once, as the journal's.
    assert [n.source for n in page.not_collected if n.source.startswith("journal")] == ["journal"]

    context = await service.context(at=page.groups[0].last_at)
    assert context.source_state.basis == "current"
    assert context.source_state.source == "spotify"
    assert "not then" in context.source_state.detail
    assert context.lines[context.event_index].logger == "frontend"


class _WedgedProcess:
    """A journalctl that never ends a line nor exits until it is killed."""

    def __init__(self):
        self.returncode = None
        self.killed = asyncio.Event()
        self.stdout = self

    async def readline(self):
        await self.killed.wait()
        return b""

    def kill(self):
        self.returncode = -9
        self.killed.set()

    async def wait(self):
        await self.killed.wait()
        return self.returncode


async def test_a_read_canceled_kills_journalctl_rather_than_waiting_on_it(monkeypatch):
    """A shutdown cancels a read in flight: journalctl must be killed, or the
    task waits on a process blocked on a pipe nobody reads, until systemd's
    stop timeout kills the backend."""
    process = _WedgedProcess()

    async def create_subprocess_exec(*args, **kwargs):
        return process
    monkeypatch.setattr(journal_module, "create_subprocess_exec", create_subprocess_exec)

    task = asyncio.ensure_future(journal_module.read(["journalctl"], max_entries=10, timeout=60))
    await asyncio.sleep(0)
    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=2)

    assert done == {task} and process.killed.is_set()


async def test_the_fake_journald_reads_what_production_asks(monkeypatch):
    """The fake answers the argv the reader really builds, `-u` included."""
    calls = _fake_journald(monkeypatch, [_entry(_ago(1), "milo-backend.service", "INFO:x:y")])
    args = journal_module.journal_args(since=_ago(2), until=NOW, newest=10)
    entries, reason, _ = await journal_module.read(args, max_entries=10)
    assert reason is None and len(entries) == 1
    assert calls[0][calls[0].index("-u") + 1] == "milo-*"


# --------------------------------------------------------------------------- #
# The system services and the kernel, at warning and above
# --------------------------------------------------------------------------- #

NEIGHBOR_SSID = "Voisin-Box 5G"
INFO_SSID = "Cafe-Gratuit"
PHONE = "4C:2E:5E:11:22:33"
BSSID = "7c:4c:a5:1e:22:10"


def _system_world(at):
    """What the system services and the kernel wrote around `at`, each line as
    the daemon writes it (wpa_supplicant 2.10, NetworkManager 1.52, bluetoothd
    5.82, the Pi's hwmon driver), at the priority it writes it at."""
    return [
        # wpa_supplicant: an unsaved network by its `ssid="…"`, the saved one
        # by `SSID '…'`, and the access point by its address.
        _entry(at - timedelta(minutes=9), "wpa_supplicant.service",
               f'wlan0: CTRL-EVENT-SSID-TEMP-DISABLED id=1 ssid="{NEIGHBOR_SSID}" '
               "auth_failures=2 duration=20 reason=WRONG_KEY", priority=4),
        _entry(at - timedelta(minutes=8), "wpa_supplicant.service",
               f"wlan0: Trying to associate with SSID '{SSID}'", priority=4),
        _entry(at - timedelta(minutes=8), "wpa_supplicant.service",
               f"wlan0: CTRL-EVENT-BEACON-LOSS bssid={BSSID}", priority=4),
        # NetworkManager names a profile it gives up on in a shape of its own.
        _entry(at - timedelta(minutes=7), "NetworkManager.service",
               f"<warn>  [1732.4] device (wlan0): Activation: failed for connection '{NEIGHBOR_SSID}'",
               priority=4),
        # bluetoothd: a phone by its address alone, on a line that names
        # neither Bluetooth nor A2DP — twice, so one group.
        *(
            _entry(at - timedelta(minutes=m), "bluetooth.service",
                   f"profiles/audio/avdtp.c:avdtp_connect_cb() connect to {PHONE}: "
                   "Connection refused (111)", priority=3, pid="300")
            for m in (6, 5)
        ),
        # The kernel: the Pi's undervoltage, at crit, and a generator's line
        # (it writes to /dev/kmsg before the journal is up).
        _kernel(at, "hwmon hwmon1: Undervoltage detected!", priority=2),
        _kernel(at - timedelta(minutes=4), "SysV service '/etc/init.d/gmediarender' lacks a native "
                "systemd unit file, automatically generating a unit file for compatibility.",
                identifier="systemd-sysv-generator"),
        # Their info lines — never read: an SSID, the network joined, a USB
        # device's product string.
        _entry(at - timedelta(minutes=3), "wpa_supplicant.service",
               f'wlan0: CTRL-EVENT-CONNECTED - Connection to {BSSID} completed [id=1 ssid="{INFO_SSID}"]'),
        _entry(at - timedelta(minutes=3), "NetworkManager.service",
               f"<info>  [1733.0] policy: auto-activating connection '{INFO_SSID}'"),
        _entry(at - timedelta(minutes=2), "bluetooth.service", "Bluetooth daemon 5.82"),
        _kernel(at - timedelta(minutes=2), "usb 1-1.3: Product: iPod de Léo", priority=6),
    ]


async def test_the_system_services_and_the_kernel_are_read_at_warning_redacted_and_grouped(
    unit, monkeypatch,
):
    """The causes Milō's own units cannot see: an undervoltage, a WiFi that
    drops, a phone bluetoothd refuses. Read at warning and above, through the
    Redactor told which unit wrote each line; their info lines never read."""
    service, errors_log = unit
    _write_errors_logs(errors_log, rotated=[], current=[])
    at = _ago(10)
    calls = _fake_journald(monkeypatch, [
        _entry(at - timedelta(minutes=1), "milo-backend.service", "INFO:source.radio:Starting radio"),
        *_system_world(at),
    ])

    page = await service.read(since=timedelta(hours=1))

    wire = json.dumps(page.model_dump(mode="json"))
    for secret in (NEIGHBOR_SSID, SSID, INFO_SSID, PHONE, PHONE.lower(), BSSID, BSSID.upper(), "iPod"):
        assert secret not in wire, secret
    by_service = {}
    for g in page.groups:
        by_service.setdefault(g.service, []).append(g)
    assert set(page.services) == {
        "milo-backend.service", "wpa_supplicant.service", "NetworkManager.service",
        "bluetooth.service", "kernel",
    }
    wpa = " / ".join(g.sample for g in by_service["wpa_supplicant.service"])
    assert 'ssid="<wifi>"' in wpa and "SSID 'wifi-1'" in wpa and "bssid=ap-1" in wpa, wpa
    assert "connection '<wifi>'" in by_service["NetworkManager.service"][0].sample
    # The phone is one device, one label, one group of two.
    [phone] = by_service["bluetooth.service"]
    assert phone.count == 2 and "connect to bt-1:" in phone.sample and phone.level == "error"
    kernel = {g.sample.split(" ", 1)[0]: g for g in by_service["kernel"]}
    assert kernel["hwmon"].level == "critical" and kernel["hwmon"].logger is None
    assert kernel["SysV"].logger == "systemd-sysv-generator" and kernel["SysV"].level == "warning"
    # The info lines of those services were not read at all.
    assert not any("Bluetooth daemon" in g.sample or "auto-activating" in g.sample for g in page.groups)
    # Each read was restricted: Milō's units at every level, the others at
    # warning (the fake refuses any other read of them).
    assert any("_TRANSPORT=kernel" in call and "-p" in call for call in calls)

    errors = await service.read(since=timedelta(hours=1), level="error")
    assert {g.service for g in errors.groups} == {"bluetooth.service", "kernel"}

    context = await service.context(at=at, service="kernel")
    assert context.lines[context.event_index].message == "hwmon hwmon1: Undervoltage detected!"
    around = [line.service for line in context.lines]
    assert "milo-backend.service" in around and "wpa_supplicant.service" in around
    assert not any("auto-activating" in line.message for line in context.lines)
    assert NEIGHBOR_SSID not in json.dumps(context.model_dump(mode="json"))


async def test_a_week_at_warning_fits_where_every_level_does_not(unit, monkeypatch):
    """Seven days of a unit hold more lines than one read returns; their
    warnings do not. Asked at warning, the reads are filtered by journalctl
    before the cap — by PRIORITY for the backend, by text for the logrus
    daemons — so the oldest warning is in and the page is not cut; asked at
    every level, the cut is said."""
    service, errors_log = unit
    service.JOURNAL_MAX_ENTRIES = 300
    _write_errors_logs(errors_log, rotated=[], current=[])
    week = timedelta(days=7)
    noise = [
        _entry(NOW - week + timedelta(minutes=30 * i), "milo-backend.service",
               f"INFO:source.radio:Tick {i}")
        for i in range(330)
    ] + [
        _entry(NOW - week + timedelta(minutes=30 * i + 1), "milo-navidrome.service",
               f'time="x" level=info msg="Scanned {i}"')
        for i in range(40)
    ]
    warnings = [
        _entry(NOW - week + timedelta(hours=2 + i), "milo-backend.service",
               f"WARNING:backend.core.multiroom.websocket:SYNC_RECONNECT {i}", priority=4)
        for i in range(20)
    ] + [
        _entry(NOW - week + timedelta(hours=3 + i), "milo-navidrome.service",
               f'time="x" level=warning msg="Slow scan {i}"')
        for i in range(10)
    ] + [
        _entry(NOW - timedelta(hours=1), "milo-spotify.service", 'level=error msg="token refused"'),
    ]
    _fake_journald(monkeypatch, noise + warnings)

    everything = await service.read(since=week)
    # Every level, the default view: the `milo-*` read of every level is cut,
    # and the warnings it cut are read beside it, whole — the page says so.
    assert everything.truncated and everything.journal_from is not None and everything.warnings_whole
    assert {(g.service, g.level): g.count for g in everything.groups if g.level != "info"} == {
        ("milo-backend.service", "warning"): 20,
        ("milo-navidrome.service", "warning"): 10,
        ("milo-spotify.service", "error"): 1,
    }

    page = await service.read(since=week, level="warning")

    assert not page.truncated and page.journal_from is None
    counts = {(g.service, g.level): g.count for g in page.groups}
    assert counts == {
        ("milo-backend.service", "warning"): 20,
        ("milo-navidrome.service", "warning"): 10,
        ("milo-spotify.service", "error"): 1,
    }
    assert min(g.first_at for g in page.groups) == NOW - week + timedelta(hours=2)


async def test_a_backend_warning_from_before_the_priority_prefix_is_kept_by_errors_log(unit, monkeypatch):
    """A line the backend wrote before its stderr carried a priority sits at
    PRIORITY 6 in the journal, out of a read at warning; errors.log holds the
    same record, and that copy is shown — once."""
    service, errors_log = unit
    at = _ago(60 * 24 * 3)
    message = "Transition failed: radio: Failed to start radio"
    _write_errors_logs(errors_log, rotated=[], current=[
        _errors_log_line(at, "WARNING", "backend.core.state", message),
    ])
    _fake_journald(monkeypatch, [
        _entry(at + timedelta(milliseconds=1), "milo-backend.service", f"WARNING:backend.core.state:{message}"),
    ])

    page = await service.read(since=timedelta(days=7), level="warning")

    assert [(g.count, g.service, g.level) for g in page.groups] == [(1, "milo-backend.service", "warning")]


# --------------------------------------------------------------------------- #
# The Pi's throttling state
# --------------------------------------------------------------------------- #

async def test_the_throttling_state_is_read_with_the_page_and_decoded(unit, monkeypatch):
    """`vcgencmd get_throttled`, asked without sudo when the page is read; the
    word as the firmware wrote it on a unit of this fleet (0xe0000: a frequency
    cap, a throttle and the soft temperature limit since boot, none now)."""
    service, errors_log = unit
    _write_errors_logs(errors_log, rotated=[], current=[])
    _fake_journald(monkeypatch, [])
    service.firmware.throttled["answer"] = "throttled=0xe0000\n"

    page = await service.read(since=timedelta(hours=1))

    assert ["vcgencmd", "get_throttled"] in service.firmware.asked
    throttling = page.throttling
    assert (throttling.value, throttling.raw) == (0xE0000, "0xe0000")
    assert {c.name: (c.now, c.since_boot) for c in throttling.conditions} == {
        "under_voltage": (False, False),
        "frequency_capped": (False, True),
        "throttled": (False, True),
        "soft_temperature_limit": (False, True),
    }
    assert "throttling" not in {n.source for n in page.not_collected}


@pytest.mark.parametrize("installed, says", [
    (None, "not installed (not a Raspberry Pi)"),
    ("/usr/bin/vcgencmd", "failed or did not answer"),
])
async def test_no_throttling_answer_has_no_state_and_says_why(unit, monkeypatch, installed, says):
    """A dev host: no value, never a `0x0` it did not read. A Pi whose
    vcgencmd fails (`milo` out of group `video`, a hung VideoCore) is not told
    it is not a Pi — the moment the state matters most."""
    from backend.core.system.logs import throttling

    service, errors_log = unit
    _write_errors_logs(errors_log, rotated=[], current=[])
    _fake_journald(monkeypatch, [])
    service.firmware.throttled["answer"] = None
    monkeypatch.setattr(throttling.shutil, "which", lambda name: installed)

    page = await service.read(since=timedelta(hours=1))

    assert page.throttling is None
    [reason] = [n.reason for n in page.not_collected if n.source == "throttling"]
    assert says in reason, reason


@pytest.mark.parametrize("word, now, since_boot", [
    ("throttled=0x0", set(), set()),
    ("throttled=0x50005", {"under_voltage", "throttled"}, {"under_voltage", "throttled"}),
    ("throttled=0x80000", set(), {"soft_temperature_limit"}),
    ("throttled=0x20002", {"frequency_capped"}, {"frequency_capped"}),
])
def test_the_throttled_word_is_read_bit_by_bit(word, now, since_boot):
    """The low half is "now", bits 16-19 "since boot" — the layout an earlier
    reader of this word got wrong (19-22), reporting an undervoltage on a unit
    that had none."""
    from backend.core.system.logs import throttling

    state = throttling.decode(throttling.parse(word))
    assert {c.name for c in state.conditions if c.now} == now
    assert {c.name for c in state.conditions if c.since_boot} == since_boot


@pytest.mark.parametrize("answer", ["", "error=1 error_msg=\"Command not registered\"", "throttled=zz"])
def test_a_word_that_is_not_the_throttled_one_reads_as_no_value(answer):
    from backend.core.system.logs import throttling

    assert throttling.parse(answer) is None


# --------------------------------------------------------------------------- #
# A speaker's kernel
# --------------------------------------------------------------------------- #

async def test_a_speakers_kernel_warnings_pass_the_severity_filter(unit, monkeypatch):
    """A speaker sends its kernel's warnings and above only, each opened by
    its syslog priority, which short-iso cannot carry: an undervoltage there
    (`dev_crit`) is critical on the page, so "errors only" keeps it, filed
    under the speaker's label, never its room name; a warning is a warning."""
    service, errors_log = unit
    _write_errors_logs(errors_log, rotated=[], current=[])
    _fake_journald(monkeypatch, [])
    stamp = _ago(200).astimezone(timezone(timedelta(hours=1))).isoformat(timespec="seconds")
    block = "\n".join([
        "[identity]", "uptime : 0d 5h 2m", "", "[journal]",
        "--- milo-client.service ---",
        f"{stamp} milo-client python[512]: INFO:app:started",
        "--- kernel ---",
        f"{stamp} milo-client kernel: <2>hwmon hwmon1: Undervoltage detected!",
        f"{stamp} milo-client kernel: <4>usb 1-1.3: device descriptor read/64, error -71",
    ])

    async def blocks():
        return [{"reachable": True, "label": "client-1", "text": block}]
    monkeypatch.setattr(service._diagnostic, "satellite_blocks", blocks)

    page = await service.read(since=timedelta(hours=6), level="warning")
    errors = await service.read(since=timedelta(hours=6), level="error")

    assert sorted((g.service, g.level, g.sample) for g in page.groups) == [
        ("client-1/kernel", "critical", "hwmon hwmon1: Undervoltage detected!"),
        ("client-1/kernel", "warning", "usb 1-1.3: device descriptor read/64, error -71"),
    ]
    assert [(g.service, g.level) for g in errors.groups] == [("client-1/kernel", "critical")]


async def test_a_journal_read_that_fails_is_named_not_the_whole_journal(unit, monkeypatch):
    """One answer makes several journal reads: a kernel read that fails is
    said as the kernel's, not as "the journal" (whose `milo-*` lines are all
    there), and two reads failing two ways are two entries, never one
    `journal` said twice (the page keys its list on what each says)."""
    service, errors_log = unit
    _write_errors_logs(errors_log, rotated=[], current=[])
    _fake_journald(monkeypatch, [
        _entry(_ago(5), "milo-backend.service", "WARNING:backend.core.state:Transition failed", priority=4),
    ])
    answering = journal_module.create_subprocess_exec

    async def failing(*args, **kwargs):
        if "_TRANSPORT=kernel" in args:
            return _FakeProcess([], 2)
        if "-g" in args:
            return _FakeProcess([], 3)
        return await answering(*args, **kwargs)
    monkeypatch.setattr(journal_module, "create_subprocess_exec", failing)

    page = await service.read(since=timedelta(hours=1))

    assert [g.service for g in page.groups] == ["milo-backend.service"]
    assert sorted((n.source, n.reason) for n in page.not_collected if n.source.startswith("journal")) == [
        ("journal/kernel", "journalctl exited with status 2"),
        ("journal/text-level", "journalctl exited with status 3"),
    ]
