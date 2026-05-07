"""
Hardware configuration routes for Milo Client.

Provides endpoints for reading/writing hardware.json and triggering
the apply-hardware script (config.txt update + reboot).
"""
import asyncio
import json
import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator, model_validator

logger = logging.getLogger(__name__)

HARDWARE_FILE = "/var/lib/milo-client/hardware.json"
MILO_SETTINGS_FILE = "/var/lib/milo/settings.json"
APPLY_HARDWARE_SCRIPT = "/usr/local/bin/milo-client-apply-hardware"

# Must match the allowlist in milo-client-apply-hardware
VALID_OVERLAYS = {
    "hifiberry-dacplus-std",
    "hifiberry-amp4pro",
    "hifiberry-amp100",
    "hifiberry-dac",
    "hifiberry-dacplushd",
    "hifiberry-dacplus",
}


class AudioUpdate(BaseModel):
    """Request body for PUT /api/hardware/audio."""
    audio_id: str
    overlay: str = ""
    volume_control: bool = True  # False for DAC cards (external amp manages volume)

    @field_validator("overlay")
    @classmethod
    def validate_overlay(cls, v):
        if v and v not in VALID_OVERLAYS:
            raise ValueError(f"Unknown overlay '{v}'. Valid: {sorted(VALID_OVERLAYS)}")
        return v

    @model_validator(mode="after")
    def validate_overlay_required(self):
        if self.audio_id != "none" and not self.overlay:
            raise ValueError("overlay is required when audio_id is not 'none'")
        return self


def _read_hardware_json() -> dict:
    """Read hardware.json, returning defaults if missing or invalid."""
    try:
        with open(HARDWARE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"audio": {"id": "none"}}


def _write_hardware_json(data: dict) -> None:
    """Atomic write to hardware.json."""
    tmp_path = HARDWARE_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, HARDWARE_FILE)


def _set_setup_completed_in_milo_settings() -> None:
    """Mark setup as completed in /var/lib/milo/settings.json (read by milo-first-boot)."""
    try:
        with open(MILO_SETTINGS_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data["setup_completed"] = True
    tmp_path = MILO_SETTINGS_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, MILO_SETTINGS_FILE)


def create_hardware_router() -> APIRouter:
    """Creates hardware router for client-side hardware management."""
    router = APIRouter(prefix="/api/hardware", tags=["hardware"])

    @router.get("")
    async def get_hardware():
        """Returns current hardware.json content."""
        return _read_hardware_json()

    @router.put("/audio")
    async def set_audio(request: AudioUpdate):
        """
        Update the audio card configuration.

        Writes hardware.json with the provided audio_id and overlay.
        Does NOT reboot — call POST /api/reboot separately.
        """
        try:
            config = _read_hardware_json()

            if request.audio_id == "none":
                config["audio"] = {"id": "none"}
            else:
                config["audio"] = {
                    "id": request.audio_id,
                    "overlay": request.overlay,
                    "volume_control": request.volume_control,
                }

            _write_hardware_json(config)
            logger.info(f"Hardware config updated: audio_id={request.audio_id}")
            return {"status": "success", "audio": config["audio"]}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error writing hardware config: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/reboot")
    async def reboot():
        """
        Apply hardware configuration and reboot.

        Runs milo-client-apply-hardware which modifies config.txt
        and reboots the system.
        """
        try:
            # Lock the role: milo-first-boot will skip the mDNS probe on subsequent
            # boots once setup_completed=true, preventing accidental client→server reverts.
            try:
                _set_setup_completed_in_milo_settings()
                logger.info("Marked setup_completed=true in /var/lib/milo/settings.json")
            except Exception as e:
                logger.error(f"Failed to mark setup_completed in milo settings: {e}")

            proc = await asyncio.create_subprocess_exec(
                "sudo", APPLY_HARDWARE_SCRIPT,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            # Wait briefly to catch immediate failures (bad script, permission denied, etc.)
            # If the process is still running after 2s, it's proceeding to reboot
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
                # Process exited within 2s — check if it failed before reaching reboot.
                # Negative returncode means killed by signal (e.g. system reboot) — not an error.
                if proc.returncode is not None and proc.returncode > 0:
                    stderr = await proc.stderr.read()
                    error_msg = stderr.decode().strip() or f"Exit code {proc.returncode}"
                    logger.error(f"Apply-hardware script failed: {error_msg}")
                    raise HTTPException(status_code=500, detail=f"Reboot script failed: {error_msg}")
            except asyncio.TimeoutError:
                # Still running after 2s — reboot is in progress, this is expected
                pass

            logger.info("Reboot triggered via milo-client-apply-hardware")
            return {"status": "success", "message": "Rebooting..."}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error triggering reboot: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
