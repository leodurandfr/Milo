# backend/core/system/presence.py
"""Proof of presence: a gesture on the unit itself before a route that reaches root,
or before a WiFi save over the recovery access point.

Nothing on this API authenticates — the LAN is the boundary — and on the server
the backend is root by construction (CLAUDE.md, invariant 5). So the routes
that hand root out are answered only once someone standing at the unit has made
a gesture no remote party can make; reading and settings stay open to the LAN.
The routes are listed, and held, by `tests/architecture/test_presence_guard.py`.
No code is ever put on a screen: the kiosk is a browser like any other, and
what it shows can be read from wherever its page is.

One gesture per unit, the first this unit can offer:

1. **a turn or a press of the rotary encoder**, when one is configured and
   running: `hardware/rotary.py` hands its input here first, and while a
   challenge waits on it, the input is the gesture and moves no volume;
2. otherwise **the Raspberry Pi 5's power button**. logind powers the unit off
   on that key, so for the challenge only, `milo-presence-inhibit.service`
   (root, started and stopped through the bare `systemctl` grant) holds a
   `handle-power-key` block inhibitor — its start fails unless logind lists
   the lock — and the key is read from the board's own input
   (`hardware/power_key.py`). An inhibitor that does not start, or a button
   that cannot be read, falls back to the third gesture — never to a press
   that would power the unit off;
3. otherwise **unplugging the unit and plugging it back in**. The challenge is
   recorded, with the boot it was opened in, by a root helper
   (`milo-presence-record`) in a directory only root writes, and
   `milo-backend.service`'s root `ExecStopPost=` clears the record at every
   stop of this process — asked for, crashed, killed or timed out alike. A
   start that finds a record from another boot has come up without a stop
   before it: a power cut. A `milo` process can neither write that record —
   every other unit running as `milo` has NoNewPrivileges, so the helper's
   sudo grant is not theirs — nor skip the hook, which systemd runs as root
   whatever ended the process.

A request from 127.0.0.1 is never a proof: every process on the unit has that
address, and nginx gives it to everything it proxies. Nothing here reads the
client's address at all. What a client may say is which gestures it can wait
through (`PRESENCE_GESTURES_HEADER`): the unit's own screen goes down with the
unit, so it cannot see a power cut to the end, and a challenge it opened for
one would only quiet the unit (below) for the phone it then has to ask from.
Such a request opens nothing and is answered at once. The header can only
refuse its sender, so nothing needs to tell who sent it.

A challenge belongs to the request that opened it. Its 428 carries a token, the
only thing that spends the proof, so the gesture made for one device proves
nothing for another that asks for the same action meanwhile. One challenge at a
time: a second asker is refused (409) rather than replacing the first, since a
replacement would hand the owner's gesture to whoever asked last. A challenge
waits `CHALLENGE_S` for its gesture, then `PROOF_S` for its token — one a power
cut proved, `PROOF_S` past the longest the unit takes to be reachable again: a
WiFi-only unit whose home network is gone puts its recovery access point back
up to `RECOVERY_AP_RAISED_WITHIN_S` after it starts, and the phone that asked
over it comes back only then.

The three gestures are things the owner does anyway: turning the knob is the
volume, the power button and the plug are how a unit is silenced. So a challenge
a stranger opened is confirmed by the owner's next turn, press or unplugging,
without a word to them — and the stranger can bring that moment on, by starting
a station or raising the volume over the LAN, which nothing guards. What bounds
it is narrower than that:

- a challenge that ends unspent — expired or cancelled — leaves the unit
  asking for no other for `COOLDOWN_S`, so one asker keeps a gesture armed at
  most `CHALLENGE_S` out of every `CHALLENGE_S + COOLDOWN_S`, not all the time.
  The quiet lives in this process: a restart, which the LAN can ask for, ends it;
- every screen hears of every open challenge (`system/presence_changed`), and
  the unit's own screen draws one whoever asked. A unit with no screen says
  nothing.

That residue is the price of a gesture the owner already makes every day;
narrowing it further (a long press only, no challenge while the unit plays) is
a choice about the gesture itself, not made here.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import math
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from backend.config.constants import PRESENCE_RECORD_CMD, PRESENCE_RECORD_FILE
from backend.core.models.presence import Gesture, PresenceAction, PresenceChallenge
from backend.core.models.ws_events import SystemPresenceChanged
from backend.core.network.service import RECOVERY_AP_RAISED_WITHIN_S
from backend.hardware import power_key
from backend.shared.background import BackgroundTaskSet

if TYPE_CHECKING:
    from backend.core.state import AudioStateMachine
    from backend.core.systemd import SystemdServiceManager
    from backend.hardware.service import HardwareService

logger = logging.getLogger(__name__)

# The request header a challenge's token travels back in. Named in the 428 that
# carries the token, so a client reads it there rather than restating it.
PRESENCE_HEADER = "X-Milo-Presence"

# The gestures a client can wait through, comma-separated `Gesture` values;
# absent, it waits through any. Sent by the unit's own screen, which a power
# cut takes down with the unit.
PRESENCE_GESTURES_HEADER = "X-Milo-Presence-Gestures"

INHIBIT_UNIT = "milo-presence-inhibit.service"

# Changes at every boot, never within one: what tells a power cut, which
# reboots, from a crash, which does not.
BOOT_ID_FILE = Path("/proc/sys/kernel/random/boot_id")


class PresenceRequired(Exception):
    """The request waits for a gesture on the unit (HTTP 428 in api/).

    `detail` names the challenge, its gesture, the token that spends it once
    the gesture is made, and the header that token goes back in.
    """

    def __init__(self, challenge: PresenceChallenge, token: str):
        super().__init__("Confirm this on the unit itself")
        self.detail = {
            "message": "Confirm this on the unit itself",
            "challenge": challenge.model_dump(mode="json"),
            "token": token,
            "header": PRESENCE_HEADER,
        }


class PresenceElsewhere(Exception):
    """The gesture this unit would wait for is one the asking client said it
    cannot wait through (HTTP 428 with no token, in api/). Nothing was opened:
    the client says so, and the request is asked again from another device.
    """

    def __init__(self, action: PresenceAction, gesture: Gesture):
        message = "Confirm this from another device: this one cannot wait for the gesture the unit asks for"
        super().__init__(message)
        self.detail = {"message": message, "action": action.value, "gesture": gesture.value}


class PresenceBusy(Exception):
    """No challenge can open now (HTTP 409 in api/): another is open, one at a
    time and never replaced, or one ended unspent too recently."""


class PresenceUnavailable(Exception):
    """No challenge can be opened that the unit could prove (HTTP 503 in api/)."""


@dataclass(eq=False)
class _Challenge:
    id: str
    action: PresenceAction
    gesture: Gesture
    token_hash: str
    deadline: float
    proven: bool = False
    # On the card through the helper, waiting for a power cut: cleared when it ends.
    recorded: bool = False
    tasks: list = field(default_factory=list)

    def answers_to(self, token: Optional[str]) -> bool:
        return token is not None and hmac.compare_digest(_hash(token), self.token_hash)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PresenceService:
    """Opens, proves and spends the challenges of the routes that reach root."""

    CHALLENGE_S = 120.0
    PROOF_S = 120.0
    # What a proof made by a power cut waits for before its `PROOF_S`: the unit
    # reachable again, over its recovery access point at worst.
    UNREACHABLE_AFTER_START_S = RECOVERY_AP_RAISED_WITHIN_S
    # After a challenge that ended unspent: long enough that a stranger who
    # opens one again the moment it ends keeps the knob armed a fraction of
    # the time, not all of it.
    COOLDOWN_S = 300.0
    HELPER_TIMEOUT_S = 10.0

    def __init__(
        self,
        hardware_service: "HardwareService",
        systemd_manager: "SystemdServiceManager",
        state_machine: "AudioStateMachine",
        *,
        record_file: Path = PRESENCE_RECORD_FILE,
        boot_id_file: Path = BOOT_ID_FILE,
    ):
        self._hardware = hardware_service
        self._systemd = systemd_manager
        self._state_machine = state_machine
        self._record_file = Path(record_file)
        self._boot_id_file = Path(boot_id_file)
        self._rotary = None
        self._board_has_power_key = False
        self._challenge: Optional[_Challenge] = None
        self._quiet_until = 0.0
        self._lock = asyncio.Lock()
        self._bg = BackgroundTaskSet(logger, "presence")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def initialize(self) -> bool:
        """Read what the previous process left: a challenge a power cut proved,
        and an inhibitor nothing holds any more."""
        self._board_has_power_key = await asyncio.to_thread(self._hardware.power_button_supported)
        found, record = await asyncio.to_thread(self._read_record)
        if found:
            # Consumed whatever it says: the challenge, if proven, lives on in
            # this process, and a record kept would be proven again by every
            # later power cut.
            await self._run_helper("clear")
            if record is not None:
                await self._restore(record)
        await self._release_stale_inhibitor()
        return True

    async def cleanup(self) -> None:
        """Cancel the challenge's tasks. The inhibitor is the next start's to
        stop (`BindsTo=` restarts it along with this unit), and a recorded
        challenge is the unit's `ExecStopPost=` to clear."""
        await self._bg.cancel_all()

    def attach_rotary(self, controller) -> None:
        """The rotary encoder, once it runs: from then on it is this unit's gesture."""
        self._rotary = controller

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def view(self) -> Optional[PresenceChallenge]:
        """The open challenge as every screen may see it (no token), or None."""
        challenge = self._challenge
        if challenge is None:
            return None
        remaining = challenge.deadline - asyncio.get_running_loop().time()
        return PresenceChallenge(
            id=challenge.id,
            action=challenge.action,
            gesture=challenge.gesture,
            status="confirmed" if challenge.proven else "pending",
            remaining_s=max(0, math.ceil(remaining)),
            cooldown_s=math.ceil(self.COOLDOWN_S),
        )

    def gesture(self) -> Gesture:
        """The gesture a challenge opened now would wait for."""
        if self._rotary is not None and self._rotary.running:
            return Gesture.ROTARY
        if self._board_has_power_key:
            return Gesture.POWER_BUTTON
        return Gesture.POWER_CYCLE

    # ------------------------------------------------------------------ #
    # The guard
    # ------------------------------------------------------------------ #

    async def require(
        self,
        action: PresenceAction,
        token: Optional[str],
        follows: Optional[frozenset[Gesture]] = None,
    ) -> None:
        """Return once `token` spends a proof of presence for `action`.

        Otherwise raises `PresenceRequired` with a new challenge, or with the
        one this token opened while its gesture is still awaited;
        `PresenceElsewhere`, opening nothing, when the gesture a new challenge
        would wait for is not among `follows` (None: the client follows any);
        `PresenceBusy` while another challenge is open or the unit is quiet
        after one that ended unspent; `PresenceUnavailable` when the challenge
        a power cut would prove cannot be recorded.
        """
        async with self._lock:
            challenge = self._challenge
            if challenge is not None and challenge.answers_to(token) and challenge.action is action:
                if not challenge.proven:
                    raise PresenceRequired(self.view(), token)
                await self._end(challenge, spent=True)
                logger.info(f"Presence proven ({challenge.gesture.value}) for {action.value}")
                opened = None
            elif challenge is not None:
                remaining = self.view().remaining_s
                logger.warning(
                    f"Refused {action.value}: a challenge for {challenge.action.value} "
                    f"is already open ({remaining} s left)"
                )
                raise PresenceBusy(
                    "Another request is waiting for a gesture on the unit "
                    f"({remaining} s left); try again once it has ended"
                )
            else:
                # Before the quiet: asked again after it, this client would be
                # sent elsewhere anyway.
                self._refuse_unfollowed(action, self.gesture(), follows)
                quiet = math.ceil(self._quiet_until - asyncio.get_running_loop().time())
                if quiet > 0:
                    logger.warning(
                        f"Refused {action.value}: the last challenge ended unanswered, "
                        f"none opens for {quiet} s"
                    )
                    raise PresenceBusy(
                        "The last confirmation asked on the unit was not given; "
                        f"the unit asks for another in {quiet} s"
                    )
                token = await self._open(action, follows)
                opened = self.view()
        await self._publish()
        if opened is not None:
            raise PresenceRequired(opened, token)

    async def cancel(self, token: Optional[str]) -> bool:
        """End the open challenge on behalf of the request that opened it.
        False when none is open, or it is not this token's."""
        async with self._lock:
            challenge = self._challenge
            if challenge is None or not challenge.answers_to(token):
                return False
            await self._end(challenge, spent=False)
            logger.info(f"Presence challenge for {challenge.action.value} cancelled")
        await self._publish()
        return True

    def on_gesture(self, gesture: Gesture) -> bool:
        """A gesture made on the unit. True when it confirmed the challenge
        waiting on it: the input was the gesture, and its caller does nothing
        else with it. Once confirmed, the knob is the volume again — the
        gesture is made, and the request it was for goes on by itself."""
        challenge = self._challenge
        if challenge is None or challenge.gesture is not gesture or challenge.proven:
            return False
        challenge.proven = True
        challenge.deadline = asyncio.get_running_loop().time() + self.PROOF_S
        logger.info(f"Presence confirmed on the unit ({gesture.value}) for {challenge.action.value}")
        self._bg.spawn(self._publish(), label="publish")
        return True

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _refuse_unfollowed(
        self, action: PresenceAction, gesture: Gesture, follows: Optional[frozenset[Gesture]],
    ) -> None:
        if follows is not None and gesture not in follows:
            logger.info(
                f"No challenge opened for {action.value}: the asking client cannot wait "
                f"for {gesture.value}"
            )
            raise PresenceElsewhere(action, gesture)

    async def _open(self, action: PresenceAction, follows: Optional[frozenset[Gesture]] = None) -> str:
        """Open a challenge for `action` and answer its token. Under the lock."""
        gesture = self.gesture()
        device = None
        if gesture is Gesture.POWER_BUTTON:
            device = await power_key.open_power_key()
            if device is None:
                logger.error("The power button cannot be read; asking for a power cut instead")
                gesture = Gesture.POWER_CYCLE
            elif not await self._systemd.start(INHIBIT_UNIT):
                device.close()
                device = None
                logger.error(
                    f"{INHIBIT_UNIT} did not start, so the power button would power "
                    "the unit off; asking for a power cut instead"
                )
                gesture = Gesture.POWER_CYCLE
            if device is None:
                # Fell back to the plug, holding nothing: still nothing opened.
                self._refuse_unfollowed(action, gesture, follows)

        token = secrets.token_urlsafe(32)
        challenge = _Challenge(
            id=secrets.token_urlsafe(9),
            action=action,
            gesture=gesture,
            token_hash=_hash(token),
            deadline=asyncio.get_running_loop().time() + self.CHALLENGE_S,
        )
        if gesture is Gesture.POWER_CYCLE:
            await self._record_power_cycle(challenge)

        self._challenge = challenge
        if device is not None:
            challenge.tasks.append(
                self._bg.spawn(self._read_power_key(challenge, device), label="power_key")
            )
        challenge.tasks.append(self._bg.spawn(self._expire(challenge), label="expire"))
        logger.info(
            f"Presence challenge opened for {action.value}: waiting {self.CHALLENGE_S:.0f} s "
            f"for {gesture.value}"
        )
        return token

    async def _end(self, challenge: _Challenge, *, spent: bool) -> None:
        """Close a challenge and give back what it held. Under the lock.

        One that ends unspent quiets the unit for `COOLDOWN_S`: opened again
        at once, it would keep the knob armed for whoever keeps asking."""
        if self._challenge is challenge:
            self._challenge = None
        if not spent:
            self._quiet_until = asyncio.get_running_loop().time() + self.COOLDOWN_S
        current = asyncio.current_task()
        for task in challenge.tasks:
            if task is not None and task is not current:
                task.cancel()
        if challenge.gesture is Gesture.POWER_BUTTON:
            if not await self._systemd.stop(INHIBIT_UNIT):
                logger.error(f"{INHIBIT_UNIT} did not stop: the power button stays inert until it does")
        if challenge.recorded:
            # Left behind, it would be proven by a power cut that came after
            # its end; the unit's ExecStopPost clears it at the next stop.
            await self._run_helper("clear")

    async def _expire(self, challenge: _Challenge) -> None:
        loop = asyncio.get_running_loop()
        while (delay := challenge.deadline - loop.time()) > 0:
            await asyncio.sleep(delay)
        async with self._lock:
            if self._challenge is not challenge:
                return
            await self._end(challenge, spent=False)
        logger.info(
            f"Presence challenge for {challenge.action.value} ended "
            + ("unspent" if challenge.proven else "with no gesture")
        )
        await self._publish()

    async def _read_power_key(self, challenge: _Challenge, device) -> None:
        try:
            await power_key.wait_for_press(device)
            self.on_gesture(Gesture.POWER_BUTTON)
        except OSError as e:
            logger.error(f"The power button stopped answering during a challenge: {e}")
        finally:
            device.close()

    async def _publish(self) -> None:
        await self._state_machine.broadcast(SystemPresenceChanged(challenge=self.view()))

    async def _release_stale_inhibitor(self) -> None:
        """A backend restarted mid-challenge leaves the inhibitor up, or has it
        restarted with itself (`BindsTo=`): the power key would stay dead until
        its RuntimeMaxSec. Only a challenge holds it, and none is open at start."""
        state = await self._systemd.unit_state(INHIBIT_UNIT)
        if state is None or state[0] in ("inactive", "failed"):
            return
        logger.warning(f"{INHIBIT_UNIT} was left up by a challenge that is gone; stopping it")
        if not await self._systemd.stop(INHIBIT_UNIT):
            logger.error(f"{INHIBIT_UNIT} did not stop: the power button stays inert until it does")

    # --- the power cut ---------------------------------------------------- #

    async def _run_helper(self, verb: str, stdin: Optional[bytes] = None) -> bool:
        """`milo-presence-record <verb>` as root; False, logged, when it failed."""
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", PRESENCE_RECORD_CMD, verb,
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(stdin), self.HELPER_TIMEOUT_S)
        except (OSError, asyncio.TimeoutError) as e:
            if proc is not None and proc.returncode is None:
                proc.kill()
            logger.error(f"{PRESENCE_RECORD_CMD} {verb} did not run: {e!r}")
            return False
        if proc.returncode != 0:
            detail = stderr.decode(errors="replace").strip() if stderr else ""
            logger.error(f"{PRESENCE_RECORD_CMD} {verb} failed (exit {proc.returncode}): {detail}")
            return False
        return True

    async def _record_power_cycle(self, challenge: _Challenge) -> None:
        """On the card, through the root helper, before the 428 asks for the plug."""
        line = f"{challenge.id} {challenge.action.value} {challenge.token_hash}\n"
        if not await self._run_helper("arm", line.encode()):
            raise PresenceUnavailable("The challenge could not be recorded on the unit")
        challenge.recorded = True

    async def _boot_id(self) -> Optional[str]:
        try:
            return (await asyncio.to_thread(self._boot_id_file.read_text)).strip() or None
        except OSError as e:
            logger.warning(f"Boot id unreadable at {self._boot_id_file} ({e}): a power cut cannot be told from a restart")
            return None

    def _read_record(self) -> tuple[bool, Optional[dict]]:
        """Whether a challenge awaiting a power cut is recorded, and the record
        if it reads as one. A record that does not parse is no proof."""
        try:
            text = self._record_file.read_text()
        except FileNotFoundError:
            return False, None
        except OSError as e:
            logger.warning(f"Dropped an unreadable challenge record ({self._record_file}): {e}")
            return True, None
        try:
            record = json.loads(text)
        except ValueError as e:
            logger.warning(f"Dropped a challenge record that does not parse ({self._record_file}): {e}")
            return True, None
        if not isinstance(record, dict):
            logger.warning(f"Dropped a challenge record that is not an object ({self._record_file})")
            return True, None
        return True, record

    async def _restore(self, record: dict) -> None:
        """A recorded challenge found at start: proven if it comes from
        another boot, since every stop in between would have cleared it.

        Its token waits `PROOF_S` past the longest the unit takes to be
        reachable again, not past this start: over the recovery access point,
        the phone that asked comes back only once the AP is up again, some
        80 s after a start, and a proof timed from the start expired before
        that phone could spend it."""
        boot = await self._boot_id()
        recorded_boot = record.get("boot_id")
        if boot is None:
            logger.warning("A challenge awaiting a power cut was dropped: this boot has no readable id")
            return
        if not recorded_boot:
            logger.warning("A challenge awaiting a power cut was dropped: its record names no boot")
            return
        if recorded_boot == boot:
            logger.warning(
                "A challenge awaiting a power cut was dropped: it was opened in this boot, "
                "and the stop hook that clears it did not run"
            )
            return
        try:
            action = PresenceAction(record["action"])
            challenge = _Challenge(
                id=str(record["id"]),
                action=action,
                gesture=Gesture.POWER_CYCLE,
                token_hash=str(record["token_sha256"]),
                deadline=asyncio.get_running_loop().time() + self.UNREACHABLE_AFTER_START_S + self.PROOF_S,
                proven=True,
            )
        except (KeyError, ValueError) as e:
            logger.warning(f"Dropped a challenge record that does not name its challenge: {e}")
            return
        self._challenge = challenge
        challenge.tasks.append(self._bg.spawn(self._expire(challenge), label="expire"))
        logger.info(
            f"Presence proven by a power cut for {action.value}; waiting "
            f"{self.UNREACHABLE_AFTER_START_S + self.PROOF_S:.0f} s for its request"
        )
