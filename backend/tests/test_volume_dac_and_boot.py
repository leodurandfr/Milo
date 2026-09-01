"""DAC mode, the boot volume push, and the zone half of the registry bus.

Three unmeasured areas of `core/volume/`, all of which decide what the room
actually plays.

**DAC mode is a whole configuration of the appliance that no test entered.**
`_volume_control = False` means an external amplifier owns the level, and Milō's
contract is then to pin CamillaDSP at exactly 0 dB and never attenuate. Every
arm of it was at zero: `initialize`, `_apply_startup_volume`, the reconnect
re-pin, `set_volume_db`/`adjust_volume_db`, and the runtime flip. The failure is
symmetric and both halves are loud — a managed unit that takes the DAC arm goes
to 0 dB, i.e. full output, because the card's own mixer is pinned at unity by
`milo-alsa-passthrough`; a DAC unit that takes the managed arm attenuates a
signal the amplifier then amplifies again.

**The boot push** (`_do_push_volume_to_all_clients`) is what restores every
client's own level after a restart, and its lazy read of the local CamillaDSP
volume is the fallback for a client with nothing persisted.

**The zone arms of `VolumeStateStore._handle_registry_event`** were at zero. The
registry bus is the one internal event system, `_emit_event` its only producer,
and a `.get()` on a key the producer does not send skips its arm in silence —
which is exactly how a renamed identifier once left two dead handlers behind.
The keys asserted here are read off the producer, not invented.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest

from backend.config.constants import DEFAULT_VOLUME_DB
from backend.core.models.volume import VolumeConfig
from backend.core.settings import SettingsService
from backend.core.multiroom.models import RegistryEventType
from backend.core.volume import VolumeService, VolumeStateStore


@pytest.fixture
def camilladsp():
    dsp = Mock()
    dsp.set_volume = AsyncMock(return_value=True)
    dsp.set_mute = AsyncMock(return_value=True)
    dsp.get_volume = AsyncMock(return_value={"main": -30.0, "mute": False})
    dsp.is_volume_control_available = Mock(return_value=True)
    dsp.wait_for_connection = AsyncMock(return_value=True)
    return dsp


@pytest.fixture
def state_machine():
    sm = Mock()
    sm.broadcast = AsyncMock()
    sm.routing_service = Mock()
    sm.routing_service.get_state = Mock(return_value={"multiroom_enabled": False})
    return sm


@pytest.fixture
def service(state_machine, camilladsp, tmp_path, monkeypatch):
    """A VolumeService whose store persists into `tmp_path`.

    `VolumeStateStore.STORAGE_PATH` is /var/lib/milo/last_volume.json on this
    machine and the live backend rewrites it as the owner turns the knob. The
    appliance-data guard refuses the write, but redirecting is what makes the
    written content assertable instead of merely refused.
    """
    monkeypatch.setattr(VolumeStateStore, "STORAGE_PATH", tmp_path / "last_volume.json")
    settings = Mock()

    # `_load_volume_config` reads every key of the `volume` section with no
    # fallback operand, by design — so answering None for it makes boot log
    # "Error loading volume config" and keep the built-in defaults, which is a
    # degraded path the tests here do not mean to be on. The one test that wants
    # that failure injects it on `_load_volume_config` itself.
    async def _get_setting(key, *a, **kw):
        return SettingsService().defaults["volume"] if key == "volume" else None

    settings.get_setting = AsyncMock(side_effect=_get_setting)
    settings.set_setting = AsyncMock()
    settings.invalidate_cache = Mock()
    svc = VolumeService(
        state_machine=state_machine,
        snapcast_service=Mock(),
        settings_service=settings,
        camilladsp_service=camilladsp,
        equalizer_client_proxy_service=Mock(),
    )
    return svc


class TestDacMode:
    """`volume_control = False`: an external amplifier owns the level."""

    async def test_startup_pins_the_dsp_at_unity_and_unmutes(self, service, camilladsp):
        """0 dB exactly, and nothing else — the amplifier expects line level.

        Any attenuation applied here is applied twice, since the amp then makes
        it up; the user compensates on the amp, and the next managed boot is
        brutally loud.
        """
        service._volume_control = False

        await service._apply_startup_volume()

        camilladsp.set_volume.assert_awaited_once_with(0.0)
        camilladsp.set_mute.assert_awaited_once_with(False)

    async def test_startup_in_dac_mode_ignores_the_persisted_level(
        self, service, camilladsp
    ):
        """The store still tracks a level (the UI shows one); DAC mode must not
        push it. Reading it here is how a −45 dB stored value would silence a
        system whose amplifier is at its normal setting."""
        service._volume_control = False
        service._state_store.ensure_local_client("aa:bb:cc:dd:ee:ff", -45.0)

        await service._apply_startup_volume()

        assert camilladsp.set_volume.await_args_list == [((0.0,), {})]

    async def test_a_reconnect_re_pins_the_dsp_at_unity(self, service, camilladsp):
        """CamillaDSP restarts with the backend and comes back at its own gain.

        This is the *only* re-push after that restart. A DAC unit whose re-pin
        never ran keeps whatever the daemon restarted with.
        """
        service._volume_control = False

        await service.reapply_current_volume()

        camilladsp.set_volume.assert_awaited_once_with(0.0)
        camilladsp.set_mute.assert_awaited_once_with(False)

    async def test_a_reconnect_in_dac_mode_never_reads_the_store(self, service, camilladsp):
        """The managed branch below it would push the stored level.

        Falling through to it on a DAC unit re-attenuates a signal the amplifier
        is about to amplify — the failure is audible and permanent until the next
        restart.
        """
        service._volume_control = False
        service._state_store.ensure_local_client("aa:bb:cc:dd:ee:ff", -60.0)

        await service.reapply_current_volume()

        assert camilladsp.set_volume.await_args_list == [((0.0,), {})]

    async def test_setting_a_level_in_direct_dac_mode_is_accepted_and_ignored(self, service):
        """Direct + DAC has no client to control, and the API must not 500.

        The rotary encoder is disabled in this mode but the mobile UI slider is
        not, and Milo-Mac can still PATCH a volume.
        """
        service._volume_control = False
        service._routing_service = Mock()
        service._routing_service.get_state = Mock(return_value={"multiroom_enabled": False})
        service._apply_volume_to_hardware = AsyncMock()

        assert await service.set_volume_db(-30.0) is True
        assert await service.adjust_volume_db(+2.0) is True

        service._apply_volume_to_hardware.assert_not_awaited()

    async def test_multiroom_dac_still_controls_its_satellites(self, service):
        """The early return is `not volume_control AND not multiroom`.

        A DAC server driving satellites must still move *them*: dropping the
        multiroom half would make every remote speaker's slider inert while the
        UI reported success.
        """
        service._volume_control = False
        service._routing_service = Mock()
        service._routing_service.get_state = Mock(return_value={"multiroom_enabled": True})
        service._get_controllable_client_ids = Mock(return_value=["aa:bb"])
        service._compute_multiroom_updates = AsyncMock(return_value={"aa:bb": -30.0})
        service._apply_volume_to_hardware = AsyncMock(return_value=True)
        service.broadcast_volume_state = AsyncMock()
        service._update_startup_volume_if_needed = AsyncMock()

        assert await service.set_volume_db(-30.0) is True

        service._apply_volume_to_hardware.assert_awaited_once()

    async def test_initialize_takes_the_dac_flag_from_hardware(self, service, caplog):
        """`hardware.json` is the authority; the wizard writes it.

        Read wrong, a unit boots in the opposite mode from the one it is wired
        for — which is the loud failure in one direction and the silent one in
        the other.
        """
        service._hardware_service = Mock()
        service._hardware_service.get_volume_control = Mock(return_value=False)

        with caplog.at_level(logging.INFO):
            assert await service.initialize() is True

        assert service._volume_control is False
        state = await service._state_store.get_complete_state()
        assert state.volume_control is False, \
            "the flag never reached the store, so the WS payload still claims Milo owns the level"
        assert "DAC mode: volume managed by external amplifier" in caplog.text

    async def test_initialize_leaves_the_default_alone_with_no_hardware_service(
        self, service
    ):
        """Fail open: a dev host with no hardware service must not fall into DAC
        mode, which is the branch that pins 0 dB."""
        service._hardware_service = None

        assert await service.initialize() is True

        assert service._volume_control is True

    async def test_initialize_reports_failure_without_stranding_the_websocket(
        self, service, caplog
    ):
        """`_availability_ready` gates the WebSocket server's first volume frame.

        Left unset on a failed init, the WS handshake waits its full timeout
        before sending anything and the UI opens with no volume at all.
        """
        service._load_volume_config = AsyncMock(side_effect=RuntimeError("settings gone"))

        with caplog.at_level(logging.ERROR):
            assert await service.initialize() is False

        assert service._availability_ready.is_set()
        assert "Volume service initialization failed" in caplog.text

    async def test_flipping_to_dac_at_runtime_pins_the_dsp_immediately(
        self, service, camilladsp
    ):
        """The settings toggle takes effect now, not at the next boot.

        Deferred, the amplifier is fed an attenuated signal until a restart, and
        the user is told the change was applied.
        """
        service._hardware_service = Mock()
        service._hardware_service.set_volume_control = AsyncMock()

        await service.set_local_volume_control(False)

        camilladsp.set_volume.assert_awaited_once_with(0.0)
        assert service._volume_control is False

    async def test_flipping_back_to_managed_restores_the_stored_level(
        self, service, camilladsp
    ):
        """Coming out of DAC mode the DSP is sitting at 0 dB — full output.

        Restoring the stored level is the only thing between the flip and the
        room at maximum volume.
        """
        service._hardware_service = Mock()
        service._hardware_service.set_volume_control = AsyncMock()
        service._state_store.ensure_local_client("aa:bb:cc:dd:ee:ff", -40.0)
        service._volume_control = False

        await service.set_local_volume_control(True)

        camilladsp.set_volume.assert_awaited_once_with(-40.0)


class TestStartupVolumeEdges:
    """`_apply_startup_volume` when the daemon or the store is not there yet."""

    async def test_a_dsp_that_never_connects_applies_nothing(
        self, service, camilladsp, caplog
    ):
        """Ten seconds of waiting, then give up — the boot must not stall.

        Pushing anyway would send the level into a service with no client and
        report it applied; the reconnect callback is what actually covers this.
        """
        camilladsp.wait_for_connection = AsyncMock(return_value=False)

        with caplog.at_level(logging.WARNING):
            await service._apply_startup_volume()

        camilladsp.set_volume.assert_not_awaited()
        assert "CamillaDSP not connected after 10s" in caplog.text

    async def test_a_fresh_boot_falls_back_to_the_configured_startup_volume(
        self, service, camilladsp
    ):
        """Before Snapcast registers anyone there is no local client to read.

        The fallback is the *configured* startup volume, not the −45 dB hard
        default: a unit configured to boot at −25 dB must not come up 20 dB
        quieter on its first boot after a reflash.
        """
        service._volume_config = VolumeConfig(startup_volume_db=-25.0)

        await service._apply_startup_volume()

        camilladsp.set_volume.assert_awaited_once_with(-25.0)

    async def test_restore_mode_uses_the_local_client_own_level(self, service, camilladsp):
        """In multiroom `startup_volume_db` tracks the GLOBAL AVERAGE.

        Applied to the local client it is the average of the whole house, which
        is wrong for this room by however much the other speakers differ.
        """
        service._volume_config = VolumeConfig(restore_last_volume=True, startup_volume_db=-25.0)
        service._state_store.ensure_local_client("aa:bb:cc:dd:ee:ff", -38.0)

        await service._apply_startup_volume()

        camilladsp.set_volume.assert_awaited_once_with(-38.0)

    async def test_fixed_mode_ignores_the_persisted_level(self, service, camilladsp):
        """The control for the test above: `restore_last_volume = False` is a
        user setting, and it must beat whatever the store remembers."""
        service._volume_config = VolumeConfig(restore_last_volume=False, startup_volume_db=-25.0)
        service._state_store.ensure_local_client("aa:bb:cc:dd:ee:ff", -38.0)

        await service._apply_startup_volume()

        camilladsp.set_volume.assert_awaited_once_with(-25.0)

    async def test_the_persisted_mute_is_restored_with_the_level(self, service, camilladsp):
        """A unit muted at shutdown must come back muted, or the first boot after
        a night's mute is a full-level surprise."""
        service._state_store.ensure_local_client("aa:bb:cc:dd:ee:ff", -38.0)
        await service._state_store.set_client_mute("aa:bb:cc:dd:ee:ff", True)

        await service._apply_startup_volume()

        camilladsp.set_mute.assert_awaited_with(True)

    async def test_a_null_startup_volume_cannot_reach_its_own_fallback(
        self, service, camilladsp
    ):
        """The `elif` that promises "no target volume, only unmuted" is inert.

        `target_volume` is a float on every path — `_validate_and_merge` runs
        `float()` over `volume.startup_volume_db` with the declared default as
        its operand, and `ClientVolume.volume_db` is typed float — so the arm
        never runs. Worse, the line above it formats the same value with `:.1f`,
        so a value that WERE None would raise a TypeError there and the boot
        would end with the daemon's mute state untouched, which is the exact
        outcome the arm exists to prevent.

        Recorded rather than fixed: making it reachable is a production edit on
        the audio path. This test states the trap so that anyone who makes the
        setting nullable meets it here first.
        """
        service._volume_config = VolumeConfig(restore_last_volume=False)
        service._volume_config.startup_volume_db = None

        with pytest.raises(TypeError):
            await service._apply_startup_volume()

        camilladsp.set_mute.assert_not_awaited()


