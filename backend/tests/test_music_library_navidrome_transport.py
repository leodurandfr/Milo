# backend/tests/test_music_library_navidrome_transport.py
"""`NavidromeClient` below its JSON boundary — the transport nothing had run.

`test_music_library_navidrome.py` tests the browse/search/playlist surface by
replacing `_make_request` with an AsyncMock. That is a deliberate boundary and it
buys the extraction rules, but it is a method *of the unit*, so everything under
it had never executed: the Subsonic token auth, the `subsonic-response` unwrap,
the `NavidromeAuthError` that credential rejection depends on, and the
`{"_network_error": True}` sentinel a still-booting sidecar answers with. This
file exercises that half against a stand-in for aiohttp, plus the cred-file
reader both clients are built from.

What breaks when these fail:

* **the auth error** is what makes `_catalog_errors` drop the cached client and
  rebuild it from a rotated cred file. Lose it and the library answers 500 for
  the rest of the session instead of healing itself;
* **the network sentinel** is what separates "Navidrome is still booting" from
  "this call failed". Every caller reads it (`start_scan`, `get_scan_status`,
  `get_starred`, `_set_star`), and mistaking one for the other either paints an
  error banner on a routine boot race or reports a scan that never started;
* **`stream_url`** is handed to mpv, not to us — it is the only URL in the source
  whose auth has to survive leaving the process, and `format=raw` is what keeps a
  FLAC bit-perfect into CamillaDSP;
* **the cred file** is the single gate on both clients existing at all.
"""
import hashlib
import logging

import aiohttp
import pytest

from backend.sources.music_library import navidrome_client as module
from backend.sources.music_library.navidrome_client import (
    NavidromeAuthError,
    NavidromeClient,
    load_navidrome_credentials,
)

BASE = "http://127.0.0.1:4533"


def ok(payload):
    """A Subsonic 200 whose envelope reports success."""
    return {"subsonic-response": {"status": "ok", **payload}}


def image(data, content_type="image/jpeg"):
    """A cover-art 200 carrying bytes, which is what getCoverArt answers."""
    return _Response(data=data, content_type=content_type)


def failed(code, message="nope"):
    """A Subsonic 200 whose envelope reports an API error (the usual shape)."""
    return {
        "subsonic-response": {
            "status": "failed",
            "error": {"code": code, "message": message},
        }
    }


class _Response:
    def __init__(self, status=200, *, json_body=None, body="", raises=None,
                 data=None, content_type="application/json"):
        self.status = status
        self._json = json_body
        self._body = body
        self._raises = raises
        self._data = data
        self.headers = {"Content-Type": content_type}

    async def text(self):
        return self._body

    async def read(self):
        return self._data

    async def json(self, content_type=None):
        if self._raises is not None:
            raise self._raises
        return self._json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Stands in for the client's aiohttp session, recording each GET."""

    def __init__(self, *replies):
        self.closed = False
        self.replies = list(replies)
        self.calls = []  # (url, kwargs)

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.replies:
            raise AssertionError("unexpected extra request")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def close(self):
        self.closed = True

    def params(self, index=0):
        """The call's query pairs as a dict of key -> list of values.

        A dict would hide the repeated-key expansion `_encode_query` exists for.
        """
        pairs = self.calls[index][1]["params"]
        out = {}
        for key, value in pairs:
            out.setdefault(key, []).append(value)
        return out


@pytest.fixture(autouse=True)
def never_dial_the_live_navidrome(monkeypatch):
    """Building a real session is an error, not a call to 127.0.0.1:4533.

    Navidrome runs on this machine and the suite's network guard only refuses
    connects *off* this host, so loopback is open. A double that gets bypassed
    must fail loudly rather than reach the appliance's own catalog daemon.
    """
    def _refuse(*args, **kwargs):
        raise AssertionError("the client built a real aiohttp session")

    monkeypatch.setattr(module.aiohttp, "ClientSession", _refuse)


@pytest.fixture
def client():
    return NavidromeClient("milo-svc", "secret", base_url=BASE)


def attach(client, *replies):
    session = _Session(*replies)
    client._session = session
    return session


# =============================================================================
# The cred file — the gate on both clients existing
# =============================================================================

