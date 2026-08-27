"""EqualizerRouter — the local half of the dispatch.

What breaks when these fail: a targeted EQ or volume command stops reaching the
server's own CamillaDSP, or reaches it and reports success when the DSP refused.
Consumers: `api/equalizer.py` (every `PUT /api/equalizer/target/{target}/...`),
`core/equalizer/multiroom_service.py` (the per-setting fan-out, which reaches
every method here through `getattr(self._equalizer_router, router_method)`), and
`core/volume/service.py`.

Why this file exists: measured 2026-08-23, **both** local branches of `_route`
were unexecuted by the whole backend suite — every existing test drives a
satellite. A single Milo with no satellite routes every EQ change through the
registry-miss fallback (`_route`, "client not in registry"), which nothing
exercised, and the local closures of the six setting methods were dark with it.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, Mock

from backend.core.multiroom.equalizer_router import EqualizerRouter
from backend.core.multiroom.models import Client


LOCAL_MAC = "aa:bb:cc:dd:ee:ff"
REMOTE_MAC = "dc:a6:32:7e:d3:43"

# (router method, kwargs, the CamillaDSPService method its local closure must reach)
LOCAL_SETTINGS = [
    ("set_volume", {"volume_db": -18.5}, "set_volume"),
    ("set_mute", {"muted": True}, "set_mute"),
    ("update_filter", {"filter_id": "eq_band_03",
                       "filter_data": {"freq": 250.0, "gain": 4.0, "q": 1.41,
                                       "filter_type": "Peaking"}}, "set_filter"),
    ("set_compressor", {"settings": {"enabled": True, "threshold": -25.0}}, "set_compressor"),
    ("set_loudness", {"settings": {"enabled": True, "high_boost": 6.0}}, "set_loudness"),
    ("set_mono", {"settings": {"enabled": True}}, "set_mono"),
]


@pytest.fixture
def camilladsp():
    """The local DSP, as EqualizerRouter's collaborator: every setter succeeds."""
    dsp = MagicMock()
    for name in ("set_volume", "set_mute", "set_filter", "set_compressor",
                 "set_loudness", "set_mono"):
        setattr(dsp, name, AsyncMock(return_value=True))
    return dsp


@pytest.fixture
def proxy():
    """The satellite transport. Nothing local may ever reach it."""
    svc = Mock()
    svc.request = AsyncMock(return_value={"status": "success"})
    return svc


@pytest.fixture
def registry():
    reg = Mock()
    reg.get_client = Mock(return_value=None)
    return reg


@pytest.fixture
def router(registry, camilladsp, proxy):
    return EqualizerRouter(registry, camilladsp, proxy)


class TestLocalRouting:
    """The two ways a command reaches this unit's own CamillaDSP."""

    @pytest.mark.parametrize("method,kwargs,dsp_method", LOCAL_SETTINGS)
    async def test_direct_mode_falls_back_to_the_local_dsp(
        self, router, camilladsp, proxy, registry, method, kwargs, dsp_method
    ):
        """An empty registry means one unit with no satellite — the default mode.

        The registry is populated only by Snapcast connections, so on a unit in
        direct mode `get_client` answers None for every mac. That fallback is the
        path every EQ change takes on such a unit, and routing it to the proxy
        instead would send it nowhere.
        """
        registry.get_client.return_value = None

        result = await getattr(router, method)(LOCAL_MAC, **kwargs)

        assert result["status"] == "success"
        assert getattr(camilladsp, dsp_method).await_count == 1
        proxy.request.assert_not_awaited()

    @pytest.mark.parametrize("method,kwargs,dsp_method", LOCAL_SETTINGS)
    async def test_a_registered_local_client_routes_to_the_dsp_not_the_proxy(
        self, router, camilladsp, proxy, registry, method, kwargs, dsp_method
    ):
        """127.0.0.1 is the server itself: it has no HTTP surface to be pushed to."""
        registry.get_client.return_value = Client(
            mac_id=LOCAL_MAC, name="Salon", ip="127.0.0.1", online=True
        )

        result = await getattr(router, method)(LOCAL_MAC, **kwargs)

        assert result["status"] == "success"
        assert getattr(camilladsp, dsp_method).await_count == 1
        proxy.request.assert_not_awaited()

    async def test_a_remote_client_still_reaches_the_proxy_and_not_the_dsp(
        self, router, camilladsp, proxy, registry
    ):
        """The other half of the same decision — without it the two above would
        pass on a router that had stopped routing anywhere but locally."""
        registry.get_client.return_value = Client(
            mac_id=REMOTE_MAC, name="Canape", ip="192.168.1.153", online=True
        )

        result = await router.set_volume(REMOTE_MAC, -18.5)

        assert result["status"] == "success"
        proxy.request.assert_awaited_once()
        camilladsp.set_volume.assert_not_awaited()


