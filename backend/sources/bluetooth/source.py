# backend/sources/bluetooth/source.py
"""
Bluetooth audio source using BlueALSA.

This source handles streaming audio from Bluetooth devices via BlueALSA.
It manages multiple systemd services, D-Bus agent for auto-pairing, and
monitors BlueALSA PCM events for connection detection.

Features:
- Multi-service management: bluetooth, bluealsa, bluealsa-aplay
- D-Bus agent for automatic pairing (NoInputNoOutput mode)
- Single device connection enforcement (via BlueALSA monitor callbacks)
- BlueALSA PCM monitoring for real-time connection events
"""
import asyncio
from typing import Dict, Any, Optional

from backend.core.audio_source import BaseAudioSource
from backend.sources.bluetooth.agent import BluetoothAgent
from backend.sources.bluetooth.monitor import BlueAlsaMonitor
from backend.shared.decorators import handle_errors


class BluetoothSource(BaseAudioSource):
    """
    Bluetooth audio source using BlueALSA.

    Family A (mute receiver): control flows from the Bluetooth sender;
    commands routed through `/api/audio/control/bluetooth` reach
    `_handle_command` (e.g. `disconnect`). Extends BaseAudioSource —
    implements `_do_start / _do_stop / _get_status / _handle_command`.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None,
        camilladsp_service=None
    ):
        super().__init__(
            source_id="bluetooth",
            service_name="milo-bluealsa.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config
        )

        self.bluetooth_service = self._config.get("bluetooth_service", "bluetooth.service")
        self.bluealsa_service = self.service_name
        self.bluealsa_aplay_service = self._config.get(
            "bluealsa_aplay_service", "milo-bluealsa-aplay.service"
        )
        self.stop_bluetooth_on_exit = self._config.get("stop_bluetooth_on_exit", True)
        self.auto_agent = self._config.get("auto_agent", True)

        # State
        self.connected_device: Optional[Dict[str, str]] = None

        # Components
        self.agent = BluetoothAgent()
        self.monitor = BlueAlsaMonitor()

        # No per-source auto-stop: BT carries no out-of-band pause signal
        # and senders re-connect instantly when the user resumes. The 12h
        # INACTIVITY_TIMEOUT in AudioStateMachine remains as the final
        # backstop. `camilladsp_service` is kept on the constructor for DI
        # compatibility with the other Family A sources.
        self.auto_stop_enabled = False
        _ = camilladsp_service  # reserved for future use

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self.connected_device = None

    async def _do_start(self) -> bool:
        """Start Bluetooth services and monitoring."""
        try:
            # 1. Start system services
            for service in [self.bluetooth_service, self.bluealsa_service]:
                if not await self._start_service(service):
                    raise RuntimeError(f"Failed to start {service}")

            # 2. Start playback service
            if not await self._start_service(self.bluealsa_aplay_service):
                raise RuntimeError(f"Failed to start {self.bluealsa_aplay_service}")

            # 3. Configure Bluetooth adapter
            if not await self._configure_adapter():
                self._logger.warning("Adapter configuration failed")

            # 4. Register D-Bus agent
            if self.auto_agent:
                if not await self.agent.register():
                    self._logger.warning("Agent registration failed")

            # 5. Set up and start BlueALSA monitor (event-based connection detection)
            self.monitor.set_callbacks(
                self._on_device_connected,
                self._on_device_disconnected
            )
            if not await self.monitor.start():
                raise RuntimeError("BlueALSA monitor failed to start")

            # 6. Detect already-connected device (e.g. backend restart during active stream)
            await self._detect_connected_device()

            # 7. Update state
            self._update_connection_state()

            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    @handle_errors(default=False)
    async def _do_stop(self) -> bool:
        """Stop monitoring and services."""
        await self._cleanup()

        # Disable discoverability
        await self._run_bluetoothctl("discoverable off\npairable off\nquit")

        # Stop BlueALSA services
        if self.stop_bluetooth_on_exit:
            await self._stop_service(self.bluealsa_aplay_service)
            await self._stop_service(self.bluealsa_service)

            # Keep bluetooth.service running if BT remote controller needs it
            bt_remote = await self._settings_service.get_setting('hardware.bt_remote')
            if not (bt_remote and bt_remote.get('enabled')):
                await self._stop_service(self.bluetooth_service)

        self._reset_playback_state()

        return True

    async def release_for_reroute(self) -> bool:
        """Multiroom reroute (release half): stop ONLY bluealsa-aplay so the
        CamillaDSP input it feeds in direct mode is freed for the snapcast
        reconcile (snapclient feeds that same CamillaDSP in multiroom mode).

        bluealsa + bluetooth.service keep running, so the A2DP link — and
        self.connected_device — survive; unlike _do_stop(), which tears the
        whole stack down and kicks the phone off. The BlueALSA monitor tracks
        PCM add/remove driven by the bluealsa daemon (i.e. the phone's A2DP
        transport), not by the bluealsa-aplay consumer, so bouncing the writer
        alone never surfaces as a disconnect.
        """
        return await self._stop_service(self.bluealsa_aplay_service)

    async def acquire_after_reroute(self) -> bool:
        """Multiroom reroute (acquire half): restart bluealsa-aplay under the
        new MILO_MODE and re-publish state. The device stayed connected and the
        monitor kept self.connected_device current, so re-broadcasting the
        connection state restores ACTIVE (the transition set it to STARTING).
        """
        if not await self._start_service(self.bluealsa_aplay_service):
            return False
        self._update_connection_state()
        return True

    async def _get_status(self) -> Dict[str, Any]:
        """Get Bluetooth-specific status."""
        bt_active = await self._is_service_active(self.bluetooth_service)
        bluealsa_active = await self._is_service_active()
        aplay_active = await self._is_service_active(self.bluealsa_aplay_service)

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

        return self.error_response(f"Unknown command: {cmd}")

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
            _, stderr = await asyncio.wait_for(proc.communicate(), 10.0)

            if proc.returncode != 0:
                return self.error_response(stderr.decode().strip())

            return self.success_response("Device disconnecting")

        except asyncio.TimeoutError:
            proc.kill()
            self._logger.error(f"Timeout disconnecting device {address}")
            return self.error_response("Disconnect timed out")
        except Exception as e:
            return self.error_response(str(e))

    # === BlueALSA Monitor Callbacks ===

    async def _on_device_connected(self, address: str, name: str) -> None:
        """Handle device connection from BlueALSA monitor."""
        # Single device enforcement: disconnect if another device is already connected
        if self.connected_device and self.connected_device.get("address") != address:
            self._logger.info(f"Disconnecting {name} ({address}) - another device already connected")
            await self._disconnect_device(address)
            return

        if not self.connected_device:
            self.connected_device = {"address": address, "name": name}
            self._logger.info(f"Device connected: {name} ({address})")
            self._update_connection_state()

    @handle_errors(default=False)
    async def _disconnect_device(self, address: str) -> bool:
        """Disconnect a device by address."""
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", "disconnect", address,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), 10.0)
        except asyncio.TimeoutError:
            proc.kill()
            self._logger.error(f"Timeout disconnecting device {address}")
            return False

        if proc.returncode != 0:
            self._logger.error(f"Disconnect failed: {stderr.decode().strip()}")
            return False

        return True

    async def _on_device_disconnected(self, address: str, name: str) -> None:
        """Handle device disconnection from BlueALSA monitor."""
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

    @handle_errors(default=False)
    async def _run_bluetoothctl(self, commands: str) -> bool:
        """Execute bluetoothctl commands."""
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        try:
            await asyncio.wait_for(proc.communicate(input=commands.encode()), 10.0)
        except asyncio.TimeoutError:
            proc.kill()
            self._logger.error("Timeout running bluetoothctl commands")
            return False
        return proc.returncode == 0

    @handle_errors(default=None)
    async def _detect_connected_device(self) -> None:
        """Detect currently connected A2DP device via BlueALSA PCM list.

        Uses bluealsa-cli list-pcms instead of bluetoothctl to only detect
        actual audio devices, filtering out HID devices (e.g. BT remotes).
        """
        proc = await asyncio.create_subprocess_exec(
            "bluealsa-cli", "list-pcms",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 10.0)
        except asyncio.TimeoutError:
            proc.kill()
            self._logger.error("Timeout listing BlueALSA PCMs")
            return

        if proc.returncode == 0:
            for line in stdout.decode().splitlines():
                device_info = self.monitor.parse_pcm_path(line.strip())
                if device_info:
                    address = device_info["address"]
                    name = await self.monitor.resolve_device_name(address)
                    self.connected_device = {"address": address, "name": name}
                    return

        # No A2DP device found
        self.connected_device = None

    async def _cleanup(self) -> None:
        """Clean up resources."""
        # Stop BlueALSA monitor
        await self.monitor.stop()

        # Unregister agent
        if self.auto_agent:
            await self.agent.unregister()

    def _update_connection_state(self) -> None:
        """Update state based on connected device."""
        device = self.connected_device or {}
        self._set_active_or_waiting(
            self.connected_device is not None,
            {"device_connected": True, "device_name": device.get("name"),
             "device_address": device.get("address")},
            {"device_connected": False, "device_name": None,
             "device_address": None}
        )

