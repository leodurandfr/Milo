#!/usr/bin/env python3
"""
Milo Client - API Service for Client Snapclient Management and DSP Control
Version: 1.2 - With CamillaDSP integration for per-client EQ
"""

import asyncio
import aiohttp
import aiofiles
import re
import tempfile
import shutil
import logging
import os
import platform
import time
from pathlib import Path
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# Try to import CamillaDSP client
try:
    from camilladsp import CamillaClient
    CAMILLADSP_AVAILABLE = True
except ImportError:
    CAMILLADSP_AVAILABLE = False

# Basic configuration
SNAPCLIENT_VERSION_REGEX = r"v(\d+\.\d+\.\d+)"
GITHUB_REPO = "badaix/snapcast"
API_PORT = 8001
UPDATE_IN_PROGRESS = False

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(
    title="Milo Client API",
    description="API for Milo client management",
    version="1.0.0"
)

class SnapclientManager:
    """Manager for snapclient operations"""
    
    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.SnapclientManager")
    
    async def get_installed_version(self) -> Optional[str]:
        """Gets the installed version of snapclient"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "snapclient", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            output_text = stdout.decode() + stderr.decode()
            
            match = re.search(SNAPCLIENT_VERSION_REGEX, output_text)
            if match:
                return match.group(1)
                
            return None
            
        except (FileNotFoundError, asyncio.TimeoutError, Exception) as e:
            self.logger.error(f"Error getting snapclient version: {e}")
            return None

    async def get_latest_github_version(self) -> Optional[str]:
        """Gets the latest version from GitHub"""
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        tag_name = data.get("tag_name", "")

                        match = re.search(SNAPCLIENT_VERSION_REGEX, tag_name)
                        if match:
                            return match.group(1)

                        # Fallback: return tag_name without the 'v'
                        return tag_name.lstrip('v')

                    return None

        except Exception as e:
            self.logger.error(f"Error getting latest version from GitHub: {e}")
            return None

    async def _get_debian_codename(self) -> str:
        """Detects the system's Debian version (bookworm, trixie, etc.)"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", "source /etc/os-release && echo $VERSION_CODENAME",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, _ = await proc.communicate()
            codename = stdout.decode().strip()

            if codename:
                self.logger.info(f"Detected Debian codename: {codename}")
                return codename
            else:
                self.logger.warning("Could not detect Debian codename, using 'bookworm' as fallback")
                return "bookworm"

        except Exception as e:
            self.logger.error(f"Error detecting Debian codename: {e}, using 'bookworm' as fallback")
            return "bookworm"

    async def is_service_running(self) -> bool:
        """Checks if the snapclient service is running"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", "milo-client-snapclient.service",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, _ = await proc.communicate()
            return stdout.decode().strip() == "active"
            
        except Exception as e:
            self.logger.error(f"Error checking service status: {e}")
            return False
    
    async def update_snapclient(self, target_version: str) -> Dict[str, Any]:
        """Updates snapclient from GitHub with APT dependency resolution"""
        global UPDATE_IN_PROGRESS

        if UPDATE_IN_PROGRESS:
            return {"success": False, "error": "Update already in progress"}

        try:
            UPDATE_IN_PROGRESS = True
            self.logger.info(f"Starting snapclient update to version {target_version}")

            # Get current version before update
            old_version = await self.get_installed_version()

            # 1. Download the .deb from GitHub
            download_result = await self._download_snapclient_deb(target_version)
            if not download_result["success"]:
                return download_result

            # 2. Stop the service
            stop_result = await self._stop_snapclient_service()
            if not stop_result:
                return {"success": False, "error": "Failed to stop snapclient service"}

            # 3. Install the .deb with APT (which resolves dependencies automatically)
            install_result = await self._install_deb_with_apt(download_result["deb_path"])
            if not install_result["success"]:
                return install_result

            # 4. Restart the service
            start_result = await self._start_snapclient_service()
            if not start_result:
                return {"success": False, "error": "Failed to start snapclient service"}

            # 5. Verify the update
            await asyncio.sleep(3)  # Wait for the service to stabilize
            new_version = await self.get_installed_version()

            if new_version == target_version:
                self.logger.info(f"Snapclient successfully updated from {old_version} to {new_version}")
                return {
                    "success": True,
                    "message": f"Snapclient updated successfully",
                    "old_version": old_version,
                    "new_version": new_version
                }
            else:
                return {
                    "success": False,
                    "error": f"Version mismatch after update: expected {target_version}, got {new_version}"
                }

        except Exception as e:
            self.logger.error(f"Update failed: {e}")
            return {"success": False, "error": str(e)}

        finally:
            UPDATE_IN_PROGRESS = False
            # Clean up temporary files
            if 'download_result' in locals() and download_result.get("temp_dir"):
                shutil.rmtree(download_result["temp_dir"], ignore_errors=True)

    async def _download_snapclient_deb(self, version: str) -> Dict[str, Any]:
        """Downloads the snapclient .deb package from GitHub with auto Debian detection"""
        try:
            # Detect Debian version
            debian_codename = await self._get_debian_codename()

            temp_dir = tempfile.mkdtemp()
            package_name = f"snapclient_{version}-1_arm64_{debian_codename}.deb"
            url = f"https://github.com/{GITHUB_REPO}/releases/download/v{version}/{package_name}"

            deb_path = Path(temp_dir) / package_name

            self.logger.info(f"Downloading {package_name} from GitHub (Debian {debian_codename})...")

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return {
                            "success": False,
                            "error": f"Download failed: HTTP {response.status}"
                        }

                    async with aiofiles.open(deb_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)

            return {
                "success": True,
                "deb_path": str(deb_path),
                "temp_dir": temp_dir
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _install_deb_with_apt(self, deb_path: str) -> Dict[str, Any]:
        """Installs a .deb package using the secure wrapper script"""
        try:
            self.logger.info(f"Installing {Path(deb_path).name} via secure wrapper...")

            proc = await asyncio.create_subprocess_exec(
                "sudo", "/usr/local/bin/milo-client-install-snapclient", deb_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                self.logger.info("Package installed successfully")
                return {"success": True}
            else:
                error_msg = stderr.decode().strip() or stdout.decode().strip()
                return {
                    "success": False,
                    "error": f"Installation failed: {error_msg}"
                }

        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _stop_snapclient_service(self) -> bool:
        """Stops the snapclient service"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "stop", "milo-client-snapclient.service",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            _, stderr = await proc.communicate()
            return proc.returncode == 0
            
        except Exception as e:
            self.logger.error(f"Failed to stop snapclient service: {e}")
            return False
    
    async def _start_snapclient_service(self) -> bool:
        """Starts the snapclient service"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "start", "milo-client-snapclient.service",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                return False
            
            # Wait for the service to be actually started
            await asyncio.sleep(2)
            
            # Check the status
            is_running = await self.is_service_running()
            return is_running
            
        except Exception as e:
            self.logger.error(f"Failed to start snapclient service: {e}")
            return False

# Global manager instance
snapclient_manager = SnapclientManager()


# === CamillaDSP Manager ===

class FilterUpdate(BaseModel):
    """Model for filter update request"""
    gain: float
    freq: Optional[float] = None
    q: Optional[float] = None


class DSPManager:
    """Manager for CamillaDSP operations"""

    def __init__(self, host: str = "127.0.0.1", port: int = 1234):
        self.logger = logging.getLogger(f"{__name__}.DSPManager")
        self.host = host
        self.port = port
        self._client = None
        self._connected = False

        # Cached state
        self._filters: List[Dict[str, Any]] = []
        self._compressor = {
            "enabled": False,
            "threshold": -20.0,
            "ratio": 4.0,
            "attack": 10.0,
            "release": 100.0,
            "makeup_gain": 0.0
        }
        self._loudness = {
            "enabled": False,
            "reference_level": 80,
            "high_boost": 5.0,
            "low_boost": 8.0
        }
        self._delay = {"left": 0.0, "right": 0.0}

    async def connect(self) -> bool:
        """Connect to local CamillaDSP"""
        if not CAMILLADSP_AVAILABLE:
            self.logger.warning("CamillaDSP client library not available")
            return False

        try:
            self._client = CamillaClient(self.host, self.port)
            await asyncio.get_event_loop().run_in_executor(
                None, self._client.connect
            )
            self._connected = True
            self.logger.info(f"Connected to CamillaDSP at {self.host}:{self.port}")
            return True
        except Exception as e:
            self.logger.warning(f"Failed to connect to CamillaDSP: {e}")
            self._connected = False
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Get DSP status"""
        if not self._connected:
            await self.connect()

        if not self._connected:
            return {"available": False, "message": "CamillaDSP not connected"}

        try:
            state = await asyncio.get_event_loop().run_in_executor(
                None, self._client.general.state
            )
            state_str = str(state).split('.')[-1].lower()

            return {
                "available": True,
                "state": state_str,
                "filters": await self.get_filters(),
                "compressor": self._compressor,
                "loudness": self._loudness,
                "delay": self._delay
            }
        except Exception as e:
            self.logger.error(f"Error getting DSP status: {e}")
            return {"available": False, "error": str(e)}

    async def _get_config(self) -> Optional[Dict[str, Any]]:
        """Get CamillaDSP config"""
        config = await asyncio.get_event_loop().run_in_executor(
            None, self._client.config.active
        )
        if config is None:
            config_path = await asyncio.get_event_loop().run_in_executor(
                None, self._client.config.file_path
            )
            if config_path:
                config = await asyncio.get_event_loop().run_in_executor(
                    None, lambda p=config_path: self._client.config.read_and_parse_file(p)
                )
        return config

    async def get_filters(self) -> List[Dict[str, Any]]:
        """Get current EQ filter configuration"""
        if not self._connected:
            return self._filters

        try:
            config = await self._get_config()
            if config and "filters" in config:
                self._filters = []
                for name, filter_data in config["filters"].items():
                    if not name.startswith("eq_band_"):
                        continue
                    params = filter_data.get("parameters", {})
                    self._filters.append({
                        "id": name,
                        "type": params.get("type", "Peaking"),
                        "freq": params.get("freq", 1000),
                        "gain": params.get("gain", 0),
                        "q": params.get("q", 1.0),
                        "enabled": True
                    })
                self._filters.sort(key=lambda f: f["id"])
            return self._filters
        except Exception as e:
            self.logger.error(f"Error getting filters: {e}")
            return self._filters

    async def set_filter(self, filter_id: str, gain: float,
                         freq: float = None, q: float = None) -> bool:
        """Update a filter band"""
        if not self._connected:
            return False

        try:
            config = await self._get_config()
            if not config:
                return False

            if "filters" not in config or filter_id not in config["filters"]:
                return False

            params = config["filters"][filter_id]["parameters"]
            params["gain"] = gain
            if freq is not None:
                params["freq"] = freq
            if q is not None:
                params["q"] = q

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )
            return True
        except Exception as e:
            self.logger.error(f"Error setting filter {filter_id}: {e}")
            return False

    async def set_compressor(self, enabled: bool = None, threshold: float = None,
                             ratio: float = None, attack: float = None,
                             release: float = None, makeup_gain: float = None) -> bool:
        """Update compressor settings"""
        if enabled is not None:
            self._compressor["enabled"] = enabled
        if threshold is not None:
            self._compressor["threshold"] = threshold
        if ratio is not None:
            self._compressor["ratio"] = ratio
        if attack is not None:
            self._compressor["attack"] = attack
        if release is not None:
            self._compressor["release"] = release
        if makeup_gain is not None:
            self._compressor["makeup_gain"] = makeup_gain

        if not self._connected:
            return True

        try:
            config = await self._get_config()
            if not config:
                return False

            if not config.get("processors"):
                config["processors"] = {}

            if self._compressor["enabled"]:
                config["processors"]["compressor"] = {
                    "type": "Compressor",
                    "parameters": {
                        "channels": 2,
                        "threshold": self._compressor["threshold"],
                        "factor": self._compressor["ratio"],
                        "attack": self._compressor["attack"] / 1000.0,
                        "release": self._compressor["release"] / 1000.0,
                        "makeup_gain": self._compressor["makeup_gain"]
                    }
                }
                self._add_processor_to_pipeline(config, "compressor")
            else:
                if "compressor" in config.get("processors", {}):
                    del config["processors"]["compressor"]
                self._remove_processor_from_pipeline(config, "compressor")

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )
            return True
        except Exception as e:
            self.logger.error(f"Error setting compressor: {e}")
            return False

    async def set_loudness(self, enabled: bool = None, reference_level: int = None,
                           high_boost: float = None, low_boost: float = None) -> bool:
        """Update loudness settings"""
        if enabled is not None:
            self._loudness["enabled"] = enabled
        if reference_level is not None:
            self._loudness["reference_level"] = reference_level
        if high_boost is not None:
            self._loudness["high_boost"] = high_boost
        if low_boost is not None:
            self._loudness["low_boost"] = low_boost

        if not self._connected:
            return True

        try:
            config = await self._get_config()
            if not config:
                return False

            if "filters" not in config:
                config["filters"] = {}

            if self._loudness["enabled"]:
                config["filters"]["loudness_low"] = {
                    "type": "Biquad",
                    "parameters": {
                        "type": "Lowshelf",
                        "freq": 100,
                        "gain": self._loudness["low_boost"],
                        "slope": 6.0
                    }
                }
                config["filters"]["loudness_high"] = {
                    "type": "Biquad",
                    "parameters": {
                        "type": "Highshelf",
                        "freq": 8000,
                        "gain": self._loudness["high_boost"],
                        "slope": 6.0
                    }
                }
                self._add_filter_to_pipeline(config, "loudness_low")
                self._add_filter_to_pipeline(config, "loudness_high")
            else:
                for name in ["loudness_low", "loudness_high"]:
                    if name in config.get("filters", {}):
                        del config["filters"][name]
                    self._remove_filter_from_pipeline(config, name)

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )
            return True
        except Exception as e:
            self.logger.error(f"Error setting loudness: {e}")
            return False

    async def set_delay(self, left: float = None, right: float = None) -> bool:
        """Set channel delay in milliseconds"""
        if left is not None:
            self._delay["left"] = max(0, min(50, left))
        if right is not None:
            self._delay["right"] = max(0, min(50, right))

        if not self._connected:
            return True

        try:
            config = await self._get_config()
            if not config:
                return False

            sample_rate = 48000

            if "filters" not in config:
                config["filters"] = {}

            if self._delay["left"] > 0:
                left_samples = int(self._delay["left"] * sample_rate / 1000)
                config["filters"]["delay_left"] = {
                    "type": "Delay",
                    "parameters": {"delay": left_samples, "unit": "samples"}
                }
                self._add_filter_to_pipeline(config, "delay_left", channels=[0])
            else:
                if "delay_left" in config.get("filters", {}):
                    del config["filters"]["delay_left"]
                self._remove_filter_from_pipeline(config, "delay_left")

            if self._delay["right"] > 0:
                right_samples = int(self._delay["right"] * sample_rate / 1000)
                config["filters"]["delay_right"] = {
                    "type": "Delay",
                    "parameters": {"delay": right_samples, "unit": "samples"}
                }
                self._add_filter_to_pipeline(config, "delay_right", channels=[1])
            else:
                if "delay_right" in config.get("filters", {}):
                    del config["filters"]["delay_right"]
                self._remove_filter_from_pipeline(config, "delay_right")

            await asyncio.get_event_loop().run_in_executor(
                None, lambda c=config: self._client.config.set_active(c)
            )
            return True
        except Exception as e:
            self.logger.error(f"Error setting delay: {e}")
            return False

    def _add_filter_to_pipeline(self, config: Dict, filter_name: str,
                                channels: List[int] = None) -> None:
        """Add a filter to the pipeline"""
        if "pipeline" not in config:
            config["pipeline"] = []

        if channels is None:
            channels = [0, 1]

        for channel in channels:
            for step in config["pipeline"]:
                if step.get("type") == "Filter" and channel in step.get("channels", []):
                    if filter_name not in step.get("names", []):
                        step["names"].append(filter_name)
                    return

    def _remove_filter_from_pipeline(self, config: Dict, filter_name: str) -> None:
        """Remove a filter from the pipeline"""
        if "pipeline" not in config:
            return
        for step in config["pipeline"]:
            if step.get("type") == "Filter" and "names" in step:
                if filter_name in step["names"]:
                    step["names"].remove(filter_name)

    def _add_processor_to_pipeline(self, config: Dict, processor_name: str) -> None:
        """Add a processor to the pipeline"""
        if "pipeline" not in config:
            config["pipeline"] = []
        for step in config["pipeline"]:
            if step.get("type") == "Processor" and step.get("name") == processor_name:
                return
        config["pipeline"].append({"type": "Processor", "name": processor_name})

    def _remove_processor_from_pipeline(self, config: Dict, processor_name: str) -> None:
        """Remove a processor from the pipeline"""
        if "pipeline" not in config:
            return
        config["pipeline"] = [
            step for step in config["pipeline"]
            if not (step.get("type") == "Processor" and step.get("name") == processor_name)
        ]


