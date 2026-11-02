# backend/api/setup.py
"""
Setup wizard API routes — first-boot configuration.

POST /api/setup/complete → atomic wizard completion (language + hardware + setup_completed)
POST /api/setup/become-client → adopt this fresh device as a multiroom client (wifi flow)

Client mode is normally handled automatically at first boot by
milo-first-boot.service (mDNS detection over ethernet). The wifi adoption
flow is the alternative path when no ethernet is available: a server pushes
the audio config + target wifi creds, the device persists them and reboots,
and milo-first-boot reads the marker on next boot to apply the client role.
"""
import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from typing import Optional, TYPE_CHECKING
from pydantic import BaseModel, Field

from backend.config.constants import MILO_DATA_DIR
from backend.core.multiroom.models import SpeakerType

if TYPE_CHECKING:
    from backend.core.network.service import NetworkService
    from backend.core.settings import SettingsService
    from backend.core.systemd import SystemdServiceManager
    from backend.hardware.service import HardwareService


logger = logging.getLogger(__name__)

PENDING_CLIENT_ROLE_FILE = MILO_DATA_DIR / "pending_client_role.json"

# The marker's only consumer, deployed by `pi-gen/stage-milo` — so it is there on
# anything flashed from the Milō image, which is how a unit is provisioned. The
# check stays because the backend also runs on a plain checkout during
# development, and there the adoption below would write a marker no boot ever
# reads: the device reboots, comes back a server, and the adopting server waits
# for a speaker that never appears. Checked, not assumed.
FIRST_BOOT_HELPER = Path("/usr/local/bin/milo-first-boot")

# Serializes /api/setup/become-client so two concurrent adopters can't race on
# the marker write + wifi profile + setup_completed sequence.
_become_client_lock = asyncio.Lock()

# The hardware.json blocks the wizard carries over from the current config, with
# the GPIO keys each one drives while enabled.
WIZARD_KEPT_PERIPHERALS = {
    "rotary_encoder": ("clk_pin", "dt_pin", "sw_pin"),
    "ir_remote": ("gpio_pin",),
    "power_button": ("led_gpio_pin",),
}


class SetupCompleteRequest(BaseModel):
    """Wizard completion payload — all settings applied atomically."""
    language: str = Field(..., description="Language key")
    audio_id: str = Field(..., description="Audio card registry ID")
    volume_control: Optional[bool] = Field(None, description="Volume management override (None = auto-detect from card category)")
    screen_type: str = Field(..., description="Screen type registry ID")


class BecomeClientRequest(BaseModel):
    """Wifi adoption payload pushed by a server to a fresh device."""
    wifi_ssid: str = Field(..., min_length=1, description="Target WiFi SSID the device must join after reboot")
    wifi_password: str = Field(default="", description="Target WiFi password (empty for open networks)")
    audio_id: str = Field(..., min_length=1, description="Audio card registry ID")
    speaker_name: str = Field(..., min_length=1, max_length=64, description="Display name for the speaker")
    speaker_type: SpeakerType = Field(..., description="Speaker physical type")


