# backend/core/system/journal_retention.py
"""How long the server keeps its systemd journal: the one caller of its helper.

`system.log_retention_days` (7, 15 or 30) is the owner's choice; what journald
does is the drop-in `milo-set-journal-retention` owns — absent at 7 days, since
the rootfs's `99-milo-journald.conf` already declares the 7-day caps, and
`MaxRetentionSec=15d` or `30d` otherwise. The two are kept in step from both
ends:

  * a change (`PUT /api/settings/log-retention`) is applied first and stored
    only once the helper has taken it (`apply`), so the setting never names a
    retention journald does not have;
  * at each start the drop-in is read back and the helper called only when it
    disagrees with the setting (`reconcile`) — a fresh image (the setting's
    default is 15 days, the rootfs file's 7), a settings.json set aside, a
    drop-in removed by hand, a setting restored from a backup. The drop-in lives
    outside every tree an update deploys, so nothing else moves it.

The reconcile runs in the background: a helper that is slow (it restarts
systemd-journald) holds up no part of the boot. On a dev host with no helper,
it is one warning and the backend starts all the same.
"""
import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from backend.config.constants import (
    JOURNAL_RETENTION_DROPIN,
    SET_JOURNAL_RETENTION_CMD,
)
from backend.shared.background import BackgroundTaskSet

if TYPE_CHECKING:
    from backend.core.settings import SettingsService

logger = logging.getLogger(__name__)

# What the image keeps with no drop-in of the helper's: 99-milo-journald.conf.
RETENTION_WITHOUT_DROPIN_DAYS = 7
# The helper restarts systemd-journald when the drop-in moved, which waits for
# journald to flush: generous, so a slow card is not read as a failure.
HELPER_TIMEOUT_S = 30.0

_MAX_RETENTION = re.compile(r"^\s*MaxRetentionSec\s*=\s*(\d+)d\s*$", re.MULTILINE)


class JournalRetentionError(RuntimeError):
    """The helper did not apply the retention asked for; the message says why."""


def read_retention_in_force(dropin: Path = JOURNAL_RETENTION_DROPIN) -> Optional[int]:
    """The retention the helper's drop-in declares, in days.

    No drop-in is the image's 7 days. A drop-in that declares no
    `MaxRetentionSec=<N>d` is an answer nobody can read: None, which disagrees
    with every setting, so the reconcile has the helper write it again.
    """
    try:
        text = dropin.read_text(encoding="utf-8")
    except FileNotFoundError:
        return RETENTION_WITHOUT_DROPIN_DAYS
    except OSError as e:
        logger.warning("Could not read the journal retention drop-in %s: %s", dropin, e)
        return None
    found = _MAX_RETENTION.findall(text)
    # journald takes the last one, and so does this.
    return int(found[-1]) if found else None


class JournalRetentionService:
    """Applies the journal retention, and puts it back in step at each start."""

    def __init__(
        self, settings_service: "SettingsService", dropin: Path = JOURNAL_RETENTION_DROPIN,
    ) -> None:
        self._settings = settings_service
        self._dropin = dropin
        self._bg = BackgroundTaskSet(logger, "journal_retention")
        # One helper run at a time: a reconcile and a change from the page.
        self._lock = asyncio.Lock()

    async def initialize(self) -> bool:
        """Start the reconcile in the background; the boot does not wait on it."""
        self._bg.spawn(self.reconcile(), label="reconcile")
        return True

    async def cleanup(self) -> None:
        await self._bg.cancel_all()

    async def apply(self, days: int) -> None:
        """Have the helper put the journal on `days`; raise when it did not.

        The value goes on stdin: the policy grants the helper with no argument
        at all.
        """
        async with self._lock:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", SET_JOURNAL_RETENTION_CMD,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(f"{days}\n".encode()), HELPER_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise JournalRetentionError(
                    f"milo-set-journal-retention did not answer within {HELPER_TIMEOUT_S:.0f} s"
                ) from None
            if proc.returncode != 0:
                detail = stderr.decode(errors="replace").strip() or "unknown error"
                raise JournalRetentionError(
                    f"milo-set-journal-retention failed (rc={proc.returncode}): {detail}"
                )
            said = [line for line in stdout.decode(errors="replace").splitlines() if line]
            logger.info("Journal retention set to %d days: %s", days, "; ".join(said))

    async def reconcile(self) -> None:
        """Call the helper only when the drop-in disagrees with the setting.

        Fails open: a host with no helper, or a helper that refuses, is one
        warning, and the journal keeps what it had.
        """
        wanted = await self._settings.get_setting("system.log_retention_days")
        in_force = await asyncio.to_thread(read_retention_in_force, self._dropin)
        if in_force == wanted:
            logger.debug("Journal retention already %s days", wanted)
            return
        logger.info(
            "Journal retention is %s days, the setting says %s: applying it",
            in_force if in_force is not None else "unreadable", wanted,
        )
        try:
            await self.apply(wanted)
        except (OSError, JournalRetentionError) as e:
            logger.warning("Journal retention left as it was (%s days asked): %s", wanted, e)
