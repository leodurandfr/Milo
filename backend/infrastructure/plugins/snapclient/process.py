"""
Gestion du processus snapclient - Version optimisée.
"""
import os
import asyncio
import logging
from typing import Optional, Dict, Any

class SnapclientProcess:
    """Gère le processus snapclient."""
    
    def __init__(self, executable_path: str = "/usr/bin/snapclient"):
        self.executable_path = executable_path
        self.process: Optional[asyncio.subprocess.Process] = None
        self.logger = logging.getLogger("plugin.snapclient.process")
    
    async def check_executable(self) -> bool:
        """Vérifie que l'exécutable snapclient existe."""
        return os.path.isfile(self.executable_path) and os.access(self.executable_path, os.X_OK)
    
    async def start(self, host: Optional[str] = None) -> bool:
        """Démarre le processus snapclient."""
        try:
            if self.process:
                await self.stop()
            
            cmd = [self.executable_path, "-j", "-s", "1"]
            if host:
                cmd.extend(["-h", host])
            
            self.logger.info(f"Démarrage: {' '.join(cmd)}")
            
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            if self.process.returncode is not None:
                return False
            
            await asyncio.sleep(0.5)
            if self.process.returncode is not None:
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur démarrage: {str(e)}")
            return False
    
    async def stop(self) -> bool:
        """Arrête le processus snapclient."""
        if not self.process:
            return True
        
        try:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                self.process.kill()
            
            try:
                await asyncio.create_subprocess_shell("killall -9 snapclient 2>/dev/null || true")
            except Exception:
                pass
            
            self.process = None
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur arrêt: {str(e)}")
            self.process = None
            return True
    
    async def get_process_info(self) -> Dict[str, Any]:
        """Récupère des informations sur le processus en cours."""
        if not self.process:
            return {"running": False}
        
        try:
            if self.process.returncode is not None:
                return {"running": False, "returncode": self.process.returncode}
            
            try:
                process = await asyncio.create_subprocess_exec(
                    "ps", "-p", str(self.process.pid), "-o", "pid=",
                    stdout=asyncio.subprocess.PIPE
                )
                stdout, _ = await process.communicate()
                
                if not stdout.strip():
                    self.process = None
                    return {"running": False, "reason": "process_not_found"}
            except Exception:
                pass
            
            return {"running": True, "pid": self.process.pid}
            
        except Exception as e:
            return {"running": False, "error": str(e)}
    
    async def restart(self, host: Optional[str] = None) -> bool:
        """Redémarre le processus snapclient."""
        await self.stop()
        return await self.start(host)