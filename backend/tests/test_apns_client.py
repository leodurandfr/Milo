# backend/tests/test_apns_client.py
"""What ApnsClient does to APNs, and what it concludes from each answer.

The mock stands for Apple. Every response shape below was observed against the
real service on 2026-09-19 from this unit (key 283578Y5GK) — the reason codes
and the validation order are measurements, not inventions.

The failure these guard against is that a push which reaches nobody looks
exactly like one that worked: APNs answers 200 for a payload the phone cannot
decode, and answers BadDeviceToken identically for a dead token, a wrong host
and a token the registry should keep.
"""
import time

import httpx
import pytest

from backend.core.push import apns_client
from backend.core.push.apns_client import (
    ApnsClient, DEAD_TOKEN_REASONS, DURABLE_REFUSALS, JWT_LIFETIME_S,
    MISREGISTERED_TOKEN_REASONS,
)
from backend.core.push.models import ApnsEnvironment, PushToken, PushTokenKind


def make_key(tmp_path, name="AuthKey_283578Y5GK.p8"):
    """A real P-256 key — the signing path must run, only Apple's trust is faked."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key_dir = tmp_path / "apns"
    key_dir.mkdir(exist_ok=True)
    (key_dir / name).write_bytes(
        ec.generate_private_key(ec.SECP256R1()).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return key_dir


def token(environment=ApnsEnvironment.SANDBOX, value="tok"):
    return PushToken(
        token=value, kind=PushTokenKind.WIDGET, environment=environment,
        device_id="phone-1", registered_at=time.time(),
    )


@pytest.fixture
async def client(tmp_path):
    apns = ApnsClient()
    apns.key_dir = make_key(tmp_path)
    apns.timesync_dir = tmp_path / "no-timesyncd"
    await apns.initialize()
    yield apns
    await apns.cleanup()


def stub_transport(client, responder):
    """Replace the pooled client's transport, keeping real httpx request building.

    The request object the assertions read is the one httpx actually built, so
    a header this code fails to set cannot be papered over by the test.
    """
    sent = []

    def handle(request):
        sent.append(request)
        return responder(request)

    for env in ApnsEnvironment:
        client._clients[env] = httpx.AsyncClient(
            base_url=f"https://{env.value}.invalid",
            transport=httpx.MockTransport(handle),
        )
    return sent


class TestSigningKey:
    """A key that cannot be used must disable pushing, not crash a boot."""

    async def test_no_key_is_not_an_error(self, tmp_path):
        """Fail open. A dev host has no Apple account, and an appliance whose
        owner never provisioned a key must still run every other feature."""
        apns = ApnsClient()
        apns.key_dir = tmp_path / "absent"
        await apns.initialize()

        assert apns.available is False
        result = await apns.send(token(), {"aps": {}}, "widgets")
        assert result.ok is False
        assert result.reason == "NoSigningKey"

    async def test_a_misnamed_key_is_refused(self, tmp_path):
        """The Key ID lives only in the filename. A renamed file would be
        signed with a `kid` guessed from nothing, which APNs answers
        InvalidProviderToken — a failure that reads as a wrong Apple team."""
        apns = ApnsClient()
        apns.key_dir = make_key(tmp_path, name="my-key.p8")
        await apns.initialize()

        assert apns.available is False

    async def test_two_keys_are_refused_rather_than_guessed(self, tmp_path):
        """Picking one would work until the day it picks the revoked one."""
        apns = ApnsClient()
        apns.key_dir = make_key(tmp_path)
        make_key(tmp_path, name="AuthKey_AAAAAAAAAA.p8")
        await apns.initialize()

        assert apns.available is False


class TestRequestShape:
    """What actually goes on the wire."""

    async def test_headers_and_topic_are_built_from_the_app_bundle(self, client):
        """The topic derives from the APP bundle plus the push type. Building it
        from the widget's bundle is the classic mistake and answers BadTopic."""
        sent = stub_transport(client, lambda r: httpx.Response(200, headers={"apns-id": "X"}))

        result = await client.send(token(), {"aps": {"content-changed": True}}, "widgets", priority=5)

        assert result.ok is True and result.apns_id == "X"
        request = sent[0]
        assert request.url.path == "/3/device/tok"
        assert request.headers["apns-topic"] == "leodurand.Milo-iOS.push-type.widgets"
        assert request.headers["apns-push-type"] == "widgets"
        assert request.headers["apns-priority"] == "5"
        assert request.headers["authorization"].startswith("bearer ")

    async def test_the_host_comes_from_the_token_not_from_the_unit(self, client):
        """One phone can hold a Debug token and a TestFlight token at once.
        Sending either to the other's host answers BadDeviceToken, which is
        indistinguishable from a malformed token — so this cannot be a setting."""
        stub_transport(client, lambda r: httpx.Response(200))

        await client.send(token(ApnsEnvironment.SANDBOX), {}, "widgets")
        await client.send(token(ApnsEnvironment.PRODUCTION), {}, "widgets")

        hosts = {env: str(c.base_url) for env, c in client._clients.items()}
        assert hosts[ApnsEnvironment.SANDBOX] != hosts[ApnsEnvironment.PRODUCTION]

    async def test_the_provider_token_is_reused_across_sends(self, client):
        """Minting one per push earns TooManyProviderTokenUpdates, and that
        throttle degrades delivery for the whole app, not for one connection."""
        sent = stub_transport(client, lambda r: httpx.Response(200))

        for _ in range(5):
            await client.send(token(), {}, "widgets")

        assert len({r.headers["authorization"] for r in sent}) == 1

    async def test_an_expired_provider_token_is_reminted(self, client):
        """APNs refuses a JWT older than an hour. The cache must expire before
        Apple does, or every push after 60 minutes answers ExpiredProviderToken."""
        sent = stub_transport(client, lambda r: httpx.Response(200))
        await client.send(token(), {}, "widgets")

        client._jwt_minted_at -= JWT_LIFETIME_S + 1
        await client.send(token(), {}, "widgets")

        assert len({r.headers["authorization"] for r in sent}) == 2


