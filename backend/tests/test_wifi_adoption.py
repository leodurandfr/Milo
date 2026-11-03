"""WifiAdoptionService — the guards that stand in front of nmcli.

What breaks when these fail: `POST /api/discovery/adopt-speaker` carries a
caller-chosen `ssid` (`AdoptSpeakerRequest.ssid` is a free-form `str`), and the
first thing `_connect_to_hotspot` does with it is `nmcli connection delete
<ssid>`. The `ssid != HOTSPOT_NAME` check is the only thing between that body
and the deletion of any NetworkManager profile on this server, its own home
wifi included. The hotspot check is what stops a fresh server from taking the
radio from the setup AP the operator is connected through, then trying to adopt
itself.
Consumer: `api/discovery.py::adopt_speaker`, which maps `AdoptionError.code`
onto an HTTP status.

Why this file exists: measured 2026-08-23, the module ran at 23 % of its lines
under the whole suite — the `def` lines and `__init__`, nothing else. No test
had ever built an AdoptionError.
"""
import asyncio
import contextlib

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.core.multiroom import wifi_adoption
from backend.core.multiroom.wifi_adoption import AdoptionError, WifiAdoptionService
from backend.core.network.service import ADOPTION_PROFILE, HOTSPOT_NAME


AUDIO = {
    "audio_id": "hifiberry-dacplus",
    "speaker_name": "Bureau",
    "speaker_type": "bookshelf",
}


@pytest.fixture
def service(monkeypatch):
    """The service with every subprocess spawn wired to explode.

    A spy asserted afterwards would be too late to be honest here: this checkout
    runs on the appliance, and a guard that lets go spawns `nmcli connection
    delete` against this very host's NetworkManager. The spawn has to fail, not
    be recorded.
    """
    def _never(*args, **kwargs):
        raise AssertionError(f"a subprocess was spawned past the guards: {args}")

    monkeypatch.setattr(wifi_adoption.asyncio, "create_subprocess_exec", _never)
    network = MagicMock()
    network.hotspot_active = False
    network.get_active_wifi_profile = AsyncMock(return_value=None)
    return WifiAdoptionService(network_service=network)


async def test_a_foreign_ssid_is_refused_before_nmcli_runs(service):
    """The one guard between a request body and `nmcli connection delete <name>`."""
    with pytest.raises(AdoptionError) as exc:
        await service.adopt_speaker(
            ssid="preconfigured", wifi_ssid="Maison", wifi_password="secret", **AUDIO
        )

    assert exc.value.code == "invalid_ssid"
    # The refused name is echoed back: proof this is the SSID branch and not
    # some other AdoptionError raised further down the flow.
    assert "preconfigured" in exc.value.detail


async def test_adoption_is_refused_while_the_server_broadcasts_the_setup_hotspot(service):
    """The AP holds the server's one radio: joining a speaker's hotspot would
    take it from the clients that reach the server through it."""
    service.network_service.hotspot_active = True

    with pytest.raises(AdoptionError) as exc:
        await service.adopt_speaker(
            ssid=HOTSPOT_NAME, wifi_ssid="Maison", wifi_password="secret", **AUDIO
        )

    assert exc.value.code == "server_in_hotspot_mode"


async def test_an_empty_target_wifi_is_refused_before_nmcli_runs(service):
    """Without it the server drops its LAN for ~30 s to push a config that
    cannot work, and the speaker comes back on no network at all."""
    with pytest.raises(AdoptionError) as exc:
        await service.adopt_speaker(
            ssid=HOTSPOT_NAME, wifi_ssid="", wifi_password="", **AUDIO
        )

    assert exc.value.code == "invalid_target_wifi"