class TestBootPush:
    """`_do_push_volume_to_all_clients` — one level per client, after a restart."""

    @pytest.fixture
    def pushable(self, service):
        service._equalizer_controller = Mock()
        service._equalizer_controller.apply_volumes_parallel = AsyncMock(return_value={})
        service._equalizer_controller.set_equalizer_mute = AsyncMock(return_value=True)
        service.broadcast_volume_state = AsyncMock()
        return service

    async def test_a_boot_with_no_client_yet_is_a_no_op_success(self, pushable, caplog):
        """The snapserver WS can be ready before the local snapclient registers.

        Reported as a failure this would log an error on every single boot; the
        CLIENT_CONNECT handler covers the real case a moment later.
        """
        pushable._online_client_ids = Mock(return_value=[])

        with caplog.at_level(logging.INFO):
            assert await pushable._do_push_volume_to_all_clients() is True

        pushable._equalizer_controller.apply_volumes_parallel.assert_not_awaited()
        assert "No online clients yet" in caplog.text

    async def test_each_client_gets_its_own_persisted_level(self, pushable):
        """Not a common target: the whole point of the restore is that the
        kitchen and the living room come back where they were."""
        pushable._online_client_ids = Mock(return_value=["aa:bb", "cc:dd"])
        pushable._state_store.ensure_local_client("aa:bb", -30.0)
        await pushable._state_store.register_client("cc:dd", volume_db=-50.0)
        pushable._equalizer_controller.apply_volumes_parallel = AsyncMock(
            return_value={"aa:bb": True, "cc:dd": True}
        )

        await pushable._do_push_volume_to_all_clients()

        pushed = pushable._equalizer_controller.apply_volumes_parallel.await_args.args[0]
        assert pushed == {"aa:bb": -30.0, "cc:dd": -50.0}

    async def test_a_client_with_nothing_persisted_joins_at_the_startup_level(
        self, pushable, camilladsp
    ):
        """A satellite adopted since the last shutdown has no stored level.

        It joins at the configured `startup_volume_db` — never at a level read
        off the local DSP. Deriving one speaker's level from another's is the one
        thing the volume-ownership rule forbids, and this fan-out was the last
        path still doing it.
        """
        pushable._volume_config.startup_volume_db = -20.0
        pushable._online_client_ids = Mock(return_value=["new:client"])
        pushable._equalizer_controller.apply_volumes_parallel = AsyncMock(
            return_value={"new:client": True}
        )

        await pushable._do_push_volume_to_all_clients()

        pushed = pushable._equalizer_controller.apply_volumes_parallel.await_args.args[0]
        assert pushed == {"new:client": -20.0}
        camilladsp.get_volume.assert_not_awaited()

    async def test_only_the_clients_that_took_the_level_have_it_stored(self, pushable):
        """Storing a level the speaker refused makes the store lie, and the next
        boot restores the lie instead of retrying."""
        pushable._online_client_ids = Mock(return_value=["ok:client", "bad:client"])
        pushable._state_store.ensure_local_client("ok:client", -30.0)
        await pushable._state_store.register_client("bad:client", volume_db=-50.0)
        pushable._equalizer_controller.apply_volumes_parallel = AsyncMock(
            return_value={"ok:client": True, "bad:client": False}
        )

        assert await pushable._do_push_volume_to_all_clients() is False

        assert pushable._state_store.get_client_volume("ok:client") == -30.0

    async def test_a_client_that_refuses_its_mute_does_not_stop_the_others(
        self, pushable, caplog
    ):
        """The mute pass runs after the volume pass, over the same list.

        Unguarded, one unreachable speaker aborts the loop and every client after
        it in the iteration keeps the mute state of the previous session.
        """
        pushable._online_client_ids = Mock(return_value=["aa:bb", "cc:dd"])
        pushable._state_store.ensure_local_client("aa:bb", -30.0)
        await pushable._state_store.register_client("cc:dd", volume_db=-30.0)
        pushable._equalizer_controller.apply_volumes_parallel = AsyncMock(
            return_value={"aa:bb": True, "cc:dd": True}
        )
        pushable._equalizer_controller.set_equalizer_mute = AsyncMock(
            side_effect=[Exception("unreachable"), True]
        )

        with caplog.at_level(logging.WARNING):
            await pushable._do_push_volume_to_all_clients()

        assert pushable._equalizer_controller.set_equalizer_mute.await_count == 2
        assert "Failed to apply mute to aa:bb" in caplog.text

    async def test_a_contended_push_gives_up_rather_than_queueing(self, service, caplog):
        """Ten seconds is already long for a boot path; the lock is held by
        another push that is doing the same work."""
        service._do_push_volume_to_all_clients = AsyncMock()

        async def _hold():
            async with service._push_lock:
                await asyncio.sleep(3600)

        real_timeout = asyncio.timeout
        holder = asyncio.create_task(_hold())
        await asyncio.sleep(0)
        try:
            with caplog.at_level(logging.WARNING):
                with pytest.MonkeyPatch.context() as mp:
                    # `service.py` does a bare `import asyncio`, so this attribute
                    # IS asyncio.timeout for the whole process: a replacement that
                    # calls the name it just replaced recurses into itself. The
                    # real one is captured first, as reader.py taught in B6.
                    mp.setattr(
                        "backend.core.volume.service.asyncio.timeout",
                        lambda _: real_timeout(0.01),
                    )
                    assert await service.push_volume_to_all_clients() is False
        finally:
            holder.cancel()

        assert "Timeout waiting for push lock" in caplog.text
        service._do_push_volume_to_all_clients.assert_not_awaited()


