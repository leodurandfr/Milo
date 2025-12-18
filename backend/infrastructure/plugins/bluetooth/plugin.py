# backend/infrastructure/plugins/bluetooth/plugin.py
"""
Optimized Bluetooth plugin for Milo using bluealsa - Clean version without EventBus
"""
import asyncio
import subprocess
from typing import Dict, Any

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.bluetooth.agent import BluetoothAgent
from backend.infrastructure.plugins.bluetooth.bluealsa_monitor import BlueAlsaMonitor
from backend.infrastructure.plugins.bluetooth.bluealsa_playback import BlueAlsaPlayback

class BluetoothPlugin(UnifiedAudioPlugin):
    """Bluetooth plugin for Milo - clean version"""
    
    def __init__(self, config: Dict[str, Any], state_machine=None):
        super().__init__("bluetooth", state_machine)
        
        # Configuration
        self.config = config
        self.bluetooth_service = config.get("bluetooth_service", "bluetooth.service")
        self.bluealsa_service = config.get("service_name", "milo-bluealsa.service")
        self.bluealsa_aplay_service = "milo-bluealsa-aplay.service"
        self.stop_bluetooth = config.get("stop_bluetooth_on_exit", True)
        self.auto_agent = config.get("auto_agent", True)
        
        # ADD: Define service_name for base class
        self.service_name = self.bluealsa_service

        # State - Renamed to avoid conflict with base class
        self.connected_device = None
        self._auto_connecting = False
        self._current_device = "milo_bluetooth"

        # Components
        self.agent = BluetoothAgent()
        self.monitor = BlueAlsaMonitor()
        self.playback = BlueAlsaPlayback()

        # Multiple connections monitoring
        self._monitoring_task = None
        self._first_connected_device = None  # First connected device
    
    async def _do_initialize(self) -> bool:
        """Bluetooth plugin-specific initialization"""
        try:
            # Check dependencies
            if not await self._check_dependencies():
                return False
            
            # Configure monitor callbacks
            self.monitor.set_callbacks(self._on_device_connected, self._on_device_disconnected)
            
            return True
        except Exception as e:
            self.logger.error(f"Bluetooth initialization error: {e}")
            return False
    
    async def _check_dependencies(self) -> bool:
        """Checks that dependencies are available"""
        for cmd in ["bluealsa-cli", "bluealsa-aplay"]:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "which", cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await proc.wait()
                if proc.returncode != 0:
                    self.logger.error(f"Missing dependency: {cmd}")
                    return False
            except Exception as e:
                self.logger.error(f"Dependency check error {cmd}: {e}")
                return False
        return True
    
    async def _do_start(self) -> bool:
        """Bluetooth plugin-specific startup"""
        try:
            # 1. Start services
            for service in [self.bluetooth_service, self.bluealsa_service]:
                if not await self.control_service(service, "start"):
                    raise RuntimeError(f"Unable to start {service}")

            # 2. Start aplay service
            if not await self.control_service(self.bluealsa_aplay_service, "start"):
                raise RuntimeError(f"Unable to start {self.bluealsa_aplay_service}")

            # 3. Configure adapter
            if not await self._configure_adapter():
                self.logger.warning("Bluetooth adapter configuration error")

            # 4. Start multiple connections monitoring
            self._monitoring_task = asyncio.create_task(self._monitor_connections())

            # 5. Register agent if requested
            if self.auto_agent and not await self.agent.register():
                self.logger.warning("Bluetooth agent registration error")

            # 6. Start BlueALSA monitoring
            if not await self.monitor.start_monitoring():
                raise RuntimeError("BlueALSA monitoring start error")

            return True
        except Exception as e:
            self.logger.error(f"Bluetooth start error: {e}")
            await self._cleanup()
            return False
    
    async def _configure_adapter(self) -> bool:
        """Configures Bluetooth adapter"""
        try:
            commands = "\n".join([
                "power on",
                "system-alias Milō · Bluetooth",
                "discoverable-timeout 0",
                "discoverable on",
                "pairable on",
                "class 0x200404",
                "quit"
            ])
            
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            
            await proc.communicate(input=commands.encode())
            return proc.returncode == 0
        except Exception as e:
            self.logger.error(f"Adapter configuration error: {e}")
            return False
    
    async def restart(self) -> bool:
        """Restarts Bluetooth audio service and re-detects connected device"""
        try:
            self.logger.info("Restarting Bluetooth plugin")

            # Restart the audio playback service
            success = await self.control_service(self.bluealsa_aplay_service, "restart")
            if not success:
                await self.notify_state_change(PluginState.ERROR, {"error": "Restart failed"})
                return False

            await asyncio.sleep(0.5)

            # Re-detect connected device via bluetoothctl
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "devices", "Connected",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await proc.communicate()

            if proc.returncode == 0:
                for line in stdout.decode().splitlines():
                    if line.startswith("Device "):
                        parts = line.split(" ", 2)
                        if len(parts) >= 3:
                            address = parts[1]
                            name = parts[2] if len(parts) > 2 else address
                            self.connected_device = {"address": address, "name": name}
                            self._first_connected_device = address
                            await self.notify_state_change(PluginState.CONNECTED, {
                                "device_connected": True,
                                "device_name": name,
                                "device_address": address
                            })
                            self.logger.info(f"Bluetooth restart: device {name} still connected")
                            return True

            # No device connected
            self.connected_device = None
            self._first_connected_device = None
            await self.notify_state_change(PluginState.READY, {"device_connected": False})
            self.logger.info("Bluetooth restart: no device connected")
            return True

        except Exception as e:
            self.logger.error(f"Bluetooth restart error: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False
    
    async def stop(self) -> bool:
        """Stops Bluetooth plugin"""
        try:
            await self._cleanup()

            # Disable discoverability
            await self._run_bluetoothctl_command("discoverable off\npairable off\nquit")

            # Stop services if configured
            if self.stop_bluetooth:
                await self.control_service(self.bluealsa_aplay_service, "stop")
                for service in [self.bluealsa_service, self.bluetooth_service]:
                    await self.control_service(service, "stop")

            # Reset state
            self.connected_device = None
            await self.notify_state_change(PluginState.INACTIVE)

            return True
        except Exception as e:
            self.logger.error(f"Bluetooth plugin stop error: {e}")
            return False

    async def _run_bluetoothctl_command(self, commands) -> bool:
        """Executes bluetoothctl commands"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )

            await proc.communicate(input=commands.encode())
            return proc.returncode == 0
        except Exception as e:
            self.logger.error(f"bluetoothctl command error: {e}")
            return False

    async def _monitor_connections(self):
        """Monitors Bluetooth connections and disconnects additional devices"""
        self.logger.info("Bluetooth connections monitoring started")

        while True:
            try:
                await asyncio.sleep(0.5)  # Check every 500ms

                # List connected devices via bluetoothctl
                proc = await asyncio.create_subprocess_exec(
                    "bluetoothctl", "devices", "Connected",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL
                )
                stdout, _ = await proc.communicate()

                if proc.returncode != 0:
                    continue

                connected_devices = []
                for line in stdout.decode().splitlines():
                    if line.startswith("Device "):
                        parts = line.split(" ", 2)
                        if len(parts) >= 3:
                            address = parts[1]
                            connected_devices.append(address)

                # If no device connected, reset
                if len(connected_devices) == 0:
                    self._first_connected_device = None
                    continue

                # If first device, register it
                if self._first_connected_device is None and len(connected_devices) == 1:
                    self._first_connected_device = connected_devices[0]
                    self.logger.info(f"First Bluetooth device: {self._first_connected_device}")
                    continue

                # If more than one device connected, disconnect all except first
                if len(connected_devices) > 1:
                    for address in connected_devices:
                        if address != self._first_connected_device:
                            self.logger.warning(
                                f"REFUSED: {address} trying to connect while "
                                f"{self._first_connected_device} is already connected - disconnecting"
                            )
                            await self._disconnect_device(address)

            except asyncio.CancelledError:
                self.logger.info("Connection monitoring stopped")
                break
            except Exception as e:
                self.logger.error(f"Connection monitoring error: {e}")
                await asyncio.sleep(1)

    async def _disconnect_device(self, address: str) -> bool:
        """Disconnects a device by address"""
        try:
            self.logger.info(f"Disconnecting device {address}")
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "disconnect", address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                self.logger.error(f"Disconnection error {address}: {stderr.decode().strip()}")
                return False

            self.logger.info(f"Device {address} disconnected successfully")
            return True
        except Exception as e:
            self.logger.error(f"Device disconnection error {address}: {e}")
            return False
    
    async def _cleanup(self) -> None:
        """Cleans up plugin resources"""
        # Stop all audio playback
        await self.playback.stop_all_playback()

        # Stop monitoring
        await self.monitor.stop_monitoring()

        # Stop connections monitoring task
        if self._monitoring_task and not self._monitoring_task.done():
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass

        # Reset state
        self._first_connected_device = None

        # Unregister Bluetooth agent
        if self.auto_agent:
            await self.agent.unregister()
    
    async def _on_device_connected(self, address: str, name: str) -> None:
        """Callback called on device connection"""
        # The monitoring loop handles blocking multiple connections
        # Here we just register the device
        if not self.connected_device:
            self.connected_device = {"address": address, "name": name}
        
        # Notify state and start playback
        await self.notify_state_change(
            PluginState.CONNECTED, 
            {
                "device_connected": True,
                "device_name": name,
                "device_address": address
            }
        )
        
        # Start audio playback
        await self.playback.start_playback(address)
    
    async def _on_device_disconnected(self, address: str, name: str) -> None:
        """Callback called on device disconnection"""
        # Check if current device
        if not self.connected_device or self.connected_device.get("address") != address:
            return
            
        # Stop playback
        await self.playback.stop_playback(address)
        
        # Reset state
        self.connected_device = None
        
        # Notify state change
        await self.notify_state_change(PluginState.READY, {"device_connected": False})
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Processes plugin commands"""
        handlers = {
            "disconnect": self._handle_disconnect,
            "restart_audio": self._handle_restart_audio,
            "restart_bluealsa": self._handle_restart_bluealsa,
            "toggle_agent": self._handle_toggle_agent
        }
        
        if command in handlers:
            return await handlers[command](data)
        
        return self.format_response(False, error=f"Unknown command: {command}")
    
    async def _handle_disconnect(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handles disconnect command"""
        if not self.connected_device:
            return self.format_response(False, error="No device connected")
        
        address = self.connected_device.get("address")
        
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "disconnect", address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                return self.format_response(False, error=stderr.decode().strip())
                
            return self.format_response(True, message=f"Device disconnecting")
        except Exception as e:
            return self.format_response(False, error=str(e))
    
    async def _handle_restart_audio(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handles audio restart command"""
        if not self.connected_device:
            return self.format_response(False, error="No device connected")
        
        address = self.connected_device.get("address")
        await self.playback.stop_playback(address)
        success = await self.playback.start_playback(address)
        
        return self.format_response(
            success, 
            message="Audio playback restarted" if success else "Audio restart failed"
        )
    
    async def _handle_restart_bluealsa(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handles bluealsa restart command"""
        result = await self.control_service(self.bluealsa_service, "restart")
        return self.format_response(
            result, 
            message="BlueALSA service restarted successfully" if result else "Restart failed"
        )
    
    async def _handle_toggle_agent(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handles agent toggle command"""
        if self.auto_agent:
            await self.agent.unregister()
            self.auto_agent = False
            return self.format_response(True, auto_agent=False, message="Bluetooth agent disabled")
        else:
            success = await self.agent.register()
            self.auto_agent = success
            message = "Bluetooth agent enabled" if success else "Agent activation failed"
            return self.format_response(success, auto_agent=success, message=message)
    
    async def get_status(self) -> Dict[str, Any]:
        """Gets current plugin state"""
        try:
            # Check services state
            bt_active = await self.service_manager.is_active(self.bluetooth_service)
            bluealsa_active = await self.service_manager.is_active(self.bluealsa_service)
            aplay_active = await self.service_manager.is_active(self.bluealsa_aplay_service)
            
            return {
                "device_connected": self.connected_device is not None,
                "device_name": self.connected_device.get("name") if self.connected_device else None,
                "device_address": self.connected_device.get("address") if self.connected_device else None,
                "bluetooth_running": bt_active,
                "bluealsa_running": bluealsa_active,
                "aplay_running": aplay_active,
                "auto_agent": self.auto_agent,
                "current_device": self._current_device
            }
        except Exception as e:
            self.logger.error(f"Status error: {e}")
            return {"device_connected": False, "current_device": self._current_device, "error": str(e)}