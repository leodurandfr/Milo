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
from fastapi import APIRouter, BackgroundTasks, HTTPException
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator

from backend.config.constants import MILO_DATA_DIR
from backend.core.multiroom.models import SPEAKER_TYPES

logger = logging.getLogger(__name__)

PENDING_CLIENT_ROLE_FILE = MILO_DATA_DIR / "pending_client_role.json"

# Serializes /api/setup/become-client so two concurrent adopters can't race on
# the marker write + wifi profile + setup_completed sequence.
_become_client_lock = asyncio.Lock()


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
    speaker_type: Literal['satellite', 'bookshelf', 'tower', 'subwoofer'] = Field(..., description="Speaker physical type")

    @field_validator('speaker_type')
    @classmethod
    def validate_speaker_type(cls, v):
        if v not in SPEAKER_TYPES:
            raise ValueError(f"Invalid speaker_type '{v}'. Must be one of: {', '.join(SPEAKER_TYPES)}")
        return v


def _atomic_write_json(path, data: dict) -> None:
    """Write JSON to ``path`` atomically (tempfile + fsync + rename)."""
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def create_setup_router(settings_service, hardware_service, systemd_manager, network_service):
    """Create setup wizard router with injected services."""
    router = APIRouter(prefix="/api/setup", tags=["setup"])

    @router.post("/complete")
    async def complete_setup(payload: SetupCompleteRequest, background_tasks: BackgroundTasks):
        """
        Atomic wizard completion: set language, save hardware config, mark setup complete, reboot.

        On failure: rolls back setup_completed to false so the wizard reappears.
        """
        from backend.hardware.registry import AUDIO_CARDS, SCREENS
        from backend.core.settings import VALID_LANGUAGES

        # Idempotency guard — prevent double-submit triggering two reboots
        already_done = await settings_service.get_setting("setup_completed")
        if already_done:
            return {"status": "rebooting"}

        # Validate language
        if payload.language not in VALID_LANGUAGES:
            raise HTTPException(status_code=400, detail=f"Invalid language: {payload.language}")

        # Validate audio card and screen
        if payload.audio_id not in AUDIO_CARDS:
            raise HTTPException(status_code=400, detail=f"Unknown audio card: {payload.audio_id}")
        if payload.screen_type not in SCREENS:
            raise HTTPException(status_code=400, detail=f"Unknown screen type: {payload.screen_type}")

        try:
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
                    "alsa_control": card["alsa_control"],
                    "overlay": card["overlay"],
                })
            if payload.volume_control is not None:
                audio_config["volume_control"] = payload.volume_control

            config = {
                "audio": audio_config,
                "screen": {
                    "type": payload.screen_type,
                    "resolution": screen["resolution"],
                },
                "rotary_encoder": hardware_service.get_full_config().get("rotary_encoder", {
                    "clk_pin": 22,
                    "dt_pin": 27,
                    "sw_pin": 23,
                }),
            }

            await hardware_service.save_config(config)
            logger.info(f"Setup wizard: hardware saved (audio={payload.audio_id}, screen={payload.screen_type})")

            # 3. Mark setup as completed
            if not await settings_service.set_setting("setup_completed", True):
                raise RuntimeError("Failed to persist setup_completed flag")
            logger.info("Setup wizard: setup_completed set to true")

            # 4. Apply config.txt changes and reboot (after HTTP response is sent)
            async def _delayed_apply():
                await asyncio.sleep(1)  # Allow HTTP response to flush to the client
                try:
                    await hardware_service.apply_and_reboot()
                except Exception as e:
                    logger.error(f"Setup wizard: hardware apply/reboot failed: {e}")

            background_tasks.add_task(_delayed_apply)

            return {"status": "rebooting"}

        except HTTPException:
            raise
        except Exception as e:
            # Rollback: ensure wizard reappears on next boot
            logger.error(f"Setup wizard failed: {e}")
            rolled_back = await settings_service.set_setting("setup_completed", False)
            if rolled_back:
                logger.info("Setup wizard: rolled back setup_completed to false")
            else:
                logger.error("Setup wizard: rollback FAILED — setup_completed may remain true")
            raise HTTPException(status_code=500, detail=f"Setup failed: {e}")

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
                except OSError:
                    pass
                raise HTTPException(status_code=500, detail=f"Failed to save WiFi profile: {e}")

            if not await settings_service.set_setting("setup_completed", True):
                logger.error("become-client: failed to persist setup_completed=true")
                try:
                    PENDING_CLIENT_ROLE_FILE.unlink()
                except OSError:
                    pass
                try:
                    await network_service.forget_network(payload.wifi_ssid)
                except Exception as e:
                    logger.warning("become-client: failed to roll back wifi profile for '%s': %s", payload.wifi_ssid, e)
                raise HTTPException(status_code=500, detail="Failed to persist setup_completed flag")
            logger.info("become-client: setup_completed=true persisted")

            async def _delayed_reboot():
                await asyncio.sleep(1)
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "sudo", "/usr/sbin/reboot",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await proc.communicate()
                    if proc.returncode is not None and proc.returncode > 0:
                        logger.error(
                            "become-client: reboot failed (rc=%d): %s",
                            proc.returncode, stderr.decode().strip() if stderr else "",
                        )
                except Exception as e:
                    logger.error("become-client: reboot subprocess failed: %s", e)

            background_tasks.add_task(_delayed_reboot)
            return {"status": "rebooting"}

    return router