class TestSyncFromEqualizer:
    """`sync_all_clients_from_equalizer` — reading each client's own level back."""

    @pytest.fixture
    def multiroom(self, service):
        service._routing_service = Mock()
        service._routing_service.get_state = Mock(return_value={"multiroom_enabled": True})
        service._equalizer_router = Mock()
        service._equalizer_router.get_volume = AsyncMock(return_value={"main": -33.0})
        service.broadcast_volume_state = AsyncMock()
        return service

    async def test_direct_mode_has_nothing_to_sync(self, service):
        service._routing_service = Mock()
        service._routing_service.get_state = Mock(return_value={"multiroom_enabled": False})

        assert await service.sync_all_clients_from_equalizer() is True

    async def test_no_registry_is_a_failure_not_a_silent_skip(self, multiroom, caplog):
        """Answering True here would report a sync that never read a single
        client, and the boot would move on believing every level current."""
        multiroom._client_registry = None

        with caplog.at_level(logging.WARNING):
            assert await multiroom.sync_all_clients_from_equalizer() is False

        assert "client registry not attached" in caplog.text

    async def test_the_local_client_is_read_from_the_store_not_the_daemon(self, multiroom):
        """SSOT. Reconstructing the local level from the live CamillaDSP inverts
        the data flow and races the boot restore that is still in flight."""
        multiroom._state_store.ensure_local_client("local:mac", -41.0)
        multiroom._client_registry = Mock()
        multiroom._client_registry.get_online_clients = Mock(
            return_value=[Mock(mac_id="local:mac", ip="127.0.0.1")]
        )

        await multiroom.sync_all_clients_from_equalizer()

        multiroom._equalizer_router.get_volume.assert_not_awaited()
        assert multiroom._state_store.get_client_volume("local:mac") == -41.0

    async def test_a_client_with_no_ip_is_skipped_without_stopping_the_sweep(
        self, multiroom, caplog
    ):
        """The registry can hold a client whose address has not resolved yet.

        Without the skip the proxy is handed a None hostname; unguarded, one
        half-registered client costs the sync for every client after it.
        """
        multiroom._client_registry = Mock()
        multiroom._client_registry.get_online_clients = Mock(return_value=[
            Mock(mac_id="no:ip", ip=None),
            Mock(mac_id="has:ip", ip="192.168.1.60"),
        ])

        with caplog.at_level(logging.WARNING):
            assert await multiroom.sync_all_clients_from_equalizer() is True

        assert "Cannot sync client no:ip: no IP address" in caplog.text
        assert multiroom._state_store.has_client("has:ip")
        assert not multiroom._state_store.has_client("no:ip")

    async def test_an_unreachable_satellite_keeps_its_persisted_level(self, multiroom):
        """Boot race: the satellite is registered before its API answers.

        Overwriting with the −45 dB default here would be pushed back to the
        speaker by the sync that follows, so a satellite that was slow to boot
        would come back near-silent every time.
        """
        multiroom._equalizer_router.get_volume = AsyncMock(return_value=None)
        await multiroom._state_store.register_client("sat:mac", volume_db=-28.0)
        multiroom._client_registry = Mock()
        multiroom._client_registry.get_online_clients = Mock(
            return_value=[Mock(mac_id="sat:mac", ip="192.168.1.60")]
        )

        await multiroom.sync_all_clients_from_equalizer()

        assert multiroom._state_store.get_client_volume("sat:mac") == -28.0

    async def test_an_unknown_unreachable_satellite_lands_on_the_default(self, multiroom):
        """Nothing to keep and nothing to read: the default is the only answer,
        and it has to be a value the store can hold rather than None."""
        multiroom._equalizer_router.get_volume = AsyncMock(return_value=None)
        multiroom._client_registry = Mock()
        multiroom._client_registry.get_online_clients = Mock(
            return_value=[Mock(mac_id="brand:new", ip="192.168.1.60")]
        )

        await multiroom.sync_all_clients_from_equalizer()

        assert multiroom._state_store.get_client_volume("brand:new") == DEFAULT_VOLUME_DB

    async def test_a_reachable_satellite_is_read_through_the_router(self, multiroom):
        """VolumeService no longer reaches a satellite directly — the router owns
        local-vs-remote dispatch, and that is the only place the distinction lives."""
        multiroom._client_registry = Mock()
        multiroom._client_registry.get_online_clients = Mock(
            return_value=[Mock(mac_id="sat:mac", ip="192.168.1.60")]
        )

        await multiroom.sync_all_clients_from_equalizer()

        multiroom._equalizer_router.get_volume.assert_awaited_once_with("sat:mac")
        assert multiroom._state_store.get_client_volume("sat:mac") == -33.0


