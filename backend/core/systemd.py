"""
Manager for systemd services.
"""
import asyncio
import logging
from typing import Dict, Any, Optional

from backend.shared.decorators import handle_errors

# Ceiling for one start/stop/restart, covering the systemctl child AND the
# settle probes that follow it. Bounding the child alone left the call's real
# worst case emergent — 10s for systemctl, plus six `is_active` probes bounded
# at 5s each, plus their sleeps: 47.5s that nothing declared and nothing above
# could be sized against.
#
# 12.5 is not a new tolerance, it is the old one written down: the 10s the
# child already had, plus the 2.5s the settle loop already spent. Declaring it
# at 10.0 would have been a quiet narrowing, taking the settle window out of
# the child's budget on exactly the loaded box where both are needed.
CONTROL_TIMEOUT = 12.5
SETTLE_PROBES = 6
SETTLE_INTERVAL = 0.5
IS_ACTIVE_TIMEOUT = 5.0


class SystemdServiceManager:
    """Generic manager for systemd services."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    async def start(self, service: str) -> bool:
        """Starts a systemd service."""
        return await self._control_service(service, "start")

    async def stop(self, service: str) -> bool:
        """Stops a systemd service."""
        return await self._control_service(service, "stop")

    async def restart(self, service: str) -> bool:
        """Restarts a systemd service."""
        return await self._control_service(service, "restart")

    async def set_enabled(self, service: str, enabled: bool) -> bool:
        """Enable or disable a unit at boot, and start/stop it now.

        Separate from `_control_service` rather than a fourth action of it:
        that helper decides success by polling `is_active`, and enablement is a
        different fact — a unit can be enabled and inactive (a oneshot that
        already ran) or active and disabled (started by hand). `--now` carries
        the runtime half, and `is-enabled` is what gets verified.
        """
        action = "enable" if enabled else "disable"
        self.logger.info(f"{action.capitalize()} service {service} (--now)")
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", action, "--now", service,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), 15.0)
            if proc.returncode != 0:
                error_msg = stderr.decode().strip() if stderr else "No error details"
                self.logger.error(f"Failed to {action} {service}: {error_msg}")
                return False
        except asyncio.TimeoutError:
            if proc:
                proc.kill()
            self.logger.error(f"Timeout ({action} {service} took more than 15 seconds)")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during {action} {service}: {e}")
            return False

        if await self.is_enabled(service) != enabled:
            self.logger.error(f"Service {service} did not become {action}d")
            return False
        return True

    async def is_enabled(self, service: str) -> bool:
        """Whether a unit is enabled at boot. No sudo — `is-enabled` is public."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-enabled", service,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), 5.0)
            # `systemctl is-enabled` exits non-zero for every state that is not
            # "enabled", so the word is the answer and the exit code is not.
            return stdout.decode().strip() == "enabled"
        except asyncio.TimeoutError:
            self.logger.error(f"Timeout checking whether {service} is enabled")
            return False
        except Exception as e:
            self.logger.error(f"Error checking whether {service} is enabled: {e}")
            return False

    async def restart_self(self, service: str) -> None:
        """Fire-and-forget restart of the unit hosting THIS process (milo-backend).

        Restarting our own unit makes systemd tear this process down mid-call, so
        the settling loop in _control_service can never observe the result. Use
        --no-block to enqueue the job and return immediately; systemd carries out
        the restart after this client exits. Failure to even enqueue (e.g. broken
        sudoers) is logged — fail-loud.
        """
        self.logger.info(f"Self-restart (fire-and-forget) of {service}")
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "restart", "--no-block", service,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), 10.0)
            if proc.returncode != 0:
                error_msg = stderr.decode().strip() if stderr else "No error details"
                self.logger.error(f"Failed to enqueue self-restart of {service}: {error_msg}")
        except asyncio.TimeoutError:
            if proc:
                proc.kill()
            self.logger.error(f"Timeout enqueuing self-restart of {service}")
        except Exception as e:
            self.logger.error(f"Self-restart of {service} failed: {e}")

    async def power(self, action: str, delay: float = 0.0) -> bool:
        """Reboot or power off the machine (action ∈ {"reboot", "poweroff"}).

        Centralizes the privileged power path (was inline `sudo reboot`/`poweroff`
        in api/system.py and api/setup.py). `delay` lets the caller flush its HTTP
        response before the box goes down. stderr + returncode are checked so a
        broken sudoers rule surfaces in errors.log instead of turning the Restart/
        Shutdown buttons into a silent no-op — fail-loud.
        """
        if action not in ("reboot", "poweroff"):
            raise ValueError(f"Invalid power action: {action!r}")
        if delay:
            await asyncio.sleep(delay)
        self.logger.info(f"System {action}")
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", action,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), 10.0)
            if proc.returncode != 0:
                error_msg = stderr.decode().strip() if stderr else "No error details"
                self.logger.error(f"System {action} failed (exit code {proc.returncode}): {error_msg}")
                return False
            return True
        except asyncio.TimeoutError:
            if proc:
                proc.kill()
            self.logger.error(f"System {action} timed out")
            return False
        except Exception as e:
            self.logger.error(f"System {action} failed: {e}")
            return False

    @handle_errors(default=None)
    async def probe_active(self, service: str) -> Optional[bool]:
        """True, False, or None when the probe could not tell.

        The three answers exist because two of them used to be one. `is_active`
        collapses None to False, which is right for a caller rendering a status
        and wrong for one deciding whether work finished: `_control_service`
        compares the answer against `expected_active`, and a `stop` expects
        False — so a probe that never came back read as "stopped successfully"
        for a unit that may still hold the ALSA device, in ~5s, well inside the
        call's own ceiling. Nothing in the return value distinguished "it is
        down" from "I could not look".

        `activating` and `deactivating` are False, not None: they are known
        states, and the unit is genuinely not where it was asked to be yet.
        """
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", service,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), IS_ACTIVE_TIMEOUT)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                proc.kill()
            self.logger.error(f"Timeout checking is_active for {service}")
            return None
        return stdout.decode().strip() == "active"

    async def main_pid(self, service: str) -> Optional[int]:
        """The unit's current main process, or None when there is none to name.

        What it is for: a source that tracks a *session* held by a daemon needs
        to know the daemon is still the one it opened that session with. The
        unit being `active` does not answer it — `Restart=` brings a unit back
        active within seconds of a crash, under a new process that knows
        nothing of the session, which is exactly how AirPlay came to hold a
        track nobody was playing (measured 2026-09-22: SIGKILL, restart 5 s
        later, and the source sat ACTIVE on a frozen playhead indefinitely).
        The pid is the identity `is_active` cannot carry.

        0 means the unit has no main process right now (stopped, or between a
        crash and its restart) and is reported as None, like an unreadable
        probe: both mean "there is no daemon here holding anything".
        """
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "show", service, "--property=MainPID", "--value",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), IS_ACTIVE_TIMEOUT)
        except asyncio.TimeoutError:
            if proc.returncode is None:
                proc.kill()
            self.logger.error(f"Timeout reading MainPID for {service}")
            return None

        raw = stdout.decode().strip()
        if not raw.isdigit():
            return None
        return int(raw) or None

    async def is_active(self, service: str) -> bool:
        """Whether the unit is known to be active — an unreadable probe is not.

        Kept as the bool every status reader wants (`api/system.py`, the
        diagnostic collectors, the lifespan sweep). A caller that acts on the
        answer rather than displaying it wants `probe_active` instead.
        """
        return await self.probe_active(service) is True

    async def get_status(self, service: str) -> Dict[str, Any]:
        """Retrieves detailed status of a service."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "show", service,
                "--property=ActiveState,SubState,ExecMainStatus",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                self.logger.error(f"Error retrieving status: {stderr.decode().strip()}")
                return {"error": "Unable to retrieve status"}

            lines = stdout.decode().strip().split('\n')
            status = {}

            for line in lines:
                if '=' in line:
                    key, value = line.split('=', 1)
                    status[key] = value

            return {
                "active": status.get("ActiveState") == "active",
                "running": status.get("SubState") == "running",
                "exit_code": int(status.get("ExecMainStatus", "0")),
                "state": status.get("ActiveState", "unknown"),
                "substate": status.get("SubState", "unknown")
            }
        except Exception as e:
            self.logger.error(f"Error retrieving status: {e}")
            return {"error": str(e)}

    async def _control_service(self, service: str, action: str) -> bool:
        """Controls a systemd service, under one deadline for the whole call.

        CONTROL_TIMEOUT covers the systemctl child and the settle probes alike:
        a probe is itself a `sudo`-free fork+exec, and on a box whose I/O is
        saturated those cost seconds each, which is how the ceiling used to
        reach 47.5s without any line saying so.

        The failure message no longer spends an `is_active` of its own to name
        the state reached. That probe bought nicer wording on the one path
        where the budget had already run out, and could add 5s to it.
        """
        proc = None
        try:
            async with asyncio.timeout(CONTROL_TIMEOUT):
                self.logger.info(f"{action.capitalize()} service {service}")

                proc = await asyncio.create_subprocess_exec(
                    "sudo", "systemctl", action, service,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE
                )

                _, stderr = await proc.communicate()

                if proc.returncode != 0:
                    error_msg = stderr.decode().strip() if stderr else "No error details"
                    self.logger.error(f"Failed to {action} {service} (exit code {proc.returncode}): {error_msg}")
                    return False

                # Wait for the service to reach the desired state. Check first, then
                # sleep — systemctl start/stop is synchronous, so the unit is usually
                # already settled on the first probe; sleeping first burned a fixed
                # 0.5s on every start AND stop (≥1s per source switch) for nothing.
                # 6 probes at t=0,0.5..2.5s: same 2.5s settle window as before, but a
                # service already settled on the first probe returns immediately.
                # probe_active, not is_active: None never equals a bool, so an
                # unreadable probe cannot satisfy the comparison. Under
                # is_active it did — for a `stop`, whose expectation is False.
                expected_active = action != "stop"
                observed = None
                for attempt in range(SETTLE_PROBES):
                    observed = await self.probe_active(service)
                    if observed == expected_active:
                        return True
                    if attempt < SETTLE_PROBES - 1:
                        await asyncio.sleep(SETTLE_INTERVAL)

                expected_state = "active" if expected_active else "inactive"
                if observed is None:
                    self.logger.error(
                        f"Could not confirm {service} reached {expected_state} "
                        f"after {action}"
                    )
                else:
                    self.logger.error(
                        f"Service {service} did not reach {expected_state} after {action}"
                    )
                return False

        except asyncio.TimeoutError:
            # Two states have no child to kill, and both are new here: the
            # deadline now opens before the spawn (proc is None) and closes
            # after the settle probes, by which point `communicate()` has
            # already reaped the child — `kill()` then raises
            # ProcessLookupError, and an exception raised inside an except arm
            # is not caught by the sibling arm below. It escaped as far as the
            # lifespan's `asyncio.gather` over the lingering-unit sweep, where
            # it aborted startup with an empty message.
            if proc and proc.returncode is None:
                proc.kill()
            self.logger.error(f"Timeout ({action} {service} exceeded {CONTROL_TIMEOUT}s)")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during {action} {service}: {e}")
            return False
