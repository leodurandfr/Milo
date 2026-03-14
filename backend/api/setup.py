# backend/api/setup.py
"""
Setup wizard API routes — first-boot configuration.

POST /api/setup/complete → atomic wizard completion (language + hardware + setup_completed)
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SetupCompleteRequest(BaseModel):
    """Wizard completion payload — all settings applied atomically."""
    language: str = Field(..., description="Language key")
    audio_id: str = Field(..., description="Audio card registry ID")
    screen_type: str = Field(..., description="Screen type registry ID")


def create_setup_router(settings_service, hardware_service):
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

        # Validate audio card
        if payload.audio_id not in AUDIO_CARDS:
            raise HTTPException(status_code=400, detail=f"Unknown audio card: {payload.audio_id}")

        # Validate screen type
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

            # 4. Apply config.txt changes and reboot (fire-and-forget with short delay)
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
