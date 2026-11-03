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
import sys
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
