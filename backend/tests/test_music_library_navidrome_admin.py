# backend/tests/test_music_library_navidrome_admin.py
"""`NavidromeAdminClient` — the library-administration window onto Navidrome.

What breaks when these fail: the Subsonic API can read a per-library catalog but
cannot create or delete a library, so every storage space the user sees (a USB
key, an SMB/NFS share) gets its handle from this client alone. Its consumer is
`NavidromeLibraryService` (libraries.py), whose own tests replace this class with
a `Mock()` — so until this file existed nothing confronted the shape of the
double with the shape of Navidrome, and 88 of the module's 112 lines had never
run.

The payload shapes below are **captured from the live sidecar on 2026-08-25**
(`POST /auth/login`, `GET /api/library`, `GET /api/user/{id}` against
127.0.0.1:4533 on this appliance), not invented — a mock that lies about the
outside world is worse than no mock. What the capture established, and what the
helpers here therefore reproduce:

* login answers ``{id, isAdmin, name, subsonicSalt, subsonicToken, token,
  username}`` — the two keys the client keeps are ``token`` and ``id``;
* an authenticated call comes back carrying a refreshed ``x-nd-authorization``;
* ``GET /api/library`` answers a **bare JSON list**, each record carrying
  ``id``/``name``/``path`` plus the ``total*`` counters libraries.py reads;
* ``GET /api/user/{id}`` returns ``userName``/``email``/``isAdmin``/``libraries``
  and **no password field at all** — so the credential-stripping in
  :meth:`grant_all_libraries` is defence, and the record it echoes back is the
  whole account.
"""
import json
import logging

import aiohttp
import pytest

from backend.sources.music_library import navidrome_admin
from backend.sources.music_library.navidrome_admin import (
    AUTH_HEADER,
    NavidromeAdminClient,
)

BASE = "http://127.0.0.1:4533"

# Captured 2026-08-25 from this appliance's Navidrome (values shortened).
LOGIN_BODY = {
    "id": "SYqy9vFe4v1tTyPuqfzRGg",
    "isAdmin": True,
    "name": "",
    "subsonicSalt": "c52bde",
    "subsonicToken": "81110cc8d82152790bc86021837585f4",
    "token": "jwt-first",
    "username": "admin",
}
LIBRARY_RECORD = {
    "id": 2,
    "name": "NAS-Leo",
    "path": "/media/milo/nas-leo-d7992dfe",
    "remotePath": "",
    "defaultNewUsers": False,
    "fullScanInProgress": False,
    "totalSongs": 2403,
    "totalAlbums": 155,
    "totalArtists": 88,
    "totalMissingFiles": 16,
}
USER_RECORD = {
    "id": "SYqy9vFe4v1tTyPuqfzRGg",
    "userName": "admin",
    "name": "",
    "email": "",
    "isAdmin": True,
    "lastLoginAt": "2026-08-25T23:32:23.305656337+02:00",
    "createdAt": "0001-01-01T00:00:00Z",
    "updatedAt": "2026-08-25T23:18:02.361117369+02:00",
    "libraries": [LIBRARY_RECORD],
}


# =============================================================================
# The outside world: one aiohttp session, recording what was asked of it
# =============================================================================