class TestRegistryZoneEvents:
    """The zone arms of the volume store's registry subscription.

    The payload keys below are read off `ClientRegistryService._emit_event` — a
    `.get()` for a key the producer does not send skips its arm with no error
    anywhere, which is how a renamed identifier left two dead handlers in this
    bus before.
    """

    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(VolumeStateStore, "STORAGE_PATH", tmp_path / "last_volume.json")
        settings = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        return VolumeStateStore(settings_service=settings)

    async def test_a_created_zone_appears_in_the_volume_state(self, store):
        await store._handle_registry_event(RegistryEventType.ZONE_CREATED, {
            "action": "created",
            "zone_id": "zone-1",
            "zone": {"name": "Salon", "client_ids": ["aa:bb", "cc:dd"]},
        })

        zone = store._zones["zone-1"]
        assert zone.name == "Salon"
        assert zone.client_ids == ["aa:bb", "cc:dd"]

    async def test_an_updated_zone_replaces_its_membership(self, store):
        """A speaker added to a zone must join that zone's volume group at once.

        Not applied, the new member keeps taking its own level while the group
        slider moves the others — the zone reads as broken from the UI.
        """
        await store._handle_registry_event(RegistryEventType.ZONE_CREATED, {
            "action": "created",
            "zone_id": "zone-1",
            "zone": {"name": "Salon", "client_ids": ["aa:bb"]},
        })

        await store._handle_registry_event(RegistryEventType.ZONE_UPDATED, {
            "action": "updated",
            "zone_id": "zone-1",
            "zone": {"name": "Salon", "client_ids": ["aa:bb", "cc:dd"]},
        })

        assert store._zones["zone-1"].client_ids == ["aa:bb", "cc:dd"]

    async def test_a_renamed_zone_carries_its_new_name(self, store):
        await store._handle_registry_event(RegistryEventType.ZONE_UPDATED, {
            "action": "updated",
            "zone_id": "zone-1",
            "zone": {"name": "Cuisine", "client_ids": ["aa:bb"]},
        })

        assert store._zones["zone-1"].name == "Cuisine"

    async def test_a_zone_with_no_name_falls_back_to_its_id(self, store):
        """`zone_to_enriched_dict` always carries a name, but the fallback is
        what keeps a zone addressable if it ever stops doing so — an unnamed
        entry renders as an empty row the user cannot select."""
        await store._handle_registry_event(RegistryEventType.ZONE_UPDATED, {
            "action": "updated",
            "zone_id": "zone-1",
            "zone": {"client_ids": ["aa:bb"]},
        })

        assert store._zones["zone-1"].name == "zone-1"

    async def test_a_deleted_zone_leaves_the_volume_state(self, store):
        """A zone that outlives its deletion keeps answering zone-average reads,
        so the UI shows a group whose speakers are already standalone."""
        await store._handle_registry_event(RegistryEventType.ZONE_CREATED, {
            "action": "created",
            "zone_id": "zone-1",
            "zone": {"name": "Salon", "client_ids": ["aa:bb"]},
        })

        await store._handle_registry_event(RegistryEventType.ZONE_DELETED, {
            "action": "deleted",
            "zone_id": "zone-1",
            "zone": {"name": "Salon", "client_ids": ["aa:bb"]},
        })

        assert "zone-1" not in store._zones

    async def test_deleting_a_zone_that_is_not_there_is_not_an_error(self, store):
        """The registry emits ZONE_DELETED from four call sites, and a zone
        dropped for having fewer than two clients can be announced twice."""
        await store._handle_registry_event(RegistryEventType.ZONE_DELETED, {
            "action": "deleted",
            "zone_id": "never-existed",
            "zone": {},
        })

        assert store._zones == {}

    @pytest.mark.parametrize("event_type", [
        RegistryEventType.ZONE_CREATED,
        RegistryEventType.ZONE_UPDATED,
    ])
    async def test_a_zone_event_with_no_payload_is_ignored(self, store, event_type):
        """The guard is `zone_id AND zone`. Without the second half an empty
        payload would create a zone with no members whose average is computed
        over nothing."""
        await store._handle_registry_event(event_type, {
            "action": "updated", "zone_id": "zone-1", "zone": {},
        })

        assert store._zones == {}

    async def test_an_updated_client_that_is_unknown_is_registered(self, store):
        """CLIENT_UPDATED can be the first the volume store hears of a client —
        `Server.OnUpdate` fires for a client that was never announced connected.

        Ignored, that speaker has no volume entry and every group operation
        silently leaves it out.
        """
        await store._handle_registry_event(RegistryEventType.CLIENT_UPDATED, {
            "mac_id": "aa:bb",
            "client": {"online": True},
        })

        assert store.has_client("aa:bb")

    async def test_an_updated_client_carries_its_online_state(self, store):
        """A client announced offline must not be counted available: the zone
        average would include a speaker that is switched off."""
        await store._handle_registry_event(RegistryEventType.CLIENT_UPDATED, {
            "mac_id": "aa:bb",
            "client": {"online": False},
        })

        assert store.has_client("aa:bb")
        assert store._clients["aa:bb"].available is False

    async def test_an_already_known_client_is_not_re_registered(self, store):
        """Re-registering resets the level to the default.

        `Server.OnUpdate` fires on every property change, so this would drop the
        speaker back to −45 dB whenever anything about it changed.
        """
        await store.register_client("aa:bb", volume_db=-20.0)

        await store._handle_registry_event(RegistryEventType.CLIENT_UPDATED, {
            "mac_id": "aa:bb",
            "client": {"online": True},
        })

        assert store.get_client_volume("aa:bb") == -20.0


