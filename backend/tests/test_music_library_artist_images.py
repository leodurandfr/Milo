# backend/tests/test_music_library_artist_images.py
"""The rule that decides which Deezer artist a photo comes from.

Navidrome's own agent keeps the first hit whose name matches, and Deezer's
search is not ordered by popularity — which is how 25 of one library's 108
artists came to wear someone else's face. `pick_artist` is the replacement, and
every one of its three filters was measured against a real failure:

* **exact name** — the first hit for "Adele" is "Adèle & Robin";
* **accent folding inside that match** — Deezer lists the same artist as "ROCé"
  and "Rocé", and a byte comparison would call them different people;
* **a real photo** — the top-ranked "Amy Winehouse" is a duplicate profile whose
  picture is Deezer's grey silhouette, a genuine resizable image that no
  byte-level rule downstream can tell from a photograph;
* **most followed** — the whole fix. `nb_fan` rides in every response and
  upstream ignores it.

The second half covers the failure Deezer serves as a success: over its 50-per-5s
ceiling it answers HTTP 200 with an `error` body and no `data`, which reads as
"this artist does not exist" to anything that only looks at `data`. Recording
that as a miss would blank an artist until the next library rescan.
"""
import asyncio

import pytest

from backend.sources.music_library.artist_images import (
    ArtistImageService,
    normalize_name,
    pick_artist,
)


def hit(name, fans, slug="c0ffee"):
    """A Deezer search hit, shaped like the API's own."""
    return {
        "id": f"{name}-{fans}",
        "name": name,
        "nb_fan": fans,
        "picture_big": f"https://cdn-images.dzcdn.net/images/artist/{slug}/500x500.jpg",
    }


# Deezer's stand-in for a profile with no picture: the MD5 of the empty string.
NO_PHOTO = "d41d8cd98f00b204e9800998ecf8427e"


class TestPickArtist:
    def test_picks_the_most_followed_exact_match_not_the_first(self):
        """The measured failure, in one assertion: Deezer returns the 741-fan
        duplicate first and the real Amy Winehouse second."""
        hits = [hit("Amy Winehouse", 741), hit("Amy Winehouse", 3865377)]

        assert pick_artist("Amy Winehouse", hits)["nb_fan"] == 3865377

    def test_rejects_a_name_that_is_not_the_one_asked_for(self):
        """"Adèle & Robin" outranks nothing — it is a different artist. With no
        exact match left, the answer is no photo rather than a wrong one."""
        hits = [hit("Adèle & Robin", 3507), hit("Adele & The Chandeliers", 555)]

        assert pick_artist("Adele", hits) is None

    def test_matches_across_accents_and_case(self):
        """Deezer spells one artist two ways; both are the same person."""
        assert pick_artist("Rocé", [hit("ROCé", 21356)])["nb_fan"] == 21356
        assert pick_artist("NORAH JONES", [hit("Norah Jones", 2390524)]) is not None

    def test_skips_the_silhouette_even_when_it_is_the_only_match(self):
        """A profile with no picture must not be chosen — Deezer still serves an
        image for it, so accepting it puts a grey silhouette in the library."""
        assert pick_artist("Gang Starr", [hit("Gang Starr", 7, slug=NO_PHOTO)]) is None

    def test_prefers_a_photo_over_a_bigger_fan_count_without_one(self):
        """Ranking runs on the candidates that have a photo, not before."""
        picked = pick_artist(
            "Eminem",
            [hit("Eminem", 19088628, slug=NO_PHOTO), hit("Eminem", 132)],
        )

        assert picked["nb_fan"] == 132

    def test_no_hits_and_no_name_are_both_no_photo(self):
        assert pick_artist("Various Artists", []) is None
        assert pick_artist("", [hit("", 10)]) is None


class TestNormalizeName:
    def test_folds_accents_case_and_inner_whitespace(self):
        assert normalize_name("  Suprême   NTM ") == normalize_name("SUPREME NTM")

    def test_keeps_different_artists_apart(self):
        assert normalize_name("Zero 7") != normalize_name("Zero7")


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Stands in for aiohttp at the Deezer boundary, counting the searches."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = 0

    def get(self, url, params=None):
        self.calls += 1
        return FakeResponse(self._payloads.pop(0))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def service(monkeypatch, tmp_path):
    """A service whose cache is a tmp dir and whose Deezer calls are not spaced
    out (the interval is a politeness budget, not behaviour under test)."""
    monkeypatch.setattr(
        "backend.sources.music_library.artist_images.ARTIST_IMAGES_DIR", tmp_path
    )
    # Both are politeness budgets rather than behaviour under test — the spacing
    # between calls and the pause after a failure. The pause has its own test.
    monkeypatch.setattr(
        "backend.sources.music_library.artist_images._BACKOFF_AFTER_FAILURE", 0
    )
    svc = ArtistImageService(get_client=lambda: asyncio.sleep(0, result=None))
    monkeypatch.setattr(svc, "_space_out", lambda: asyncio.sleep(0))
    return svc


def install_session(monkeypatch, session):
    monkeypatch.setattr(
        "backend.sources.music_library.artist_images.aiohttp.ClientSession",
        lambda **kwargs: session,
    )