@pytest.mark.parametrize("unbuildable", [{"hidden": True}, {"key_mgmt": "sae"}])
async def test_a_hidden_or_wpa3_server_network_is_refused_before_the_speaker_reboots(
    service, speaker, unbuildable
):
    """The speaker rebuilds its target from an SSID and a PSK alone, as
    wpa-psk and never hidden: handed the server's hidden or WPA3 network, it
    answers 200, reboots onto a profile that never associates, and only a cable
    or a reflash brings it back. Refused before the server leaves its network
    (the fixture fails any spawn) and before the push."""
    service.network_service.get_active_wifi_profile.return_value = {
        "ssid": "Maison", "password": "secret", "hidden": False, "key_mgmt": "wpa-psk", **unbuildable,
    }

    with pytest.raises(AdoptionError) as exc:
        await service.adopt_speaker(
            ssid=HOTSPOT_NAME, wifi_ssid="Maison", wifi_password="secret", **AUDIO
        )

    assert exc.value.code == "unsupported_target_wifi"
    assert speaker.posts == []


async def test_a_target_wifi_other_than_the_servers_own_is_not_judged_by_it(adopting, speaker):
    """A network typed by hand cannot be read from here: the server's own
    profile says nothing about it, so a hidden server network refuses nothing
    else."""
    adopting.network_service.get_active_wifi_profile.return_value = {**MAISON, "ssid": "Grenier", "hidden": True}

    await _adopt(adopting)

    assert [body["wifi_ssid"] for _, body in speaker.posts] == ["Maison"]


# =============================================================================
# The orchestration itself
# =============================================================================

class _Proc:
    """One spawned process, scripted."""

    def __init__(self, returncode, stdout, stderr, hangs=False):
        self.returncode = returncode
        self._out = stdout.encode()
        self._err = stderr.encode()
        self._hangs = hangs
        self.killed = False

    async def communicate(self):
        if self._hangs:
            await asyncio.sleep(3600)
        return self._out, self._err

    def kill(self):
        self.killed = True


class _Spawns:
    """Stand-in for every process this service starts, in order.

    The boundary is `asyncio.create_subprocess_exec`, not `_run_nmcli`: patching
    the private would pin a method name and hide the argv, and the argv is the
    contract — `nmcli connection delete <name>` is destructive and
    `connection.autoconnect no` is what keeps a crashed adoption from leaving
    this server permanently rejoining the speaker.

    The replacement is a real coroutine function on purpose: an ordinary
    callable makes `create_subprocess_exec` behave like a MagicMock and the
    awaits below fail for a reason that has nothing to do with the code.
    """

    def __init__(self):
        self.calls = []
        self.scripted = {}
        self.procs = []

    def script(self, match: str, returncode=0, stdout="", stderr="", hangs=False):
        self.scripted[match] = (returncode, stdout, stderr, hangs)

    async def __call__(self, program, *args, **kwargs):
        argv = (program, *args)
        self.calls.append(argv)
        joined = " ".join(argv)
        for match, result in self.scripted.items():
            if match in joined:
                proc = _Proc(*result)
                break
        else:
            proc = _Proc(0, "", "")
        self.procs.append(proc)
        return proc

    def argv_containing(self, *fragments):
        return [c for c in self.calls if all(f in " ".join(c) for f in fragments)]

    def indexes_of(self, *fragments):
        return [i for i, call in enumerate(self.calls)
                if all(f in " ".join(call) for f in fragments)]

    def index_of(self, *fragments):
        found = self.indexes_of(*fragments)
        if not found:
            raise AssertionError(f"no spawn matching {fragments}: {self.calls}")
        return found[0]


# `nmcli -t -f NAME,DEVICE connection show --active` as a wifi server answers it:
# the wired link, the server's own setup hotspot, and the home wifi on wlan0.
ACTIVE_WIFI_SERVER = f"Wired connection 1:eth0\n{HOTSPOT_NAME}:wlan1\nMaison:wlan0"
# The same on this appliance, which is wired: nothing on wlan0 to restore.
ACTIVE_WIRED_SERVER = "Wired connection 1:eth0"

GATEWAY_LINE = "default via 10.42.0.1 dev wlan0 proto dhcp metric 600"


