# backend/tests/test_cd_routes.py
"""`GET /api/cd/cover/{disc_id}` — the CD source's only route.

Not one line of the handler had ever run, and most of it is a guard: `disc_id`
comes straight out of the URL and is used to build a filesystem path under
`/var/lib/milo/cd_covers/`. Two checks stand between it and the disk — a name
check on the way in, and a containment check on the resolved path on the way
out — and a guard nothing exercises is a guard nobody knows is still there.

Same shape as the SSRF guard on the radio favicon proxy: the endpoint reads
harmless (it serves album art), so the check is the whole of it.

Consumer: `AudioPlayerFull` / `AudioSourceStatus` via `metadata.cover_url`,
which the data service sets to `/api/cd/cover/{disc_id}` for a cached jacket.
"""
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config.constants import CD_COVERS_DIR
from backend.sources.cd.routes import setup_cd_routes

JPEG = b"\xff\xd8\xff\xe0" + b"J" * 512


@pytest.fixture
def covers(tmp_path, monkeypatch):
    """Point the containment check at a directory this test owns.

    The route resolves CD_COVERS_DIR itself, so the module-level binding is what
    has to move — not the service's `_covers_dir`.
    """
    monkeypatch.setattr("backend.sources.cd.routes.CD_COVERS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(covers):
    source = Mock()
    source.data_service = Mock()
    source.data_service.get_cover_path = Mock(
        side_effect=lambda disc_id: (
            str(covers / f"{disc_id}.jpg")
            if (covers / f"{disc_id}.jpg").exists() else None
        )
    )
    app = FastAPI()
    app.include_router(setup_cd_routes(lambda: source), prefix="/api")
    return TestClient(app), source


class TestServingACover:
    def test_a_cached_jacket_is_served_as_an_image(self, client, covers):
        """The non-triviality check the refusals below rest on: this route can
        succeed, so a 400 elsewhere is the guard and not a broken fixture."""
        (covers / "disc-1.jpg").write_bytes(JPEG)
        c, _source = client

        resp = c.get("/api/cd/cover/disc-1")

        assert resp.status_code == 200
        assert resp.content == JPEG
        assert resp.headers["content-type"] == "image/jpeg"

    def test_the_jacket_is_cached_hard_by_the_browser(self, client, covers):
        """A disc ID is a hash of the TOC, so the bytes behind it never change;
        without this the player refetches the cover on every render."""
        (covers / "disc-1.jpg").write_bytes(JPEG)
        c, _source = client
        assert "max-age=31536000" in c.get("/api/cd/cover/disc-1").headers["cache-control"]

    def test_a_disc_with_no_jacket_is_a_404_and_not_an_error(self, client, caplog):
        """Plenty of discs have no image in the Cover Art Archive. Logged at
        ERROR this would raise the WebSocket error banner on a normal disc."""
        c, _source = client
        with caplog.at_level("ERROR", logger="backend.sources.cd.routes"):
            resp = c.get("/api/cd/cover/disc-nobody-has")

        assert resp.status_code == 404
        assert caplog.records == []


class TestTheDiscIdGuard:
    """`disc_id` is untrusted path input, joined onto the covers directory.

    Two things stand in front of the disk and they were measured, not assumed:
    Starlette resolves `.`/`..`/`/` out of the path before routing, so most
    shapes never reach the handler at all; what does reach it meets the name
    check, and then the resolved path is re-checked against the store.

    A 400 is deliberate rather than 404: a traversal attempt is not a missing
    cover, and it is logged at ERROR so it reaches the operator.
    """

    @pytest.mark.parametrize("disc_id", [
        "../../../etc/passwd",
        "sub/disc-1",
        "/etc/passwd",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "..%2f..%2fetc",
    ])
    def test_a_path_shaped_id_never_reaches_the_store(self, client, disc_id):
        c, source = client
        resp = c.get(f"/api/cd/cover/{disc_id}")

        assert resp.status_code in (400, 404)
        source.data_service.get_cover_path.assert_not_called()

    def test_an_id_that_is_not_its_own_basename_is_refused(self, client, caplog):
        """The first clause of the guard, and the only input measured to reach
        it: `.` survives Starlette's normalisation when percent-encoded, and
        `Path(".").name` is `""`, which is not `"."`.

        Its sibling `..` does NOT trip this clause — see the test below — so
        this is the one place the clause is load-bearing.
        """
        c, source = client
        with caplog.at_level("ERROR", logger="backend.sources.cd.routes"):
            resp = c.get("/api/cd/cover/%2e")

        assert resp.status_code == 400
        source.data_service.get_cover_path.assert_not_called()
        assert any("traversal" in r.message for r in caplog.records)

    def test_a_backslash_escape_is_refused_by_the_handler_itself(self, client, caplog):
        """A backslash is not a separator to Starlette, so this one does reach
        the handler — it is the check in the route that stops it, and the only
        one of these shapes for which that is true."""
        c, source = client
        with caplog.at_level("ERROR", logger="backend.sources.cd.routes"):
            resp = c.get("/api/cd/cover/..%5C..%5Cwindows")

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid disc ID"
        source.data_service.get_cover_path.assert_not_called()
        assert any("traversal" in r.message for r in caplog.records)

    def test_a_bare_dot_dot_gets_past_the_name_check_and_is_defused_downstream(
            self, client, covers):
        """Measured, and the reason the two layers are not redundant.

        `Path("..").name` is `".."`, so the first guard does NOT trip on it and
        `get_cover_path` is called. What makes it harmless is that the store
        appends `.jpg`: the lookup becomes `<covers>/...jpg`, an ordinary file
        name inside the directory, and the containment check that follows sees
        nothing to refuse. The guard is not what stops this one — the extension
        is. Pinned so that a store which stops appending an extension shows up
        here rather than on a unit.
        """
        c, source = client
        resp = c.get("/api/cd/cover/%2e%2e")

        source.data_service.get_cover_path.assert_called_once_with("..")
        assert resp.status_code == 404
        assert not any(p.name.startswith("..") and p.exists()
                       for p in covers.parent.iterdir() if p != covers)

    def test_an_ordinary_disc_id_is_not_caught_by_the_guard(self, client, covers):
        """The guard must not reject the real thing: a libdiscid ID is 28 chars
        of base64 with `.`, `_` and `-` in the alphabet."""
        disc_id = "xUFa5T3lJZ0v_x8P2Xd.6bC7wSA-"
        (covers / f"{disc_id}.jpg").write_bytes(JPEG)
        c, _source = client
        assert c.get(f"/api/cd/cover/{disc_id}").status_code == 200

    def test_a_cover_path_pointing_outside_the_covers_directory_is_refused(
            self, client, covers, tmp_path):
        """The second guard, and the one that is load-bearing. `get_cover_path`
        builds the path; a store pointed somewhere else, or a jacket moved, is
        what makes its answer worth re-checking after resolution."""
        outside = tmp_path.parent / "outside-the-store.jpg"
        outside.write_bytes(JPEG)
        c, source = client
        source.data_service.get_cover_path = Mock(return_value=str(outside))

        resp = c.get("/api/cd/cover/disc-1")

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid disc ID"

    def test_a_symlink_out_of_the_store_is_followed_and_refused(self, client, covers, tmp_path):
        """`Path.resolve()` is what makes the containment check meaningful: a
        symlink sitting inside the store whose target is not would pass a string
        test on the unresolved path."""
        secret = tmp_path.parent / "secret.jpg"
        secret.write_bytes(b"not a jacket")
        (covers / "disc-link.jpg").symlink_to(secret)
        c, _source = client

        assert c.get("/api/cd/cover/disc-link").status_code == 400
