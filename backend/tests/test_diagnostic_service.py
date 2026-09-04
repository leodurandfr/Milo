"""`DiagnosticService.generate` — the export must survive the thing it reports.

The report exists for the moment an appliance is misbehaving in a stranger's
home, which is exactly the moment its own probes stop answering: a NAS that
takes ten seconds to admit it is gone, a satellite that is unplugged, a daemon
that will not talk. So the behaviours pinned here are all failure behaviours.

What breaks when these fail:

* **a section that vanishes** reads as "nothing to report here", which is the
  opposite of what a failed probe means — and it is the silent kind of wrong,
  because nobody counts headings;
* **a section that takes the export down** leaves the user with a spinner and
  the maintainer with nothing at all, for the one fault the report was opened
  to describe;
* **an unbounded journal** is a report that will not fit in the issue body it
  was written for, and the truncation lands on whichever unit happened to be
  noisiest rather than on the oldest lines;
* **a satellite reported as unreachable when it merely predates the route**
  sends the diagnosis at the network when the answer is a fleet update.
"""
import asyncio
import json

import pytest

from backend.core.multiroom.models import Client, EqualizerSettings, Zone
from backend.core.system.diagnostic import render, satellite
from backend.core.system.diagnostic.service import DiagnosticService

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Stand-ins for the outside world
# --------------------------------------------------------------------------- #

class _Registry:
    """The client registry, holding one local client and one satellite."""

    def __init__(self, clients=None, zones=None, equalizer=None):
        self._clients = clients if clients is not None else {}
        self._zones = zones or {}
        self._equalizer = equalizer or {}

    def get_all_clients(self):
        return self._clients

    def get_all_zones(self):
        return self._zones

    def get_client_equalizer(self, mac):
        return self._equalizer.get(mac)


def _client(mac, name, ip, *, zone=None):
    """`is_local` is derived from the IP, so 127.0.0.1 is what makes one local."""
    client = Client(mac_id=mac, name=name, ip=ip, zone_id=zone)
    client.online = True
    return client


async def _generate(monkeypatch, tmp_path, **services):
    """Run a real export with the two data files pointed at `tmp_path`.

    Nothing else is stubbed: the probes really do run, because a report built
    entirely out of mocks would prove only that the mocks agree.
    """
    from backend.core.system.diagnostic import collectors
    from backend.core.system.diagnostic import service as service_module

    data_file = tmp_path / "music_library_data.json"
    if not data_file.exists():
        data_file.write_text(json.dumps({"shares": [], "known_usb": {}}))
    errors_log = tmp_path / "errors.log"
    if not errors_log.exists():
        errors_log.write_text("")

    monkeypatch.setattr(service_module, "MUSIC_LIBRARY_DATA_FILE", data_file)
    monkeypatch.setattr(collectors, "MUSIC_LIBRARY_DATA_FILE", data_file)
    monkeypatch.setattr(service_module, "ERROR_LOG_FILE", errors_log)
    return await DiagnosticService(**services).generate()


# --------------------------------------------------------------------------- #
# A failing section is written down, not dropped
# --------------------------------------------------------------------------- #

async def test_a_section_that_raises_becomes_a_heading_that_says_so(monkeypatch, tmp_path):
    """The report is still produced, and the failure is in it under its own name."""
    from backend.core.system.diagnostic import collectors

    async def boom(ctx):
        raise RuntimeError("snapserver refused the connection")

    monkeypatch.setattr(collectors, "multiroom", boom)
    result = await _generate(monkeypatch, tmp_path)

    assert "===== MULTIROOM =====" in result["report"]
    assert "NOT COLLECTED — RuntimeError: snapserver refused the connection" in result["report"]
    assert {"section": "MULTIROOM", "reason": "RuntimeError: snapserver refused the connection"} \
        in result["unavailable"]


async def test_a_section_that_hangs_is_cut_at_its_own_budget(monkeypatch, tmp_path):
    """A wedged probe costs its section, never the export.

    The hung NAS case: `stat` on a dead mount blocks in the kernel until the
    client gives up, which was measured at 10.18 s on this fleet — longer than
    anyone will hold a spinner.
    """
    from backend.core.system.diagnostic import collectors

    async def forever(ctx):
        await asyncio.Event().wait()

    monkeypatch.setattr(collectors, "storage", forever)
    monkeypatch.setattr(DiagnosticService, "SECTION_TIMEOUT", 0.05)
    result = await _generate(monkeypatch, tmp_path)

    assert "did not answer within 0.05 s" in result["report"]
    assert any(item["section"] == "STORAGE" for item in result["unavailable"])
    # And the sections after it still arrived.
    assert "===== NETWORK =====" in result["report"]


