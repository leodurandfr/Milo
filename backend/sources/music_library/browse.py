# backend/sources/music_library/browse.py
"""Unprivileged browsing of a NAS to build a share config (the add-share wizard).

Lets the frontend walk a server the way Finder does — list its shares, drill into
folders, and validate credentials — *without mounting anything and without root*:

- SMB: ``smbclient`` (userspace) lists shares (``-L``) and folder contents
  (``-c ls``), and its exit status doubles as credential validation. Password is
  passed via the ``PASSWD`` env var, never argv (argv is world-readable in /proc).
- NFS: ``showmount -e`` lists a server's exports. NFS has no lightweight browse
  (you would have to mount), so the wizard stops at export selection — no folder
  drill-down (a deliberate scope choice; SMB is the common NAS-music case).

Only the *final* mount is privileged (via milo-mount). Everything here runs as the
milo backend user. Fail-closed with a typed status the UI can branch on
(ok / auth_required / unreachable / error) rather than a bare exception.
"""
import asyncio
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger("source.music_library.browse")

# smbclient/showmount can hang on a dead host; cap every call.
SMB_TIMEOUT_S = 15.0
NFS_TIMEOUT_S = 10.0

# smbclient share types worth showing (skip Printer/IPC and $-suffixed admin shares).
_SMB_DISK_TYPE = "Disk"

# smbclient error markers → our typed status.
_AUTH_MARKERS = ("NT_STATUS_LOGON_FAILURE", "NT_STATUS_ACCESS_DENIED", "NT_STATUS_WRONG_PASSWORD")
_UNREACHABLE_MARKERS = (
    "NT_STATUS_HOST_UNREACHABLE",
    "NT_STATUS_CONNECTION_REFUSED",
    "NT_STATUS_IO_TIMEOUT",
    "NT_STATUS_BAD_NETWORK_NAME",
    "Connection to",  # "Connection to HOST failed"
    "Name resolution",
)


