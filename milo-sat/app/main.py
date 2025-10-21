#!/usr/bin/env python3
"""
Milo Sat - API Service for Satellite Snapclient Management
Version: 1.0
"""

import asyncio
import re
import logging
import os
import platform
import time
from typing import Dict, Any, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import uvicorn

# Configuration de base
SNAPCLIENT_VERSION_REGEX = r"v(\d+\.\d+\.\d+)"
API_PORT = 8001
UPDATE_IN_PROGRESS = False

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(
    title="Milo Sat API",
    description="API pour la gestion des satellites Milo",
    version="1.0.0"
)

class SnapclientManager:
    """Gestionnaire pour les opérations snapclient"""
    
    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.SnapclientManager")
    
    async def get_installed_version(self) -> Optional[str]:
        """Récupère la version installée de snapclient"""
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
    
    async def is_service_running(self) -> bool:
        """Vérifie si le service snapclient est en cours d'exécution"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", "milo-sat-snapclient.service",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, _ = await proc.communicate()
            return stdout.decode().strip() == "active"
            
        except Exception as e:
            self.logger.error(f"Error checking service status: {e}")
            return False
    
    async def update_snapclient(self) -> Dict[str, Any]:
        """Met à jour snapclient via les dépôts APT"""
        global UPDATE_IN_PROGRESS

        if UPDATE_IN_PROGRESS:
            return {"success": False, "error": "Update already in progress"}

        try:
            UPDATE_IN_PROGRESS = True
            self.logger.info("Starting snapclient update from APT repositories")

            # Récupérer la version actuelle avant mise à jour
            old_version = await self.get_installed_version()

            # 1. Arrêter le service
            stop_result = await self._stop_snapclient_service()
            if not stop_result:
                return {"success": False, "error": "Failed to stop snapclient service"}

            # 2. Mettre à jour via APT
            update_result = await self._update_from_apt()
            if not update_result["success"]:
                return update_result

            # 3. Redémarrer le service
            start_result = await self._start_snapclient_service()
            if not start_result:
                return {"success": False, "error": "Failed to start snapclient service"}

            # 4. Vérifier la mise à jour
            await asyncio.sleep(3)  # Attendre que le service soit stable
            new_version = await self.get_installed_version()

            self.logger.info(f"Snapclient updated from {old_version} to {new_version}")
            return {
                "success": True,
                "message": f"Snapclient updated successfully",
                "old_version": old_version,
                "new_version": new_version
            }

        except Exception as e:
            self.logger.error(f"Update failed: {e}")
            return {"success": False, "error": str(e)}

        finally:
            UPDATE_IN_PROGRESS = False
    
    
    async def _update_from_apt(self) -> Dict[str, Any]:
        """Met à jour snapclient via les dépôts APT"""
        try:
            env = {
                "DEBIAN_FRONTEND": "noninteractive",
                "DEBCONF_NONINTERACTIVE_SEEN": "true",
                "APT_LISTCHANGES_FRONTEND": "none"
            }

            # 1. Mettre à jour la liste des paquets
            self.logger.info("Updating APT package list...")
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-E", "apt", "update",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **env}
            )

            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                return {
                    "success": False,
                    "error": f"APT update failed: {stderr.decode()}"
                }

            # 2. Installer/Mettre à jour snapclient
            self.logger.info("Installing/updating snapclient from APT repositories...")
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-E", "apt", "install", "-y",
                "-o", "Dpkg::Options::=--force-confdef",
                "-o", "Dpkg::Options::=--force-confnew",
                "snapclient",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **env}
            )

            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                return {"success": True}
            else:
                return {
                    "success": False,
                    "error": f"APT install failed: {stderr.decode()}"
                }

        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _stop_snapclient_service(self) -> bool:
        """Arrête le service snapclient"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "stop", "milo-sat-snapclient.service",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            _, stderr = await proc.communicate()
            return proc.returncode == 0
            
        except Exception as e:
            self.logger.error(f"Failed to stop snapclient service: {e}")
            return False
    
    async def _start_snapclient_service(self) -> bool:
        """Démarre le service snapclient"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "start", "milo-sat-snapclient.service",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                return False
            
            # Attendre que le service soit vraiment démarré
            await asyncio.sleep(2)
            
            # Vérifier l'état
            is_running = await self.is_service_running()
            return is_running
            
        except Exception as e:
            self.logger.error(f"Failed to start snapclient service: {e}")
            return False

# Instance globale du gestionnaire
snapclient_manager = SnapclientManager()

def get_system_uptime() -> int:
    """Récupère l'uptime du système en secondes"""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
            return int(uptime_seconds)
    except Exception:
        return 0

def get_hostname() -> str:
    """Récupère le hostname du système"""
    return platform.node()

# Routes API

@app.get("/health")
async def health_check():
    """Endpoint de santé basique"""
    return {
        "status": "healthy",
        "timestamp": int(time.time()),
        "hostname": get_hostname()
    }

@app.get("/status")
async def get_status():
    """Récupère le statut complet du satellite"""
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
    """Récupère uniquement la version de snapclient"""
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
    """Lance la mise à jour de snapclient via APT"""
    global UPDATE_IN_PROGRESS

    if UPDATE_IN_PROGRESS:
        raise HTTPException(status_code=409, detail="Update already in progress")

    try:
        # Récupérer la version actuelle
        current_version = await snapclient_manager.get_installed_version()

        # Lancer la mise à jour en arrière-plan
        async def do_update():
            result = await snapclient_manager.update_snapclient()
            logger.info(f"Update completed: {result}")

        background_tasks.add_task(do_update)

        return {
            "success": True,
            "message": f"Update started from APT repositories",
            "current_version": current_version
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error starting update: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/update/status")
async def get_update_status():
    """Récupère le statut de la mise à jour en cours"""
    return {
        "update_in_progress": UPDATE_IN_PROGRESS,
        "timestamp": int(time.time())
    }

# Point d'entrée principal
if __name__ == "__main__":
    logger.info(f"Starting Milo Sat API on port {API_PORT}")
    logger.info(f"Hostname: {get_hostname()}")
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=API_PORT,
        log_level="info",
        access_log=True
    )