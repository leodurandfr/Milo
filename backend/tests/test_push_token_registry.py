# backend/tests/test_push_token_registry.py
"""Persistence and replacement rules of the APNs token registry.

Every failure here is silent in the field: a token this registry loses, keeps
past its death, or stores under the wrong kind produces a push that APNs
accepts and delivers nowhere. There is no error to read on the phone and none
in the journal — the emitter reports a 200.
"""
import asyncio
import json
import time

import pytest
from unittest.mock import AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.push import create_push_router
from backend.core.push.models import ApnsEnvironment, PushTokenKind
from backend.core.push.token_registry import PushTokenRegistry
from backend.shared.persistence import SchemaVersionMismatch

WIDGET = PushTokenKind.WIDGET
PTS = PushTokenKind.PUSH_TO_START
SESSION = PushTokenKind.SESSION


@pytest.fixture
def registry(tmp_path):
    reg = PushTokenRegistry()
    reg.tokens_file = tmp_path / "push_tokens.json"
    return reg


def reopened(registry):
    """A second registry over the same file — what a backend restart sees."""
    reg = PushTokenRegistry()
    reg.tokens_file = registry.tokens_file
    return reg


class TestPersistence:
    """The registry survives a restart, and refuses a file it cannot trust."""

    async def test_the_three_kinds_round_trip_with_their_environment(self, registry):
        """The environment is the field with no second chance: a sandbox token
        sent to the production host answers BadDeviceToken, which is a 400 that
        looks exactly like a malformed token. Losing it across a restart turns
        every push to that device into that silence."""
        await registry.register("tok-w", WIDGET, ApnsEnvironment.SANDBOX, "phone-1")
        await registry.register("tok-p", PTS, ApnsEnvironment.PRODUCTION, "phone-1")
        await registry.register(
            "tok-s", SESSION, ApnsEnvironment.SANDBOX, "phone-1", session_id="sess-1"
        )

        restarted = reopened(registry)
        await restarted.initialize()

        assert [t.token for t in restarted.tokens_for(WIDGET)] == ["tok-w"]
        assert restarted.tokens_for(WIDGET)[0].environment is ApnsEnvironment.SANDBOX
        assert restarted.tokens_for(PTS)[0].environment is ApnsEnvironment.PRODUCTION
        assert restarted.token_for_session("sess-1").token == "tok-s"
        assert restarted.token_for_session("sess-nope") is None

    async def test_a_fresh_install_writes_nothing(self, registry):
        """An empty registry is a valid state. Seeding the file at boot would
        create something to back up and restore for no information."""
        await registry.initialize()

        assert not registry.tokens_file.exists()
        assert registry.tokens_for(WIDGET) == []

    async def test_a_version_drift_is_fail_loud(self, registry):
        """The schema-bump protocol: refuse and let dependencies.py print the
        reset banner, rather than read a shape this code does not know."""
        registry.tokens_file.write_text(json.dumps({"schema_version": 0, "tokens": {}}))

        with pytest.raises(SchemaVersionMismatch):
            await registry.initialize()

    async def test_a_record_missing_a_field_is_fail_loud(self, registry):
        """`registered_at` decides the 410 race. Defaulting it in the loader
        would answer that race with "now", which keeps every dead token
        forever — so an incomplete record raises instead."""
        registry.tokens_file.write_text(json.dumps({
            "schema_version": PushTokenRegistry.SCHEMA_VERSION,
            "tokens": {"tok": {
                "kind": "widget", "environment": "sandbox",
                "device_id": "phone-1", "session_id": None, "last_push_at": None,
            }},
        }))

        with pytest.raises(KeyError):
            await registry.initialize()


