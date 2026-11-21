# backend/sources/tidal/runtime.py
"""The Tidal Connect runtime tree, which the unit downloads itself.

The image does not carry it: the daemon is Tidal's proprietary SDK with iFi's
device certificate, and a release is not Milō's to redistribute them in. The
first time Tidal is selected, or enabled in the Dock, the backend asks
`milo-deploy-update install-tidal-runtime` to build it as root — the
checkout's `provisioning/tidal_connect_runtime.py`, every byte checked against
a digest, the tree verified, then renamed into place. So the tree is whole or
absent, and its loader being there is what "installed" means: it is the file
the launcher (`rootfs/usr/local/bin/milo-tidal-connect`) refuses to start
without, and the two spellings of its path are held equal by
`tests/architecture/test_daemon_rendezvous.py`.

What it says to the screen is the source's availability: `installing` while
the helper runs, `download_failed` once it failed (until the next attempt);
nothing otherwise, a tree never asked for included — selecting Tidal is what
asks for it.

The helper runs in the backend's cgroup, as every sudo child does: a backend
restart kills a build in flight, which leaves only the staging directory the
next build starts by removing.
"""
import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

from backend.config.constants import DEPLOY_UPDATE_CMD
from backend.shared.background import BackgroundTaskSet

TIDAL_RUNTIME_ROOT = "/opt/milo/tidal-connect"
# The tree's own dynamic loader, which the launcher runs the daemon through.
RUNTIME_LOADER = "lib/ld-linux-armhf.so.3"
INSTALL_SUBCOMMAND = "install-tidal-runtime"


def failure_cause(output: str) -> str:
    """What a failed install says went wrong, in one line.

    The helper's own `ERROR: the Tidal Connect runtime could not be built`
    always comes last and names nothing, so it is never the answer alone: the
    builder's last `FATAL:` line is (a digest mismatch, a misaligned library),
    with the indented lines under it — verify() names the libraries it refuses
    there, one per line, and its heading alone names none — else the last two
    lines, which hold a traceback's exception before the wrapper. The helper's
    output reaches no journal but this one log line."""
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if not lines:
        return "no output"
    fatal = [i for i, line in enumerate(lines) if line.startswith("FATAL:")]
    if fatal:
        cause = [lines[fatal[-1]]]
        for line in lines[fatal[-1] + 1:]:
            if not line[0].isspace():
                break
            cause.append(line.strip())
        return " / ".join(cause)
    return " / ".join(line.strip() for line in lines[-2:])


class TidalRuntime:
    """Whether the runtime tree is there, and its one install at a time."""

    def __init__(
        self,
        root: str,
        logger: logging.Logger,
        on_change: Callable[[], None],
        on_settled: Callable[[bool], Awaitable[None]],
    ):
        self._loader = Path(root) / RUNTIME_LOADER
        self._logger = logger
        self._on_change = on_change
        self._on_settled = on_settled
        # Not the source's `_bg`: that set is drained on every stop of the
        # source, and a failed start stops it right after asking for the tree.
        self._bg = BackgroundTaskSet(logger, "source.tidal.runtime")
        self._installing = False
        self._failed = False

    @property
    def loader(self) -> Path:
        """The file whose presence is the tree's: the launcher runs through it."""
        return self._loader

    async def present(self) -> bool:
        """Whether the tree is installed (its loader is in place)."""
        return await asyncio.to_thread(self._loader.is_file)

    def availability(self) -> Optional[str]:
        if self._installing:
            return "installing"
        if self._failed:
            return "download_failed"
        return None

    def prepare(self) -> None:
        """Install the tree unless it is there already. Not awaited: the Dock's
        answer does not wait for a download of several hundred MB."""
        self._bg.spawn(self._prepare(), label="prepare")

    async def _prepare(self) -> None:
        if not await self.present():
            self.install()

    def install(self) -> None:
        """Start the install, unless one runs. A failed one is tried again."""
        if self._installing:
            return
        self._installing = True
        self._failed = False
        self._on_change()
        self._bg.spawn(self._install(), label="install")

    async def _install(self) -> None:
        self._logger.info("Downloading the Tidal Connect runtime (first use on this unit)")
        output = ""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", DEPLOY_UPDATE_CMD, INSTALL_SUBCOMMAND,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode(errors="replace").strip()
            exited = proc.returncode
        except OSError as e:
            output, exited = str(e), None
        installed = await self.present()
        self._installing = False
        self._failed = not installed
        if installed:
            self._logger.info("Tidal Connect runtime installed")
        else:
            self._logger.error(
                "Tidal Connect runtime not installed (helper exit %s): %s",
                exited, failure_cause(output),
            )
        self._on_change()
        await self._on_settled(installed)

    async def cleanup(self) -> None:
        """Backend teardown. The helper itself is root's and runs on until the
        backend's cgroup goes."""
        await self._bg.cancel_all()
