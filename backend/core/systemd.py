"""
Manager for systemd services.
"""
import asyncio
import logging
from typing import Dict, Any

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
    
    async def is_active(self, service: str) -> bool:
        """Checks if a service is active."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", service,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await proc.communicate()
            return stdout.decode().strip() == "active"
        except Exception as e:
            self.logger.error(f"Error checking service {service}: {e}")
            return False
    
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

            # Use sudo for necessary permissions
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
            
            # Wait for service to reach desired state
            expected_active = action != "stop"
            for i in range(5):
                await asyncio.sleep(0.5)
                active = await self.is_active(service)
                if active == expected_active:
                    return True

            # More explicit error message if expected state is not reached
            actual_state = "active" if await self.is_active(service) else "inactive"
            expected_state = "active" if expected_active else "inactive"
            self.logger.error(f"Service {service} is {actual_state} but expected {expected_state} after {action}")
            return False
            
        except asyncio.TimeoutError:
            self.logger.error(f"Timeout ({action} {service} took more than 10 seconds)")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during {action} {service}: {e}")
            return False