class TestReplacement:
    """What a new token supersedes — the rule that keeps dead tokens out."""

    async def test_a_re_registered_widget_replaces_the_old_one(self, registry):
        """iOS reissues on reinstall and never says so. Keeping both leaves a
        token that is dead but not yet known to be, and it is only revealed by
        a 410 on a push nobody may send for days."""
        await registry.register("tok-old", WIDGET, ApnsEnvironment.SANDBOX, "phone-1")
        await registry.register("tok-new", WIDGET, ApnsEnvironment.SANDBOX, "phone-1")

        assert [t.token for t in registry.tokens_for(WIDGET)] == ["tok-new"]

    async def test_another_device_keeps_its_own(self, registry):
        """Replacement is scoped to the install. A second phone must not evict
        the first one's widget token."""
        await registry.register("tok-a", WIDGET, ApnsEnvironment.SANDBOX, "phone-1")
        await registry.register("tok-b", WIDGET, ApnsEnvironment.SANDBOX, "phone-2")

        assert {t.token for t in registry.tokens_for(WIDGET)} == {"tok-a", "tok-b"}

    async def test_a_session_token_is_scoped_to_its_session_not_its_device(self, registry):
        """Two Now Playing sessions on one phone each hold their own token —
        evicting by device would silence the one that is still running."""
        await registry.register("tok-1", SESSION, ApnsEnvironment.SANDBOX, "phone-1", "sess-1")
        await registry.register("tok-2", SESSION, ApnsEnvironment.SANDBOX, "phone-1", "sess-2")
        await registry.register("tok-1b", SESSION, ApnsEnvironment.SANDBOX, "phone-1", "sess-1")

        assert registry.token_for_session("sess-1").token == "tok-1b"
        assert registry.token_for_session("sess-2").token == "tok-2"
        assert len(registry.tokens_for(SESSION)) == 2

    async def test_a_reissued_string_moves_instead_of_duplicating(self, registry):
        """APNs reissues a token string across installs. Keyed by the string,
        the record must move — two records under one key is not a state the
        file can even hold, so the loser would be whichever was written last."""
        await registry.register("tok", WIDGET, ApnsEnvironment.SANDBOX, "phone-1")
        await registry.register("tok", WIDGET, ApnsEnvironment.SANDBOX, "phone-2")

        held = registry.tokens_for(WIDGET)
        assert len(held) == 1
        assert held[0].device_id == "phone-2"


class TestReboot:
    """A phone that restarts loses every Now Playing session, silently."""

    async def test_a_reboot_drops_the_sessions_that_did_not_survive(self, registry):
        """Nothing else reveals it: the emitter keeps pushing `update` to a
        session that no longer exists and APNs answers 200 to every one. The
        device cannot repair itself — `RemoteMediaSession` is unavailable to
        app extensions — but it can say when it booted."""
        await registry.register("sess-tok", SESSION, ApnsEnvironment.SANDBOX,
                                "phone-1", "sess-1")
        booted = time.time() + 1

        await registry.register("widget-tok", WIDGET, ApnsEnvironment.SANDBOX,
                                "phone-1", boot_time=booted)

        assert registry.token_for_session("sess-1") is None
        assert registry.was_lost_to_reboot("sess-1")
        assert [t.token for t in registry.tokens_for(WIDGET)] == ["widget-tok"]

    async def test_a_session_opened_after_the_boot_is_kept(self, registry):
        """The phone reboots, the app opens a session, and the widget reports
        the same boot time afterwards. Dropping by device alone would take the
        live session with it."""
        booted = time.time() - 60
        await registry.register("sess-tok", SESSION, ApnsEnvironment.SANDBOX,
                                "phone-1", "sess-1", boot_time=booted)

        await registry.register("widget-tok", WIDGET, ApnsEnvironment.SANDBOX,
                                "phone-1", boot_time=booted)

        assert registry.token_for_session("sess-1").token == "sess-tok"
        assert not registry.was_lost_to_reboot("sess-1")

    async def test_another_phone_is_untouched(self, registry):
        """One phone rebooting says nothing about another's sessions."""
        await registry.register("sess-tok", SESSION, ApnsEnvironment.SANDBOX,
                                "phone-2", "sess-2")

        await registry.register("widget-tok", WIDGET, ApnsEnvironment.SANDBOX,
                                "phone-1", boot_time=time.time() + 1)

        assert registry.token_for_session("sess-2").token == "sess-tok"

    async def test_a_caller_that_says_nothing_invalidates_nothing(self, registry):
        """An older build of the app sends no `boot_time`. Treating its silence
        as a reboot would drop the session it is holding."""
        await registry.register("sess-tok", SESSION, ApnsEnvironment.SANDBOX,
                                "phone-1", "sess-1")

        await registry.register("widget-tok", WIDGET, ApnsEnvironment.SANDBOX, "phone-1")

        assert registry.token_for_session("sess-1").token == "sess-tok"


