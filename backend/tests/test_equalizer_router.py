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
    with the whole backend suite still green; only `set_volume` and the three
    getters had a remote test. `test_milo_client_contract.py` proves the paths
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