async def test_every_section_is_present_exactly_once_even_when_all_of_them_fail(
    monkeypatch, tmp_path
):
    """The file's shape does not depend on the appliance's health.

    A maintainer reads these reports by skimming for a heading; one that appears
    only on a healthy unit is one they will not think to look for on a sick one.
    """
    from backend.core.system.diagnostic import collectors

    async def boom(ctx):
        raise RuntimeError("nope")

    titles = [title for title, _ in DiagnosticService()._sections()]
    for _, fn in DiagnosticService()._sections():
        monkeypatch.setattr(collectors, fn.__name__, boom, raising=False)
    monkeypatch.setattr("backend.core.system.diagnostic.service._errors_log", boom)
    monkeypatch.setattr("backend.core.system.diagnostic.service._journal", boom)
    monkeypatch.setattr("backend.core.system.diagnostic.service._previous_boot", boom)

    result = await _generate(monkeypatch, tmp_path)
    for title in [*titles, "SATELLITES", "NOT COLLECTED"]:
        assert result["report"].count(f"===== {title} =====") == 1, title


async def test_the_unavailable_list_and_the_headings_agree(monkeypatch, tmp_path):
    """The UI lists what the file lists, or one of the two is lying.

    The list is what the user sees under the buttons before deciding to send;
    a section missing from it is a gap they were not warned about.
    """
    from backend.core.system.diagnostic import collectors

    async def boom(ctx):
        raise RuntimeError("nope")

    monkeypatch.setattr(collectors, "versions", boom)
    monkeypatch.setattr(collectors, "network", boom)
    result = await _generate(monkeypatch, tmp_path)

    in_text = {
        block.split(" =====")[0]
        for block in result["report"].split("===== ")[1:]
        if block.split(" =====")[1].lstrip().startswith("NOT COLLECTED —")
    }
    in_list = {item["section"] for item in result["unavailable"]}
    assert in_text == in_list


# --------------------------------------------------------------------------- #
# The budgets
# --------------------------------------------------------------------------- #

async def test_the_report_never_exceeds_the_issue_body_it_is_written_for(
    monkeypatch, tmp_path
):
    """60 000 bytes, and the cut is stamped rather than silent.

    The destination is a GitHub issue body, capped at 65 536 characters. A
    report that has to be split or attached stops being the one gesture this
    feature exists to be.
    """
    from backend.core.system.diagnostic import collectors

    async def huge(ctx):
        return "x" * 200_000

    monkeypatch.setattr(collectors, "audio_path", huge)
    result = await _generate(monkeypatch, tmp_path)

    assert len(result["report"].encode("utf-8")) <= render.MAX_REPORT_BYTES
    assert "===== TRUNCATED =====" in result["report"]
    assert "bytes cut from the end" in result["report"]


def test_a_chatty_unit_does_not_starve_a_quiet_one():
    """Measured: snapserver writes 34 KB of the 42 KB this unit logs in half an
    hour. A first-come fill hands it the whole journal budget, and the thirteen
    other units — the ones a fault is usually in — arrive empty."""
    chatty = [f"snapserver line {i:04d} " + "y" * 60 for i in range(400)]
    quiet = ["camilladsp said something once"]

    kept = render.round_robin({"chatty": chatty, "quiet": quiet}, 2_000)

    assert kept["quiet"] == quiet
    assert 0 < len(kept["chatty"]) < len(chatty)
    # Newest survive, oldest are cut.
    assert kept["chatty"][-1] == chatty[-1]


def test_a_long_line_is_cut_with_the_cut_marked():
    """A stack trace on one line must not eat a unit's whole share, and a line
    that was shortened must not read as a line that ended there."""
    line = "a" * 1_000
    out = render.cap_line(line)
    assert len(out) == render.MAX_LINE_CHARS
    assert out.endswith("…")
    assert render.cap_line("short") == "short"


# --------------------------------------------------------------------------- #
# The satellites
# --------------------------------------------------------------------------- #