# Global DSP manager instance
dsp_manager = DSPManager()

def get_system_uptime() -> int:
    """Gets the system uptime in seconds"""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
            return int(uptime_seconds)
    except Exception:
        return 0

def get_hostname() -> str:
    """Gets the system hostname"""
    return platform.node()

# API Routes

@app.get("/health")
async def health_check():
    """Basic health endpoint"""
    return {
        "status": "healthy",
        "timestamp": int(time.time()),
        "hostname": get_hostname()
    }

@app.get("/status")
async def get_status():
    """Gets the complete client status"""
    try:
        hostname = get_hostname()
        uptime = get_system_uptime()
        snapclient_version = await snapclient_manager.get_installed_version()
        snapclient_running = await snapclient_manager.is_service_running()
        
        return {
            "hostname": hostname,
            "uptime": uptime,
            "snapclient": {
                "version": snapclient_version,
                "running": snapclient_running,
                "status": "running" if snapclient_running else "stopped"
            },
            "update_in_progress": UPDATE_IN_PROGRESS,
            "timestamp": int(time.time())
        }
        
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/version")
async def get_version():
    """Gets only the snapclient version"""
    try:
        version = await snapclient_manager.get_installed_version()
        
        if version:
            return {
                "version": version,
                "timestamp": int(time.time())
            }
        else:
            return {
                "version": None,
                "error": "Could not determine snapclient version",
                "timestamp": int(time.time())
            }
            
    except Exception as e:
        logger.error(f"Error getting version: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/update")
