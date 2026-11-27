"""`GET /diagnostic` — the only way anything a satellite knows leaves it.

A satellite has no log surface in the Milō UI. Its journal, its unit states and
its ALSA state reach a maintainer through this route and through nothing else,
which makes it the half of a multiroom fault that is normally invisible — and
the half most likely to be why a report is being generated at all.

What breaks when these fail:

* **the three keys** are the contract the server reads back
  (`backend/tests/contracts/test_milo_client_contract.py` checks the names; only
  this file checks that they are populated). A satellite returning a shape the
  server cannot read is reported as broken rather than as answering;
* **the 500** is the difference between "this satellite is missing one probe"
  and "this satellite is unreachable". The server has one call and a short
  budget, so a route that raises turns a small gap into a whole absent block;
* **the payload's own whitelist** is this side's version of the server's
  redaction rule. The unit whitelist is what keeps NetworkManager and
  wpa_supplicant — which log the SSID of every network they touch — out of a
  file destined for a public issue.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from routes.diagnostic import UNIT_GLOB, create_diagnostic_router  # noqa: E402
import routes.diagnostic as diagnostic  # noqa: E402


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(create_diagnostic_router())
    return TestClient(app)


def test_the_payload_carries_the_three_keys_the_server_reads(client):
    """Populated, not merely present: the server pastes `text` in verbatim."""
    body = client.get("/diagnostic").json()

    assert set(body) == {"hostname", "text", "unavailable"}
    assert isinstance(body["hostname"], str) and body["hostname"]
    assert isinstance(body["unavailable"], list)
    # Identity is read from /proc and the app's own files, so it answers even on
    # a machine that is not a satellite — an empty block here means the whole
    # collection stopped working, not that this host is unusual.
    assert "[identity]" in body["text"]
    assert "uptime" in body["text"]


def test_a_probe_that_fails_costs_its_own_line_and_not_the_block(client, monkeypatch):
    """The server has one call and a 6 s budget. A 500 here would report a
    satellite that is merely missing one probe as an absent block."""
    async def boom():
        raise OSError("/proc/asound went away")

    monkeypatch.setattr(diagnostic, "_camilladsp", boom)
    response = client.get("/diagnostic")

    assert response.status_code == 200
    body = response.json()
    reasons = {item["section"]: item["reason"] for item in body["unavailable"]}
    assert "camilladsp" in reasons
    assert "/proc/asound went away" in reasons["camilladsp"]
    assert "[identity]" in body["text"]


def test_a_satellite_whose_probes_answer_nothing_says_so(client, monkeypatch):
    """A satellite whose units systemd does not list is the opposite of
    healthy, and a journal that returned no line is not a quiet one: both must
    be named in `unavailable` rather than leave a report that reads as complete
    with no `[units]` or `[journal]` block."""
    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(diagnostic, "_run", nothing)
    body = client.get("/diagnostic").json()

    reasons = {item["section"]: item["reason"] for item in body["unavailable"]}
    assert "units" in reasons
    assert "milo-client" in reasons["units"]
    assert "journal" in reasons


ADM_GID, JOURNAL_GID = 4, 101


def _groups(monkeypatch, *gids):
    """The account's groups, as the kernel reports them, on a host whose group
    database knows `adm` and `systemd-journal`."""
    known = {"adm": ADM_GID, "systemd-journal": JOURNAL_GID}

    def getgrnam(name):
        if name not in known:
            raise KeyError(name)
        return SimpleNamespace(gr_gid=known[name])

    monkeypatch.setattr(diagnostic.os, "geteuid", lambda: 1234)
    monkeypatch.setattr(diagnostic.os, "getgroups", lambda: list(gids))
    monkeypatch.setattr(diagnostic.os, "getegid", lambda: 1234)
    monkeypatch.setattr(diagnostic.grp, "getgrnam", getgrnam)


def _journal_reasons(body):
    return [item["reason"] for item in body["unavailable"] if item["section"] == "journal"]


def test_a_journal_read_without_adm_is_named_partial(client, monkeypatch):
    """An account outside `adm` reads only its own journal file: journalctl
    exits 0 with every systemd[1] line and the whole kernel ring missing, and
    the hint it prints goes to stderr. The block then looks complete while it
    no longer says why a unit failed, so the gap must be named under the
    satellite's heading in the server's report."""
    _groups(monkeypatch, 1234, 29)
    response = client.get("/diagnostic")

    assert response.status_code == 200
    reasons = _journal_reasons(response.json())
    assert any("adm" in reason for reason in reasons), reasons


def test_a_journal_read_from_adm_is_not_flagged(client, monkeypatch):
    """The counterpart: an account in `adm` (both satellites since 2026-09-20)
    reads the whole journal and must not be reported as partial."""
    _groups(monkeypatch, 1234, ADM_GID)
    reasons = _journal_reasons(client.get("/diagnostic").json())

    assert not any("adm" in reason for reason in reasons), reasons


def test_the_journal_is_restricted_to_the_satellites_own_units(client, monkeypatch):
    """The free-text whitelist, this side.

    An unrestricted `journalctl` also answers for NetworkManager and
    wpa_supplicant. Nothing here may ask for one, and the glob has to stay the
    satellite's own prefix.
    """
    calls = []
    real_run = diagnostic._run

    async def recording(args, timeout=diagnostic.PROBE_TIMEOUT):
        calls.append(list(args))
        return await real_run(args, timeout)

    monkeypatch.setattr(diagnostic, "_run", recording)
    client.get("/diagnostic")

    journal_calls = [c for c in calls if c and c[0] == "journalctl"]
    assert journal_calls, "the extractor recorded no journal call at all"
    for call in journal_calls:
        # Either a named unit or the kernel ring — never a bare read.
        assert "-u" in call or "-k" in call, call
        for index, token in enumerate(call):
            if token == "-u":
                assert call[index + 1].startswith("milo-client"), call
    assert UNIT_GLOB == "milo-client*"


def test_the_identity_lines_name_the_code_that_answers(client, identity_at_startup):
    """A maintainer reads these lines to learn why a push did not take. An update
    whose restart was refused has already written the next identity; reading the
    files per request would print the code this process never ran."""
    version, payload = identity_at_startup
    version.write_text("v0.2.0")
    payload.write_text("NEWPAYLOAD")

    text = client.get("/diagnostic").json()["text"]

    assert "app version     : v0.1.0" in text
    assert "app payload     : OLDPAYLOAD" in text


def test_the_report_names_when_the_app_started(client, monkeypatch):
    """After a push a maintainer reads this report to learn since when the new
    code runs; a line that printed the request time answered that question with
    a different value on every report."""
    import routes.health as health

    monkeypatch.setattr(health, "APP_STARTED_AT", 1_000_000_000)

    text = client.get("/diagnostic").json()["text"]
    line = next(line for line in text.splitlines() if line.startswith("api started at"))
    value = line.split(":", 1)[1].strip()

    assert "(now)" not in line
    assert datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z").timestamp() == 1_000_000_000


class _Systemd:
    """systemctl as systemd 257 was measured to answer, for one unit state:
    `is-active` prints the state and exits 3 unless it is `active`, while
    `show` exits 0 in every state."""

    SUB_STATES = {"active": "running", "inactive": "dead", "failed": "failed", "activating": "start"}

    def __init__(self, state):
        self.state = state
        self.sub = self.SUB_STATES[state]

    def answer(self, args):
        unit = "milo-client-camilladsp.service"
        if args[:2] == ["systemctl", "list-units"]:
            return 0, f"{unit} loaded {self.state} {self.sub} Milo DSP\n"
        if args[:2] == ["systemctl", "is-active"]:
            return (0 if self.state == "active" else 3), f"{self.state}\n"
        if args[:2] == ["systemctl", "show"] and "--value" in args:
            values = {"ActiveState": self.state, "SubState": self.sub}
            return 0, f"{values.get(args[args.index('-p') + 1], '')}\n"
        if args[:2] == ["systemctl", "show"]:
            return 0, f"Id={unit}\nActiveState={self.state}\nSubState={self.sub}\nNRestarts=5\n"
        return 0, ""


class _FakeProc:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self._stdout = stdout.encode()

    async def communicate(self):
        return self._stdout, b""


@pytest.mark.parametrize("state", ["active", "inactive", "failed", "activating"])
def test_camilladsp_section_names_the_state_systemd_reports(client, monkeypatch, state):
    """The DSP line is read in the states a maintainer opens a report for: a
    unit that failed or sits inactive must not read `unknown` two lines below
    the `[units]` block that names its real state."""
    systemd = _Systemd(state)

    async def fake_exec(*args, **kwargs):
        return _FakeProc(*systemd.answer(list(args)))

    monkeypatch.setattr(diagnostic.asyncio, "create_subprocess_exec", fake_exec)
    text = client.get("/diagnostic").json()["text"]

    blocks = {block.split("\n", 1)[0]: block for block in text.split("\n\n")}
    units_line = next(
        line for line in blocks["[units]"].splitlines()
        if line.startswith("milo-client-camilladsp.service")
    )
    dsp_line = next(
        line for line in blocks["[camilladsp]"].splitlines()
        if line.startswith("milo-client-camilladsp.service")
    )
    assert units_line.split()[1] == state
    assert dsp_line.split(":", 1)[1].strip() == units_line.split()[1]


def test_an_oversized_journal_loses_its_oldest_lines_not_its_newest(client, monkeypatch):
    """The block is capped for the server's budget, and the journal is its last
    section, each unit oldest line first. Cut from the end of the text, the
    report lost the newest lines and every unit after the cut — the lines it
    is generated for. Each unit keeps its newest lines instead, and the cut
    says it happened."""
    units = ["milo-client.service", "milo-client-camilladsp.service", "milo-client-snapclient.service"]
    real_run = diagnostic._run

    async def run(args, timeout=diagnostic.PROBE_TIMEOUT):
        if args[0] == "systemctl" and "list-units" in args:
            return "\n".join(f"{unit} loaded active running x" for unit in units)
        if args[0] == "journalctl" and "-k" in args:
            # The kernel tail is read as JSON (its priority kept).
            return "\n".join(json.dumps({
                "__REALTIME_TIMESTAMP": str(1762423200_000000 + n * 60_000_000), "PRIORITY": "4",
                "SYSLOG_IDENTIFIER": "kernel[1]", "_HOSTNAME": "host",
                "MESSAGE": f"line {n} " + "x" * 120,
            }) for n in range(diagnostic.JOURNAL_LINES_PER_UNIT))
        if args[0] == "journalctl":
            name = args[args.index("-u") + 1]
            return "\n".join(
                f"2026-11-06T10:{n:02d}:00+00:00 host {name}[1]: line {n} " + "x" * 120
                for n in range(diagnostic.JOURNAL_LINES_PER_UNIT)
            )
        return await real_run(args, timeout)

    monkeypatch.setattr(diagnostic, "_run", run)
    text = client.get("/diagnostic").json()["text"]

    assert len(text.encode("utf-8")) <= diagnostic.MAX_TEXT_BYTES
    assert "truncated on the satellite" not in text
    newest = diagnostic.JOURNAL_LINES_PER_UNIT - 1
    for name in [*units, "kernel"]:
        opening = f"{name}[1]: <4>" if name == "kernel" else f"{name}[1]: "
        assert f"{opening}line {newest} " in text, name
        assert f"{opening}line 0 " not in text, name
    assert "older lines cut" in text


def test_the_kernel_tail_is_the_whole_boots_warnings(client, monkeypatch):
    """A speaker's undervoltage happens at power-up, hours before anyone reads
    its logs, and is a cause of dropouts: the kernel tail asks for warnings and
    above (`-p warning`) of the whole current boot (`-k`), never the units'
    six-hour window, which held none of them on two speakers measured. Each
    line carries its priority (`<2>`): the server files a critical line as
    critical, which short-iso cannot say."""
    calls = []
    entry = {
        "__REALTIME_TIMESTAMP": "1764219603000000", "PRIORITY": "2",
        "SYSLOG_IDENTIFIER": "kernel", "_HOSTNAME": "milo-client",
        "MESSAGE": "hwmon hwmon1: Undervoltage detected!",
    }

    async def run(args, timeout=diagnostic.PROBE_TIMEOUT):
        calls.append(list(args))
        if args[0] == "systemctl" and "list-units" in args:
            return "milo-client.service loaded active running x"
        if args[0] == "journalctl" and "-k" in args:
            return json.dumps(entry) + "\n"
        return None

    monkeypatch.setattr(diagnostic, "_run", run)
    text = client.get("/diagnostic").json()["text"]

    [kernel] = [c for c in calls if c[0] == "journalctl" and "-k" in c]
    assert kernel[kernel.index("-p") + 1] == "warning"
    assert kernel[kernel.index("-o") + 1] == "json" and "--since" not in kernel
    [unit] = [c for c in calls if c[0] == "journalctl" and "-u" in c]
    assert unit[unit.index("--since") + 1] == "-6h" and "-b" in unit and "-p" not in unit
    assert unit[unit.index("-o") + 1] == "short-iso"
    stamp = datetime.fromtimestamp(1764219603).astimezone().isoformat(timespec="seconds")
    assert f"--- kernel ---\n{stamp} milo-client kernel: <2>hwmon hwmon1: Undervoltage detected!" in text


# --------------------------------------------------------------------------- #
# `GET /diagnostic/journal` — a window of days, for the server's logs page
# --------------------------------------------------------------------------- #

class _Journalctl:
    """journalctl as the window reads spawn it: answers per read, by its name."""

    def __init__(self, answers):
        self.answers = answers  # read name -> (returncode, stdout, stderr)
        self.calls = []

    async def __call__(self, *args, **_):
        argv = list(args)
        self.calls.append(argv)
        name = ("kernel" if "_TRANSPORT=kernel" in argv
                else "text-level" if "-g" in argv else "warnings")
        code, out, err = self.answers.get(name, (0, "", ""))

        async def communicate():
            return out.encode(), err.encode()
        return SimpleNamespace(communicate=communicate, returncode=code,
                               kill=lambda: None, wait=None)


def _line(**fields):
    return json.dumps({"__REALTIME_TIMESTAMP": "1764219603000000", "PRIORITY": "6", **fields})


def test_the_window_is_read_at_warning_from_the_satellites_own_units_and_kernel(client, monkeypatch):
    """The free-text whitelist holds here too: a read names the satellite's
    units or the kernel's transport, never a bare read, which would answer for
    NetworkManager and wpa_supplicant and their SSIDs. Each read is the
    window asked, capped, newest kept, at warning — by priority, or by text
    for the two units whose level is in their text alone."""
    journalctl = _Journalctl({})
    monkeypatch.setattr(diagnostic.asyncio, "create_subprocess_exec", journalctl)

    body = client.get("/diagnostic/journal", params={"since": 100.5, "until": 200.25}).json()

    assert [read["name"] for read in body["reads"]] == ["warnings", "text-level", "kernel"]
    assert len(journalctl.calls) == 3
    for call in journalctl.calls:
        assert call[0] == "journalctl"
        assert "-u" in call or "_TRANSPORT=kernel" in call, call
        for index, token in enumerate(call):
            if token == "-u":
                assert call[index + 1].startswith("milo-client"), call
        assert call[call.index("--since") + 1] == "@100.500000"
        assert call[call.index("--until") + 1] == "@200.250000"
        assert call[call.index("-n") + 1] == str(diagnostic.JOURNAL_WINDOW_MAX_ENTRIES)
    warnings, text_level, kernel = journalctl.calls
    assert warnings[warnings.index("-p") + 1] == "warning"
    assert kernel[kernel.index("-p") + 1] == "warning"
    assert text_level[text_level.index("-p") + 1] == "notice..debug"
    assert text_level[text_level.index("-g") + 1] == diagnostic.TEXT_LEVEL_GREP


@pytest.mark.parametrize("message, kept", [
    ("2026-11-25 23:43:15,855 - services.equalizer.EqualizerService - ERROR - Not connected", True),
    ("2026-11-25 23:43:17,355 - services.equalizer.EqualizerService - WARNING - Failed to connect", True),
    ("2026-11-25 23:43:12.780842 WARN [camilladsp] <src/bin.rs:1061> Forcing a shutdown", True),
    ('INFO:     192.168.1.55:55848 - "PUT /equalizer/filters HTTP/1.1" 400 Bad Request', True),
    ('INFO:     192.168.1.55:58228 - "GET /diagnostic HTTP/1.1" 200 OK', False),
    ("2026-11-27 11:54:53.703065 INFO [camilladsp] <src/bin.rs:848> Running on linux, aarch64", False),
    ("2026-11-27 13:50:00,000 - services.registration - INFO - Registered", False),
])
def test_the_text_level_read_keeps_what_says_warning_or_worse(message, kept):
    """The pattern journalctl is handed, as written (PCRE2 and Python agree on
    it): a line the text read misses is a warning of the app or of CamillaDSP
    the page never shows, since journald filed it at priority 6."""
    import re
    assert bool(re.search(diagnostic.TEXT_LEVEL_GREP, message)) is kept


def test_each_read_answers_its_entries_its_cut_and_its_failure_alone(client, monkeypatch):
    """A read that fails costs its own entries, never the answer; a `-g` that
    matched nothing (journalctl exits 1, and says nothing) is an empty read,
    not a failure; a read that reached its cap says so, since its older lines
    in the window are missing."""
    monkeypatch.setattr(diagnostic, "JOURNAL_WINDOW_MAX_ENTRIES", 2)
    journalctl = _Journalctl({
        "warnings": (0, _line(MESSAGE="(Alsa) XRUN", PRIORITY="3") + "\n"
                     + _line(MESSAGE="(Snapclient) deprecated", PRIORITY="4") + "\n", ""),
        "text-level": (1, "", ""),
        "kernel": (1, "", "Failed to open journal: Permission denied"),
    })
    monkeypatch.setattr(diagnostic.asyncio, "create_subprocess_exec", journalctl)

    reads = {read["name"]: read for read in
             client.get("/diagnostic/journal", params={"since": 1, "until": 2}).json()["reads"]}

    assert [e["MESSAGE"] for e in reads["warnings"]["entries"]] == ["(Alsa) XRUN", "(Snapclient) deprecated"]
    assert reads["warnings"]["cut"] is True and reads["warnings"]["reason"] is None
    assert reads["text-level"] == {"name": "text-level", "kernel": False, "entries": [],
                                   "cut": False, "reason": None}
    assert reads["kernel"]["kernel"] is True and reads["kernel"]["entries"] == []
    assert reads["kernel"]["reason"] == "journalctl exited 1: Failed to open journal: Permission denied"


def test_a_window_that_ends_before_it_starts_is_refused(client):
    assert client.get("/diagnostic/journal", params={"since": 5, "until": 5}).status_code == 422
