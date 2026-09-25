# backend/core/push/token_registry.py
"""Persistent registry of the APNs device tokens Milō pushes to.

Three tokens per iOS install, one file, one lock. The registry stores and
purges; it never talks to APNs — the emitter does, and hands back what APNs
said about a token (see ``purge``).

Keyed by the token string, because that is the only identifier the two sides
agree on: APNs names a dead token by its string and by nothing else, so a
registry keyed on anything richer could not act on a 410 without a reverse
index. ``device_id`` is carried on the record instead, which is all the
replacement rules below need.
"""
import asyncio
import logging
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from backend.config.constants import PUSH_TOKENS_FILE
from backend.core.push.models import ApnsEnvironment, PushToken, PushTokenKind
from backend.shared.persistence import load_versioned_json, save_versioned_json


class PushTokenRegistry:
    """Stores the widget, push-to-start and session tokens of every iOS install."""

    SCHEMA_VERSION: int = 1

    def __init__(self):
        self.tokens_file: Path = PUSH_TOKENS_FILE
        self.logger = logging.getLogger("push.tokens")
        self._file_lock = asyncio.Lock()
        self._tokens: Dict[str, PushToken] = {}
        self._lost_sessions: set[str] = set()

    async def initialize(self) -> None:
        """Load the file so a schema mismatch surfaces at boot, not at first push.

        Raises SchemaVersionMismatch on version drift; the handler in
        dependencies.py::init_async logs the banner and SystemExit(1)s. A
        missing file is a fresh install and stays unwritten until the first
        registration — an empty registry is a valid state, so seeding one on
        disk would only create a file to back up.
        """
        async with self._file_lock:
            self._tokens = await self._load_locked()
        self.logger.info(f"Push token registry loaded: {len(self._tokens)} token(s)")

    # =========================================================================
    # READ — synchronous, served from memory
    # =========================================================================

    def tokens_for(self, kind: PushTokenKind) -> List[PushToken]:
        """Every registered token of one kind.

        Synchronous on purpose: the emitter reads this on the push path, which
        the coalescer already rate-limits, and an await there would let a
        registration interleave between picking the targets and sending.
        """
        return [t for t in self._tokens.values() if t.kind == kind]

    def tokens_for_session(self, session_id: str) -> List[PushToken]:
        """Every token carrying `update` and `end` for one Now Playing session,
        newest first.

        One per DEVICE, not one per session: a `start` goes to every
        push-to-start token, so every phone and iPad with the app opens the
        same session id and reports its own token for it. Measured 2026-09-25:
        session d23da1d7 registered by the iPhone at 11:03:17, then by the iPad
        at 11:17:46 — which evicted the iPhone's, and from then on every
        `update` went to the iPad while the phone's card sat frozen.
        """
        return sorted(
            (
                t for t in self._tokens.values()
                if t.kind == PushTokenKind.SESSION and t.session_id == session_id
            ),
            key=lambda t: t.registered_at,
            reverse=True,
        )

    def token_for_session(self, session_id: str) -> Optional[PushToken]:
        """The most recently registered of `tokens_for_session`."""
        return next(iter(self.tokens_for_session(session_id)), None)

    def was_lost_to_reboot(self, session_id: str) -> bool:
        """Did this session die with a restart of the phone that held it?

        The emitter needs to tell two silences apart, and they want opposite
        things. A session whose token has not arrived *yet* must be kept — the
        registration comes from an app extension whose only route here is an
        mDNS lookup that fails now and then, and giving up on it opened a rival
        session that the phone showed and nobody fed. A session whose token was
        taken away because the device rebooted must be let go, or this side
        holds an id that addresses nothing for as long as playback lasts.

        Only a reboot lands here, and only because the device said so.
        """
        return session_id in self._lost_sessions

    def newest_session_token(self) -> Optional[PushToken]:
        """The session token registered most recently, of any session.

        This is how Milō learns about a session it did not open. A session can
        be started from either end — by a push to the push-to-start token, or
        by the app itself while it is running — and only the device knows which
        one the system actually kept. It says so by registering that session's
        token, and the most recent registration is the most recent thing the
        device has said on the subject.

        Older entries are not evidence of anything: ``register`` supersedes by
        ``session_id``, so a session that ended leaves its token behind until a
        push to it returns a 410 and ``purge`` takes it. Reading the newest one
        is what keeps those from being mistaken for the live session.

        Only sessions belonging to a device that still holds a push-to-start
        token count. ``device_id`` is minted by the app and kept in its shared
        container, so it does not always outlive the install: measured
        2026-09-19, one phone went from ``DF45773A`` to ``7823EF03`` and left
        eleven session tokens behind under the name it no longer answered to.
        The reboot rule could not reach them — it matches on ``device_id`` — so
        the newest orphan was adopted and every update went to a session that
        had not existed for half an hour. The push-to-start token is the one
        thing a running install always re-registers, which makes it the record
        of which names are still answered to.
        """
        live_devices = {
            t.device_id for t in self._tokens.values()
            if t.kind == PushTokenKind.PUSH_TO_START
        }
        sessions = [
            t for t in self._tokens.values()
            if t.kind == PushTokenKind.SESSION and t.device_id in live_devices
        ]
        return max(sessions, key=lambda t: t.registered_at, default=None)

    # =========================================================================
    # WRITE
    # =========================================================================

    async def register(
        self,
        token: str,
        kind: PushTokenKind,
        environment: ApnsEnvironment,
        device_id: str,
        session_id: Optional[str] = None,
        boot_time: Optional[float] = None,
    ) -> None:
        """Record a token, replacing the one it supersedes.

        Replacement is what keeps the registry from filling with tokens that are
        dead but not yet known to be: iOS reissues on reinstall, and the old one
        is only revealed by a 410 on a push nobody may send for days.

        What a new token supersedes depends on its kind:

        * ``WIDGET`` / ``PUSH_TO_START`` — the same kind on the same
          ``device_id``. One install holds one of each.
        * ``SESSION`` — the same ``session_id`` on the same ``device_id``. A
          session has one token per device, and a resumed session reuses its
          id; another device holding the same session keeps its own — see
          ``tokens_for_session``.

        The token string itself is also a key, so re-registering an existing
        string under another device moves the record rather than duplicating it.
        """
        now = time.time()
        record = PushToken(
            token=token,
            kind=kind,
            environment=environment,
            device_id=device_id,
            session_id=session_id,
            registered_at=now,
        )

        def apply(tokens: Dict[str, PushToken]) -> bool:
            for superseded in self._superseded_by(tokens, record):
                del tokens[superseded]
            for gone in self._lost_to_reboot(tokens, record, boot_time):
                self.logger.info("Device rebooted — dropping a session token it lost")
                self._lost_sessions.add(tokens[gone].session_id)
                del tokens[gone]
            tokens[token] = record
            return True

        await self._mutate(apply)
        self.logger.info(
            f"Registered {kind.value} token ({environment.value}) for device {device_id}"
        )

    async def unregister(self, token: str) -> bool:
        """Drop a token the app asked to remove. False when it was not held."""
        def apply(tokens: Dict[str, PushToken]) -> bool:
            return tokens.pop(token, None) is not None

        removed = await self._mutate(apply)
        if removed:
            self.logger.info("Unregistered token on request")
        return removed

    async def drop_sessions_absent_from(
        self, device_id: str, live_session_ids: List[str]
    ) -> int:
        """Forget this device's session tokens for sessions it no longer holds.

        A session token outlives its session, and nothing on this side can tell
        the difference. APNs accepts every push to it and answers 200 — the
        phone throws the payload away in silence, because there is no session
        behind it any more. Measured 2026-09-19: the phone logged
        `Could not find the specified now playing client` while Milō, restarted
        from a clean slate, adopted that very id — `session 2eb3b71b adopted
        (was None)` — and went on feeding it. Nothing ever started a new one.

        ``was_lost_to_reboot`` only covers a phone that restarted. Everything
        else that ends a session leaves no mark: a reinstall does (the bundle
        container changes every time), and so does the system reclaiming one.
        In development a reinstall is the common case, not the corner one.

        The app is the only party that knows, and it does know: it enumerates
        the live sessions every couple of seconds while it runs. This is that
        report. Silence says nothing — a backgrounded app reports nothing at
        all — so only an explicit list that OMITS a session retires it.

        Scoped to the reporting device: a phone knows its own sessions and
        nobody else's, and an empty list from one must not touch another's.
        """
        live = set(live_session_ids)

        def apply(tokens: Dict[str, PushToken]) -> bool:
            doomed = [
                token for token, held in tokens.items()
                if held.kind == PushTokenKind.SESSION
                and held.device_id == device_id
                and held.session_id not in live
            ]
            for token in doomed:
                tokens.pop(token)
            return bool(doomed)

        before = len(self._tokens)
        if not await self._mutate(apply):
            return 0
        dropped = before - len(self._tokens)
        self.logger.info(
            f"Device {device_id} reports {len(live)} live session(s); "
            f"dropped {dropped} stale session token(s)"
        )
        return dropped

    async def purge(self, token: str, *, invalidated_at: Optional[float] = None) -> bool:
        """Drop a token APNs refused. False when it was kept or not held.

        `invalidated_at` is the unix timestamp a 410 Unregistered carries, in
        SECONDS — APNs sends it in milliseconds and the emitter divides. It says
        when the token stopped being valid, and Apple's rule is to delete only
        if the token has not been registered again since.

        That guard is not decoration. Without it this sequence deletes a live
        token: the app is reinstalled and registers its new token while a push
        to the old one is still in flight; the 410 comes back after the new
        registration and takes the new token with it. The device then receives
        nothing until its next launch, with no trace anywhere — the push path
        reports success, and the registry is simply empty.

        A ``BadDeviceToken`` (400) carries no timestamp: the emitter calls this
        without one, and the token goes unconditionally.
        """
        def apply(tokens: Dict[str, PushToken]) -> bool:
            held = tokens.get(token)
            if held is None:
                return False
            if invalidated_at is not None and held.registered_at > invalidated_at:
                self.logger.info(
                    "APNs reported a token invalid, but it was registered again "
                    "since — keeping it"
                )
                return False
            del tokens[token]
            return True

        purged = await self._mutate(apply)
        if purged:
            self.logger.info("Purged a token APNs no longer accepts")
        return purged

    async def mark_pushed(self, tokens: List[str], at: Optional[float] = None) -> None:
        """Stamp `last_push_at`, so an operator reading the file can see silence.

        Nothing reads this back — it exists because the only way to tell a token
        that is idle from one that is broken is when it was last used, and the
        file is the only surface this registry has.
        """
        stamp = at if at is not None else time.time()

        def apply(held: Dict[str, PushToken]) -> bool:
            changed = False
            for token in tokens:
                if token in held:
                    held[token].last_push_at = stamp
                    changed = True
            return changed

        await self._mutate(apply)

    # =========================================================================
    # PRIVATE
    # =========================================================================

    @staticmethod
    def _lost_to_reboot(
        tokens: Dict[str, PushToken],
        record: PushToken,
        boot_time: Optional[float],
    ) -> List[str]:
        """Session tokens this device cannot hold any more, because it rebooted.

        A restart destroys every Now Playing session on the phone and announces
        it to nobody: the emitter keeps pushing `update` to a session that no
        longer exists, APNs accepts each one with a 200, and the Lock Screen
        stays empty for good. Measured 2026-09-19 — phone restarted with music
        playing, and not one `start` sent afterwards.

        The device cannot repair itself — `RemoteMediaSession` is unavailable to
        app extensions, so only the foreground app can open or adopt one — but
        it can say when it booted, and it already POSTs here. A boot time later
        than the one a session token was registered under means that token
        belongs to a session that did not survive.

        Silence stays silent: a caller that sends no `boot_time` invalidates
        nothing, which is what keeps an older build of the app working.
        """
        if boot_time is None:
            return []
        return [
            key for key, held in tokens.items()
            if held.kind == PushTokenKind.SESSION
            and held.device_id == record.device_id
            and held.registered_at < boot_time
        ]

    @staticmethod
    def _superseded_by(tokens: Dict[str, PushToken], record: PushToken) -> List[str]:
        """Token strings `record` replaces — see ``register`` for the rules."""
        if record.kind == PushTokenKind.SESSION:
            return [
                key for key, held in tokens.items()
                if held.kind == PushTokenKind.SESSION
                and held.session_id == record.session_id
                and held.device_id == record.device_id
            ]
        return [
            key for key, held in tokens.items()
            if held.kind == record.kind and held.device_id == record.device_id
        ]

    async def _mutate(self, apply: Callable[[Dict[str, PushToken]], bool]) -> bool:
        """Read → mutate → write under a single hold of ``_file_lock``.

        ``apply`` edits the dict in place and returns whether anything changed;
        the file is rewritten only when it did. It is deliberately synchronous:
        an await inside it would reopen the window this closes.

        Taking the lock once is the whole point, and the interleave it closes is
        this registry's own: a purge triggered by a failed push runs in the
        background while a registration arrives over HTTP. Loading and saving
        under two separate holds lets them read the same state and lets the
        second write drop the first one's edit — which here means either a dead
        token resurrected or a fresh one deleted.
        """
        async with self._file_lock:
            tokens = await self._load_locked()
            changed = apply(tokens)
            self._tokens = tokens
            if changed:
                await save_versioned_json(
                    self.tokens_file,
                    {"tokens": {k: v.to_dict() for k, v in tokens.items()}},
                    self.SCHEMA_VERSION,
                )
        return changed

    async def _load_locked(self) -> Dict[str, PushToken]:
        """Read and decode the file. The caller must already hold ``_file_lock``."""
        data = await load_versioned_json(self.tokens_file, self.SCHEMA_VERSION)
        if not data:
            return {}
        return {
            token: PushToken.from_dict(token, record)
            for token, record in data["tokens"].items()
        }