class TestOrphans:
    """`device_id` does not always outlive the install that minted it."""

    async def test_a_session_from_a_device_that_no_longer_answers_is_ignored(
        self, registry
    ):
        """Measured 2026-09-19: one phone went from `DF45773A` to `7823EF03`
        and left eleven session tokens behind under the name it no longer
        answered to. The reboot rule matches on `device_id`, so it could not
        reach them — the newest orphan was adopted and every update went to a
        session that had not existed for half an hour."""
        await registry.register("old-sess", SESSION, ApnsEnvironment.SANDBOX,
                                "phone-old", "sess-old")
        await registry.register("pts", PTS, ApnsEnvironment.SANDBOX, "phone-new")

        assert registry.newest_session_token() is None

    async def test_a_session_from_the_device_that_answers_is_taken(self, registry):
        await registry.register("pts", PTS, ApnsEnvironment.SANDBOX, "phone-new")
        await registry.register("sess", SESSION, ApnsEnvironment.SANDBOX,
                                "phone-new", "sess-1")

        assert registry.newest_session_token().session_id == "sess-1"

    async def test_the_newest_of_two_live_devices_wins(self, registry):
        """Nothing here is scoped per device yet — one session is published at a
        time — so the rule stays "the freshest thing a live install said"."""
        await registry.register("pts-a", PTS, ApnsEnvironment.SANDBOX, "phone-a")
        await registry.register("pts-b", PTS, ApnsEnvironment.SANDBOX, "phone-b")
        await registry.register("sess-a", SESSION, ApnsEnvironment.SANDBOX,
                                "phone-a", "sess-a")
        await registry.register("sess-b", SESSION, ApnsEnvironment.SANDBOX,
                                "phone-b", "sess-b")

        assert registry.newest_session_token().session_id == "sess-b"


class TestPurge:
    """The 410 path, including the race that deletes a live token."""

    async def test_a_dead_token_goes(self, registry):
        await registry.register("tok", WIDGET, ApnsEnvironment.SANDBOX, "phone-1")

        assert await registry.purge("tok", invalidated_at=time.time() + 10) is True
        assert registry.tokens_for(WIDGET) == []

    async def test_a_bad_device_token_goes_unconditionally(self, registry):
        """A 400 BadDeviceToken carries no timestamp, so there is nothing to
        compare against — the emitter calls purge without one."""
        await registry.register("tok", WIDGET, ApnsEnvironment.SANDBOX, "phone-1")

        assert await registry.purge("tok") is True
        assert registry.tokens_for(WIDGET) == []

    async def test_a_token_registered_after_the_410_is_kept(self, registry):
        """The race this guard exists for, and the reason the registry stores
        `registered_at` at all.

        The app is reinstalled and registers its new token while a push to the
        old one is still in flight. The 410 comes back *after* that
        registration, naming a string iOS has just reissued to the same device
        — purging on the name alone takes the live token with it, and the phone
        receives nothing until its next launch. Nothing reports this: the push
        path answered 200 and the registry is simply empty.
        """
        invalidated_at = time.time()
        await asyncio.sleep(0.01)
        await registry.register("tok", WIDGET, ApnsEnvironment.SANDBOX, "phone-1")

        assert await registry.purge("tok", invalidated_at=invalidated_at) is False
        assert [t.token for t in registry.tokens_for(WIDGET)] == ["tok"]

    async def test_purging_a_token_never_held_is_not_an_error(self, registry):
        """APNs naming a token this unit never had is routine — a second
        appliance, or a registry an operator reset. It is not worth a 500."""
        assert await registry.purge("who") is False


class TestConcurrency:
    """Two writers, one file."""

    async def test_a_purge_and_eight_registrations_all_survive(self, registry):
        """The interleave `_mutate` closes: a purge fired by a failed push runs
        in the background while registrations arrive over HTTP. Loading and
        saving under two separate holds lets them read the same state and lets
        the last write drop every edit made since it read — which here means a
        session token that the app believes is registered and that Milō will
        never push to.
        """
        await registry.register("doomed", WIDGET, ApnsEnvironment.SANDBOX, "phone-1")

        await asyncio.gather(
            registry.purge("doomed"),
            *(
                registry.register(
                    f"tok-{i}", SESSION, ApnsEnvironment.SANDBOX, "phone-1", f"sess-{i}"
                )
                for i in range(8)
            ),
        )

        restarted = reopened(registry)
        await restarted.initialize()
        assert len(restarted.tokens_for(SESSION)) == 8
        assert restarted.tokens_for(WIDGET) == []