async def browse_share(
    share_type: str,
    host: str,
    path: str = "",
    credentials: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    """Browse one level of a server.

    ``path`` empty → top level (SMB shares / NFS exports); non-empty (SMB only) →
    the folders inside ``<share>/<subpath>``. Returns
    ``{status, entries, message}`` where entries are
    ``{name, path, kind}`` (kind ∈ share|dir|export) and status ∈
    ok|auth_required|unreachable|error.
    """
    if share_type == "nfs":
        return await _browse_nfs(host)
    if share_type == "cifs":
        return await _browse_smb(host, path, credentials)
    return {"status": "error", "entries": [], "message": f"Unsupported type: {share_type}"}


# === SMB ===

async def _browse_smb(
    host: str, path: str, credentials: Optional[Dict[str, str]]
) -> Dict[str, object]:
    if not path:
        return await _smb_list_shares(host, credentials)
    return await _smb_list_folders(host, path, credentials)


async def _smb_list_shares(
    host: str, credentials: Optional[Dict[str, str]]
) -> Dict[str, object]:
    """List a server's disk shares (``smbclient -L`` in grep-able ``-g`` mode)."""
    rc, out, err = await _smbclient(["-L", f"//{host}", "-g"], credentials)
    if rc != 0:
        return _smb_error_result(out, err)

    entries: List[Dict[str, str]] = []
    for line in out.splitlines():
        # -g format: "Type|Name|Comment"
        parts = line.split("|")
        if len(parts) < 2 or parts[0] != _SMB_DISK_TYPE:
            continue
        name = parts[1]
        if not name or name.endswith("$"):  # skip admin/hidden shares (C$, ADMIN$…)
            continue
        entries.append({"name": name, "path": name, "kind": "share"})
    entries.sort(key=lambda e: e["name"].lower())
    return {"status": "ok", "entries": entries, "message": ""}


async def _smb_list_folders(
    host: str, path: str, credentials: Optional[Dict[str, str]]
) -> Dict[str, object]:
    """List the sub-folders of ``<share>/<subpath>`` (``smbclient //host/share -c ls``)."""
    share, _, subpath = path.partition("/")
    # smbclient uses backslash path separators inside a share.
    ls_target = subpath.replace("/", "\\")
    command = f'cd "{ls_target}"; ls' if ls_target else "ls"
    rc, out, err = await _smbclient([f"//{host}/{share}", "-c", command], credentials)
    if rc != 0:
        return _smb_error_result(out, err)

    entries: List[Dict[str, str]] = []
    for name in _parse_smb_ls_dirs(out):
        entries.append({"name": name, "path": f"{path}/{name}", "kind": "dir"})
    entries.sort(key=lambda e: e["name"].lower())
    return {"status": "ok", "entries": entries, "message": ""}


def _parse_smb_ls_dirs(output: str) -> List[str]:
    """Directory names from ``smbclient ... -c ls`` output.

    Each entry line is ``<name>  <attrs>  <size>  <Www Mmm dd hh:mm:ss yyyy>`` —
    7 whitespace-delimited trailing fields after the name, so the name (which may
    contain spaces) is everything before them. Directories carry ``D`` in attrs.
    """
    dirs: List[str] = []
    for line in output.splitlines():
        tokens = line.split()
        if len(tokens) < 8:  # name + 7 trailing fields (dirs always have a name)
            continue
        attrs = tokens[-7]
        # A valid entry row has a numeric size in the field after the attributes.
        if not tokens[-6].isdigit():
            continue
        if "D" not in attrs:
            continue
        name = line.rsplit(None, 7)[0].strip()
        if name in (".", ".."):
            continue
        dirs.append(name)
    return dirs


async def _smbclient(
    args: List[str], credentials: Optional[Dict[str, str]]
) -> tuple:
    """Run smbclient with credentials via env (never argv). Returns (rc, out, err).

    No credentials → ``-N`` (anonymous/guest). rc=-1 signals a spawn/timeout
    failure (smbclient missing or a hung host).
    """
    cmd = ["smbclient", *args]
    env = dict(os.environ)
    if credentials and credentials.get("password"):
        cmd += ["-U", credentials.get("username") or "guest"]
        if credentials.get("domain"):
            cmd += ["-W", credentials["domain"]]
        env["PASSWD"] = credentials["password"]
    else:
        cmd.append("-N")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        logger.debug("smbclient not available — SMB browse disabled")
        return -1, "", "smbclient not installed"
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=SMB_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", "NT_STATUS_IO_TIMEOUT"
    except Exception as exc:  # never propagate — the route returns a typed status
        logger.debug("smbclient failed: %s", exc)
        return -1, "", str(exc)
    return proc.returncode, out_b.decode("utf-8", "ignore"), err_b.decode("utf-8", "ignore")


def _smb_error_result(out: str, err: str) -> Dict[str, object]:
    """Typed error envelope for a failed smbclient run. Classifies on BOTH
    streams — smbclient prints ``NT_STATUS_*`` (auth/connection) to *stdout*, not
    stderr — and surfaces whichever stream carried the message."""
    return {
        "status": _classify_smb_error(f"{err}\n{out}"),
        "entries": [],
        "message": (err.strip() or out.strip()),
    }


def _classify_smb_error(diagnostics: str) -> str:
    if any(m in diagnostics for m in _AUTH_MARKERS):
        return "auth_required"
    if any(m in diagnostics for m in _UNREACHABLE_MARKERS):
        return "unreachable"
    return "error"


# === NFS ===

async def _browse_nfs(host: str) -> Dict[str, object]:
    """List a server's NFS exports (``showmount -e``). No folder drill-down."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "showmount", "-e", "--no-headers", host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {"status": "error", "entries": [], "message": "showmount not installed"}
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=NFS_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"status": "unreachable", "entries": [], "message": "timeout"}
    if proc.returncode != 0:
        return {"status": "unreachable", "entries": [], "message": err_b.decode("utf-8", "ignore").strip()}

    entries: List[Dict[str, str]] = []
    for line in out_b.decode("utf-8", "ignore").splitlines():
        export = line.split()[0] if line.split() else ""
        if export.startswith("/"):
            entries.append({"name": export, "path": export, "kind": "export"})
    entries.sort(key=lambda e: e["name"].lower())
    return {"status": "ok", "entries": entries, "message": ""}
