# backend/features/bluetooth/source.py
"""
Bluetooth audio source using BlueALSA.

This source handles streaming audio from Bluetooth devices via BlueALSA.
It manages multiple systemd services, D-Bus agent for auto-pairing, and
monitors BlueALSA PCM events for connection detection.

Features:
- Multi-service management: bluetooth, bluealsa, bluealsa-aplay
- D-Bus agent for automatic pairing (NoInputNoOutput mode)
- Single device connection enforcement
- BlueALSA PCM monitoring for real-time connection events
"""
import asyncio
from typing import Dict, Any, Optional

from backend.core.audio_source import BaseAudioSource, SourceState
from backend.core.events import EventBus
from backend.features.bluetooth.agent import BluetoothAgent
from backend.features.bluetooth.monitor import BlueAlsaMonitor


class BluetoothSource(BaseAudioSource):
    """
    Bluetooth audio source using BlueALSA.

    Implements AudioSource Protocol with:
    - start(): Start Bluetooth services, agent, and monitoring
    - stop(): Stop services and cleanup
    - restart(): Restart audio playback service
    - status(): Get current status with connected device
    - command(): Handle disconnect, restart_audio, etc.

    Events emitted:
    - source.started: Bluetooth source activated
    - source.stopped: Bluetooth source deactivated
    - source.state_changed: State changed (READY/CONNECTED)
    """

    def __init__(
        self,
        event_bus: EventBus,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        systemd_manager=None
    ):
        """
        Initialize Bluetooth source.

        Args:
            event_bus: EventBus for state notifications
            config: Optional configuration dict with:
                - bluetooth_service: System Bluetooth service (default "bluetooth.service")
                - bluealsa_service: BlueALSA service name (default "milo-bluealsa.service")
                - bluealsa_aplay_service: Playback service (default "milo-bluealsa-aplay.service")
                - stop_bluetooth_on_exit: Stop services on stop (default True)
                - auto_agent: Enable auto-pairing agent (default True)
            state_machine: Optional state machine for state synchronization
            systemd_manager: Optional SystemdServiceManager (injected via DI)
        """
        super().__init__(
            source_id="bluetooth",
            service_name="milo-bluealsa.service",
            event_bus=event_bus,
            state_machine=state_machine,
            systemd_manager=systemd_manager
        )

        config = config or {}
        self.bluetooth_service = config.get("bluetooth_service", "bluetooth.service")
        self.bluealsa_service = self.service_name
        self.bluealsa_aplay_service = config.get(
            "bluealsa_aplay_service", "milo-bluealsa-aplay.service"
        )
        self.stop_bluetooth_on_exit = config.get("stop_bluetooth_on_exit", True)
        self.auto_agent = config.get("auto_agent", True)

        # State
        self.connected_device: Optional[Dict[str, str]] = None
        self._first_connected_device: Optional[str] = None
        self._restart_in_progress = False
        self._restart_lock = asyncio.Lock()

        # Components
        self.agent = BluetoothAgent()
        self.monitor = BlueAlsaMonitor()

        # Tasks
        self._monitor_task: Optional[asyncio.Task] = None

    async def _do_start(self) -> bool:
        """Start Bluetooth services and monitoring."""
        try:
            # 1. Start system services
            for service in [self.bluetooth_service, self.bluealsa_service]:
                if not await self._start_service_by_name(service):
                    raise RuntimeError(f"Failed to start {service}")

            # 2. Start playback service
            if not await self._start_service_by_name(self.bluealsa_aplay_service):
                raise RuntimeError(f"Failed to start {self.bluealsa_aplay_service}")

            # 3. Configure Bluetooth adapter
            if not await self._configure_adapter():
                self._logger.warning("Adapter configuration failed")

            # 4. Start connection monitoring task
            self._monitor_task = asyncio.create_task(self._monitor_connections())

            # 5. Register D-Bus agent
            if self.auto_agent:
                if not await self.agent.register():
                    self._logger.warning("Agent registration failed")

            # 6. Set up and start BlueALSA monitor
            self.monitor.set_callbacks(
                self._on_device_connected,
                self._on_device_disconnected
            )
            if not await self.monitor.start():
                raise RuntimeError("BlueALSA monitor failed to start")

            # 7. Update state
            self._update_connection_state()

            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    async def _do_stop(self) -> bool:
        """Stop monitoring and services."""
        try:
            await self._cleanup()

            # Disable discoverability
            await self._run_bluetoothctl("discoverable off\npairable off\nquit")

            # Stop services if configured
            if self.stop_bluetooth_on_exit:
                await self._stop_service_by_name(self.bluealsa_aplay_service)
                for service in [self.bluealsa_service, self.bluetooth_service]:
                    await self._stop_service_by_name(service)

            # Reset state
            self.connected_device = None
            self._first_connected_device = None

            return True

        except Exception as e:
            self._logger.error(f"Stop failed: {e}")
            return False

    async def _do_restart(self) -> bool:
        """Restart audio playback service and re-detect device."""
        async with self._restart_lock:
            try:
                self._logger.info("Restarting Bluetooth audio")
                self._restart_in_progress = True

                # Restart playback service
                if not await self._restart_service_by_name(self.bluealsa_aplay_service):
                    self._restart_in_progress = False
                    return False

                await asyncio.sleep(0.5)

                # Re-detect connected device
                await self._detect_connected_device()

                self._restart_in_progress = False
                self._update_connection_state()

                return True

            except Exception as e:
                self._restart_in_progress = False
                self._logger.error(f"Restart failed: {e}")
                return False

    async def _get_status(self) -> Dict[str, Any]:
        """Get Bluetooth-specific status."""
        bt_active = await self._is_service_active_by_name(self.bluetooth_service)
        bluealsa_active = await self._is_service_active()
        aplay_active = await self._is_service_active_by_name(self.bluealsa_aplay_service)

        return {
            "device_connected": self.connected_device is not None,
            "device_name": self.connected_device.get("name") if self.connected_device else None,
            "device_address": self.connected_device.get("address") if self.connected_device else None,
            "bluetooth_running": bt_active,
            "bluealsa_running": bluealsa_active,
            "aplay_running": aplay_active,
            "auto_agent": self.auto_agent
        }

    async def _handle_command(self, cmd: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle Bluetooth-specific commands."""
        if cmd == "disconnect":
            return await self._cmd_disconnect()

        if cmd == "restart_audio":
            return await self._cmd_restart_audio()

        if cmd == "restart_bluealsa":
            return await self._cmd_restart_bluealsa()

        if cmd == "toggle_agent":
            return await self._cmd_toggle_agent()

        return self.error_response(f"Unknown command: {cmd}")

    # === Command Handlers ===

    async def _cmd_disconnect(self) -> Dict[str, Any]:
        """Disconnect current device."""
        if not self.connected_device:
            return self.error_response("No device connected")

        address = self.connected_device.get("address")
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "disconnect", address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                return self.error_response(stderr.decode().strip())

            return self.success_response("Device disconnecting")

        except Exception as e:
            return self.error_response(str(e))

    async def _cmd_restart_audio(self) -> Dict[str, Any]:
        """Restart audio playback."""
        if not self.connected_device:
            return self.error_response("No device connected")

        success = await self._do_restart()
        return (
            self.success_response("Audio playback restarted")
            if success else self.error_response("Audio restart failed")
        )

    async def _cmd_restart_bluealsa(self) -> Dict[str, Any]:
        """Restart BlueALSA service."""
        success = await self._restart_service_by_name(self.bluealsa_service)
        return (
            self.success_response("BlueALSA service restarted")
            if success else self.error_response("Restart failed")
        )

    async def _cmd_toggle_agent(self) -> Dict[str, Any]:
        """Toggle auto-pairing agent."""
        if self.auto_agent:
            await self.agent.unregister()
            self.auto_agent = False
            return self.success_response("Agent disabled", auto_agent=False)
        else:
            success = await self.agent.register()
            self.auto_agent = success
            message = "Agent enabled" if success else "Agent activation failed"
            return self.success_response(message, auto_agent=success)

    # === Connection Monitoring ===

    async def _monitor_connections(self) -> None:
        """Monitor for additional connections and enforce single device."""
        self._logger.info("Connection monitoring started")

        while True:
            try:
                await asyncio.sleep(0.5)

                # List connected devices
                proc = await asyncio.create_subprocess_exec(
                    "bluetoothctl", "devices", "Connected",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL
                )
                stdout, _ = await proc.communicate()

                if proc.returncode != 0:
                    continue

                connected = []
                for line in stdout.decode().splitlines():
                    if line.startswith("Device "):
                        parts = line.split(" ", 2)
                        if len(parts) >= 2:
                            connected.append(parts[1])

                # Reset if no devices
                if not connected:
                    self._first_connected_device = None
                    continue

                # Register first device
                if self._first_connected_device is None and len(connected) == 1:
                    self._first_connected_device = connected[0]
                    self._logger.info(f"First device: {self._first_connected_device}")
                    continue

                # Disconnect additional devices
                if len(connected) > 1:
                    for addr in connected:
                        if addr != self._first_connected_device:
                            self._logger.warning(
                                f"Disconnecting {addr} - another device is connected"
                            )
                            await self._disconnect_device(addr)

            except asyncio.CancelledError:
                self._logger.info("Connection monitoring stopped")
                break
            except Exception as e:
                self._logger.error(f"Monitor error: {e}")
                await asyncio.sleep(1)

    async def _disconnect_device(self, address: str) -> bool:
        """Disconnect a device by address."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "disconnect", address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                self._logger.error(f"Disconnect failed: {stderr.decode().strip()}")
                return False

            return True
        except Exception as e:
            self._logger.error(f"Disconnect error: {e}")
            return False

    # === BlueALSA Monitor Callbacks ===

    async def _on_device_connected(self, address: str, name: str) -> None:
        """Handle device connection from BlueALSA monitor."""
        if not self.connected_device:
            self.connected_device = {"address": address, "name": name}

        self._logger.info(f"Device connected: {name} ({address})")
        self._update_connection_state()

    async def _on_device_disconnected(self, address: str, name: str) -> None:
        """Handle device disconnection from BlueALSA monitor."""
        # Ignore during restart
        if self._restart_in_progress:
            self._logger.debug(f"Ignoring disconnect during restart: {name}")
            return

        # Check if current device
        if not self.connected_device:
            return
        if self.connected_device.get("address") != address:
            return

        self.connected_device = None
        self._logger.info(f"Device disconnected: {name} ({address})")
        self._update_connection_state()

    # === Helper Methods ===

    async def _configure_adapter(self) -> bool:
        """Configure Bluetooth adapter via bluetoothctl."""
        commands = "\n".join([
            "power on",
            "discoverable-timeout 0",
            "discoverable on",
            "pairable on",
            "class 0x200404",  # Audio device class
            "quit"
        ])
        return await self._run_bluetoothctl(commands)

    async def _run_bluetoothctl(self, commands: str) -> bool:
        """Execute bluetoothctl commands."""
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
            self._logger.error(f"bluetoothctl error: {e}")
            return False

    async def _detect_connected_device(self) -> None:
        """Detect currently connected device via bluetoothctl."""
        try:
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
                            return

            # No device found
            self.connected_device = None
            self._first_connected_device = None

        except Exception as e:
            self._logger.error(f"Device detection error: {e}")

    async def _cleanup(self) -> None:
        """Clean up resources."""
        # Stop BlueALSA monitor
        await self.monitor.stop()

        # Cancel monitoring task
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        # Unregister agent
        if self.auto_agent:
            await self.agent.unregister()

        # Reset state
        self._first_connected_device = None

    def _update_connection_state(self) -> None:
        """Update state based on connected device."""
        if self.connected_device:
            self.set_state(SourceState.CONNECTED, {
                "device_connected": True,
                "device_name": self.connected_device.get("name"),
                "device_address": self.connected_device.get("address")
            })
        else:
            self.set_state(SourceState.READY, {
                "device_connected": False,
                "device_name": None,
                "device_address": None
            })

    # === Service Management Helpers ===

    async def _start_service_by_name(self, name: str) -> bool:
        """Start a systemd service by name."""
        try:
            return await self._service_manager.start(name)
        except Exception as e:
            self._logger.error(f"Failed to start {name}: {e}")
            return False

    async def _stop_service_by_name(self, name: str) -> bool:
        """Stop a systemd service by name."""
        try:
            return await self._service_manager.stop(name)
        except Exception as e:
            self._logger.error(f"Failed to stop {name}: {e}")
            return False

    async def _restart_service_by_name(self, name: str) -> bool:
        """Restart a systemd service by name."""
        try:
            return await self._service_manager.restart(name)
        except Exception as e:
            self._logger.error(f"Failed to restart {name}: {e}")
            return False

    async def _is_service_active_by_name(self, name: str) -> bool:
        """Check if a systemd service is active by name."""
        try:
            return await self._service_manager.is_active(name)
        except Exception:
            return False