class TestCredentialFile:

    def test_a_provisioned_file_yields_the_service_account(self, tmp_path):
        cred = tmp_path / "milo-service.cred"
        cred.write_text("# provisioned by milo-navidrome-provision\n"
                        "\n"
                        "username=milo-svc\n"
                        "password=s3cr3t\n")

        assert load_navidrome_credentials(cred) == {
            "username": "milo-svc", "password": "s3cr3t",
        }

    def test_a_password_containing_an_equals_sign_survives_whole(self, tmp_path):
        """Provisioning generates the password, so it can hold anything base64
        emits — a value split on the first `=` is an account that cannot log in."""
        cred = tmp_path / "milo-service.cred"
        cred.write_text("username=milo-svc\npassword=a=b==\n")

        assert load_navidrome_credentials(cred)["password"] == "a=b=="

    def test_a_half_written_file_is_no_credentials_at_all(self, tmp_path):
        """First boot writes this file asynchronously. A half-read of it must be
        None — the callers retry — rather than a client that authenticates with
        an empty password against an account that has one."""
        cred = tmp_path / "milo-service.cred"
        cred.write_text("username=milo-svc\n")

        assert load_navidrome_credentials(cred) is None

    def test_an_absent_file_fails_open(self, tmp_path):
        assert load_navidrome_credentials(tmp_path / "nothing.cred") is None

    def test_a_client_is_built_from_it_or_not_at_all(self, tmp_path):
        cred = tmp_path / "milo-service.cred"
        cred.write_text("username=milo-svc\npassword=s3cr3t\n")

        built = NavidromeClient.from_cred_file(cred, base_url=BASE)
        assert built is not None and built._username == "milo-svc"
        assert NavidromeClient.from_cred_file(tmp_path / "gone.cred") is None


# =============================================================================
# _make_request — auth, unwrap, and the three ways a call can end
# =============================================================================

class TestSubsonicTransport:

    async def test_every_call_carries_token_auth_with_a_fresh_salt(self, client):
        """Subsonic token auth is md5(password + salt) with the salt sent in the
        clear; a per-call salt is what stops a captured query from being
        replayed. The token is *derived* here, never restated — a test that
        typed the digest would only assert md5."""
        session = attach(client, _Response(json_body=ok({})), _Response(json_body=ok({})))

        await client.get_scan_status()
        await client.get_scan_status()

        first, second = session.params(0), session.params(1)
        assert first["u"] == ["milo-svc"]
        assert first["v"] and first["c"]
        salt = first["s"][0]
        assert first["t"] == [hashlib.md5(f"secret{salt}".encode()).hexdigest()]
        assert second["s"] != first["s"], "the salt was reused between calls"
        assert second["t"] != first["t"]

    async def test_the_endpoint_is_addressed_under_rest_and_asks_for_json(self, client):
        session = attach(client, _Response(json_body=ok({})))

        await client.get_scan_status()

        assert session.calls[0][0] == f"{BASE}/rest/getScanStatus"
        assert session.params(0)["f"] == ["json"]

    async def test_a_multi_valued_scope_goes_out_as_a_repeated_key(self, client):
        """Subsonic's convention, and the reason params travel as pairs rather
        than a dict: a scope collapsed to one value browses one storage space
        and silently hides the others."""
        session = attach(client, _Response(json_body=ok({"starred2": {}})))

        await client.get_starred([2, 5, 9])

        assert session.params(0)["musicFolderId"] == ["2", "5", "9"]

    async def test_a_successful_envelope_is_unwrapped(self, client):
        attach(client, _Response(json_body=ok({"scanStatus": {"scanning": True, "count": 12}})))

        assert await client.get_scan_status() == {"scanning": True, "count": 12}

    async def test_rejected_credentials_are_raised_not_swallowed(self, client):
        """Subsonic 40/41. The route wrapper turns this into a 503 *and* drops the
        cached client so the next request re-reads a rotated cred file; degrading
        quietly instead would leave the library dead until a backend restart."""
        attach(client, _Response(json_body=failed(40, "Wrong username or password")))

        with pytest.raises(NavidromeAuthError):
            await client.get_scan_status()

    async def test_token_auth_refused_for_the_user_is_the_same_verdict(self, client):
        attach(client, _Response(json_body=failed(41)))

        with pytest.raises(NavidromeAuthError):
            await client.get_scan_status()

    async def test_any_other_api_error_is_a_miss_not_an_auth_failure(self, client, caplog):
        attach(client, _Response(json_body=failed(70, "Data not found")))

        with caplog.at_level(logging.ERROR, logger="source.music_library.navidrome"):
            assert await client.get_scan_status() is None

        assert any("70" in r.getMessage() for r in caplog.records)

    async def test_a_non_200_is_a_miss_and_its_body_is_logged(self, client, caplog):
        attach(client, _Response(500, body="upstream exploded"))

        with caplog.at_level(logging.ERROR, logger="source.music_library.navidrome"):
            assert await client.get_scan_status() is None

        assert any("upstream exploded" in r.getMessage() for r in caplog.records)

    async def test_a_sidecar_still_booting_answers_the_network_sentinel(self, client):
        """Not None: the sentinel is what tells every caller "ask again later"
        instead of "this failed". milo-navidrome is PartOf=milo-backend, so it is
        down on every backend restart and this is the ordinary case."""
        attach(client, aiohttp.ClientOSError(111, "Connection refused"))

        assert await client._make_request("ping") == {"_network_error": True}

    async def test_an_unexpected_failure_is_a_miss_not_a_sentinel(self, client):
        """Only a *transient* failure earns the retry-later reading."""
        attach(client, _Response(json_body=None, raises=ValueError("not JSON")))

        assert await client._make_request("ping") is None

    async def test_an_auth_error_survives_the_catch_all(self, client):
        """It is raised from inside the same `try` that swallows everything else;
        the re-raise arm is what keeps it from being downgraded to a plain miss."""
        attach(client, _Response(json_body=failed(40)))

        with pytest.raises(NavidromeAuthError):
            await client._make_request("getArtists")