@pytest.fixture
def spawns(monkeypatch):
    spawns = _Spawns()
    spawns.script("connection show --active", stdout=ACTIVE_WIFI_SERVER)
    spawns.script("ip -4 route show default", stdout=GATEWAY_LINE)
    monkeypatch.setattr(wifi_adoption.asyncio, "create_subprocess_exec", spawns)
    # The lease poll is wall-clock bounded, and this checkout runs ON the
    # appliance: left at its 15 s production deadline, a regression that stops
    # the gateway from parsing turns every test here into a 15 s spin at full
    # CPU — measured, and CPU starvation on this Pi is what desynchronises
    # snapcast in the next room. Nothing below asserts on either value.
    monkeypatch.setattr(wifi_adoption, "GATEWAY_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(wifi_adoption, "GATEWAY_WAIT_TIMEOUT", 0.05)
    return spawns


@pytest.fixture
def speaker(monkeypatch):
    """The fresh speaker's `/api/setup/become-client`, as an HTTP stand-in."""
    class _Speaker:
        """What the speaker answered, and what it was asked."""

        def __init__(self):
            self.status = 200
            self.posts = []

    speaker = _Speaker()

    class _Response:
        def __init__(self):
            self.status = speaker.status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def text(self):
            return ""

    class _Session:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, **kwargs):
            speaker.posts.append((url, kwargs.get("json")))
            return _Response()

    monkeypatch.setattr(wifi_adoption.aiohttp, "ClientSession", _Session)
    return speaker


# What `NetworkService.get_active_wifi_profile` reads for the home wifi above.
MAISON = {"ssid": "Maison", "password": "secret", "hidden": False, "key_mgmt": "wpa-psk"}


@pytest.fixture
def adopting(spawns, speaker):
    network = MagicMock()
    network.hotspot_active = False
    network.get_active_wifi_profile = AsyncMock(return_value=MAISON)
    return WifiAdoptionService(network_service=network)


async def _adopt(service):
    return await service.adopt_speaker(
        ssid=HOTSPOT_NAME, wifi_ssid="Maison", wifi_password="secret", **AUDIO
    )