class TestEqualizerControllerRetry:
    """The retry a satellite that is slow to answer depends on.

    `set_equalizer_volume` recurses on `asyncio.TimeoutError` — twice, 0.5 s
    apart — and both the retry and the give-up were at zero. The timeout is 5 s
    per attempt, so this path is what a satellite waking from a WiFi power-save
    hits: without the retry, every level change to a sleeping speaker is a hard
    failure, the level is not stored, and the speaker comes back where it was.
    """

    @pytest.fixture
    def controller(self):
        from backend.core.volume import EqualizerController

        ctrl = EqualizerController(
            camilladsp_service=Mock(),
            client_proxy_service=Mock(),
            equalizer_router=Mock(),
        )
        ctrl._timeout = 0.01
        return ctrl

    @pytest.fixture
    def instant_backoff(self, monkeypatch):
        """Collapse RETRY_DELAY without removing it.

        0.5 s x 2 attempts is a second of wall clock per test; the count of
        attempts is what is asserted, never the delay.
        """
        from backend.core.volume import EqualizerController

        monkeypatch.setattr(EqualizerController, "RETRY_DELAY", 0)

    async def test_a_slow_client_is_retried_and_can_still_succeed(
        self, controller, instant_backoff
    ):
        """One dropped packet must not cost the level change.

        Nothing else re-sends it: `_do_push_volume_to_all_clients` records only
        what succeeded, so a client whose first attempt timed out would keep its
        old level until the next boot.
        """
        attempts = {"n": 0}

        async def _set_volume(mac_id, volume_db, force=False):
            attempts["n"] += 1
            if attempts["n"] == 1:
                await asyncio.sleep(3600)
            return {"status": "success"}

        controller._router.set_volume = _set_volume

        assert await controller.set_equalizer_volume("aa:bb", -30.0) is True
        assert attempts["n"] == 2

    async def test_the_retries_are_bounded_and_the_failure_is_reported(
        self, controller, instant_backoff, caplog
    ):
        """Three attempts total, then error. The recursion is what makes the
        bound necessary at all — an unbounded one would hold the push lock for
        as long as the speaker stays away.
        """
        attempts = {"n": 0}

        async def _set_volume(mac_id, volume_db, force=False):
            attempts["n"] += 1
            await asyncio.sleep(3600)

        controller._router.set_volume = _set_volume

        with caplog.at_level(logging.ERROR):
            assert await controller.set_equalizer_volume("aa:bb", -30.0) is False

        assert attempts["n"] == controller.RETRY_ATTEMPTS + 1
        assert "Timeout setting volume for aa:bb" in caplog.text

    async def test_the_force_flag_survives_every_retry(self, controller, instant_backoff):
        """`force` bypasses the router's online check and is what the
        reconnection sync passes. Dropped on the retry, the second attempt is
        short-circuited as "client offline" — the exact state the sync exists to
        recover from — and the satellite silently keeps its old level.
        """
        seen = []

        async def _set_volume(mac_id, volume_db, force=False):
            seen.append(force)
            if len(seen) == 1:
                await asyncio.sleep(3600)
            return {"status": "success"}

        controller._router.set_volume = _set_volume

        await controller.set_equalizer_volume("aa:bb", -30.0, force=True)

        assert seen == [True, True]

    async def test_a_router_that_raises_is_not_retried(self, controller, caplog):
        """Only a timeout is transient. A raising router is a bug or an
        unreachable host, and retrying it costs two more 5 s waits inside a lock.
        """
        attempts = {"n": 0}

        async def _set_volume(mac_id, volume_db, force=False):
            attempts["n"] += 1
            raise RuntimeError("router exploded")

        controller._router.set_volume = _set_volume

        with caplog.at_level(logging.WARNING):
            assert await controller.set_equalizer_volume("aa:bb", -30.0) is False

        assert attempts["n"] == 1
        assert "Failed to set volume for aa:bb" in caplog.text

    async def test_a_raising_router_costs_only_the_mute_it_was_asked_for(
        self, controller, caplog
    ):
        """`set_equalizer_mute` has no retry by design — it is called once per
        client in the boot push's second pass, and a raise there must answer
        False rather than abort the pass."""
        async def _set_mute(mac_id, mute, force=False):
            raise RuntimeError("router exploded")

        controller._router.set_mute = _set_mute

        with caplog.at_level(logging.WARNING):
            assert await controller.set_equalizer_mute("aa:bb", True) is False

        assert "Failed to set mute for aa:bb" in caplog.text


