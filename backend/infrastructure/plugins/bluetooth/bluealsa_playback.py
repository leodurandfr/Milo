"""
Audio playback manager via systemd - Milo version
"""
import asyncio
import logging

class BlueAlsaPlayback:
    """Manages audio playback with milo-bluealsa-aplay.service"""
    
    def __init__(self):
        self.logger = logging.getLogger("plugin.bluetooth.playback")
        self.service_name = "milo-bluealsa-aplay.service"
    
    async def start_playback(self, address: str) -> bool:
        """Starts audio playback via systemd service"""
        try:
            # Service is configured to auto-detect devices
            # We just check that it's active
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await proc.communicate()
            
            if stdout.decode().strip() != "active":
                # Start service if not active
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "systemctl", "start", self.service_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.wait()
                return proc.returncode == 0
            
            return True
        except Exception as e:
            self.logger.error(f"Playback startup error: {e}")
            return False
    
    async def stop_playback(self, address: str) -> bool:
        """Stops audio playback - optional since service handles automatically"""
        # We could stop the service, but it's configured to
        # automatically handle connections/disconnections
        return True
    
    async def stop_all_playback(self) -> None:
        """Stops playback service"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "stop", self.service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
        except Exception as e:
            self.logger.error(f"Service stop error: {e}")
    
    def is_playing(self, address: str) -> bool:
        """Checks if service is active"""
        try:
            import subprocess
            result = subprocess.run(
                ["systemctl", "is-active", self.service_name],
                capture_output=True,
                text=True
            )
            return result.stdout.strip() == "active"
        except Exception:
            return False