class _Response:
    """One aiohttp response, used as the async context manager aiohttp returns.

    ``text()`` is serialised from ``json_body`` rather than defaulting to empty:
    ``_request`` reads the body as text *first* and treats an empty one as the
    ``{}`` of a 204, so a double that answers JSON over an empty text body is a
    double that cannot happen.
    """

    def __init__(self, status=200, *, body=None, json_body=None, headers=None):
        self.status = status
        self._body = json.dumps(json_body) if body is None and json_body is not None else (body or "")
        self._json = json_body
        self.headers = headers or {}

    async def text(self):
        return self._body

    async def json(self, content_type=None):
        if self._json is None:
            raise ValueError("no JSON body")
        return self._json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Stands in for the client's aiohttp session, and only for it.

    Records every call so a test can assert the *sequence* — which URL, in which
    order, under which token — rather than the mere presence of one.
    """

    def __init__(self, *, logins=None, replies=None):
        self.closed = False
        self.logins = list(logins or [])
        self.replies = list(replies or [])
        self.calls = []  # (verb, url, kwargs)

    def _next(self, queue, what):
        if not queue:
            raise AssertionError(f"unexpected extra {what} call")
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._next(self.logins, "login")

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._next(self.replies, "API")

    async def close(self):
        self.closed = True

    # --- readers the assertions use -----------------------------------------
    @property
    def urls(self):
        return [url for _, url, _ in self.calls]

    def tokens(self):
        """The bearer token carried by each authenticated call, in order."""
        return [
            kwargs["headers"][AUTH_HEADER]
            for _, _, kwargs in self.calls
            if AUTH_HEADER in kwargs.get("headers", {})
        ]


@pytest.fixture(autouse=True)
def never_dial_the_live_navidrome(monkeypatch):
    """Make building a real session an error, not a request to 127.0.0.1:4533.

    Navidrome runs on this machine and the suite's network guard only refuses
    connects *off* this host, so loopback is wide open. A test whose double is
    bypassed must fail loudly here rather than reach the appliance's own
    catalog daemon (the model of `test_wifi_adoption.py::service`: make it fail,
    do not spy on it).
    """
    def _refuse(*args, **kwargs):
        raise AssertionError("the client built a real aiohttp session")

    monkeypatch.setattr(navidrome_admin.aiohttp, "ClientSession", _refuse)


@pytest.fixture
def client():
    return NavidromeAdminClient("milo-svc", "secret", base_url=BASE)


def attach(client, session):
    client._session = session
    return session


def logged_in(client, *, replies, token="jwt-first"):
    """A client that will log in successfully, then serve ``replies``."""
    body = {**LOGIN_BODY, "token": token}
    return attach(client, _Session(logins=[_Response(json_body=body)], replies=replies))


# =============================================================================
# AUTH
# =============================================================================

class TestLogin:

    async def test_the_service_password_is_exchanged_for_a_session_jwt(self, client):
        """The credentials go in the JSON body of /auth/login, and both the token
        and our own user id are kept — the id is what addresses the PUT that
        grants library access."""
        session = attach(
            client, _Session(logins=[_Response(json_body=LOGIN_BODY)]),
        )

        assert await client._login() is True

        verb, url, kwargs = session.calls[0]
        assert (verb, url) == ("POST", f"{BASE}/auth/login")
        assert kwargs["json"] == {"username": "milo-svc", "password": "secret"}
        assert client._token == LOGIN_BODY["token"]
        assert client.user_id == LOGIN_BODY["id"]

    async def test_a_login_answering_no_token_is_a_failed_login(self, client):
        """Navidrome answering 200 without a token would otherwise leave the
        client believing it is authenticated and every later call unauthorised."""
        body = {k: v for k, v in LOGIN_BODY.items() if k != "token"}
        attach(client, _Session(logins=[_Response(json_body=body)]))

        assert await client._login() is False
        assert client._token is None

    async def test_a_rejected_login_keeps_no_token(self, client):
        attach(client, _Session(logins=[_Response(401, body="unauthorized")]))

        assert await client._login() is False
        assert client._token is None

    async def test_navidrome_still_booting_is_not_an_error_banner(self, client, caplog):
        """A connection refused while the sidecar starts is INFO, a real fault is
        ERROR. `WebSocketLogHandler` paints the UI banner from ERROR, and
        milo-navidrome is PartOf=milo-backend: it goes down with every backend
        restart, so the boot race is routine and must not look like a failure.

        The exception is aiohttp's own, not a bare OSError: measured against a
        closed loopback port, a refused connect raises `ClientConnectorError`
        (a `ClientOSError`), and `is_network_error` only ever classifies aiohttp
        types — a plain `ConnectionRefusedError` takes the ERROR arm."""
        attach(client, _Session(
            logins=[aiohttp.ClientOSError(111, "Connection refused")],
        ))

        with caplog.at_level(logging.INFO, logger="source.music_library.navidrome_admin"):
            assert await client._login() is False

        levels = {r.levelno for r in caplog.records}
        assert levels == {logging.INFO}, [r.getMessage() for r in caplog.records]

    async def test_an_unexpected_login_failure_is_an_error(self, client, caplog):
        attach(client, _Session(logins=[TypeError("broken payload")]))

        with caplog.at_level(logging.INFO, logger="source.music_library.navidrome_admin"):
            assert await client._login() is False

        assert {r.levelno for r in caplog.records} == {logging.ERROR}


class TestSessionLifecycle:

    async def test_close_releases_the_session_and_forgets_the_token(self, client):
        session = logged_in(client, replies=[])
        await client._login()

        await client.close()

        assert session.closed is True
        assert client._session is None
        assert client._token is None

    def test_no_cred_file_means_no_client(self, tmp_path):
        """First-boot provisioning writes the cred file asynchronously; until it
        lands, libraries.py must get None and retry rather than build a client
        that cannot authenticate."""
        assert NavidromeAdminClient.from_cred_file(tmp_path / "absent.cred") is None

    def test_a_provisioned_cred_file_builds_a_client(self, tmp_path):
        cred = tmp_path / "milo-service.cred"
        cred.write_text("username=milo-svc\npassword=hunter2\n")

        built = NavidromeAdminClient.from_cred_file(cred, base_url=BASE)

        assert built is not None
        assert built._username == "milo-svc"
        assert built._password == "hunter2"


# =============================================================================
# REQUESTS
# =============================================================================

class TestAuthenticatedRequests:

    async def test_the_jwt_travels_in_navidromes_own_header(self, client):
        """Navidrome reads its session JWT from `x-nd-authorization`, not from a
        plain Authorization: — measured on the live sidecar. A call sent with the
        wrong header name is an unauthenticated call, i.e. a library write that
        never happened while the log stays quiet."""
        session = logged_in(client, replies=[_Response(json_body=[LIBRARY_RECORD])])

        await client.list_libraries()

        assert session.tokens() == ["Bearer jwt-first"]

    async def test_a_401_costs_exactly_one_re_login_and_the_new_token_is_used(self, client):
        """Navidrome invalidates sessions on restart, and it restarts with every
        backend restart (PartOf=). The retry is what keeps a mount from failing
        on the first call after that."""
        session = attach(client, _Session(
            logins=[
                _Response(json_body={**LOGIN_BODY, "token": "jwt-first"}),
                _Response(json_body={**LOGIN_BODY, "token": "jwt-second"}),
            ],
            replies=[
                _Response(401, body="expired"),
                _Response(json_body=[LIBRARY_RECORD]),
            ],
        ))

        assert await client.list_libraries() == [LIBRARY_RECORD]
        assert session.tokens() == ["Bearer jwt-first", "Bearer jwt-second"]

    async def test_a_second_401_gives_up_rather_than_looping(self, client):
        """The retry is bounded at one: an account Navidrome will never accept
        must not spin a request loop against the sidecar."""
        session = attach(client, _Session(
            logins=[
                _Response(json_body={**LOGIN_BODY, "token": "jwt-first"}),
                _Response(json_body={**LOGIN_BODY, "token": "jwt-second"}),
            ],
            replies=[_Response(401, body="expired"), _Response(401, body="expired")],
        ))

        assert await client.list_libraries() is None
        assert len(session.tokens()) == 2

    async def test_the_refreshed_token_replaces_the_one_we_sent(self, client):
        """Every authenticated answer carries a fresh `x-nd-authorization`
        (measured). Ignoring it lets the held JWT age out, which turns every
        later call into a 401 plus a re-login round trip."""
        session = logged_in(client, replies=[
            _Response(json_body=[], headers={AUTH_HEADER: "jwt-refreshed"}),
            _Response(json_body=[]),
        ])

        await client.list_libraries()
        await client.list_libraries()

        assert session.tokens() == ["Bearer jwt-first", "Bearer jwt-refreshed"]

    async def test_an_empty_success_body_is_a_success_not_a_failure(self, client):
        """`delete_library` and `rename_library` both read `result is not None`,
        so an empty 200/204 — which is what Navidrome answers a DELETE — must
        decode to `{}`. Read as a failure it would leave `changed` False, the
        reconcile short of its target, and the retry loop running for ever."""
        logged_in(client, replies=[_Response(200, body="")])

        assert await client.delete_library(2) is True

    async def test_a_rejected_write_is_reported_with_its_body(self, client, caplog):
        """Navidrome's 400s carry the reason (`pathInvalid` for a directory that
        is not mounted); losing it leaves an operator with a silent no-op."""
        logged_in(client, replies=[
            _Response(400, body='{"errors":{"path":"pathInvalid"}}'),
        ])

        with caplog.at_level(logging.ERROR, logger="source.music_library.navidrome_admin"):
            assert await client.create_library("iPod", "/media/milo/IPOD") is None

        assert any("pathInvalid" in r.getMessage() for r in caplog.records)

    async def test_a_transport_failure_answers_none_without_retrying(self, client):
        session = logged_in(client, replies=[aiohttp.ClientOSError(104, "Connection reset")])

        assert await client.list_libraries() is None
        assert len(session.tokens()) == 1


# =============================================================================
# LIBRARIES
# =============================================================================

class TestLibraryCrud:

    async def test_could_not_ask_is_not_there_are_none(self, client):
        """The distinction the reconciler is built on: None means the question
        failed, [] means Navidrome has no library. Collapsing the first into the
        second tells `_converge` every library is gone."""
        logged_in(client, replies=[_Response(500, body="boom")])
        assert await client.list_libraries() is None

    async def test_an_empty_catalog_is_an_empty_list(self, client):
        logged_in(client, replies=[_Response(json_body=[])])
        assert await client.list_libraries() == []

    async def test_creating_a_library_makes_it_visible_to_new_accounts(self, client):
        session = logged_in(client, replies=[_Response(json_body=LIBRARY_RECORD)])

        created = await client.create_library("NAS-Leo", "/media/milo/nas-leo-d7992dfe")

        verb, url, kwargs = session.calls[-1]
        assert (verb, url) == ("POST", f"{BASE}/api/library")
        assert kwargs["json"] == {
            "name": "NAS-Leo",
            "path": "/media/milo/nas-leo-d7992dfe",
            "defaultNewUsers": True,
        }
        assert created == LIBRARY_RECORD

    async def test_a_rename_carries_the_path_it_is_not_moving(self, client):
        """This PUT replaces the record and Navidrome rejects one without a path
        (400 `{"errors":{"path":"required"}}`), so a name-only payload is a
        rename that silently never happens — the storage filter keeps the old
        name for ever with nothing in any log."""
        session = logged_in(client, replies=[_Response(json_body=LIBRARY_RECORD)])

        assert await client.rename_library(2, "NAS", "/media/milo/nas-leo") is True

        verb, url, kwargs = session.calls[-1]
        assert (verb, url) == ("PUT", f"{BASE}/api/library/2")
        assert kwargs["json"] == {"name": "NAS", "path": "/media/milo/nas-leo"}

    async def test_a_refused_rename_is_reported_as_failure(self, client):
        logged_in(client, replies=[_Response(400, body="required")])
        assert await client.rename_library(2, "NAS", "/media/milo/nas-leo") is False

    async def test_deleting_a_library_addresses_it_by_id(self, client):
        session = logged_in(client, replies=[_Response(200, body="")])

        assert await client.delete_library(7) is True

        assert session.calls[-1][:2] == ("DELETE", f"{BASE}/api/library/7")

    async def test_a_refused_delete_is_reported_as_failure(self, client):
        logged_in(client, replies=[_Response(403, body="library with ID 1 cannot be deleted")])
        assert await client.delete_library(1) is False


# =============================================================================
# USER ACCESS — read, merge, write
# =============================================================================

class TestGrantAllLibraries:

    async def test_the_whole_account_is_written_back_not_just_its_libraries(self, client):
        """`PUT /api/user/{id}` replaces the record. Sending only `libraryIds`
        blanks `userName`, and an account with no user name cannot authenticate
        at all — the Subsonic API included, which is every browse and every
        stream. That is a locked-out appliance, so the GET is not an
        optimisation to skip.

        The expectation is derived from the record the GET answered, not
        restated: every key it carried must come back."""
        session = logged_in(client, replies=[
            _Response(json_body=USER_RECORD),
            _Response(json_body={"id": USER_RECORD["id"]}),
        ])
        await client._login()

        assert await client.grant_all_libraries([1, 2]) is True

        verb, url, kwargs = session.calls[-1]
        assert (verb, url) == ("PUT", f"{BASE}/api/user/{USER_RECORD['id']}")
        sent = kwargs["json"]
        assert set(USER_RECORD) <= set(sent)
        assert all(sent[key] == USER_RECORD[key] for key in USER_RECORD)
        assert sent["libraryIds"] == [1, 2]

    async def test_a_password_the_get_hands_back_is_never_echoed(self, client):
        """Today's Navidrome returns no password field (measured), so this is the
        defence rather than the observed case: echoing an empty one back would
        *set* it, and the whole appliance authenticates with that password."""
        session = logged_in(client, replies=[
            _Response(json_body={**USER_RECORD, "password": "", "currentPassword": ""}),
            _Response(json_body={}),
        ])
        await client._login()

        assert await client.grant_all_libraries([2]) is True

        sent = session.calls[-1][2]["json"]
        assert "password" not in sent and "currentPassword" not in sent
        assert sent["userName"] == "admin"

    async def test_an_unreadable_account_is_never_overwritten(self, client):
        """The guard that makes the merge safe: if the read failed there is
        nothing to merge, and writing anyway is the blanking above."""
        session = logged_in(client, replies=[_Response(500, body="boom")])
        await client._login()

        assert await client.grant_all_libraries([2]) is False
        assert not any(verb == "PUT" for verb, _, _ in session.calls)

    async def test_a_refused_write_is_reported_so_the_reconcile_retries(self, client):
        """`_converge` turns a False here into a failed reconcile, which is what
        schedules the retry — a library that exists but nobody can see answers
        every browse empty."""
        logged_in(client, replies=[
            _Response(json_body=USER_RECORD),
            _Response(400, body="nope"),
        ])
        await client._login()

        assert await client.grant_all_libraries([2]) is False

    async def test_it_logs_in_first_when_it_has_no_user_id_yet(self, client):
        """The id addresses the PUT, and it only ever arrives with a login."""
        session = logged_in(client, replies=[
            _Response(json_body=USER_RECORD),
            _Response(json_body={}),
        ])

        assert client.user_id is None
        assert await client.grant_all_libraries([2]) is True
        assert session.calls[0][1] == f"{BASE}/auth/login"
