# backend/tests/test_music_library_browse.py
"""Tests for the add-share wizard's NAS browsing + live mount status.

Covers the smbclient share/folder parsers and error classification, the NFS
export lister, the resilient /shares/browse route envelope, and
StorageManager.get_mounted_share_ids (the per-share connected indicator).
"""
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.sources.music_library import browse as browse_mod
from backend.sources.music_library.browse import (
    _classify_smb_error,
    _parse_smb_ls_dirs,
    browse_share,
)
from backend.sources.music_library.models import ShareRequest
from backend.sources.music_library.routes import router, setup_music_library_routes
from backend.sources.music_library.storage import StorageManager


class TestClassifySmbError:
    def test_auth(self):
        assert _classify_smb_error("session setup failed: NT_STATUS_LOGON_FAILURE") == "auth_required"

    def test_unreachable(self):
        assert _classify_smb_error("Connection to nas failed (NT_STATUS_HOST_UNREACHABLE)") == "unreachable"

    def test_other(self):
        assert _classify_smb_error("some other smbclient noise") == "error"


class TestParseSmbLsDirs:
    def test_dirs_only_names_with_spaces(self):
        output = "\n".join([
            "  .                                   D        0  Mon Jan  1 00:00:00 2024",
            "  ..                                  D        0  Mon Jan  1 00:00:00 2024",
            "  Albums                              D        0  Tue Feb  2 12:00:00 2024",
            "  My Music                            D        0  Tue Feb  2 12:00:00 2024",
            "  cover.jpg                           A   123456  Tue Feb  2 12:00:00 2024",
            "",
            "\t\t63000 blocks available",
        ])
        assert _parse_smb_ls_dirs(output) == ["Albums", "My Music"]

    def test_hidden_dir_attrs(self):
        # A directory can carry extra attribute flags (DHS) — still a directory.
        line = "  .Trash                             DH       0  Tue Feb  2 12:00:00 2024"
        assert _parse_smb_ls_dirs(line) == [".Trash"]


class TestSmbListShares:
    async def test_lists_disk_shares_skips_admin_and_ipc(self):
        out = "Disk|Music|\nDisk|Photos|Family photos\nIPC|IPC$|IPC Service\nDisk|C$|Admin"
        with patch.object(browse_mod, "_smbclient", AsyncMock(return_value=(0, out, ""))):
            r = await browse_share("cifs", "nas.local", "")
        assert r["status"] == "ok"
        assert [e["name"] for e in r["entries"]] == ["Music", "Photos"]
        assert all(e["kind"] == "share" for e in r["entries"])

    async def test_auth_required_from_stdout(self):
        # smbclient prints NT_STATUS_LOGON_FAILURE to stdout, not stderr — the
        # classifier must read both streams.
        with patch.object(
            browse_mod, "_smbclient",
            AsyncMock(return_value=(1, "session setup failed: NT_STATUS_LOGON_FAILURE", "")),
        ):
            r = await browse_share("cifs", "nas.local", "")
        assert r["status"] == "auth_required"
        assert r["entries"] == []

    async def test_folder_drill_builds_paths(self):
        ls = "  Albums                              D        0  Tue Feb  2 12:00:00 2024"
        with patch.object(browse_mod, "_smbclient", AsyncMock(return_value=(0, ls, ""))):
            r = await browse_share("cifs", "nas.local", "Music")
        assert r["status"] == "ok"
        assert r["entries"] == [{"name": "Albums", "path": "Music/Albums", "kind": "dir"}]


class TestNfsExports:
    async def test_lists_exports(self):
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"/volume1/music 192.168.1.0/24\n/volume1/backup *\n", b""))
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            r = await browse_share("nfs", "nas.local", "")
        assert r["status"] == "ok"
        assert [e["path"] for e in r["entries"]] == ["/volume1/backup", "/volume1/music"]
        assert all(e["kind"] == "export" for e in r["entries"])

    async def test_unreachable_on_nonzero(self):
        proc = MagicMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"clnt_create: RPC: Timed out"))
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            r = await browse_share("nfs", "10.0.0.9", "")
        assert r["status"] == "unreachable"


class TestBrowseRoute:
    @pytest.fixture
    def api(self):
        app = FastAPI()
        setup_music_library_routes(lambda: MagicMock())
        app.include_router(router, prefix="/api")
        return TestClient(app)

    def test_envelope(self, api):
        canned = {"status": "ok", "entries": [{"name": "Music", "path": "Music", "kind": "share"}], "message": ""}
        with patch(
            "backend.sources.music_library.routes.browse_share",
            AsyncMock(return_value=canned),
        ):
            r = api.post("/api/music-library/shares/browse", json={"type": "cifs", "host": "nas.local", "path": ""})
        assert r.status_code == 200
        assert r.json() == canned

    def test_rejects_missing_host(self, api):
        r = api.post("/api/music-library/shares/browse", json={"type": "cifs", "path": ""})
        assert r.status_code == 422

    def test_rejects_bad_type(self, api):
        r = api.post("/api/music-library/shares/browse", json={"type": "afp", "host": "nas", "path": ""})
        assert r.status_code == 422


class TestShareRequestPath:
    def test_allows_spaces_in_path(self):
        # Real folders are named "My Music" — the path may contain spaces.
        assert ShareRequest(type="cifs", host="192.168.1.10", path="My Music", name="NAS").path == "My Music"

    def test_strips_and_rejects_control_chars_in_path(self):
        assert ShareRequest(type="cifs", host="h", path="  Music  ", name="N").path == "Music"
        with pytest.raises(ValueError):
            ShareRequest(type="cifs", host="h", path="Mu\tsic", name="N")

    def test_host_still_rejects_whitespace(self):
        with pytest.raises(ValueError):
            ShareRequest(type="cifs", host="bad host", path="Music", name="N")


class TestMountedShareIds:
    def test_reads_proc_mounts(self):
        proc_mounts = (
            "/dev/sda1 /media/milo/USB-KEY vfat ro,nosuid 0 0\n"
            "//nas/Music /media/milo/music-abc123 cifs ro 0 0\n"
            "//nas2/Photos /media/milo/photos-def456/sub cifs ro 0 0\n"
            "tmpfs /media/milo tmpfs rw 0 0\n"          # exactly the root — excluded
            "proc /proc proc rw 0 0\n"
            "/dev/root / ext4 rw 0 0\n"
        )
        mgr = StorageManager(AsyncMock(return_value=None))
        with patch("builtins.open", mock_open(read_data=proc_mounts)):
            ids = mgr.get_mounted_share_ids()
        assert ids == {"USB-KEY", "music-abc123", "photos-def456"}

    def test_fail_open_on_error(self):
        mgr = StorageManager(AsyncMock(return_value=None))
        with patch("builtins.open", side_effect=OSError("nope")):
            assert mgr.get_mounted_share_ids() == set()