class TestLocalVerdictReachesTheEnvelope:
    """A DSP that refused must not be reported as a success."""

    @pytest.mark.parametrize("method,kwargs,dsp_method", LOCAL_SETTINGS)
    async def test_a_refusing_dsp_makes_the_envelope_an_error(
        self, router, camilladsp, registry, method, kwargs, dsp_method
    ):
        """CamillaDSPService returns False when the daemon is down, and the
        caller (`multiroom_service`) reads `.get("status") != "error"`. A local
        refusal that came back as "success" is the success-on-failure class this
        repo has paid for most.
        """
        registry.get_client.return_value = None
        getattr(camilladsp, dsp_method).return_value = False

        result = await getattr(router, method)(LOCAL_MAC, **kwargs)

        # Non-triviality first: an error envelope also comes out of a body that
        # never ran, so the call has to be shown to have happened.
        assert getattr(camilladsp, dsp_method).await_count == 1
        assert result["status"] == "error"
        assert "success" not in result


class TestLocalRoutingIsRefused:
    """The two cases that must not reach the DSP at all."""

    async def test_a_dac_client_is_skipped_before_any_routing(
        self, router, camilladsp, proxy, registry
    ):
        """A DAC card's amp owns the level: Milo attenuating too would stack
        two attenuations on one signal."""
        registry.get_client.return_value = Client(
            mac_id=LOCAL_MAC, name="DAC", ip="127.0.0.1", online=True,
            volume_control=False,
        )

        result = await router.set_volume(LOCAL_MAC, -18.5)

        assert result == {"status": "skipped", "reason": "external_volume_control"}
        camilladsp.set_volume.assert_not_awaited()
        proxy.request.assert_not_awaited()

    async def test_no_registry_and_no_dsp_reports_an_error_envelope(self, proxy):
        """Nothing to route to is an error, never a silent success."""
        router = EqualizerRouter(None, None, proxy)

        result = await router.set_volume(LOCAL_MAC, -18.5)

        assert result["status"] == "error"
        assert "success" not in result
        proxy.request.assert_not_awaited()


# (router method, kwargs, the satellite path its remote closure must PUT to)
REMOTE_SETTINGS = [
    ("set_mute", {"muted": True}, "/equalizer/mute", {"muted": True}),
    ("update_filter",
     {"filter_id": "eq_band_03",
      "filter_data": {"freq": 250.0, "gain": 4.0, "q": 1.41, "filter_type": "Peaking"}},
     "/equalizer/filter/eq_band_03",
     {"freq": 250.0, "gain": 4.0, "q": 1.41, "filter_type": "Peaking"}),
    ("set_compressor", {"settings": {"enabled": True, "threshold": -25.0}},
     "/equalizer/compressor", {"enabled": True, "threshold": -25.0}),
    ("set_loudness", {"settings": {"enabled": True, "high_boost": 6.0}},
     "/equalizer/loudness", {"enabled": True, "high_boost": 6.0}),
    ("set_mono", {"settings": {"enabled": False}}, "/equalizer/mono", {"enabled": False}),
]


