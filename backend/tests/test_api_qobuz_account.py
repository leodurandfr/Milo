# backend/tests/test_api_qobuz_account.py
"""
/api/qobuz/account — the login status, the login URL, and the logout.

Why this file exists: measured 2026-08-25, `backend/api/qobuz_account.py` ran at
39.7 % of its lines and its three routes had never been entered.

What makes this router worth pinning is that it reads and writes qobuz-proxy's
own token cache rather than talking to the sidecar's HTTP API — the sidecar only
runs while Qobuz is the active source, so an API read would report "not
connected" from the settings screen every other time. That makes the file on
disk the account's state, and this router the only thing that edits it:

* `get_account` is the connected/disconnected line on the settings screen. It
  must read `authenticated` off the two keys the sidecar re-authenticates from,
  not off the file merely existing;
* `logout` has to leave the cache without those keys or the sidecar signs
  straight back in on its next start, and the user's "disconnect" undoes itself
  the next time they play something;
* `_clear_credentials` must preserve everything else in that file — it is
  qobuz-proxy's, not Milō's, and rewriting it whole would drop state Milō does
  not know the meaning of.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, Mock

from backend.api import qobuz_account
from backend.api.qobuz_account import QOBUZ_SERVICE, create_qobuz_account_router


CREDENTIALS = {
    "user_id": 1234567,
    "user_auth_token": "a-token",
    "email": "someone@example.com",
}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """qobuz-proxy's token cache, moved off the appliance's own data dir."""
    path = tmp_path / "credentials.json"
    monkeypatch.setattr(qobuz_account, "QOBUZ_CREDENTIALS_FILE", path)
    return path


@pytest.fixture
def systemd():
    manager = Mock()
    manager.start = AsyncMock(return_value=True)
    manager.is_active = AsyncMock(return_value=False)
    return manager


@pytest.fixture
def client(systemd):
    app = FastAPI()
    app.include_router(create_qobuz_account_router(systemd))
    return TestClient(app)


class TestAccountStatus:

    def test_a_cached_token_reads_as_connected(self, client, cache):
        cache.write_text(json.dumps(CREDENTIALS))

        data = client.get("/api/qobuz/account").json()["data"]

        assert data == {"authenticated": True, "email": "someone@example.com"}

    def test_no_cache_at_all_reads_as_disconnected(self, client, cache):
        """A unit that has never logged in has no file. Raising here would put
        the settings screen's error banner on every Qobuz section for the whole
        life of a fresh appliance.
        """
        data = client.get("/api/qobuz/account").json()["data"]

        assert data == {"authenticated": False, "email": None}

    @pytest.mark.parametrize("content", [
        json.dumps({"email": "someone@example.com"}),               # no token at all
        json.dumps({"user_id": 1234567}),                           # half a token
        json.dumps({"user_auth_token": "a-token"}),                 # the other half
    ])
    def test_half_a_token_is_not_a_connected_account(self, client, cache, content):
        """The sidecar re-authenticates from user_id *and* user_auth_token. With
        one of them the screen would say connected while every play fails, which
        is the one state the user cannot act on.
        """
        cache.write_text(content)

        assert client.get("/api/qobuz/account").json()["data"]["authenticated"] is False

    def test_a_truncated_cache_reads_as_disconnected_rather_than_failing(
        self, client, cache
    ):
        """A file cut short by a power loss mid-write. Disconnected is a state
        the user can fix from the screen; a 500 is not.
        """
        cache.write_text('{"user_id": 12345')

        data = client.get("/api/qobuz/account").json()["data"]

        assert data == {"authenticated": False, "email": None}


class TestLoginUrl:

    def test_the_sidecar_is_started_before_its_url_is_handed_out(
        self, client, systemd
    ):
        """milo-qobuz.service only runs while Qobuz is the active source, and
        the URL points at :8689 on the proxy itself. Handing it over without
        starting the unit sends the browser to connection-refused.
        """
        client.get("/api/qobuz/account/login-url")

        systemd.start.assert_awaited_once_with(QOBUZ_SERVICE)

    def test_the_url_and_its_callback_both_point_at_the_host_the_client_used(
        self, client
    ):
        """`origin` is where qobuz-proxy sends the OAuth callback that exchanges
        the code and starts the speaker. Pointed anywhere but back at the proxy,
        the login completes in the browser and the speaker never appears.
        """
        url = client.get(
            "/api/qobuz/account/login-url", headers={"Host": "milo.local"}
        ).json()["data"]["login_url"]

        assert url.startswith("http://milo.local:8689/auth/login?")
        assert "origin=http%3A%2F%2Fmilo.local%3A8689" in url

    def test_a_sidecar_that_will_not_start_is_a_503_and_not_a_dead_link(
        self, client, systemd
    ):
        systemd.start = AsyncMock(return_value=False)

        response = client.get("/api/qobuz/account/login-url")

        assert response.status_code == 503


class TestLogout:

    def test_the_token_keys_are_dropped_and_the_rest_of_the_file_survives(
        self, client, cache
    ):
        """The file is qobuz-proxy's, not Milō's. Rewriting it whole would drop
        whatever else the sidecar keeps there, which Milō has no way to rebuild.
        """
        cache.write_text(json.dumps({**CREDENTIALS, "device_id": "milo-1", "zone": "fr"}))

        response = client.post("/api/qobuz/account/logout")

        assert response.status_code == 200
        assert json.loads(cache.read_text()) == {"device_id": "milo-1", "zone": "fr"}

    def test_a_logout_with_the_sidecar_down_still_clears_the_cache(
        self, client, cache, systemd
    ):
        """This is the usual case: the sidecar only runs while Qobuz plays, and
        nobody disconnects an account mid-track. Skipping the clear because
        there is nothing to relay to leaves the token on disk, and the next
        start signs back in.
        """
        cache.write_text(json.dumps(CREDENTIALS))
        systemd.is_active = AsyncMock(return_value=False)

        client.post("/api/qobuz/account/logout")

        assert json.loads(cache.read_text()) == {}

    def test_a_running_sidecar_is_told_first_so_the_live_session_ends(
        self, client, cache, systemd, monkeypatch
    ):
        """Clearing the file alone leaves the speaker advertised and playing
        from an in-memory token until the process dies.
        """
        cache.write_text(json.dumps(CREDENTIALS))
        systemd.is_active = AsyncMock(return_value=True)
        posted = []

        class _Resp:
            status = 200

            async def __aenter__(self): return self
            async def __aexit__(self, *_): return False

        class _Session:
            def __init__(self, **_): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_): return False

            def post(self, url):
                posted.append(url)
                return _Resp()

        monkeypatch.setattr(qobuz_account.aiohttp, "ClientSession", _Session)

        client.post("/api/qobuz/account/logout")

        assert posted == ["http://127.0.0.1:8689/api/auth/logout"]
        assert json.loads(cache.read_text()) == {}

    def test_a_cache_with_no_token_is_left_untouched(self, client, cache):
        """Logging out twice must not rewrite a file that has nothing to drop —
        the second write is a chance to corrupt state Milō does not own, bought
        for nothing.
        """
        cache.write_text(json.dumps({"device_id": "milo-1"}))
        before = cache.stat().st_mtime_ns

        response = client.post("/api/qobuz/account/logout")

        assert response.status_code == 200
        assert cache.stat().st_mtime_ns == before
