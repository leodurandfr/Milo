# backend/hardware/ir_remote.py
"""
IR remote controller (Apple Remote 1st gen, white) via TSOP4838 on GPIO17.

The kernel rc-core subsystem decodes the modified-NEC 32-bit scancodes emitted
by the Apple Remote (manufacturer prefix 0x87EE | device_id | command) and,
once the per-remote keymap is loaded, surfaces them as EV_KEY events on the
gpio_ir_recv input device.

Two mutually-exclusive read modes — guarded by an asyncio.Lock — are exposed:

- **runtime**: filter EV_KEY events to dispatch volume / play-pause / track,
  and resolve MENU clicks. At resolution time (400 ms after the first press)
  the resolver branches on whether MENU is still held: held → transition to
  NONE + screen sleep, released with 1 tap → cycle next audio source in
  dock order, released with 2+ taps → transition to NONE. Active whenever
  the controller is enabled and a remote is paired.
- **pairing**: filter EV_MSC/MSC_SCAN to capture one valid Apple scancode,
  extract its device_id, then regenerate the keymap so only that remote is
  recognized going forward. Bounded by a timeout.
"""
import asyncio
import logging
import time
from typing import Optional, Tuple

from backend.config.constants import AUDIO_SOURCE_APPS
from backend.core.models.audio_state import AudioSource
from backend.hardware import keymap_writer
from backend.hardware.keymap_writer import APPLE_MANUFACTURER
from backend.hardware.playback_dispatch import PlaybackDispatcher
from backend.hardware.volume_accumulator import VolumeAccumulator

try:
    import evdev
    EVDEV_AVAILABLE = True
except ImportError:
    EVDEV_AVAILABLE = False

logger = logging.getLogger(__name__)

# Filename pattern advertised by the gpio-ir overlay on rc-core.
GPIO_IR_DEVICE_NAME = "gpio_ir_recv"

DEFAULT_PAIRING_TIMEOUT = 15.0  # seconds

# Window for disambiguating MENU single-click (cycle source) from
# double-click (transition to NONE). Mirrors PlaybackDispatcher's window
# so the UX feels consistent across hardware controllers.
MENU_CLICK_WINDOW = 0.4


class UnsupportedRemoteError(Exception):
    """Raised when a captured scancode doesn't carry the Apple manufacturer prefix."""


def parse_apple_scancode(scancode: int) -> Tuple[int, int]:
    """Decode a 32-bit Apple scancode → (command, device_id).

    Raises UnsupportedRemoteError if the manufacturer prefix doesn't match.
    """
    manufacturer = (scancode >> 16) & 0xFFFF
    device_id = (scancode >> 8) & 0xFF
    command = scancode & 0xFF
    if manufacturer != APPLE_MANUFACTURER:
        raise UnsupportedRemoteError(scancode)
    return command, device_id


def _find_gpio_ir_device() -> Optional[str]:
    """Return the evdev path of the gpio_ir_recv input device, if present."""
    if not EVDEV_AVAILABLE:
        return None
    for path in evdev.list_devices():
        try:
            device = evdev.InputDevice(path)
        except Exception:
            continue
        try:
            if device.name == GPIO_IR_DEVICE_NAME:
                return path
        finally:
            device.close()
    return None