class TestRemoteRouting:
    """The satellite half of the same dispatch, for the five EQ writes.

    What breaks when these fail: a mute, a band, the compressor, loudness or
    mono changed from the UI stops reaching Canapé or Bureau — or reaches the
    wrong endpoint on it, which the satellite answers 200 to while changing
    something else.

    Why this class exists: measured 2026-08-25 by the Lot A eviscration sweep.
    The remote closure of these five could each be replaced by `return None`
    with the whole backend suite still green; only `set_volume` had a remote
    test. (The three getters had none either — they were outside the sweep's
    population because they never ran at all; TestReads below covers them.)
    `test_milo_client_contract.py` proves the paths
    exist and that the satellite reads every key sent — it cannot see which of
    them a given setting is sent to, and the four dict-bodied settings are
    indistinguishable to it.

    Each body here is made distinct on purpose: with `{"enabled": True}` for all
    of them, swapping two endpoints would keep every assertion green.
    """

    @pytest.mark.parametrize("method,kwargs,path,body", REMOTE_SETTINGS)
    async def test_the_setting_is_put_to_its_own_endpoint_on_the_satellite(
        self, router, camilladsp, proxy, registry, method, kwargs, path, body
    ):
        registry.get_client.return_value = Client(
            mac_id=REMOTE_MAC, name="Canape", ip="192.168.1.153", online=True
        )

        result = await getattr(router, method)(REMOTE_MAC, **kwargs)

        assert result["status"] == "success"
        proxy.request.assert_awaited_once_with("192.168.1.153", "PUT", path, body)
        for dsp_method in ("set_mute", "set_filter", "set_compressor",
                           "set_loudness", "set_mono"):
            getattr(camilladsp, dsp_method).assert_not_awaited()


# (router method, the satellite path its remote closure must GET)
REMOTE_READS = [
    ("get_status", "/equalizer/status"),
    ("get_volume", "/equalizer/volume"),
]


class TestReads:
    """The read half of the same dispatch — status, levels and volume.

    What breaks when these fail: the EQ tab of a satellite shows the *server's*
    DSP instead of the speaker's (`api/equalizer.py::get_equalizer_status`), the
    VU meters of a satellite animate on the wrong DSP
    (`core/equalizer/levels_monitor.py`), or `core/volume/service.py` reads back
    a level that belongs to another machine. All three answer 200 while lying,
    which is why the endpoint each one asks for is what is asserted.

    Why this class exists: measured 2026-08-25, `get_status`, `get_levels` and
    `get_volume` had **zero** executed lines under the whole backend suite —
    neither arm, not even the `_route` call itself. They were invisible to the
    Lot A sweep for that very reason: a method that never runs is not a mutation
    target. That is the case where reading the lines is the only instrument.
    """

    @pytest.mark.parametrize("method,path", REMOTE_READS)
    async def test_a_remote_read_asks_the_satellite_its_own_endpoint(
        self, router, camilladsp, proxy, registry, method, path
    ):
        registry.get_client.return_value = Client(
            mac_id=REMOTE_MAC, name="Canape", ip="192.168.1.153", online=True
        )

        await getattr(router, method)(REMOTE_MAC)

        proxy.request.assert_awaited_once_with("192.168.1.153", "GET", path)

    async def test_a_remote_levels_read_uses_the_transport_s_own_helper(
        self, router, proxy, registry
    ):
        """Levels are the one read that does not go through `proxy.request`.

        `get_equalizer_levels` exists because levels are polled several times a
        second and answered non-raising; routing them through the raising
        `request` would turn a satellite blip into an error banner.
        """
        registry.get_client.return_value = Client(
            mac_id=REMOTE_MAC, name="Canape", ip="192.168.1.153", online=True
        )
        proxy.get_equalizer_levels = AsyncMock(return_value={"rms": [-30.0, -30.5]})

        result = await router.get_levels(REMOTE_MAC)

        proxy.get_equalizer_levels.assert_awaited_once_with("192.168.1.153")
        proxy.request.assert_not_awaited()
        assert result == {"rms": [-30.0, -30.5]}

    async def test_a_local_read_asks_this_unit_s_dsp_and_not_the_satellite(
        self, router, camilladsp, proxy, registry
    ):
        """The registry-miss fallback again: on a unit with no satellite, every
        read takes this path."""
        camilladsp.get_status = AsyncMock(return_value={"available": True, "bypassed": False})
        camilladsp.get_levels = AsyncMock(return_value={"rms": [-12.0, -12.0]})

        assert await router.get_status(LOCAL_MAC) == {"available": True, "bypassed": False}
        assert await router.get_levels(LOCAL_MAC) == {"rms": [-12.0, -12.0]}
        proxy.request.assert_not_awaited()

    async def test_a_local_volume_read_is_reshaped_for_its_caller(
        self, router, camilladsp, registry
    ):
        """`core/volume/service.py` reads `main` and `mute` — CamillaDSP's own
        answer carries more, and the remote arm returns the satellite's body
        verbatim, so this closure is the only place the two shapes are made to
        agree."""
        camilladsp.get_volume = AsyncMock(
            return_value={"main": -22.5, "mute": True, "clipped_samples": 0})

        assert await router.get_volume(LOCAL_MAC) == {"main": -22.5, "mute": True}

    async def test_a_registered_local_client_without_a_dsp_answers_the_declared_default(
        self, registry, proxy
    ):
        """A dev host with no CamillaDSP must answer a level, not raise into the
        volume service's boot path — and not restate the constant either."""
        from backend.config.constants import DEFAULT_VOLUME_DB

        registry.get_client.return_value = Client(
            mac_id=LOCAL_MAC, name="Milo", ip="127.0.0.1", online=True
        )
        router = EqualizerRouter(registry, None, proxy)

        assert await router.get_volume(LOCAL_MAC) == {"main": DEFAULT_VOLUME_DB, "mute": False}
        proxy.request.assert_not_awaited()

    async def test_a_satellite_without_a_transport_never_falls_back_to_the_local_dsp(
        self, camilladsp, registry
    ):
        """Answering a satellite target from this unit's DSP is worse than failing.

        A read would report the living room's EQ as the satellite's, and the
        same `_route` guard covers the writes: it would apply a satellite's
        settings to the server's own speaker.
        """
        registry.get_client.return_value = Client(
            mac_id=REMOTE_MAC, name="Canape", ip="192.168.1.153", online=True
        )
        router = EqualizerRouter(registry, camilladsp, None)

        result = await router.get_status(REMOTE_MAC)

        assert result["status"] == "error"
        camilladsp.get_status.assert_not_called()