class TestWhatTheSentinelDecides:

    async def test_a_scan_asked_of_an_absent_sidecar_is_not_a_scan(self, client):
        """`start_scan` returning True on a call that never happened is what
        `request_scan` would read as "indexing is under way": the storage space
        then waits for the hourly pass with nothing said."""
        attach(client, aiohttp.ClientOSError(111, "Connection refused"))

        assert await client.start_scan() is False

    async def test_a_scan_that_was_accepted_is_incremental(self, client):
        session = attach(client, _Response(json_body=ok({"scanStatus": {"scanning": True}})))

        assert await client.start_scan() is True
        assert session.calls[0][0] == f"{BASE}/rest/startScan"
        assert session.params(0)["fullScan"] == ["false"]

    async def test_scan_status_is_unknown_rather_than_idle_when_unreachable(self, client):
        attach(client, aiohttp.ClientOSError(111, "Connection refused"))

        assert await client.get_scan_status() is None

    async def test_favourites_are_empty_rather_than_broken_when_unreachable(self, client):
        """The favourites view is a read; an unreachable sidecar shows nothing,
        it does not raise into the route."""
        attach(client, aiohttp.ClientOSError(111, "Connection refused"))

        assert await client.get_starred([2]) == {"song": [], "album": [], "artist": []}

    async def test_an_empty_scope_never_reaches_the_network(self, client):
        """No mounted storage space means no question to ask — and asking would
        return the *whole* catalog, since Subsonic reads a missing scope as
        "everywhere"."""
        session = attach(client)

        assert await client.get_starred([]) == {"song": [], "album": [], "artist": []}
        assert session.calls == []

    async def test_the_starred_envelope_is_normalised_to_three_lists(self, client):
        """Navidrome omits a bucket that is empty; a caller iterating a missing
        key is a favourites screen that raises instead of showing what there is."""
        attach(client, _Response(json_body=ok({"starred2": {"song": [{"id": "s1"}]}})))

        assert await client.get_starred([2]) == {
            "song": [{"id": "s1"}], "album": [], "artist": [],
        }


# =============================================================================
# The URL mpv is handed
# =============================================================================

class TestStreamUrl:

    def test_the_stream_url_authenticates_itself_and_refuses_transcoding(self, client):
        """mpv fetches this URL out of process, so the auth has to live in the
        query string; `format=raw` is what makes Navidrome pass the original
        bytes through untouched, which is the whole point of the source."""
        url = client.stream_url("song-42")

        assert url.startswith(f"{BASE}/rest/stream?")
        query = url.split("?", 1)[1]
        pairs = dict(part.split("=", 1) for part in query.split("&"))
        assert pairs["id"] == "song-42"
        assert pairs["format"] == "raw"
        salt = pairs["s"]
        assert pairs["t"] == hashlib.md5(f"secret{salt}".encode()).hexdigest()
        assert pairs["u"] == "milo-svc"

    def test_two_stream_urls_do_not_share_a_salt(self, client):
        first = client.stream_url("song-1")
        second = client.stream_url("song-2")

        assert first.split("s=")[1] != second.split("s=")[1]


