# backend/api/setup.py
"""
Setup wizard API routes — first-boot configuration.

POST /api/setup/complete → atomic wizard completion (language + hardware + setup_completed)
"""
import asyncio
import logging
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Services disabled when mode is "client" (server-only services)
CLIENT_DISABLED_SERVICES = [
    "milo-spotify",
    "milo-airplay",
    "milo-radio",
    "milo-podcast",
    "milo-bluealsa",
    "milo-bluealsa-aplay",
    "milo-mac",
    "milo-snapserver-multiroom",
    "milo-kiosk",
]


class SetupCompleteRequest(BaseModel):
    """Wizard completion payload — all settings applied atomically."""
    mode: Literal["server", "client"] = Field(..., description="Device mode: server or client")
    language: str = Field(..., description="Language key")
    audio_id: str = Field(..., description="Audio card registry ID")
    screen_type: str = Field(..., description="Screen type registry ID")


def create_setup_router(settings_service, hardware_service, systemd_manager):
    """Create setup wizard router with injected services."""
    router = APIRouter(prefix="/api/setup", tags=["setup"])

    @router.post("/complete")
    async def complete_setup(payload: SetupCompleteRequest):
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

        is_client = payload.mode == "client"

        # Validate audio card and screen (server only — client uses defaults)
        if not is_client:
            if payload.audio_id not in AUDIO_CARDS:
                raise HTTPException(status_code=400, detail=f"Unknown audio card: {payload.audio_id}")
            if payload.screen_type not in SCREENS:
                raise HTTPException(status_code=400, detail=f"Unknown screen type: {payload.screen_type}")

        try:
            # 1. Set language
            if not await settings_service.set_setting("language", payload.language):
                raise RuntimeError("Failed to persist language setting")
            logger.info(f"Setup wizard: language set to {payload.language}")

            # 2. Save mode
            if not await settings_service.set_setting("mode", payload.mode):
                raise RuntimeError("Failed to persist mode setting")
            logger.info(f"Setup wizard: mode set to {payload.mode}")

            # 3. Save hardware config
            if is_client:
                # Client mode: no audio card, no screen
                config = {
                    "audio": {"id": "none"},
                    "screen": {"type": "none", "resolution": None},
                    "rotary_encoder": hardware_service.get_full_config().get("rotary_encoder", {
                        "clk_pin": 22,
                        "dt_pin": 27,
                        "sw_pin": 23,
                    }),
                }
            else:
                # Server mode: full hardware config
                card = AUDIO_CARDS[payload.audio_id]
                screen = SCREENS[payload.screen_type]

                audio_config = {"id": payload.audio_id}
                if card["overlay"]:
                    audio_config.update({
                        "card_name": card["card_name"],
                        "alsa_control": card["alsa_control"],
                        "overlay": card["overlay"],
                    })

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
            logger.info(f"Setup wizard: hardware saved (mode={payload.mode}, audio={payload.audio_id}, screen={payload.screen_type})")

            # 4. Client-specific: set hostname and disable server services
            if is_client:
                await _configure_client_mode(systemd_manager)

            # 5. Mark setup as completed
            if not await settings_service.set_setting("setup_completed", True):
                raise RuntimeError("Failed to persist setup_completed flag")
            logger.info("Setup wizard: setup_completed set to true")

            # 6. Apply config.txt changes and reboot (fire-and-forget with short delay)
            async def _delayed_apply():
                await asyncio.sleep(1)  # Allow HTTP response to be sent
                try:
                    await hardware_service.apply_and_reboot()
                except Exception as e:
                    logger.error(f"Setup wizard: hardware apply/reboot failed: {e}")

            asyncio.create_task(_delayed_apply())

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

    return router


async def _configure_client_mode(systemd_manager):
    """Configure the system for client mode: set hostname and disable server services."""
    # Set hostname to "milo-client" for mDNS discovery
    if not await systemd_manager.set_hostname("milo-client"):
        raise RuntimeError("Failed to set hostname to milo-client")
    logger.info("Setup wizard: hostname set to milo-client")

    # Disable server-only services (they won't start on next boot)
    for service in CLIENT_DISABLED_SERVICES:
        success = await systemd_manager.disable(service)
        if success:
            logger.info(f"Setup wizard: disabled {service}")
        else:
            logger.warning(f"Setup wizard: failed to disable {service} (may not exist)")
