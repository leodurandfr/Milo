# backend/tests/test_music_library_wizard_input.py
"""The add-share wizard's two untrusted surfaces: smbclient's argv, and the
request bodies that reach it and `milo-mount`.

What breaks when these fail:

* **`_smbclient`** is the wizard's only subprocess and it ran at 0 % — the
  existing browse tests replace it wholesale to test the parsers above it. Its
  contract is one line of its own docstring: *the password travels in the
  `PASSWD` environment variable, never in argv*, because argv is world-readable
  in `/proc`. Any local process on this appliance can read `/proc/*/cmdline`, so
  a password moved into `-U user%pass` is the NAS password handed to anything
  running here, for as long as the call lasts.
* **the validators** are the only thing standing between a request body and two
  destinations that take it literally: `milo-mount`'s argv, and the CIFS cred
  file it writes. Every `raise` arm in `models.py` had never fired. The one with
  a stated mechanism is the credential guard — a newline inside a password puts
  a second `password=` line in the cred file, and `milo-mount`'s own filter
  keeps whichever lines carry the three keys `mount.cifs` reads, so the injected
  one survives it.

Nothing here spawns: `create_subprocess_exec` is replaced by a recorder that
never reaches the operating system, so a guard that lets go fails the test
rather than running smbclient against a real host.
"""
import asyncio
import os

import pytest
from pydantic import ValidationError

from backend.sources.music_library import browse as browse_mod
from backend.sources.music_library.browse import browse_share
from backend.sources.music_library.models import (
    ShareBrowseRequest,
    ShareRequest,
    UsbNameRequest,
)


class _Proc:
    """A finished child process. Never spawned — handed straight back."""

    def __init__(self, rc=0, out=b"", err=b"", hangs=False):
        self.returncode = rc
        self._out = out
        self._err = err
        self._hangs = hangs
        self.killed = False
        self.waited = False

    async def communicate(self, input=None):
        if self._hangs:
            # Bounded just above the fixture's reduced timeout: a mutation that
            # removes the production `wait_for` must go red in seconds, not sit
            # on a core until the runner's ceiling.
            await asyncio.sleep(1.0)
        return self._out, self._err

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True


@pytest.fixture
def spawn(monkeypatch):
    """Records what smbclient/showmount *would* have been run with.

    A spy checked afterwards would be too late: this checkout is the appliance
    and the wizard's host comes from the request body, so a call that escapes
    reaches a real machine on the LAN. Nothing is spawned at all.
    """
    class _Recorder:
        def __init__(self):
            self.calls = []          # (argv, env)
            self.result = _Proc()
            self.raises = None

        async def __call__(self, *argv, **kwargs):
            self.calls.append((list(argv), kwargs.get("env")))
            if self.raises is not None:
                raise self.raises
            return self.result

        @property
        def argv(self):
            return self.calls[-1][0]

        @property
        def env(self):
            return self.calls[-1][1] or {}

    recorder = _Recorder()
    monkeypatch.setattr(browse_mod.asyncio, "create_subprocess_exec", recorder)
    return recorder


@pytest.fixture(autouse=True)
def short_timeouts(monkeypatch):
    """Production waits 15 s on a dead host; the test waits 0.2 s for the same
    branch. Left at 15 s a `wait_for` mutation would hang instead of failing."""
    monkeypatch.setattr(browse_mod, "SMB_TIMEOUT_S", 0.2)
    monkeypatch.setattr(browse_mod, "NFS_TIMEOUT_S", 0.2)


CREDS = {"username": "leo", "password": "hunter2", "domain": "WORKGROUP"}


# =============================================================================
# smbclient — the password must not be visible in /proc
# =============================================================================

