"""
Hardware configuration routes for Milo Client.

Provides endpoints for reading/writing hardware.json and triggering
the apply-hardware script (config.txt update + reboot). The card is a setting,
open to the LAN like the others; the reboot answers only this unit's server.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator, model_validator

from atomic_file import write_atomic
from server_secret import require_server_secret
from services import registration

logger = logging.getLogger(__name__)

APPLY_HARDWARE_SCRIPT = "/usr/local/bin/milo-client-apply-hardware"

# Exactly the overlay set of backend/hardware/registry.py::AUDIO_CARDS, restated
# here because the satellite has no access to it — and pinned by
# backend/tests/architecture/test_audio_overlay_allowlist.py, along with the same
# list in /usr/local/lib/milo/hardware-helpers.sh that the apply script reads.
VALID_OVERLAYS = {
    "hifiberry-dacplus-std",
    "hifiberry-dacplus-pro",
    "hifiberry-dacplusadcpro",
    "hifiberry-amp4pro",
    "hifiberry-amp100",
    "hifiberry-dacplushd",
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
        with open(registration.HARDWARE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"audio": {"id": "none"}}


def _write_hardware_json(data: dict) -> None:
    """Atomic write to hardware.json, which the launcher and the apply helper read."""
    write_atomic(registration.HARDWARE_FILE, json.dumps(data, indent=2))


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
        Does NOT reboot — call POST /api/hardware/reboot separately.
        """
        config = _read_hardware_json()

        if request.audio_id == "none":
            config["audio"] = {"id": "none"}
        else:
            config["audio"] = {
                "id": request.audio_id,
                "overlay": request.overlay,
                "volume_control": request.volume_control,
            }

        try:
            _write_hardware_json(config)
        except OSError as e:
            logger.error(f"Hardware config not written: {e}")
            raise HTTPException(status_code=500, detail=f"hardware.json not written: {e}") from e
        logger.info(f"Hardware config updated: audio_id={request.audio_id}")
        return {"status": "success", "audio": config["audio"]}

    @router.post("/reboot", dependencies=[Depends(require_server_secret)])
    async def reboot():
        """
        Apply hardware configuration and reboot.

        Runs milo-client-apply-hardware which modifies config.txt
        and reboots the system. Only for this unit's server
        (`server_secret.py`): for a speaker still waiting to be configured,
        which no admission has reached, this is the request that pins it.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", APPLY_HARDWARE_SCRIPT,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        except OSError as e:
            logger.error(f"Apply-hardware script not started: {e}")
            raise HTTPException(status_code=500, detail=f"Reboot script not started: {e}") from e
        # Wait briefly to catch immediate failures (bad script, permission denied, etc.)
        # If the process is still running after 2s, it's proceeding to reboot.
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            # Still running after 2s — reboot is in progress, this is expected
            pass
        else:
            # Exited within 2s — check if it failed before reaching reboot.
            # Negative returncode means killed by signal (e.g. system reboot) — not an error.
            if proc.returncode is not None and proc.returncode > 0:
                stderr = await proc.stderr.read()
                error_msg = stderr.decode().strip() or f"Exit code {proc.returncode}"
                logger.error(f"Apply-hardware script failed: {error_msg}")
                raise HTTPException(status_code=500, detail=f"Reboot script failed: {error_msg}")

        logger.info("Reboot triggered via milo-client-apply-hardware")
        return {"status": "success", "message": "Rebooting..."}

    return router