class TestQuotaIsNotAMiss:
    async def test_a_quota_refusal_is_retried_rather_than_remembered(
        self, monkeypatch, service
    ):
        """Deezer answers a rate-limit breach with HTTP 200 and an `error` body.
        Read as data-less it looks exactly like "no such artist" — and caching
        that would leave the artist blank until the next rescan. The second call
        must reach Deezer again, and succeed."""
        quota = {"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}}
        session = FakeSession([quota, {"data": [hit("Sade", 1448904)]}])
        install_session(monkeypatch, session)
        monkeypatch.setattr(service, "_download", lambda url: asyncio.sleep(0, result=b"jpeg"))

        assert await service.get_image("Sade") is None
        assert await service.get_image("Sade") == (b"jpeg", "image/jpeg")
        assert session.calls == 2

    async def test_a_genuine_miss_is_asked_once(self, monkeypatch, service):
        """The other side of it: an artist Deezer really does not have must not
        cost a search per render."""
        session = FakeSession([{"data": [hit("Various Artists Tribute", 12)]}])
        install_session(monkeypatch, session)

        assert await service.get_image("Various Artists") is None
        assert await service.get_image("Various Artists") is None
        assert session.calls == 1

    async def test_a_rescan_gives_a_missing_artist_another_chance(
        self, monkeypatch, service
    ):
        """`invalidate` is wired to the rescan hook: an artist with no photo
        today may have one after the library changes."""
        session = FakeSession([{"data": []}, {"data": [hit("Moussa", 4806)]}])
        install_session(monkeypatch, session)
        monkeypatch.setattr(service, "_download", lambda url: asyncio.sleep(0, result=b"jpeg"))

        assert await service.get_image("Moussa") is None
        service.invalidate()

        assert await service.get_image("Moussa") == (b"jpeg", "image/jpeg")


class TestBackoff:
    async def test_one_failure_parks_every_later_search(self, monkeypatch, tmp_path):
        """A unit with no outbound HTTPS would otherwise pay one 10 s timeout per
        artist, serialised, on every render — the whole list stalling with no
        backoff. The pause is on the service, not on a name, so no artist is
        remembered as photo-less because the network was down."""
        monkeypatch.setattr(
            "backend.sources.music_library.artist_images.ARTIST_IMAGES_DIR", tmp_path
        )
        svc = ArtistImageService(get_client=lambda: asyncio.sleep(0, result=None))
        monkeypatch.setattr(svc, "_space_out", lambda: asyncio.sleep(0))
        session = FakeSession([{"error": {"code": 4}}])
        install_session(monkeypatch, session)

        assert await svc.get_image("Sade") is None
        assert await svc.get_image("Nas") is None
        assert session.calls == 1
        # Parked, never remembered: neither artist is filed as having no photo.
        assert svc._missing == set()


class TestServesFromDisk:
    async def test_a_resolved_photo_is_cached_and_not_searched_again(
        self, monkeypatch, service, tmp_path
    ):
        """One search per artist for the life of the cache — the appliance keeps
        serving the photo with no network at all."""
        session = FakeSession([{"data": [hit("Metronomy", 268812)]}])
        install_session(monkeypatch, session)
        monkeypatch.setattr(service, "_download", lambda url: asyncio.sleep(0, result=b"jpeg"))

        assert await service.get_image("Metronomy") == (b"jpeg", "image/jpeg")
        assert len(list(tmp_path.glob("*.jpg"))) == 1

        fresh = ArtistImageService(get_client=lambda: asyncio.sleep(0, result=None))
        assert await fresh.get_image("Metronomy") == (b"jpeg", "image/jpeg")
        assert session.calls == 1

    async def test_an_artist_name_that_is_not_a_filename_still_caches(
        self, monkeypatch, service
    ):
        """The reason the cache is keyed by hash: "AC/DC" cannot be a file name,
        and it is the same trap that leaves those artists unrepresentable in
        Navidrome's own ArtistImageFolder."""
        session = FakeSession([{"data": [hit("AC/DC", 12345678)]}])
        install_session(monkeypatch, session)
        monkeypatch.setattr(service, "_download", lambda url: asyncio.sleep(0, result=b"jpeg"))

        assert await service.get_image("AC/DC") == (b"jpeg", "image/jpeg")


class TestCoverIds:
    async def test_only_artist_ids_are_claimed(self, service):
        """The cover route calls this on every miss, album ids included — it has
        to answer None for anything that is not an artist, without a lookup."""
        assert await service.get_cover("al-3nUFxsGWpvvHfA8IuXqRsc_0") is None
        assert await service.get_cover("mf-1x2y3z") is None

    async def test_the_artist_id_is_read_back_out_of_the_cover_id(self, monkeypatch):
        """`ar-<artist id>_<n>`: the suffix moves when the art does, so it is
        stripped before Navidrome is asked who this is."""
        asked = []

        class Client:
            async def get_artist(self, artist_id):
                asked.append(artist_id)
                return {"name": "Portishead"}

        svc = ArtistImageService(get_client=lambda: asyncio.sleep(0, result=Client()))
        monkeypatch.setattr(svc, "get_image", lambda name: asyncio.sleep(0, result=None))

        await svc.get_cover("ar-2oQcvqplsiJFiOwhVpp2Ow_0")

        assert asked == ["2oQcvqplsiJFiOwhVpp2Ow"]

    async def test_the_name_lookup_is_memoised(self, monkeypatch):
        """One getArtist per artist, not one per cover request."""
        calls = []

        class Client:
            async def get_artist(self, artist_id):
                calls.append(artist_id)
                return {"name": "Portishead"}

        svc = ArtistImageService(get_client=lambda: asyncio.sleep(0, result=Client()))
        monkeypatch.setattr(svc, "get_image", lambda name: asyncio.sleep(0, result=None))

        await svc.get_cover("ar-abc_0")
        await svc.get_cover("ar-abc_1")

        assert calls == ["abc"]