class TestCredentialsNeverReachArgv:

    async def test_the_password_travels_in_the_environment_alone(self, spawn):
        """argv is world-readable in /proc/*/cmdline: every process running as
        any user on this appliance can read it while the call is in flight."""
        await browse_share("cifs", "nas.local", "", CREDS)

        assert "hunter2" not in " ".join(spawn.argv)
        assert spawn.env["PASSWD"] == "hunter2"

    async def test_the_account_and_domain_are_the_only_credential_argv(self, spawn):
        await browse_share("cifs", "nas.local", "", CREDS)

        assert "-U" in spawn.argv and spawn.argv[spawn.argv.index("-U") + 1] == "leo"
        assert "-W" in spawn.argv and spawn.argv[spawn.argv.index("-W") + 1] == "WORKGROUP"

    async def test_a_share_with_no_password_is_browsed_anonymously(self, spawn):
        """`-N` is what stops smbclient blocking on a terminal prompt no one can
        answer — the call would then be killed by the timeout and reported as an
        unreachable host, which is not what is wrong."""
        await browse_share("cifs", "nas.local", "", None)

        assert "-N" in spawn.argv
        assert "PASSWD" not in {k: v for k, v in spawn.env.items() if k == "PASSWD"}
        assert "-U" not in spawn.argv

    async def test_a_domain_free_account_sends_no_domain_flag(self, spawn):
        await browse_share("cifs", "nas.local", "", {"username": "leo", "password": "p"})

        assert "-W" not in spawn.argv

    async def test_the_inherited_environment_is_carried_over(self, spawn):
        """smbclient reads the caller's environment for its own configuration;
        replacing it wholesale rather than extending it is how a call starts
        behaving differently from the same command typed on the unit."""
        await browse_share("cifs", "nas.local", "", CREDS)

        assert set(os.environ) <= set(spawn.env)


class TestSmbclientFailsSoftly:

    async def test_a_missing_smbclient_disables_smb_browsing_without_raising(self, spawn):
        """A dev host without samba-client must still serve the wizard: the route
        answers a typed status, not a 500."""
        spawn.raises = FileNotFoundError("smbclient")

        result = await browse_share("cifs", "nas.local", "", None)

        assert result["status"] == "error"
        assert "not installed" in result["message"]

    async def test_a_hung_host_is_killed_and_reported_as_unreachable(self, spawn):
        """An SMB call to a NAS that answers its ARP but nothing else hangs for
        ever; the wizard has a user waiting on it."""
        spawn.result = _Proc(hangs=True)

        result = await browse_share("cifs", "nas.local", "", None)

        assert result["status"] == "unreachable"
        assert spawn.result.killed is True
        assert spawn.result.waited is True, "the killed child was never reaped"

    async def test_a_wrong_password_is_read_from_stdout(self, spawn):
        """smbclient prints its NT_STATUS lines to *stdout*, not stderr. Read
        from stderr alone, a wrong password looks like a generic error and the
        wizard shows no password field to correct."""
        spawn.result = _Proc(rc=1, out=b"session setup failed: NT_STATUS_LOGON_FAILURE\n")

        result = await browse_share("cifs", "nas.local", "", CREDS)

        assert result["status"] == "auth_required"

    async def test_output_is_decoded_leniently(self, spawn):
        """A share name in a legacy code page must not take the whole listing
        down with a UnicodeDecodeError."""
        spawn.result = _Proc(rc=0, out=b"Disk|Mus\xffique|\n")

        result = await browse_share("cifs", "nas.local", "", None)

        assert result["status"] == "ok"
        assert [entry["name"] for entry in result["entries"]]


class TestNfsExports:

    async def test_a_missing_showmount_is_a_typed_error(self, spawn):
        spawn.raises = FileNotFoundError("showmount")

        result = await browse_share("nfs", "nas.local")

        assert result["status"] == "error"
        assert "not installed" in result["message"]

    async def test_a_hung_server_is_killed_and_reported_as_unreachable(self, spawn):
        spawn.result = _Proc(hangs=True)

        result = await browse_share("nfs", "nas.local")

        assert result["status"] == "unreachable"
        assert spawn.result.killed is True
        assert spawn.result.waited is True

    async def test_an_unknown_type_never_spawns_anything(self, spawn):
        result = await browse_share("afp", "nas.local")

        assert result["status"] == "error"
        assert spawn.calls == []