class TestVerdicts:
    """What each APNs answer means for the token."""

    @pytest.mark.parametrize("reason", sorted(DEAD_TOKEN_REASONS))
    async def test_a_dead_token_is_reported_dead(self, client, reason):
        """These are the only answers that may cost a token. Derived from
        the production set rather than retyped, so adding one cannot leave this
        test agreeing with a stale copy of it."""
        stub_transport(client, lambda r: httpx.Response(400, json={"reason": reason}))

        result = await client.send(token(), {}, "nowplaying")

        assert result.dead is True and result.ok is False

    @pytest.mark.parametrize("reason", sorted(MISREGISTERED_TOKEN_REASONS))
    async def test_a_misregistered_token_is_logged_at_warning(self, client, caplog, reason):
        """Measured 2026-11-02: Milo-iOS declared its Now Playing tokens
        `production` after iOS 27.2 had made them sandbox. Each was purged on
        its first push, at info, for three days — and the card never got an
        update. The warning names what the registration claimed."""
        stub_transport(client, lambda r: httpx.Response(400, json={"reason": reason}))

        with caplog.at_level("INFO", logger=apns_client.__name__):
            result = await client.send(token(ApnsEnvironment.PRODUCTION), {}, "nowplaying")

        assert result.dead is True
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "production" in warnings[0].message and reason in warnings[0].message

    @pytest.mark.parametrize("reason", sorted(DEAD_TOKEN_REASONS - MISREGISTERED_TOKEN_REASONS))
    async def test_a_token_that_simply_died_stays_below_warning(self, client, caplog, reason):
        """An uninstall or an ended session is routine: errors.log must not
        fill with it."""
        stub_transport(client, lambda r: httpx.Response(410, json={"reason": reason}))

        with caplog.at_level("INFO", logger=apns_client.__name__):
            result = await client.send(token(), {}, "nowplaying")

        assert result.dead is True
        assert not [r for r in caplog.records if r.levelno >= 30]

    async def test_an_expired_session_token_is_dead(self, client):
        """Measured 2026-09-25: a Now Playing session's token, its session
        ended the evening before, answered `410 ExpiredToken` to every push —
        38 in a morning, each logged as an error, because the reason was not
        one that purges and the token was addressed again on every change."""
        stub_transport(client, lambda r: httpx.Response(410, json={"reason": "ExpiredToken"}))

        result = await client.send(token(), {}, "nowplaying")

        assert result.dead is True

    async def test_a_refused_push_type_never_costs_the_token(self, client):
        """Measured: APNs checks the push type BEFORE the device token, so
        InvalidPushType says nothing about the token. Purging on it would empty
        the registry over a typo in a header."""
        stub_transport(client, lambda r: httpx.Response(400, json={"reason": "InvalidPushType"}))

        result = await client.send(token(), {}, "banana")

        assert result.dead is False
        assert result.reason == "InvalidPushType"

    async def test_an_untrusted_key_never_costs_the_token(self, client):
        """Same ordering argument one step earlier: the key is checked before
        everything. A 403 is an operator problem, not a device problem."""
        stub_transport(client, lambda r: httpx.Response(403, json={"reason": "InvalidProviderToken"}))

        result = await client.send(token(), {}, "nowplaying")

        assert result.dead is False

    async def test_a_410_timestamp_is_converted_to_seconds(self, client):
        """APNs sends milliseconds. Handing that to purge() unconverted compares
        milliseconds against seconds, so `registered_at > invalidated_at` is
        never true and no dead token is ever removed — the registry fills up in
        silence, which is the exact failure the guard was added to prevent."""
        ms = 1789770000000
        stub_transport(client, lambda r: httpx.Response(
            410, json={"reason": "Unregistered", "timestamp": ms}))

        result = await client.send(token(), {}, "nowplaying")

        assert result.dead is True
        # Asserted as a DOMAIN, not as `ms / 1000` — restating the production
        # expression here would pass whatever that expression became. A value in
        # seconds sits next to time.time(); the unconverted one is ~1000x larger.
        assert time.time() / 2 < result.invalidated_at < time.time() * 2

    async def test_an_unreachable_apns_never_costs_the_token(self, client):
        """A router reboot must not empty the registry. This is the difference
        between "Apple says this token is dead" and "Apple did not answer"."""
        def boom(request):
            raise httpx.ConnectError("no route to host")

        stub_transport(client, boom)

        result = await client.send(token(), {}, "widgets")

        assert result.ok is False and result.dead is False
        assert result.reason == "Unreachable"


