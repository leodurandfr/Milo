"""One way to replace a file on the satellite's card: whole, or not at all.

Every file this app writes is one a later start reads back: the environment
file systemd hands the units, hardware.json, the CamillaDSP config, the update
trial's state, the server's secret and addresses. A power cut during a plain
overwrite leaves it truncated, and the start that follows reads the half it
finds — a room silent until someone deletes a file by hand. So each is written
beside itself under a name of its own, flushed, given its mode, renamed over the
old one, and the directory flushed so the rename survives the cut too.

`milo-client-update-guard` keeps its own copy: it is a rootfs script, which
imports nothing from `app/` (the update it judges may have just replaced it).
"""
import os
import tempfile
from pathlib import Path
from typing import Union


def write_atomic(path: Union[str, Path], content: Union[str, bytes], *, mode: int = 0o644) -> None:
    """`content` in `path`, replaced whole and made durable, with `mode`. Blocking.

    A reader, or a power cut, sees the old content or the new, never half. The
    mode is set on the new file rather than inherited from the umask: the files
    the units and the root helpers read stay readable to them, and a secret
    asks for 0600.
    """
    path = Path(path)
    data = content.encode("utf-8") if isinstance(content, str) else content
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