# =============================================================================
# The request bodies — every `raise` arm had never fired
# =============================================================================

class TestShareRequestGuards:
    """`host` and `path` become milo-mount arguments; the credential fields
    become lines of the CIFS cred file it writes."""

    def _share(self, **overrides):
        body = {"type": "cifs", "host": "nas.local", "path": "Music", "name": "NAS"}
        body.update(overrides)
        return ShareRequest(**body)

    @pytest.mark.parametrize("field", ["name", "username", "password", "domain"])
    def test_a_newline_in_a_credential_field_is_refused(self, field):
        """The stated mechanism: `password=a\\npassword=b` reaches milo-mount on
        stdin, its filter keeps every line carrying one of the three keys
        mount.cifs reads, and the injected one survives into the cred file."""
        with pytest.raises(ValidationError):
            self._share(**{field: "a\npassword=elsewhere"})

    def test_a_null_byte_is_refused_too(self):
        with pytest.raises(ValidationError):
            self._share(password="a\x00b")

    def test_an_ordinary_password_with_punctuation_is_kept(self):
        """The guard must not be a character allowlist — a generated NAS password
        holds symbols, and refusing them is a share that cannot be added."""
        share = self._share(password="p@ss w0rd!#=%&")

        assert share.password == "p@ss w0rd!#=%&"

    def test_a_host_with_whitespace_is_refused(self):
        """It reaches the mount syscall as one argument; a space in it is a
        second argument to whatever reads the line."""
        with pytest.raises(ValidationError):
            self._share(host="nas local")

    def test_a_host_that_is_only_whitespace_is_refused(self):
        with pytest.raises(ValidationError):
            self._share(host="   ")

    def test_a_path_that_is_only_whitespace_is_refused(self):
        """Stripped to nothing, it would mount the share root — a different
        share from the one the wizard showed."""
        with pytest.raises(ValidationError):
            self._share(path="   ")

    def test_a_path_may_hold_spaces_because_folders_do(self):
        assert self._share(path="Music/My Albums").path == "Music/My Albums"

    def test_nfs_refuses_credentials_rather_than_ignoring_them(self):
        """Plain NFS authorises by UID and host, so a username here would mount
        identically while `has_credentials` told the edit screen otherwise — the
        same class of lie as a share that kept a stale password."""
        with pytest.raises(ValidationError):
            ShareRequest(type="nfs", host="nas.local", path="/export/music",
                         name="NAS", username="leo")

    def test_nfs_without_credentials_is_accepted(self):
        share = ShareRequest(type="nfs", host="nas.local", path="/export/music", name="NAS")

        assert share.username is None


class TestBrowseRequestGuards:

    def test_a_control_character_in_the_browse_path_is_refused(self):
        """It reaches smbclient's `--directory=` slot."""
        with pytest.raises(ValidationError):
            ShareBrowseRequest(type="cifs", host="nas.local", path="Music\n-c;!sh")

    def test_a_host_with_whitespace_is_refused(self):
        with pytest.raises(ValidationError):
            ShareBrowseRequest(type="cifs", host="nas local")

    def test_an_empty_browse_path_means_the_top_level(self):
        assert ShareBrowseRequest(type="cifs", host="nas.local").path == ""


class TestUsbNameGuards:

    def test_a_control_character_in_a_key_name_is_refused(self):
        """The name becomes that key's Navidrome library name, and travels back
        out in the storages payload the UI renders."""
        with pytest.raises(ValidationError):
            UsbNameRequest(name="iPod\x07de Claire")

    def test_the_name_is_stored_trimmed(self):
        assert UsbNameRequest(name="  iPod de Claire  ").name == "iPod de Claire"

    def test_an_empty_name_is_allowed_and_means_forget_it(self):
        """Emptying the field is how the filesystem label is restored; refusing
        it would leave a mistyped name with no way back."""
        assert UsbNameRequest(name="").name == ""