class TestClockNotSet:
    """A Pi has no battery clock: until timesyncd's first synchronization the
    wall clock is the one the card last saved, and a token dated from it is
    already expired at Apple (ExpiredProviderToken, measured at a boot)."""

    async def test_nothing_reaches_apns_before_the_first_synchronization(self, client, tmp_path):
        client.timesync_dir = tmp_path / "timesync"
        client.timesync_dir.mkdir()
        sent = stub_transport(client, lambda r: httpx.Response(200))

        held = await client.send(token(), {"aps": {}}, "widgets")

        assert sent == [], "a token dated from an unset clock was sent to Apple"
        assert held.ok is False and held.dead is False, "a held push cost its token"

        (client.timesync_dir / "synchronized").touch()
        went = await client.send(token(), {"aps": {}}, "widgets")

        assert went.ok is True and len(sent) == 1


def banners(caplog):
    """The records the banner would show: ERROR and above under `backend`,
    which is where main.py hangs the WebSocketLogHandler."""
    return [r for r in caplog.records
            if r.levelno >= 40 and (r.name == "backend" or r.name.startswith("backend."))]


class TestWhatReachesTheBanner:
    """This logger sits under `backend`, so every ERROR is a red banner on the
    kiosk and in every browser. A refusal that holds until the owner acts is
    said once; Apple's passing trouble, which the service sends again spaced,
    is never one."""

    @pytest.mark.parametrize("reason", sorted(DURABLE_REFUSALS))
    async def test_a_durable_refusal_is_one_banner_per_episode(self, client, caplog, reason):
        """A revoked key answered every push, and each of its five spaced
        retries, with the same banner. Said once; a push Apple takes ends the
        episode, and the next refusal is news again."""
        answer = {"status": 403, "json": {"reason": reason}}
        stub_transport(client, lambda r: httpx.Response(answer["status"], json=answer["json"]))

        with caplog.at_level("DEBUG", logger="backend"):
            for _ in range(6):
                await client.send(token(), {}, "nowplaying")
            assert len(banners(caplog)) == 1

            answer.update(status=200, json={})
            await client.send(token(), {}, "nowplaying")
            answer.update(status=403, json={"reason": reason})
            await client.send(token(), {}, "nowplaying")

        assert len(banners(caplog)) == 2

    @pytest.mark.parametrize("status, reason", [
        (429, "TooManyRequests"), (500, "InternalServerError"), (503, "ServiceUnavailable"),
    ])
    async def test_apples_passing_trouble_raises_no_banner(self, client, caplog, status, reason):
        """The service sends these again every 30 s: a banner per retry for a
        fault no gesture on this unit corrects. Kept at warning, so errors.log
        still has them, with what Apple said."""
        stub_transport(client, lambda r: httpx.Response(status, json={"reason": reason}))

        with caplog.at_level("DEBUG", logger="backend"):
            for _ in range(3):
                result = await client.send(token(), {}, "nowplaying")

        assert result.ok is False and result.dead is False
        assert banners(caplog) == []
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 3 and all(f"{status} {reason}" in m for m in warnings)