def _atomic_write_json(path, data: dict) -> None:
    """Write JSON to ``path`` atomically (tempfile + fsync + rename)."""
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def create_setup_router(
    settings_service: "SettingsService",
    hardware_service: "HardwareService",
    network_service: "NetworkService",
    systemd_manager: "SystemdServiceManager"
):
    """Create setup wizard router with injected services."""
    router = APIRouter(prefix="/api/setup", tags=["setup"])

    # A wizard completion in flight, from the route's first line to the end of
    # its apply. `setup_completed` cannot guard that window: it is written only
    # once milo-apply-hardware has rewritten config.txt, seconds after the
    # route answered.
    completing = False

    @router.post("/complete")
    async def complete_setup(payload: SetupCompleteRequest, background_tasks: BackgroundTasks):
        """
        Atomic wizard completion: set language, save hardware config, mark setup complete, reboot.

        On failure setup_completed stays false, so the wizard reappears. A submit
        arriving while another is in flight, or once setup is complete, answers
        "rebooting" and applies nothing.
        """
        nonlocal completing
        from backend.hardware.registry import AUDIO_CARDS, SCREENS, reserved_pins
        from backend.core.settings import VALID_LANGUAGES

        # Idempotency guard — a second device pressing Finish (the kiosk and a
        # phone) must not run a second milo-apply-hardware over config.txt nor
        # a second reboot. Checked and set before the first await: a check
        # separated from its write by an await lets both submits pass.
        if completing:
            return {"status": "rebooting"}
        completing = True
        scheduled = False

        try:
            if await settings_service.get_setting("setup_completed"):
                return {"status": "rebooting"}

            # Validate language
            if payload.language not in VALID_LANGUAGES:
                raise HTTPException(status_code=400, detail=f"Invalid language: {payload.language}")

            # Validate audio card and screen
            if payload.audio_id not in AUDIO_CARDS:
                raise HTTPException(status_code=400, detail=f"Unknown audio card: {payload.audio_id}")
            if payload.screen_type not in SCREENS:
                raise HTTPException(status_code=400, detail=f"Unknown screen type: {payload.screen_type}")

            # 1. Set language
            if not await settings_service.set_setting("language", payload.language):
                raise RuntimeError("Failed to persist language setting")
            logger.info(f"Setup wizard: language set to {payload.language}")

            # 2. Save hardware config
            card = AUDIO_CARDS[payload.audio_id]
            screen = SCREENS[payload.screen_type]

            audio_config = {"id": payload.audio_id}
            if card["overlay"]:
                audio_config.update({
                    "card_name": card["card_name"],
                    "overlay": card["overlay"],
                })
            if payload.volume_control is not None:
                audio_config["volume_control"] = payload.volume_control

            current = hardware_service.get_full_config()
            # A peripheral kept on a pin the new card's overlay holds would
            # fight it for the line at the next boot. It is wired to that pin,
            # so moving the pin would not move it: it is turned off instead.
            held = reserved_pins(payload.audio_id)
            kept = {}
            for key, pin_keys in WIZARD_KEPT_PERIPHERALS.items():
                block = current[key]
                clash = held & {block[pin_key] for pin_key in pin_keys}
                if block["enabled"] and clash:
                    block = {**block, "enabled": False}
                    logger.warning(
                        f"Setup wizard: {key} disabled, GPIO {sorted(clash)} "
                        f"held by {card['label']}"
                    )
                kept[key] = block
            config = {
                "audio": audio_config,
                "screen": {
                    "type": payload.screen_type,
                    "resolution": screen["resolution"],
                },
                "rotary_encoder": kept["rotary_encoder"],
                # Preserve the install-time gpio-ir overlay: milo-apply-hardware
                # strips the IR block from config.txt unless ir_remote.enabled is
                # True here, so dropping the key would disable the receiver.
                "ir_remote": kept["ir_remote"],
                # Carried for the same reason: the apply writes the EEPROM from
                # it, and a reflash leaves the EEPROM as the last unit set it,
                # so the wizard's apply is what brings a reflashed board back
                # to starting on power.
                "power_button": kept["power_button"],
            }

            await hardware_service.save_config(config)
            logger.info(f"Setup wizard: hardware saved (audio={payload.audio_id}, screen={payload.screen_type})")

            # 3. Write config.txt, THEN flip the flag, THEN reboot.
            #
            # The order is the whole point and it is not the obvious one. The
            # flag used to be persisted here, before the overlay reached
            # config.txt — so a power cut in that window (the user pulls the
            # plug because "it is taking a while") left a unit that considered
            # itself configured, booting with no dtoverlay: no sound, no
            # wizard, no message anywhere. Applying first makes every cut before
            # the flag bring the wizard back, and a cut after it lands on a
            # config.txt that is already correct.
            #
            # The submit is released however this ends: once the flag is
            # written it guards on its own, and an apply that failed leaves the
            # wizard on screen, to be submitted again.
            async def _delayed_apply():
                nonlocal completing
                try:
                    await asyncio.sleep(1)  # Allow HTTP response to flush to the client
                    try:
                        await hardware_service.apply_and_reboot(reboot=False)
                    except Exception as e:
                        logger.error(f"Setup wizard: hardware apply failed: {e}")
                        return

                    if not await settings_service.set_setting("setup_completed", True):
                        logger.error("Setup wizard: failed to persist setup_completed — not rebooting")
                        return
                    logger.info("Setup wizard: setup_completed set to true")

                    await systemd_manager.power("reboot")
                finally:
                    completing = False

            background_tasks.add_task(_delayed_apply)
            scheduled = True

            return {"status": "rebooting"}

        except HTTPException:
            raise
        except Exception as e:
            # No rollback of `setup_completed`: nothing here sets it any more.
            # It is written by the background task above, after config.txt has
            # been rewritten, so a failure on this path leaves it false and the
            # wizard reappears on its own — which is what the old rollback
            # existed to guarantee.
            logger.error(f"Setup wizard failed: {e}")
            raise HTTPException(status_code=500, detail=f"Setup failed: {e}")
        finally:
            if not scheduled:
                completing = False

    @router.post("/become-client")
    async def become_client(payload: BecomeClientRequest, background_tasks: BackgroundTasks):
        """
        Adopt this fresh device as a multiroom client (wifi flow).

        Steps:
          1. Persist /var/lib/milo/pending_client_role.json so milo-first-boot
             switches into client mode + applies hardware on next boot.
          2. Save the target WiFi profile (no live switch — the open hotspot
             stays up so the HTTP response can still reach the server).
          3. Mark setup_completed=true so milo-first-boot's skip-check sees a
             role-locked device on the boot AFTER the client switch.
          4. Schedule a fire-and-forget reboot once the response is sent.

        On any failure before the reboot is scheduled, the marker file is
        removed so the device stays a fresh server (retry is safe).
        """
        from backend.hardware.registry import AUDIO_CARDS, is_dac_card

        if payload.audio_id not in AUDIO_CARDS or payload.audio_id == "none":
            valid = [k for k in AUDIO_CARDS if k != "none"]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid audio_id '{payload.audio_id}'. Must be one of: {', '.join(valid)}",
            )

        async with _become_client_lock:
            if not FIRST_BOOT_HELPER.is_file():
                logger.error(
                    "become-client: %s is absent — this unit was not provisioned "
                    "from the Milo image and cannot apply a client role at boot",
                    FIRST_BOOT_HELPER,
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"This device cannot be adopted over WiFi: {FIRST_BOOT_HELPER} "
                        "is not installed, so the client role would never be applied. "
                        "Flash the Milo image on this device."
                    ),
                )

            already_done = await settings_service.get_setting("setup_completed")
            if already_done:
                raise HTTPException(
                    status_code=409,
                    detail="Device already configured (setup_completed=true)",
                )

            card = AUDIO_CARDS[payload.audio_id]
            overlay = card.get("overlay") or ""
            volume_control = not is_dac_card(payload.audio_id)

            marker = {
                "audio_id": payload.audio_id,
                "overlay": overlay,
                "volume_control": volume_control,
                "speaker_name": payload.speaker_name,
                "speaker_type": payload.speaker_type,
            }

            try:
                MILO_DATA_DIR.mkdir(parents=True, exist_ok=True)
                _atomic_write_json(PENDING_CLIENT_ROLE_FILE, marker)
                logger.info(
                    "become-client: pending_client_role.json written (audio=%s, name=%s, type=%s)",
                    payload.audio_id, payload.speaker_name, payload.speaker_type,
                )
            except OSError as e:
                logger.error("become-client: failed to write marker file: %s", e)
                raise HTTPException(status_code=500, detail=f"Failed to write client role marker: {e}")

            try:
                await network_service.save_network(
                    payload.wifi_ssid,
                    payload.wifi_password if payload.wifi_password else None,
                )
                logger.info("become-client: wifi profile saved for SSID '%s'", payload.wifi_ssid)
            except Exception as e:
                logger.error("become-client: failed to save wifi profile for '%s': %s", payload.wifi_ssid, e)
                try:
                    PENDING_CLIENT_ROLE_FILE.unlink()
                except OSError as unlink_err:
                    logger.debug(
                        "Pending client role file unlink failed (already absent?): %s",
                        unlink_err,
                    )
                raise HTTPException(status_code=500, detail=f"Failed to save WiFi profile: {e}")

            if not await settings_service.set_setting("setup_completed", True):
                logger.error("become-client: failed to persist setup_completed=true")
                try:
                    PENDING_CLIENT_ROLE_FILE.unlink()
                except OSError as unlink_err:
                    logger.debug(
                        "Pending client role file unlink failed (already absent?): %s",
                        unlink_err,
                    )
                try:
                    await network_service.forget_network(payload.wifi_ssid)
                except Exception as e:
                    logger.warning("become-client: failed to roll back wifi profile for '%s': %s", payload.wifi_ssid, e)
                raise HTTPException(status_code=500, detail="Failed to persist setup_completed flag")
            logger.info("become-client: setup_completed=true persisted")

            # 1s delay lets the HTTP response flush before the box reboots.
            background_tasks.add_task(systemd_manager.power, "reboot", 1.0)
            return {"status": "rebooting"}

    return router