# =============================================================================
# Cover art — the arms below the ones the browse tests reach
# =============================================================================

class TestCoverArtResilience:

    async def test_an_unreachable_sidecar_is_a_missing_cover_not_an_error(self, client, caplog):
        """A cover miss 404s and the frontend draws Milō's own placeholder. A
        boot-race fetch must not paint the error banner on top of that."""
        attach(client, aiohttp.ClientOSError(111, "Connection refused"))

        with caplog.at_level(logging.INFO, logger="source.music_library.navidrome"):
            assert await client._fetch_cover_bytes("cov-1", None) is None

        assert {r.levelno for r in caplog.records} == {logging.INFO}

    async def test_an_unexpected_cover_failure_is_an_error(self, client, caplog):
        attach(client, TypeError("broken"))

        with caplog.at_level(logging.INFO, logger="source.music_library.navidrome"):
            assert await client._fetch_cover_bytes("cov-1", None) is None

        assert {r.levelno for r in caplog.records} == {logging.ERROR}

    async def test_the_same_bytes_at_two_sizes_is_a_stand_in_not_a_cover(self, client):
        """Navidrome answers 200 with a picture for an artist it could not
        resolve. Reporting that as a cover is how a whole index came to look
        like every artist had a photo — so it is the route's 404, and Milō's own
        placeholder, that must come out of it.

        The probe is the entire rule: real artwork is resized, a stand-in is
        passed through, so two sizes tell them apart with nothing hardcoded.
        """
        session = attach(client, image(b"stand-in"), image(b"stand-in"))

        assert await client.get_cover_art("ar-1", size=160) is None

        # Two probes and no third fetch: the requested size is never asked for,
        # because there is nothing to deliver.
        assert len(session.calls) == 2
        # getCoverArt takes a plain dict here, not the repeated-key pairs the
        # scoped browse calls need, so read it directly rather than via params().
        probes = [session.calls[i][1]["params"]["size"] for i in range(2)]
        assert probes[0] != probes[1]

    async def test_artwork_that_resizes_is_served_at_the_size_asked_for(self, client):
        session = attach(client, image(b"small"), image(b"smaller"), image(b"the-cover"))

        result = await client.get_cover_art("al-1", size=300)

        assert result == (b"the-cover", "image/jpeg")
        assert session.calls[2][1]["params"]["size"] == 300

    async def test_a_confirmed_cover_is_not_probed_again(self, client):
        """The probe costs two fetches. Paying them once per item rather than
        once per request is the whole reason the memo exists — a grid scrolling
        past 200 albums would otherwise triple its traffic."""
        attach(client, image(b"a"), image(b"b"), image(b"cover"))
        await client.get_cover_art("al-1", size=300)

        session = attach(client, image(b"cover"))
        assert await client.get_cover_art("al-1", size=300) == (b"cover", "image/jpeg")
        assert len(session.calls) == 1

    async def test_a_stand_in_is_re_probed_every_time(self, client):
        """Navidrome re-asks its metadata agent on every request, so an artist
        with no photo this minute can have one the next. Remembering the miss
        would freeze that gap for the life of the process."""
        attach(client, image(b"stand-in"), image(b"stand-in"))
        assert await client.get_cover_art("ar-1", size=160) is None

        session = attach(client, image(b"now"), image(b"resized"), image(b"real"))
        assert await client.get_cover_art("ar-1", size=160) == (b"real", "image/jpeg")
        assert len(session.calls) == 3

    async def test_a_probe_that_fails_hides_nothing(self, client):
        """Fail open. A Navidrome hiccup during the probe must not turn a real
        cover into a 404 — the appliance shows the art it has."""
        attach(client, aiohttp.ClientOSError(111, "refused"), image(b"cover"))

        assert await client.get_cover_art("al-1", size=300) == (b"cover", "image/jpeg")

    async def test_a_rescan_forgets_which_covers_were_confirmed_real(self, client):
        """A rescan is the one event that can take an album's art away; keeping
        the memo would serve the placeholder as real art until a restart."""
        client._real_art.add("cov-1")

        client.invalidate_cover_memo()

        assert client._real_art == set()


class TestSessionLifecycle:

    async def test_close_releases_the_session(self, client):
        session = attach(client)

        await client.close()

        assert session.closed is True
        assert client._session is None
