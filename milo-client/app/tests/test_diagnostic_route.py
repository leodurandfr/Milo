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


def test_a_journal_that_does_not_answer_is_named_rather_than_silent(client, monkeypatch):
    """journalctl needs the `adm` group, and the two satellites are not
    provisioned identically — one runs its app as `milo-client`, the other as
    `milo`. A satellite whose account cannot read the journal must say so, not
    return a report that looks complete and holds no logs."""
    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(diagnostic, "_run", nothing)
    body = client.get("/diagnostic").json()

    reasons = {item["section"] for item in body["unavailable"]}
    assert "journal" in reasons
    assert "units" in reasons


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
