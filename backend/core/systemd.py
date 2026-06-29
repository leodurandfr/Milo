"""
Manager for systemd services.
"""
import asyncio
import logging
from typing import Dict, Any

from backend.shared.decorators import handle_errors

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

    @handle_errors(default=False)
    async def is_active(self, service: str) -> bool:
        """Checks if a service is active."""
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", service,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 5.0)
        except asyncio.TimeoutError:
            proc.kill()
            self.logger.error(f"Timeout checking is_active for {service}")
            return False
        return stdout.decode().strip() == "active"

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
        """Controls a systemd service."""
        try:
            self.logger.info(f"{action.capitalize()} service {service}")

            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", action, service,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )

            _, stderr = await asyncio.wait_for(proc.communicate(), 10.0)

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
            expected_active = action != "stop"
            for attempt in range(6):
                if await self.is_active(service) == expected_active:
                    return True
                if attempt < 5:
                    await asyncio.sleep(0.5)

            # More explicit error message if expected state is not reached
            actual_state = "active" if await self.is_active(service) else "inactive"
            expected_state = "active" if expected_active else "inactive"
            self.logger.error(f"Service {service} is {actual_state} but expected {expected_state} after {action}")
            return False

        except asyncio.TimeoutError:
            proc.kill()
            self.logger.error(f"Timeout ({action} {service} took more than 10 seconds)")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during {action} {service}: {e}")
            return False
