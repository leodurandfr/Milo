"""A file set aside leaves a lasting notice, until the owner acknowledges it.

The live banner (`WebSocketLogHandler`) is the only other word of a set-aside,
and it is lost at boot — when most happen, before any client listens. What
breaks when these fail: a damaged card resets the settings (and brings the
setup wizard back) or empties the radio favorites with nothing on any screen
saying why, and the owner reflashes a unit that only needed reading.

Every set-aside here is made by a real owner's loader on a damaged file — never
a record built by the test — and what is asserted is what the notice service
then holds, wrote and broadcast.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.health import create_health_router
from backend.api.system import create_system_router
from backend.core.models.ws_events import SystemNoticesChanged
from backend.core.settings import SettingsService
from backend.core.system.notices import SetAsideNotices
from backend.shared.persistence import SET_ASIDE_SUFFIX, load_versioned_json_sync
from backend.sources.radio.data import StationDataService


def _state_machine():
    machine = Mock()
    machine.broadcast = AsyncMock()
    return machine


def _notices(tmp_path, machine=None):
    svc = SetAsideNotices(state_machine=machine or _state_machine())
    svc.notices_file = tmp_path / "notices.json"
    return svc


def _damaged_settings(tmp_path) -> SettingsService:
    svc = SettingsService()
    svc.settings_file = str(tmp_path / "settings.json")
    Path(svc.settings_file).write_bytes(b'{"schema_version": ')
    return svc


def _damaged_radio(tmp_path) -> StationDataService:
    svc = StationDataService()
    svc._data_file = tmp_path / "radio_data.json"
    svc._data_file.write_bytes(b"[]")
    return svc


def _broadcast_lists(machine):
    """The notice lists every `system/notices_changed` carried, in order."""
    return [
        [n.file for n in call.args[0].notices]
        for call in machine.broadcast.await_args_list
        if isinstance(call.args[0], SystemNoticesChanged)
    ]


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_a_set_aside_made_before_the_service_exists_is_claimed_and_kept(tmp_path):
    """STEP 3b reads settings.json synchronously, before any service is up: the
    set-aside it makes there is the one that matters most (the wizard comes back),
    and it must still be on screen once the wizard is done — so it is kept, and a
    service built at the next boot reads it back."""
    settings = _damaged_settings(tmp_path)
    settings.get_setting_sync("language")  # the boot's first read

    machine = _state_machine()
    notices = _notices(tmp_path, machine)
    await notices.initialize()

    (notice,) = notices.notices()
    assert notice.file == settings.settings_file
    (kept,) = tmp_path.glob(f"settings.json.*{SET_ASIDE_SUFFIX}")
    assert notice.kept_as == kept.name
    assert _broadcast_lists(machine) == [[settings.settings_file]]

    next_boot = _notices(tmp_path)
    await next_boot.initialize()
    assert next_boot.notices() == notices.notices()


async def test_a_set_aside_after_the_service_is_up_is_announced_and_persisted(tmp_path):
    machine = _state_machine()
    notices = _notices(tmp_path, machine)
    await notices.initialize()
    assert notices.notices() == [] and _broadcast_lists(machine) == []

    radio = _damaged_radio(tmp_path)
    await radio.initialize()
    await _settle()

    assert [n.file for n in notices.notices()] == [str(radio._data_file)]
    assert _broadcast_lists(machine) == [[str(radio._data_file)]]
    stored = json.loads(notices.notices_file.read_text())
    assert [entry["id"] for entry in stored["set_aside"]] == [n.id for n in notices.notices()]


async def test_a_set_aside_reported_off_the_loop_reaches_the_list(tmp_path):
    """A sync loader run on a worker thread reports there: the record crosses to
    the loop rather than touching the list from another thread."""
    notices = _notices(tmp_path)
    await notices.initialize()
    damaged = tmp_path / "hardware.json"
    damaged.write_bytes(b"not json")

    await asyncio.to_thread(load_versioned_json_sync, damaged, 1)
    await _settle()

    assert [n.file for n in notices.notices()] == [str(damaged)]


async def test_an_acknowledged_notice_is_gone_for_every_screen_and_every_boot(tmp_path):
    settings = _damaged_settings(tmp_path)
    settings.get_setting_sync("language")
    radio = _damaged_radio(tmp_path)
    machine = _state_machine()
    notices = _notices(tmp_path, machine)
    await notices.initialize()
    await radio.initialize()
    await _settle()
    first, second = notices.notices()

    assert await notices.acknowledge(first.id) is True
    assert await notices.acknowledge(first.id) is False  # another screen was first

    assert notices.notices() == [second]
    assert _broadcast_lists(machine)[-1] == [second.file]
    next_boot = _notices(tmp_path)
    await next_boot.initialize()
    assert next_boot.notices() == [second]


async def test_an_unreadable_notices_file_becomes_a_notice_itself(tmp_path):
    notices = _notices(tmp_path)
    notices.notices_file.write_text(json.dumps({"schema_version": 1, "set_aside": [{"id": "x"}]}))

    await notices.initialize()

    (notice,) = notices.notices()
    assert notice.file == str(notices.notices_file)
    assert len(list(tmp_path.glob(f"notices.json.*{SET_ASIDE_SUFFIX}"))) == 1


async def test_a_released_service_receives_nothing_more(tmp_path):
    notices = _notices(tmp_path)
    await notices.initialize()
    await notices.cleanup()

    await _damaged_radio(tmp_path).initialize()
    await _settle()

    assert notices.notices() == []
    # …and the record waits for the next keeper instead of being lost.
    following = _notices(tmp_path / "next")
    (tmp_path / "next").mkdir()
    await following.initialize()
    assert len(following.notices()) == 1


class TestRoutes:
    """What a page reads at boot and at every resync, and how it acknowledges."""

    @pytest.fixture
    async def notices(self, tmp_path):
        settings = _damaged_settings(tmp_path)
        settings.get_setting_sync("language")
        svc = _notices(tmp_path)
        await svc.initialize()
        return svc

    def _client(self, notices):
        app = FastAPI()
        app.include_router(create_system_router(Mock(), notice_service=notices), prefix="/api/system")
        settings = Mock(get_setting=AsyncMock(return_value=True))
        app.include_router(create_health_router(
            Mock(get_current_state=Mock(return_value={})), Mock(), settings,
            Mock(hotspot_active=False), Mock(), Mock(), notice_service=notices,
        ))
        return TestClient(app)

    async def test_the_notices_ride_in_the_initial_state_and_the_get(self, notices):
        client = self._client(notices)
        expected = [n.model_dump() for n in notices.notices()]
        assert len(expected) == 1

        assert client.get("/api/system/notices").json()["data"]["notices"] == expected
        assert client.get("/api/initial-state").json()["notices"] == expected

    async def test_a_delete_acknowledges_and_a_second_one_is_a_404(self, notices):
        client = self._client(notices)
        (notice,) = notices.notices()

        assert client.delete(f"/api/system/notices/{notice.id}").status_code == 200
        assert client.delete(f"/api/system/notices/{notice.id}").status_code == 404
        assert client.get("/api/system/notices").json()["data"]["notices"] == []

    async def test_a_write_that_fails_keeps_the_notice_listed(self, notices, caplog):
        """The card the notice is about may be the one refusing the write: the
        acknowledgement is then a logged 500, and the notice stays where the
        page's re-read and the next boot both find it."""
        client = self._client(notices)
        (notice,) = notices.notices()
        notices.notices_file.parent.chmod(0o500)
        try:
            response = client.delete(f"/api/system/notices/{notice.id}")
        finally:
            notices.notices_file.parent.chmod(0o700)

        assert response.status_code == 500
        assert any(r.levelname == "ERROR" for r in caplog.records)
        assert client.get("/api/system/notices").json()["data"]["notices"] == [notice.model_dump()]
        next_boot = _notices(notices.notices_file.parent)
        await next_boot.initialize()
        assert [n.id for n in next_boot.notices()] == [notice.id]