class _Session:
    """Stands in for aiohttp: one scripted reply per satellite, in order."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        reply = self.replies.pop(0)

        class _Ctx:
            async def __aenter__(_self):
                if isinstance(reply, Exception):
                    raise reply
                return reply

            async def __aexit__(_self, *exc):
                return False

        return _Ctx()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Response:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload or {}

    async def json(self):
        return self._payload


def _fake_session(monkeypatch, *replies):
    session = _Session(*replies)
    monkeypatch.setattr(satellite.aiohttp, "ClientSession", lambda **kw: session)
    return session


async def test_a_satellite_that_does_not_answer_is_named_and_the_export_survives(
    monkeypatch,
):
    """A speaker that is unplugged is the normal state of a multiroom install,
    and the block it leaves has to say which speaker and why."""
    _fake_session(monkeypatch, asyncio.TimeoutError())
    clients = [_client("dc:a6:32:7e:d3:43", "Canapé", "192.168.1.153")]
    labels = satellite.labels_for(clients)

    results = await satellite.collect_all(clients, labels)
    text = satellite.render(results, render.SATELLITE_BUDGET_BYTES)

    assert results[0]["reachable"] is False
    assert "powered off" in text and "192.168.1.153" in text
    assert "Canapé" not in text
    assert satellite.unavailable_names(results) == [
        {"section": "satellite client-1", "reason": results[0]["reason"]}
    ]


async def test_a_satellite_that_predates_the_route_is_not_reported_as_unreachable(
    monkeypatch,
):
    """404 is a third verdict, and it points at a different action.

    A satellite is always updated BY the version it is already running, so a
    push lands one release behind. Reporting that as "unreachable" sends the
    diagnosis at the network when the answer is a fleet update.
    """
    _fake_session(monkeypatch, _Response(404))
    clients = [_client("dc:a6:32:7e:d3:43", "Canapé", "192.168.1.153")]

    results = await satellite.collect_all(clients, satellite.labels_for(clients))

    assert results[0]["reachable"] is False
    assert "predates the diagnostic route" in results[0]["reason"]
    assert "powered off" not in results[0]["reason"]


async def test_a_satellite_block_is_cut_to_its_share_and_says_so(monkeypatch):
    """Two satellites split the budget equally rather than first-come: a shared
    pool hands the whole of it to whichever the loop reached first, and the
    other — possibly the broken one — arrives empty."""
    _fake_session(
        monkeypatch,
        _Response(200, {"hostname": "milo-client", "text": "z" * 40_000, "unavailable": []}),
        _Response(200, {"hostname": "milo-client-2", "text": "short block", "unavailable": []}),
    )
    clients = [
        _client("aa:aa:aa:aa:aa:aa", "Canapé", "192.168.1.153"),
        _client("bb:bb:bb:bb:bb:bb", "Bureau", "192.168.1.60"),
    ]
    results = await satellite.collect_all(clients, satellite.labels_for(clients))
    text = satellite.render(results, 4_000)

    assert "milo-client-2" in text and "short block" in text
    assert "bytes cut)" in text
    assert len(text.encode("utf-8")) < 6_000


async def test_a_satellite_label_is_the_same_one_the_rest_of_the_report_uses(
    monkeypatch, tmp_path
):
    """The labels are the only handle on a speaker in the file, so the registry,
    the EQ block and the satellite heading have to agree on them — a `client-1`
    in one half and a `client-2` in the other is worse than printing the name.
    """
    _fake_session(monkeypatch, asyncio.TimeoutError())
    zone = Zone(name="Salon", id="zone-uuid", client_ids=["dc:a6:32:7e:d3:43"])
    clients = {
        "dc:a6:32:7e:d3:43": _client(
            "dc:a6:32:7e:d3:43", "Canapé", "192.168.1.153", zone="zone-uuid"
        ),
        "2c:cf:67:b9:46:6f": _client("2c:cf:67:b9:46:6f", "Milō", "127.0.0.1"),
    }
    registry = _Registry(
        clients=clients,
        zones={"zone-uuid": zone},
        equalizer={mac: EqualizerSettings() for mac in clients},
    )
    result = await _generate(monkeypatch, tmp_path, registry_service=registry)
    report = result["report"]

    # MAC order: 2c:… is client-1 (and local), dc:… is client-2.
    assert "client-1 (local)" in report
    assert report.count("client-2") >= 3  # registry row, zone membership, EQ row
    assert "satellite client-2" in str(result["unavailable"])
    assert "zone-1" in report and "zone-uuid" not in report