class IrRemoteController:
    """Apple Remote IR controller.

    Pairing is the prerequisite — the runtime listener stays idle until a
    device_id is captured and the rc-core keymap is regenerated. Once paired,
    only the bound remote produces KEY_* events; other Apple Remotes in range
    are filtered out at the kernel level by the keymap, not by Python.
    """

    def __init__(self, volume_service, state_machine, settings_service, screen_controller):
        self.volume_service = volume_service
        self.state_machine = state_machine
        self.settings_service = settings_service
        self.screen_controller = screen_controller

        self.enabled: bool = False
        self.paired: bool = False
        self.device_id: Optional[int] = None
        self.paired_at: Optional[float] = None

        self._device_path: Optional[str] = None
        self._runtime_task: Optional[asyncio.Task] = None
        self._pairing_in_progress: bool = False
        self._pairing_cancel_event: Optional[asyncio.Event] = None

        # Mode lock — only one of (runtime, pairing) may hold an evdev read at a time
        self._mode_lock = asyncio.Lock()

        # Shared multi-source dispatchers
        self._volume = VolumeAccumulator(volume_service)
        self._dispatcher = PlaybackDispatcher(state_machine)

        # MENU click resolver — at T+MENU_CLICK_WINDOW after the first press,
        # we resolve based on whether MENU is still held (hold → screen sleep)
        # or already released (1 tap = cycle source, 2+ taps = transition to
        # NONE). Tracking `_menu_pressed` lets the resolver disambiguate hold
        # from tap without a separate hold-detection timer.
        self._menu_click_count = 0
        self._menu_click_timer: Optional[asyncio.TimerHandle] = None
        self._menu_resolve_task: Optional[asyncio.Task] = None
        self._menu_pressed = False

    # ========================================================================
    # LIFECYCLE
    # ========================================================================

    async def initialize(self) -> bool:
        """Initialize the IR remote controller.

        Always returns True — IR is an opt-in feature; missing evdev or
        missing kernel device must not crash the backend.
        """
        if not EVDEV_AVAILABLE:
            logger.info("evdev not installed — IR remote controller disabled")
            return True

        await self._load_config_from_settings()

        if not self.enabled or not self.paired:
            logger.info(
                "IR remote controller idle (enabled=%s, paired=%s)",
                self.enabled, self.paired,
            )
            return True

        await self._start_runtime_listener()
        return True

    async def cleanup(self) -> None:
        """Stop background tasks."""
        await self._stop_runtime_listener()
        self._dispatcher.cancel()
        self._cancel_menu_click_timer()
        await self._volume.cleanup()
        logger.info("IR remote controller cleaned up")

    # ========================================================================
    # CONFIGURATION
    # ========================================================================

    async def _load_config_from_settings(self) -> None:
        config = await self.settings_service.get_setting('hardware.ir_remote') or {}
        self.enabled = bool(config.get('enabled', False))
        device_id = config.get('device_id')
        if isinstance(device_id, int) and 0 <= device_id <= 0xFF:
            self.device_id = device_id
            self.paired = True
        else:
            self.device_id = None
            self.paired = False
        paired_at = config.get('paired_at')
        self.paired_at = float(paired_at) if isinstance(paired_at, (int, float)) else None

    async def _persist_config(self) -> None:
        config = {
            'enabled': self.enabled,
            'device_id': self.device_id,
            'paired_at': self.paired_at,
        }
        await self.settings_service.set_setting('hardware.ir_remote', config)

    async def update_config(self, partial: dict) -> None:
        """Apply a partial config update (currently only the `enabled` flag)."""
        if 'enabled' in partial:
            new_enabled = bool(partial['enabled'])
            if new_enabled != self.enabled:
                self.enabled = new_enabled
                if self.enabled and self.paired:
                    await self._start_runtime_listener()
                else:
                    await self._stop_runtime_listener()

        await self._persist_config()
        await self._broadcast_status()

    def get_status(self) -> dict:
        return {
            "available": EVDEV_AVAILABLE,
            "enabled": self.enabled,
            "paired": self.paired,
            "device_id": self.device_id,
            "paired_at": self.paired_at,
            "listening": self._runtime_task is not None and not self._runtime_task.done(),
            "pairing_in_progress": self._pairing_in_progress,
        }

    async def _broadcast_status(self) -> None:
        await self.state_machine.broadcast_event(
            "settings", "ir_remote_status_changed",
            {"source": "settings", **self.get_status()},
        )

    # ========================================================================
    # RUNTIME LISTENER
    # ========================================================================

    async def _start_runtime_listener(self) -> None:
        if self._runtime_task and not self._runtime_task.done():
            return
        if not EVDEV_AVAILABLE:
            return

        self._device_path = _find_gpio_ir_device()
        if not self._device_path:
            logger.warning(
                "gpio_ir_recv device not found — is the gpio-ir overlay loaded?"
            )
            return

        self._runtime_task = asyncio.create_task(self._runtime_loop())
        logger.info("IR runtime listener started on %s", self._device_path)

    async def _stop_runtime_listener(self) -> None:
        task = self._runtime_task
        self._runtime_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _runtime_loop(self) -> None:
        """Consume EV_KEY events from the rc-core device and dispatch them.

        The kernel keymap is the strict filter — only scancodes whose
        device_id matches the paired remote produce EV_KEY events. We trust
        that filter and don't re-validate the scancode at userspace level.

        Event values: 1 = key down, 0 = key up, 2 = kernel autorepeat.
        Volume +/- accept autorepeats so holding the button accumulates
        steps through VolumeAccumulator (Dock.vue hold-to-repeat parity).
        The 4 non-volume buttons stay single-press — autorepeats on
        play/pause, next, prev, or menu would double-fire those actions.
        """
        try:
            device = evdev.InputDevice(self._device_path)
        except OSError as e:
            logger.error("Failed to open IR device %s: %s", self._device_path, e)
            return

        volume_keys = (evdev.ecodes.KEY_VOLUMEUP, evdev.ecodes.KEY_VOLUMEDOWN)

        try:
            async with self._mode_lock:
                async for event in device.async_read_loop():
                    if event.type != evdev.ecodes.EV_KEY:
                        continue
                    if event.value == 1:
                        await self._handle_keycode(event.code)
                    elif event.value == 2 and event.code in volume_keys:
                        await self._handle_keycode(event.code)
                    elif event.value == 0 and event.code == evdev.ecodes.KEY_HOMEPAGE:
                        # MENU released — the resolver checks this flag at
                        # T+MENU_CLICK_WINDOW to distinguish tap from hold.
                        self._menu_pressed = False
        except asyncio.CancelledError:
            raise
        except OSError as e:
            logger.warning("IR device read failed: %s", e)
        except Exception as e:
            logger.error("IR runtime loop error: %s", e)
        finally:
            try:
                device.close()
            except Exception:
                pass

    async def _handle_keycode(self, code: int) -> None:
        """Map an EV_KEY code to a Milō action."""
        try:
            if code == evdev.ecodes.KEY_VOLUMEUP:
                step = self.volume_service.volume_config.step_ir_remote_db
                self._volume.accumulate(step)
            elif code == evdev.ecodes.KEY_VOLUMEDOWN:
                step = self.volume_service.volume_config.step_ir_remote_db
                self._volume.accumulate(-step)
            elif code == evdev.ecodes.KEY_PLAYPAUSE:
                await self._dispatcher.dispatch_play_pause()
            elif code == evdev.ecodes.KEY_NEXTSONG:
                await self._dispatcher.dispatch_track("next")
            elif code == evdev.ecodes.KEY_PREVIOUSSONG:
                await self._dispatcher.dispatch_track("prev")
            elif code == evdev.ecodes.KEY_HOMEPAGE:
                # Menu button: 1 click cycles to next audio source in dock
                # order, 2+ clicks within MENU_CLICK_WINDOW transition to NONE.
                self._register_menu_click()
            else:
                logger.debug("Unmapped IR keycode: %d", code)
        except Exception as e:
            logger.error("Error handling IR keycode %d: %s", code, e)

    # ========================================================================
    # MENU CLICK RESOLVER
    # ========================================================================

    def _register_menu_click(self) -> None:
        """Increment the MENU click counter and (re)arm the resolution timer."""
        self._menu_pressed = True
        self._menu_click_count += 1
        if self._menu_click_timer:
            self._menu_click_timer.cancel()
        loop = asyncio.get_running_loop()
        self._menu_click_timer = loop.call_later(MENU_CLICK_WINDOW, self._fire_menu_resolution)

    def _fire_menu_resolution(self) -> None:
        """Spawn the resolver task, tracking it so cleanup can cancel it."""
        self._menu_resolve_task = asyncio.create_task(self._resolve_menu_clicks())

    def _cancel_menu_click_timer(self) -> None:
        if self._menu_click_timer:
            self._menu_click_timer.cancel()
            self._menu_click_timer = None
        if self._menu_resolve_task and not self._menu_resolve_task.done():
            self._menu_resolve_task.cancel()
        self._menu_resolve_task = None
        self._menu_click_count = 0
        self._menu_pressed = False

    async def _resolve_menu_clicks(self) -> None:
        """Apply the MENU action once the click window has expired.

        Resolution rule: button still held at T+MENU_CLICK_WINDOW takes
        priority over click count — a held button is a hold gesture, even
        if a previous tap-release-tap sequence accumulated a count of 2.
        """
        try:
            count = self._menu_click_count
            held = self._menu_pressed
            self._menu_click_count = 0
            self._menu_click_timer = None
            if held:
                await self.state_machine.transition_to_source(AudioSource.NONE)
                await self.screen_controller.force_sleep()
            elif count >= 2:
                await self.state_machine.transition_to_source(AudioSource.NONE)
            elif count == 1:
                next_source = await self._next_audio_source_in_dock_order()
                if next_source is not None:
                    await self.state_machine.transition_to_source(next_source)
        except Exception as e:
            logger.error("Error resolving MENU clicks: %s", e)

    async def _next_audio_source_in_dock_order(self) -> Optional[AudioSource]:
        """Return the audio source following the active one in dock order.

        Uses `settings.dock.enabled_apps` (the user-customized dock order)
        filtered to audio sources. When the active source is NONE or not
        present in the dock, wraps to the first audio app.
        """
        dock = await self.settings_service.get_setting('dock') or {}
        enabled_apps = dock.get('enabled_apps') or []
        audio_apps = [app for app in enabled_apps if app in AUDIO_SOURCE_APPS]
        if not audio_apps:
            return None
        current = self.state_machine.system_state.active_source
        if current == AudioSource.NONE:
            return AudioSource(audio_apps[0])
        try:
            idx = audio_apps.index(current.value)
        except ValueError:
            return AudioSource(audio_apps[0])
        return AudioSource(audio_apps[(idx + 1) % len(audio_apps)])

    # ========================================================================
    # PAIRING
    # ========================================================================

    async def start_pairing(self, timeout_seconds: float = DEFAULT_PAIRING_TIMEOUT) -> dict:
        """Listen for one Apple scancode, save its device_id, regenerate keymap.

        Returns a result dict consumable by the API route:
            {"status": "success" | "timeout" | "cancelled" | "unsupported" | "error",
             "device_id": int (on success), "message": str}

        While pairing is active, the runtime listener is paused — both modes
        share the same evdev device under the `_mode_lock`.
        """
        if not EVDEV_AVAILABLE:
            return {"status": "error", "message": "evdev not available"}
        if self._pairing_in_progress:
            return {"status": "error", "message": "Pairing already in progress"}

        device_path = _find_gpio_ir_device()
        if not device_path:
            return {
                "status": "error",
                "message": "IR receiver not detected — is the gpio-ir overlay loaded?",
            }

        # Pause the runtime listener so pairing can take the evdev device exclusively.
        await self._stop_runtime_listener()

        self._pairing_in_progress = True
        self._pairing_cancel_event = asyncio.Event()
        await self._broadcast_status()

        try:
            result = await self._capture_one_scancode(device_path, timeout_seconds)
        finally:
            self._pairing_in_progress = False
            self._pairing_cancel_event = None
            await self._broadcast_status()

        if result.get("status") == "success":
            device_id = result["device_id"]
            try:
                await keymap_writer.apply_keymap(device_id)
            except Exception as e:
                logger.error("Failed to apply keymap for device_id=0x%02X: %s", device_id, e)
                return {"status": "error", "message": f"Failed to apply keymap: {e}"}

            self.device_id = device_id
            self.paired = True
            self.paired_at = time.time()
            # Auto-enable on first pairing so the remote works immediately.
            self.enabled = True
            await self._persist_config()
            await self._start_runtime_listener()
            await self._broadcast_status()

        return result

    async def _capture_one_scancode(
        self, device_path: str, timeout_seconds: float
    ) -> dict:
        """Read EV_MSC/MSC_SCAN events until one decodes as an Apple scancode.

        Both inner tasks acquire `_mode_lock`; we MUST await their completion
        on every exit path so the lock is released before the caller restarts
        the runtime listener. `task.cancel()` alone is not enough — the
        CancelledError propagates asynchronously and the `async with` only
        exits when the task body actually unwinds.
        """
        try:
            device = evdev.InputDevice(device_path)
        except OSError as e:
            return {"status": "error", "message": f"Failed to open IR device: {e}"}

        async def _reader() -> dict:
            async with self._mode_lock:
                async for event in device.async_read_loop():
                    if event.type != evdev.ecodes.EV_MSC:
                        continue
                    if event.code != evdev.ecodes.MSC_SCAN:
                        continue
                    try:
                        _command, device_id = parse_apple_scancode(int(event.value))
                    except UnsupportedRemoteError:
                        # Surface this immediately rather than keep listening —
                        # the user can dismiss the dialog and try again.
                        return {
                            "status": "unsupported",
                            "message": "Unsupported remote (not an Apple Remote)",
                        }
                    return {"status": "success", "device_id": device_id}

        async def _wait_cancel() -> dict:
            await self._pairing_cancel_event.wait()
            return {"status": "cancelled", "message": "Pairing cancelled"}

        reader_task = asyncio.create_task(_reader())
        cancel_task = asyncio.create_task(_wait_cancel())
        result: dict
        try:
            done, pending = await asyncio.wait(
                {reader_task, cancel_task},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            # Drain pending tasks so `_mode_lock` is released and the
            # backing evdev fd is no longer being read before we close it.
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if not done:
                result = {"status": "timeout", "message": "No remote detected"}
            else:
                result = done.pop().result()
        except asyncio.CancelledError:
            for task in (reader_task, cancel_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(reader_task, cancel_task, return_exceptions=True)
            raise
        finally:
            try:
                device.close()
            except Exception:
                pass
        return result

    async def cancel_pairing(self) -> bool:
        """Cancel an in-flight pairing capture."""
        if not self._pairing_in_progress or not self._pairing_cancel_event:
            return False
        self._pairing_cancel_event.set()
        return True

    async def unpair(self) -> None:
        """Forget the paired remote and clear the kernel keymap."""
        await self._stop_runtime_listener()

        try:
            await keymap_writer.clear_kernel_keymap()
        except Exception as e:
            # Don't block the unpair if the helper fails — settings/state still need clearing.
            logger.warning("Failed to clear kernel keymap during unpair: %s", e)

        self.paired = False
        self.device_id = None
        self.paired_at = None
        # Keep enabled=False so the runtime listener doesn't auto-restart on next pair.
        self.enabled = False
        await self._persist_config()
        await self._broadcast_status()