class TestLiveSessionReports:
    """The device says which sessions it still holds; the rest are let go.

    A session token outlives its session and nothing on this side can see it:
    APNs answers 200, the phone discards the payload, and the emitter never
    sends a `start` because a token exists. Measured 2026-09-19 — the phone
    logged `Could not find the specified now playing client` while Milō adopted
    that very id.
    """

    async def test_a_session_the_device_no_longer_lists_is_dropped(self, registry):
        await registry.register(
            "tok-live", SESSION, ApnsEnvironment.PRODUCTION, "phone-1",
            session_id="live")
        await registry.register(
            "tok-ghost", SESSION, ApnsEnvironment.PRODUCTION, "phone-1",
            session_id="ghost")

        dropped = await registry.drop_sessions_absent_from("phone-1", ["live"])

        assert dropped == 1
        assert registry.token_for_session("ghost") is None
        assert registry.token_for_session("live") is not None

    async def test_an_empty_report_is_a_report(self, registry):
        """« I hold none » is the case that most needs clearing, and it arrives
        as an empty list. What says nothing is an app that is not running — and
        an app that is not running does not call this at all."""
        await registry.register(
            "tok-ghost", SESSION, ApnsEnvironment.PRODUCTION, "phone-1",
            session_id="ghost")

        assert await registry.drop_sessions_absent_from("phone-1", []) == 1
        assert registry.token_for_session("ghost") is None

    async def test_one_phone_cannot_retire_another_phones_session(self, registry):
        """A device knows its own sessions and nobody else's. Unscoped, the
        first empty report from any phone would wipe the household."""
        await registry.register(
            "tok-other", SESSION, ApnsEnvironment.PRODUCTION, "phone-2",
            session_id="theirs")

        assert await registry.drop_sessions_absent_from("phone-1", []) == 0
        assert registry.token_for_session("theirs") is not None

    async def test_the_other_kinds_are_untouched(self, registry):
        """Only sessions are reported here. Taking the push-to-start token with
        them would remove the one address that can open the next session — the
        exact opposite of the repair."""
        await registry.register("tok-p", PTS, ApnsEnvironment.PRODUCTION, "phone-1")
        await registry.register("tok-w", WIDGET, ApnsEnvironment.SANDBOX, "phone-1")

        await registry.drop_sessions_absent_from("phone-1", [])

        assert len(registry.tokens_for(PTS)) == 1
        assert len(registry.tokens_for(WIDGET)) == 1

    async def test_the_drop_survives_a_restart(self, registry):
        """In memory only, the ghost would come back at the next boot and the
        emitter would resume addressing it."""
        await registry.register(
            "tok-ghost", SESSION, ApnsEnvironment.PRODUCTION, "phone-1",
            session_id="ghost")
        await registry.drop_sessions_absent_from("phone-1", [])

        restarted = reopened(registry)
        await restarted.initialize()

        assert restarted.token_for_session("ghost") is None