class TestZoneDelta:
    """`apply_zone_volume_delta` — the group slider, and what it answers when it can't move."""

    @pytest.fixture
    def zoned(self, service):
        service._equalizer_controller = Mock()
        service._equalizer_controller.apply_volumes_parallel = AsyncMock(return_value={})
        service.broadcast_volume_state = AsyncMock()
        service._update_startup_volume_if_needed = AsyncMock()
        return service

    async def test_a_contended_zone_delta_answers_the_current_average(
        self, zoned, caplog
    ):
        """The route returns this float to the UI slider.

        A raise would 500 the slider; a fabricated default would snap every
        speaker in the room to a level nobody asked for. Answering the average
        that already holds makes the gesture a no-op the user simply repeats.
        """
        zoned._state_store.compute_zone_average = Mock(return_value=-33.0)
        real_timeout = asyncio.timeout

        async def _hold():
            async with zoned._volume_lock:
                await asyncio.sleep(3600)

        holder = asyncio.create_task(_hold())
        await asyncio.sleep(0)
        try:
            with caplog.at_level(logging.WARNING):
                with pytest.MonkeyPatch.context() as mp:
                    mp.setattr(
                        "backend.core.volume.service.asyncio.timeout",
                        lambda _: real_timeout(0.01),
                    )
                    assert await zoned.apply_zone_volume_delta("zone-1", +2.0) == -33.0
        finally:
            holder.cancel()

        assert "Timeout waiting for volume lock" in caplog.text
        zoned._equalizer_controller.apply_volumes_parallel.assert_not_awaited()

    async def test_an_empty_zone_answers_its_average_without_a_fan_out(
        self, zoned, caplog
    ):
        """A zone whose members were all removed still exists until the registry
        deletes it; the slider must not send an empty fan-out and must not raise.
        """
        zoned._state_store.apply_zone_delta = AsyncMock(return_value={})
        zoned._state_store.compute_zone_average = Mock(return_value=-40.0)

        with caplog.at_level(logging.WARNING):
            assert await zoned.apply_zone_volume_delta("zone-1", +2.0) == -40.0

        assert "No clients to update in zone zone-1" in caplog.text
        zoned._equalizer_controller.apply_volumes_parallel.assert_not_awaited()
