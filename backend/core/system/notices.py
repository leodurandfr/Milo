# backend/core/system/notices.py
"""The lasting notice of every persisted file set aside, until the owner has read it.

A file that cannot be read is set aside and its owner starts from its defaults
(`shared/persistence.py`), and the unit always starts. The one ERROR that says
so reaches a screen only through the live banner (`WebSocketLogHandler`), which
is lost at boot — when most set-asides happen, before any client listens — and
untranslated. So each set-aside is also kept here, in `notices.json`, offered in
the initial state and by `GET /api/system/notices`, announced by
`system/notices_changed`, and dropped only once acknowledged
(`DELETE /api/system/notices/{id}`): a set-aside `settings.json` brings the setup
wizard back, and the banner is still there once the wizard is done.

`report_set_aside` is the only producer: it hands every record to the keeper
`claim_set_asides` installed — this service, once initialized — and the ones
made before that (the boot's sync read of settings.json, STEP 3b) are claimed at
`initialize`. Only set-asides: a notice of another kind is a second model, added
by the lot that needs it.
"""
import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, List, Optional

from pydantic import ValidationError

from backend.config.constants import NOTICES_FILE
from backend.core.models.notices import SetAsideNotice
from backend.core.models.ws_events import SystemNoticesChanged
from backend.shared.background import BackgroundTaskSet
from backend.shared.persistence import (
    SetAside,
    claim_set_asides,
    load_versioned_json,
    release_set_asides,
    save_versioned_json,
    set_aside,
)

if TYPE_CHECKING:
    from backend.core.state import AudioStateMachine

logger = logging.getLogger(__name__)

REQUIRED_TOP_LEVEL_KEYS = ("set_aside",)

# A card that sets a file aside at every boot cannot, since a set-aside is a
# rename, but a bound keeps the file small whatever happens: the oldest go first.
MAX_NOTICES = 50


class SetAsideNotices:
    """The set-asides the owner has not acknowledged yet, persisted."""

    SCHEMA_VERSION = 1

    def __init__(self, state_machine: "AudioStateMachine") -> None:
        self.notices_file = NOTICES_FILE
        self._state_machine = state_machine
        self._notices: List[SetAsideNotice] = []
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._bg = BackgroundTaskSet(logger, "notices")

    async def initialize(self) -> bool:
        """Read what was kept, then take over every set-aside, earlier ones included."""
        self._loop = asyncio.get_running_loop()
        data = await load_versioned_json(
            self.notices_file, self.SCHEMA_VERSION, required=REQUIRED_TOP_LEVEL_KEYS
        )
        try:
            self._notices = [SetAsideNotice(**entry) for entry in data.get("set_aside", [])]
        except (TypeError, ValidationError) as e:
            self._notices = []
            await set_aside(self.notices_file, f"a notice it cannot rebuild: {e!r}")
        # Claimed after the load, so this file's own set-aside, if any, is
        # among the records it is handed.
        earlier = claim_set_asides(self._receive)
        for record in earlier:
            self._add(record)
        if earlier:
            await self._persist_and_announce()
        return True

    async def cleanup(self) -> None:
        release_set_asides(self._receive)
        await self._bg.cancel_all()

    def notices(self) -> List[SetAsideNotice]:
        """Every notice not yet acknowledged, oldest first."""
        return list(self._notices)

    async def acknowledge(self, notice_id: str) -> bool:
        """Drop one notice, once the owner has read it. False if there is none by that id.

        Written before it is dropped from memory: a write that fails (a full or
        read-only card, the case this file exists for) raises with the notice
        still listed here as on disk, so the page that reads it back shows it
        again instead of losing it until the next boot.
        """
        async with self._lock:
            kept = [n for n in self._notices if n.id != notice_id]
            if len(kept) == len(self._notices):
                return False
            await self._save(kept)
            # Read again past the write: a set-aside reported meanwhile was
            # appended, and its own persist runs once this lock is released.
            self._notices = [n for n in self._notices if n.id != notice_id]
        await self._announce()
        return True

    def _receive(self, record: SetAside) -> None:
        """The keeper `report_set_aside` calls, on whichever thread reported."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._accept(record)
        elif self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._accept, record)

    def _accept(self, record: SetAside) -> None:
        self._add(record)
        self._bg.spawn(self._persist_and_announce(), label="set_aside")

    def _add(self, record: SetAside) -> None:
        self._notices.append(SetAsideNotice(
            id=uuid.uuid4().hex,
            file=str(record.file),
            kept_as=record.kept.name,
            reason=record.reason,
            at=record.at,
        ))
        del self._notices[:-MAX_NOTICES]

    async def _persist_and_announce(self) -> None:
        async with self._lock:
            await self._save(self._notices)
        await self._announce()

    async def _save(self, notices: List[SetAsideNotice]) -> None:
        await save_versioned_json(
            self.notices_file,
            {"set_aside": [n.model_dump() for n in notices]},
            self.SCHEMA_VERSION,
        )

    async def _announce(self) -> None:
        await self._state_machine.broadcast(SystemNoticesChanged(notices=self.notices()))