class TestAdoptionOrchestration:
    """The server leaves its own network to reach the speaker — and must come back.

    What breaks when these fail: `_adopt_impl` walks the server off its home
    wifi onto the speaker's setup hotspot, and the `finally` that deletes the
    temporary profile and brings the home connection back is the only thing that
    returns it. If it stops firing, a wifi server sits on a hotspot that stops
    broadcasting the moment the speaker reboots — no LAN, no UI, nothing left to
    fix it with. Measured 2026-08-25: the module ran at 29 % of its lines and
    every line below the three entry guards was dark.

    Consumer: `POST /api/discovery/adopt-speaker`, which maps `AdoptionError.code`
    onto an HTTP status the setup wizard reads.
    """

    async def test_the_home_wifi_is_read_before_the_server_leaves_it(self, adopting, spawns):
        """Order is the whole point: reading it after joining the hotspot would
        capture the hotspot, and `_restore_connection` would bring *that* back."""
        await _adopt(adopting)

        assert spawns.index_of("connection", "show", "--active") < \
            spawns.index_of("connection", "up", ADOPTION_PROFILE)

    async def test_the_temp_profile_is_created_without_autoconnect(self, adopting, spawns):
        """The profile is torn down in a `finally`; a crash or power cut before
        it would otherwise leave this server rejoining the speaker for good."""
        await _adopt(adopting)

        added = spawns.argv_containing("connection", "add")
        assert len(added) == 1
        assert "connection.autoconnect" in added[0]
        assert added[0][added[0].index("connection.autoconnect") + 1] == "no"

    async def test_the_speaker_is_handed_the_home_credentials_at_its_gateway(
        self, adopting, speaker
    ):
        """The gateway comes from the DHCP lease the hotspot served, never a
        constant — a speaker on a different subnet is still reachable."""
        result = await _adopt(adopting)

        assert result == {"ssid": HOTSPOT_NAME, "gateway": "10.42.0.1"}
        url, body = speaker.posts[0]
        assert url == "http://10.42.0.1:8000/api/setup/become-client"
        assert body == {"audio_id": AUDIO["audio_id"], "speaker_name": AUDIO["speaker_name"],
                        "speaker_type": AUDIO["speaker_type"],
                        "wifi_ssid": "Maison", "wifi_password": "secret"}

    async def test_the_server_comes_home_after_a_successful_adoption(self, adopting, spawns):
        """The temp profile is deleted AFTER it was used, not only before it.

        `_connect_to_hotspot` already deletes any leftover of the same name on
        the way in, so asserting that a delete merely happened would be
        satisfied by that one and blind to the teardown disappearing.
        """
        await _adopt(adopting)

        deletes = spawns.indexes_of("connection", "delete", ADOPTION_PROFILE)
        assert len(deletes) == 2, "one on the way in, one on the way out"
        assert deletes[-1] > spawns.index_of("connection", "up", ADOPTION_PROFILE)
        assert spawns.argv_containing("connection", "up", "Maison")

    async def test_the_server_comes_home_when_the_speaker_rejects_the_push(
        self, adopting, spawns, speaker
    ):
        """The failure that matters: the speaker answered, and answered no.

        Everything up to here succeeded, so the server is on the hotspot with
        its home wifi down. Losing the `finally` here is what strands it.
        """
        speaker.status = 500

        with pytest.raises(AdoptionError) as exc:
            await _adopt(adopting)

        assert exc.value.code == "push_rejected"
        assert len(spawns.indexes_of("connection", "delete", ADOPTION_PROFILE)) == 2
        assert spawns.argv_containing("connection", "up", "Maison")

    async def test_an_already_configured_speaker_is_named_as_such(self, adopting, speaker):
        """409 is the speaker saying setup_completed is already true. The wizard
        maps the code to its own message; a generic push_rejected would send the
        operator looking for a network fault that is not there."""
        speaker.status = 409

        with pytest.raises(AdoptionError) as exc:
            await _adopt(adopting)

        assert exc.value.code == "already_configured"

    async def test_the_server_comes_home_when_no_lease_ever_arrives(
        self, adopting, spawns, monkeypatch
    ):
        """Associated but never addressed — the speaker's DHCP never answered."""
        monkeypatch.setattr(wifi_adoption, "GATEWAY_WAIT_TIMEOUT", 0)
        spawns.script("ip -4 route show default", stdout="")

        with pytest.raises(AdoptionError) as exc:
            await _adopt(adopting)

        assert exc.value.code == "no_gateway"
        assert spawns.argv_containing("connection", "up", "Maison")

    async def test_a_failed_association_is_cleaned_up_on_its_own_path(
        self, adopting, spawns
    ):
        """`_connect_to_hotspot` raises before the `try/finally` is entered, so
        its cleanup is a second, separate site — one that is easy to forget."""
        spawns.script(f"connection up {ADOPTION_PROFILE}", returncode=4, stderr="no such network")

        with pytest.raises(AdoptionError) as exc:
            await _adopt(adopting)

        assert exc.value.code == "hotspot_connect_failed"
        assert len(spawns.indexes_of("connection", "delete", ADOPTION_PROFILE)) == 2
        assert spawns.argv_containing("connection", "up", "Maison")

    async def test_an_nmcli_that_never_returns_is_killed_and_reported(
        self, adopting, spawns, monkeypatch
    ):
        """A hung `connection up` must not hold the adoption lock forever."""
        monkeypatch.setattr(wifi_adoption, "HOTSPOT_CONNECT_TIMEOUT", 0.01)
        spawns.script(f"connection up {ADOPTION_PROFILE}", hangs=True)

        with pytest.raises(AdoptionError) as exc:
            await _adopt(adopting)

        assert exc.value.code == "hotspot_connect_failed"
        assert any(p.killed for p in spawns.procs), "the hung process was left running"

    async def test_a_refused_restore_falls_back_to_releasing_the_interface(
        self, adopting, spawns
    ):
        """The home AP can still be hidden by the speaker's hotspot when we come
        back. Releasing wlan0 lets NM reconnect on its own once it stops."""
        spawns.script("connection up Maison", returncode=4, stderr="unavailable")

        await _adopt(adopting)

        assert spawns.argv_containing("device", "disconnect", "wlan0")


