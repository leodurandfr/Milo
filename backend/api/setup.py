# backend/api/setup.py
"""
Setup wizard API routes — first-boot configuration.

POST /api/setup/complete → wizard completion (language + hardware + setup_completed, or
                           setup_awaiting_network over the recovery AP until the first link)
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
from typing import TYPE_CHECKING, Optional
from pydantic import BaseModel, Field, field_validator

from backend.api.models import validate_configurable_audio_id
from backend.api.programs import refuse_while_milo_updates
from backend.config.constants import MILO_DATA_DIR
from backend.core.multiroom.models import SpeakerType

if TYPE_CHECKING:
    from backend.core.network.service import NetworkService
    from backend.core.settings import SettingsService
    from backend.core.systemd import SystemdServiceManager
    from backend.core.updates.update import UpdateService
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
    volume_control: bool = Field(..., description="Whether CamillaDSP attenuates (False: a DAC's amplifier owns the volume)")
    screen_type: str = Field(..., description="Screen type registry ID")

    @field_validator("audio_id")
    @classmethod
    def validate_audio_id(cls, v: str) -> str:
        return validate_configurable_audio_id(v)


class BecomeClientRequest(BaseModel):
    """Wifi adoption payload pushed by a server to a fresh device."""
    wifi_ssid: str = Field(..., min_length=1, description="Target WiFi SSID the device must join after reboot")
    wifi_password: str = Field(default="", description="Target WiFi password (empty for open networks)")
    audio_id: str = Field(..., description="Audio card registry ID")
    speaker_name: str = Field(..., min_length=1, max_length=64, description="Display name for the speaker")
    speaker_type: SpeakerType = Field(..., description="Speaker physical type")

    @field_validator("audio_id")
    @classmethod
    def validate_audio_id(cls, v: str) -> str:
        return validate_configurable_audio_id(v)


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
    systemd_manager: "SystemdServiceManager",
    update_service: Optional["UpdateService"] = None,
):
    """Create setup wizard router with injected services."""
    router = APIRouter(prefix="/api/setup", tags=["setup"])

    # A wizard completion in flight, from the route's first line to the reboot.
    # Left set once the reboot is on its way: over the AP the unit is not set up
    # yet (below), so `setup_completed` cannot guard the seconds before it goes
    # down.
    completing = False

    async def _has_a_link() -> bool:
        """Whether the unit is linked to a network now (`NetworkStatus.linked`,
        the rule the link listener below is called on) — its first connection,
        if the wizard was run on it. A status nmcli could not read is no link:
        the listener confirms the setup when one comes up."""
        try:
            status = await network_service.get_network_status()
        except (asyncio.TimeoutError, OSError) as e:
            logger.warning(f"Setup wizard: network status unreadable, counted as no link: {e!r}")
            return False
        return status.linked

    async def _confirm_on_first_link() -> None:
        """The unit gained a link: if the wizard's choices wait for it, it is set up.

        Over the recovery AP the wizard saves the home network without joining it
        (`NetworkService.save_network`), so nothing has validated the key when it
        reboots. Counted as set up then, a mistyped key left a unit whose AP
        serves set-up rules: no wizard, and the key could not be typed again.
        A unit that never got through the wizard is left alone: an Ethernet
        cable on a fresh unit is a link too, and its wizard stays.
        """
        if not await settings_service.get_setting("setup_awaiting_network"):
            return
        # One write: set up and still waiting, torn apart, would skip role
        # detection at every boot of a unit counted as set up.
        if not await settings_service.set_settings(
            {"setup_completed": True, "setup_awaiting_network": False}
        ):
            logger.error("Setup wizard: the first link came up, but setup_completed could not be written")
            return
        logger.info("Setup wizard: the unit joined its network — setup completed")

    network_service.add_link_listener(_confirm_on_first_link)

    async def _reboot() -> None:
        """The reboot, after the response. One that does not happen releases the
        submit, so the wizard on screen can be sent again."""
        nonlocal completing
        try:
            rebooting = await systemd_manager.power("reboot", 1.0)
        except Exception as e:
            logger.error(f"Setup wizard: reboot failed: {e}")
            rebooting = False
        if rebooting is False:
            completing = False

    @router.post("/complete")
    async def complete_setup(payload: SetupCompleteRequest, background_tasks: BackgroundTasks):
        """
        Wizard completion: set language, save and apply the hardware config, mark setup, reboot.

        The apply runs within the request, so a refusal answers 500 with the
        helper's reason and leaves the previous hardware config, the wizard on
        screen. The unit counts as set up once it has a link: at once when it
        has one now, else at the first one after the reboot (over the recovery
        AP, where the network was saved, not joined) — until then the wizard
        comes back, on its Network step. A submit arriving while another is in
        flight, or once setup is complete, answers "rebooting" and applies
        nothing.
        """
        nonlocal completing
        from backend.hardware.registry import AUDIO_CARDS, SCREENS, audio_config, reserved_pins
        from backend.core.settings import VALID_LANGUAGES

        refuse_while_milo_updates(update_service, "setup completion")

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

            if payload.screen_type not in SCREENS:
                raise HTTPException(status_code=400, detail=f"Unknown screen type: {payload.screen_type}")

            # 1. Set language
            if not await settings_service.set_setting("language", payload.language):
                raise RuntimeError("Failed to persist language setting")
            logger.info(f"Setup wizard: language set to {payload.language}")

            # 2. The hardware config
            card = AUDIO_CARDS[payload.audio_id]
            screen = SCREENS[payload.screen_type]

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
                "audio": audio_config(payload.audio_id, payload.volume_control),
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

            # 3. Write config.txt, THEN mark the setup, THEN reboot.
            #
            # The order is the whole point and it is not the obvious one. The
            # flag used to be persisted before the overlay reached config.txt —
            # so a power cut in that window (the user pulls the plug because
            # "it is taking a while") left a unit that considered itself
            # configured, booting with no dtoverlay: no sound, no wizard, no
            # message anywhere. Applying first makes every cut before the flag
            # bring the wizard back, and a cut after it lands on a config.txt
            # that is already correct. The Hardware page's transaction, the
            # previous config put back on a refusal.
            # A refusal reaches the handler below: 500, the helper's reason.
            await hardware_service.replace_config(config)
            logger.info(f"Setup wizard: hardware applied (audio={payload.audio_id}, screen={payload.screen_type})")

            # Linked now, the network the wizard showed is the one it has: set
            # up. Else the network was saved over the AP, its key untried: the
            # first link after the reboot sets the unit up, and until then the
            # wizard comes back.
            if await _has_a_link():
                if not await settings_service.set_settings(
                    {"setup_completed": True, "setup_awaiting_network": False}
                ):
                    raise RuntimeError("Failed to persist setup_completed")
                logger.info("Setup wizard: setup_completed set to true")
            else:
                if not await settings_service.set_setting("setup_awaiting_network", True):
                    raise RuntimeError("Failed to persist setup_awaiting_network")
                logger.info("Setup wizard: no link yet — set up once the unit joins its network")

            # Only the reboot is deferred, so the response reaches the browser first.
            background_tasks.add_task(_reboot)
            scheduled = True

            return {"status": "rebooting"}

        except HTTPException:
            raise
        except Exception as e:
            # No rollback of `setup_completed`: it is written last, after
            # config.txt has been rewritten, so a failure before it leaves it
            # false and the wizard reappears on its own.
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
        from backend.hardware.registry import audio_config

        refuse_while_milo_updates(update_service, "client adoption")
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
            # Its wizard ran, as a server, and it waits for its first link to
            # count as set up (`complete_setup`): not a fresh unit to adopt.
            if await settings_service.get_setting("setup_awaiting_network"):
                raise HTTPException(
                    status_code=409,
                    detail="Device already configured as a server (setup waits for its first link)",
                )

            audio = audio_config(payload.audio_id)
            marker = {
                "audio_id": audio["id"],
                "overlay": audio["overlay"],
                "volume_control": audio["volume_control"],
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
