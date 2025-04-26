# backend/infrastructure/process/process_manager.py
"""
Gestionnaire centralisé des processus pour les plugins audio
"""
import asyncio
import subprocess
import logging
from typing import Dict, Any, Optional
from backend.domain.audio_state import AudioSource, PluginState


class ProcessManager:
    """Gère le cycle de vie des processus des plugins audio"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.processes: Dict[AudioSource, Optional[subprocess.Popen]] = {}
        self._locks: Dict[AudioSource, asyncio.Lock] = {}
    
    def _get_lock(self, source: AudioSource) -> asyncio.Lock:
        """Récupère le verrou pour une source"""
        if source not in self._locks:
            self._locks[source] = asyncio.Lock()
        return self._locks[source]
    
    async def start_process(self, source: AudioSource, command: list) -> bool:
        """Démarre un processus pour une source spécifique"""
        async with self._get_lock(source):
            try:
                # Arrêter le processus existant si nécessaire
                if source in self.processes and self.processes[source]:
                    await self.stop_process(source)
                
                # Démarrer le nouveau processus
                self.processes[source] = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                # Attendre un peu pour s'assurer que le processus démarre
                await asyncio.sleep(0.2)
                
                # Vérifier que le processus est toujours en cours
                if self.processes[source].poll() is None:
                    self.logger.info(f"Processus démarré pour {source.value}")
                    return True
                else:
                    self.logger.error(f"Le processus pour {source.value} s'est arrêté immédiatement")
                    return False
                    
            except Exception as e:
                self.logger.error(f"Erreur démarrage processus {source.value}: {e}")
                return False
    
    async def stop_process(self, source: AudioSource) -> bool:
        """Arrête un processus pour une source spécifique"""
        async with self._get_lock(source):
            try:
                process = self.processes.get(source)
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    
                    self.processes[source] = None
                    self.logger.info(f"Processus arrêté pour {source.value}")
                    return True
                return True  # Rien à arrêter
            except Exception as e:
                self.logger.error(f"Erreur arrêt processus {source.value}: {e}")
                return False
    
    async def stop_all_processes(self) -> None:
        """Arrête tous les processus actifs"""
        for source in list(self.processes.keys()):
            await self.stop_process(source)
    
    def is_process_running(self, source: AudioSource) -> bool:
        """Vérifie si un processus est en cours pour une source"""
        process = self.processes.get(source)
        return process is not None and process.poll() is None
    
    def get_process_info(self, source: AudioSource) -> Dict[str, Any]:
        """Récupère les informations sur un processus"""
        process = self.processes.get(source)
        if process:
            return {
                "running": process.poll() is None,
                "pid": process.pid if process.poll() is None else None
            }
        return {"running": False, "pid": None}