class TestRoutes:
    """POST /api/push/tokens, DELETE /api/push/tokens/{token}, POST /sessions."""

    @pytest.fixture
    def mock_registry(self):
        registry = AsyncMock()
        registry.register = AsyncMock(return_value=None)
        registry.unregister = AsyncMock(return_value=True)
        registry.drop_sessions_absent_from = AsyncMock(return_value=0)
        return registry

    @pytest.fixture
    def mock_service(self):
        service = AsyncMock()
        service.align_session_to_playback = AsyncMock(return_value=None)
        return service

    @pytest.fixture
    def client(self, mock_registry, mock_service):
        app = FastAPI()
        app.include_router(create_push_router(mock_registry, mock_service))
        return TestClient(app)

    def test_a_registration_reaches_the_registry_as_typed_values(self, client, mock_registry):
        """The route must hand the service enums, not the strings off the wire:
        `tokens_for` compares on identity, so a string would match nothing and
        the token would be stored and never selected."""
        response = client.post("/api/push/tokens", json={
            "token": "tok", "kind": "push_to_start",
            "environment": "production", "device_id": "phone-1",
        })

        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        kwargs = mock_registry.register.await_args.kwargs
        assert kwargs["kind"] is PushTokenKind.PUSH_TO_START
        assert kwargs["environment"] is ApnsEnvironment.PRODUCTION

    def test_a_missing_environment_is_refused(self, client, mock_registry):
        """No default, by design: guessing the host is the BadDeviceToken
        silence, and it is unattributable once it happens."""
        response = client.post("/api/push/tokens", json={
            "token": "tok", "kind": "widget", "device_id": "phone-1",
        })

        assert response.status_code == 422
        mock_registry.register.assert_not_awaited()

    def test_a_session_token_without_its_session_is_refused(self, client, mock_registry):
        """It would be stored and never selected — `token_for_session` is the
        only way an update or an end is addressed."""
        response = client.post("/api/push/tokens", json={
            "token": "tok", "kind": "session",
            "environment": "sandbox", "device_id": "phone-1",
        })

        assert response.status_code == 422
        mock_registry.register.assert_not_awaited()

    def test_a_session_id_on_a_widget_token_is_refused(self, client, mock_registry):
        """The caller confused the two kinds. Stored, it is a widget token that
        looks addressable by session and is not."""
        response = client.post("/api/push/tokens", json={
            "token": "tok", "kind": "widget", "environment": "sandbox",
            "device_id": "phone-1", "session_id": "sess-1",
        })

        assert response.status_code == 422
        mock_registry.register.assert_not_awaited()

    def test_deleting_an_unknown_token_is_404(self, client, mock_registry):
        """The app withdrawing a token Milō does not hold is a disagreement
        worth reporting — unlike a purge, where APNs naming an unknown token is
        routine."""
        mock_registry.unregister.return_value = False

        assert client.delete("/api/push/tokens/tok").status_code == 404

    def test_deleting_a_held_token_succeeds(self, client, mock_registry):
        response = client.delete("/api/push/tokens/tok")

        assert response.status_code == 200
        mock_registry.unregister.assert_awaited_once_with("tok")

    def test_a_live_session_report_reaches_the_registry(self, client, mock_registry):
        """The app's half of the ghost repair."""
        response = client.post("/api/push/sessions", json={
            "device_id": "phone-1", "session_ids": ["sess-a", "sess-b"],
        })

        assert response.status_code == 200
        mock_registry.drop_sessions_absent_from.assert_awaited_once_with(
            "phone-1", ["sess-a", "sess-b"])

    def test_an_absent_session_list_means_none(self, client, mock_registry):
        """Holding nothing is the report that matters most, so the field
        defaults rather than 422s — an app with no sessions should not have to
        remember to say so in two ways."""
        response = client.post("/api/push/sessions", json={"device_id": "phone-1"})

        assert response.status_code == 200
        mock_registry.drop_sessions_absent_from.assert_awaited_once_with("phone-1", [])

    def test_a_report_aligns_the_session_with_what_is_playing(
        self, client, mock_service
    ):
        """The emitter opens on a playback event and closes after a grace, and
        neither reaches the two cases the app hits: music already playing with
        no session, and a source change that leaves the card on the old track.
        Dropping this call leaves both, with nothing to read anywhere."""
        response = client.post("/api/push/sessions", json={
            "device_id": "phone-1", "session_ids": ["sess-a"],
        })

        assert response.status_code == 200
        mock_service.align_session_to_playback.assert_awaited_once_with("phone-1")

    def test_the_ghosts_are_retired_before_the_session_is_aligned(
        self, client, mock_registry, mock_service
    ):
        """A session token outlives its session, and "a session is held" is the
        one input the alignment turns on. Aligned first, a ghost reads as a live
        card and the `end` that should close it is never sent."""
        order = []
        mock_registry.drop_sessions_absent_from.side_effect = (
            lambda *_: order.append("retire") or 0
        )
        mock_service.align_session_to_playback.side_effect = (
            lambda *_: order.append("align")
        )

        client.post("/api/push/sessions", json={"device_id": "phone-1"})

        assert order == ["retire", "align"]