async def update_snapclient(background_tasks: BackgroundTasks):
    """Starts the snapclient update from GitHub"""
    global UPDATE_IN_PROGRESS

    if UPDATE_IN_PROGRESS:
        raise HTTPException(status_code=409, detail="Update already in progress")

    try:
        # Get the latest available version on GitHub
        latest_version = await snapclient_manager.get_latest_github_version()
        if not latest_version:
            raise HTTPException(status_code=500, detail="Could not determine latest version")

        # Check if an update is needed
        current_version = await snapclient_manager.get_installed_version()
        if current_version == latest_version:
            return {
                "success": False,
                "message": "Already up to date",
                "current_version": current_version,
                "latest_version": latest_version
            }

        # Start the update in background
        async def do_update():
            result = await snapclient_manager.update_snapclient(latest_version)
            logger.info(f"Update completed: {result}")

        background_tasks.add_task(do_update)

        return {
            "success": True,
            "message": f"Update started: {current_version} -> {latest_version}",
            "current_version": current_version,
            "target_version": latest_version
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting update: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/update/status")
async def get_update_status():
    """Gets the status of the ongoing update"""
    return {
        "update_in_progress": UPDATE_IN_PROGRESS,
        "timestamp": int(time.time())
    }


# === DSP API Routes ===

@app.get("/dsp/status")
async def get_dsp_status():
    """Get DSP status and filter configuration"""
    try:
        status = await dsp_manager.get_status()
        return status
    except Exception as e:
        logger.error(f"Error getting DSP status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dsp/filters")
async def get_dsp_filters():
    """Get current EQ filter configuration"""
    try:
        filters = await dsp_manager.get_filters()
        return {"filters": filters}
    except Exception as e:
        logger.error(f"Error getting filters: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/dsp/filter/{filter_id}")
async def update_dsp_filter(filter_id: str, update: FilterUpdate):
    """Update a single EQ filter band"""
    try:
        success = await dsp_manager.set_filter(
            filter_id=filter_id,
            gain=update.gain,
            freq=update.freq,
            q=update.q
        )
        if success:
            return {"status": "success", "filter_id": filter_id}
        else:
            raise HTTPException(status_code=400, detail="Failed to update filter")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating filter: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/dsp/reset")
async def reset_dsp_filters():
    """Reset all EQ filters to flat (0 dB gain)"""
    try:
        filters = await dsp_manager.get_filters()
        for f in filters:
            await dsp_manager.set_filter(f["id"], gain=0.0)
        return {"status": "success", "message": "All filters reset to flat"}
    except Exception as e:
        logger.error(f"Error resetting filters: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dsp/compressor")
async def get_compressor():
    """Get compressor settings"""
    return dsp_manager._compressor


@app.put("/dsp/compressor")
async def update_compressor(
    enabled: bool = None,
    threshold: float = None,
    ratio: float = None,
    attack: float = None,
    release: float = None,
    makeup_gain: float = None
):
    """Update compressor settings"""
    try:
        success = await dsp_manager.set_compressor(
            enabled=enabled,
            threshold=threshold,
            ratio=ratio,
            attack=attack,
            release=release,
            makeup_gain=makeup_gain
        )
        if success:
            return {"status": "success", **dsp_manager._compressor}
        else:
            raise HTTPException(status_code=400, detail="Failed to update compressor")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating compressor: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dsp/loudness")
async def get_loudness():
    """Get loudness compensation settings"""
    return dsp_manager._loudness


@app.put("/dsp/loudness")
async def update_loudness(
    enabled: bool = None,
    reference_level: int = None,
    high_boost: float = None,
    low_boost: float = None
):
    """Update loudness compensation settings"""
    try:
        success = await dsp_manager.set_loudness(
            enabled=enabled,
            reference_level=reference_level,
            high_boost=high_boost,
            low_boost=low_boost
        )
        if success:
            return {"status": "success", **dsp_manager._loudness}
        else:
            raise HTTPException(status_code=400, detail="Failed to update loudness")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating loudness: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dsp/delay")
async def get_delay():
    """Get channel delay settings"""
    return dsp_manager._delay


@app.put("/dsp/delay")
async def update_delay(left: float = None, right: float = None):
    """Update channel delay settings"""
    try:
        success = await dsp_manager.set_delay(left=left, right=right)
        if success:
            return {"status": "success", **dsp_manager._delay}
        else:
            raise HTTPException(status_code=400, detail="Failed to update delay")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating delay: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Main entry point
if __name__ == "__main__":
    logger.info(f"Starting Milo Client API on port {API_PORT}")
    logger.info(f"Hostname: {get_hostname()}")
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=API_PORT,
        log_level="info",
        access_log=True
    )