class TestTheAdoptionAndTheRecoveryAccessPoint:
    """Two owners, one radio: the adoption and NetworkService's recovery AP.

    What breaks when these fail: the adoption's temporary profile was named
    `Milō`, like the AP. On a WiFi-only server the adoption leaves the home
    network, NetworkService reads "no link" and, 45 s later, raises its AP —
    whose first step deletes the profile named `Milō` the push was riding, and
    the adoption's cleanup then deletes the AP's. Consumer:
    `NetworkService.recovery_held`, the hold the adoption runs inside.
    """

    async def test_the_temporary_profile_is_not_named_like_the_access_point(self, adopting, spawns):
        """It joins the SSID `Milō`, under a profile name the AP never uses."""
        await _adopt(adopting)

        added = spawns.argv_containing("connection", "add")[0]
        assert added[added.index("ssid") + 1] == HOTSPOT_NAME
        assert added[added.index("con-name") + 1] != HOTSPOT_NAME
        deleted = [call[-1] for call in spawns.calls if call[1:3] == ("connection", "delete")]
        assert deleted, "the adoption deleted nothing at all"
        assert HOTSPOT_NAME not in deleted, "the adoption deleted the access point's profile"

    async def test_the_recovery_ap_is_held_from_the_hotspot_until_home_is_restored(
        self, adopting, spawns
    ):
        """Held before the server leaves its network, released only once it is
        back on it — a hold ending at the push would let the timer fire during
        a restore that takes its 30 s."""
        @contextlib.asynccontextmanager
        async def recovery_held():
            spawns.calls.append(("<hold>",))
            yield
            spawns.calls.append(("</hold>",))

        adopting.network_service.recovery_held = recovery_held

        await _adopt(adopting)

        held = spawns.calls.index(("<hold>",))
        released = spawns.calls.index(("</hold>",))
        assert held < spawns.index_of("connection", "up", ADOPTION_PROFILE)
        assert spawns.index_of("connection", "up", "Maison") < released

    async def test_an_access_point_raised_as_the_hold_begins_refuses_the_adoption(
        self, adopting, spawns
    ):
        """Entering the hold lets a timer already raising the AP finish: read
        before it, `hotspot_active` would miss that AP and the adoption would
        take the radio it is coming up on."""
        @contextlib.asynccontextmanager
        async def recovery_held():
            adopting.network_service.hotspot_active = True
            yield

        adopting.network_service.recovery_held = recovery_held

        with pytest.raises(AdoptionError) as exc:
            await _adopt(adopting)

        assert exc.value.code == "server_in_hotspot_mode"
        assert spawns.calls == []


class TestWhichConnectionIsRestored:
    """`_get_active_wifi_name` picks what the server is put back onto."""

    async def test_the_servers_own_setup_hotspot_is_never_taken_for_the_home_wifi(
        self, adopting, spawns
    ):
        """Both profiles are named `Milō`. Restoring the server's own hotspot
        would leave it broadcasting a setup AP instead of rejoining the house."""
        spawns.script("connection show --active",
                      stdout=f"{HOTSPOT_NAME}:wlan0\nWired connection 1:eth0")

        await _adopt(adopting)

        assert not spawns.argv_containing("connection", "up", "Wired connection 1")
        # Counted, not merely absent: the hotspot IS brought up once, on the way
        # in. Filtering that name out of the restore check is what made an
        # earlier version of this test blind to the very guard it names.
        assert len(spawns.indexes_of("connection", "up", HOTSPOT_NAME)) == 1
        assert spawns.indexes_of("connection", "up", HOTSPOT_NAME) == \
            spawns.indexes_of("connection", "up", ADOPTION_PROFILE)

    async def test_a_wired_server_has_no_wifi_to_restore(self, adopting, spawns):
        """This appliance is wired: nothing on wlan0 before, nothing to bring
        back after. A restore attempt here would activate a profile nobody asked
        for."""
        spawns.script("connection show --active", stdout=ACTIVE_WIRED_SERVER)

        await _adopt(adopting)

        assert spawns.indexes_of("connection", "up") == \
            spawns.indexes_of("connection", "up", ADOPTION_PROFILE)
        assert not spawns.argv_containing("device", "disconnect")

    async def test_a_name_carrying_a_colon_survives_the_terse_parser(
        self, adopting, spawns
    ):
        """nmcli terse output escapes a literal ':' in an SSID. Splitting on the
        raw separator would restore a truncated name — i.e. nothing."""
        spawns.script("connection show --active",
                      stdout="Chez\\:Moi:wlan0")

        await _adopt(adopting)

        assert spawns.argv_containing("connection", "up", "Chez:Moi")