class TestTheLocalArmWithoutCamillaDSP:
    """The nine local arms when CamillaDSP is not wired.

    Measured 2026-08-27: eight identical refusals, none of them ever executed.
    The service is optional in the constructor, and `dependencies.py` builds it
    lazily — so a boot that reordered the wiring, or a dev host with no daemon,
    reaches every one of these. What they must not do is answer as though the
    write landed: `EqualizerRouter` is the transport behind
    `PUT /api/equalizer/target/local/...`, and the route's envelope is read
    verbatim by the UI. The two getters answer `available: False` instead of an
    error envelope because they feed a status panel, not a write.
    """

    @pytest.fixture
    def router_without_camilla(self, registry, proxy):
        # A registered LOCAL client: without one `_route` answers its own
        # "client not found" refusal and the eight inner arms stay unreached.
        registry.get_client.return_value = Client(
            mac_id=LOCAL_MAC, name="Milō", ip="127.0.0.1", online=True
        )
        return EqualizerRouter(registry, None, proxy)

    @pytest.mark.parametrize("method,kwargs,_dsp", LOCAL_SETTINGS)
    async def test_a_write_without_the_daemon_is_an_error_envelope(
        self, router_without_camilla, method, kwargs, _dsp
    ):
        result = await getattr(router_without_camilla, method)(LOCAL_MAC, **kwargs)

        assert result["status"] == "error"
        assert "not available" in result["message"]
        assert "success" not in result, "the envelope rule: never a bare boolean"

    async def test_the_status_read_says_unavailable_rather_than_erroring(
        self, router_without_camilla
    ):
        """It backs the EQ tab's header; an error envelope there is a fault
        banner every time the tab is opened on a host with no daemon."""
        result = await router_without_camilla.get_status(LOCAL_MAC)

        assert result["available"] is False
        assert "not available" in result["error"]

    async def test_the_levels_read_says_unavailable_and_nothing_else(
        self, router_without_camilla
    ):
        """The VU meters poll this at 10 Hz. A message field here would be
        rebuilt ten times a second for a condition that does not change."""
        assert await router_without_camilla.get_levels(LOCAL_MAC) == {"available": False}
