"""
Gestion du processus snapclient - Version optimisée.
"""
import os
import asyncio
import logging
from typing import Optional, Dict, Any


class SnapclientProcess:
    """
    Gère le processus snapclient, y compris son démarrage, son arrêt et sa surveillance.
    Version simplifiée.
    """
    
    def __init__(self, executable_path: str = "/usr/bin/snapclient"):
        """
        Initialise le gestionnaire de processus.
        
        Args:
            executable_path: Chemin vers l'exécutable snapclient
        """
        self.executable_path = executable_path
        self.process: Optional[asyncio.subprocess.Process] = None
        self.logger = logging.getLogger("plugin.snapclient.process")
    
    async def check_executable(self) -> bool:
        """
        Vérifie que l'exécutable snapclient existe.
        
        Returns:
            bool: True si l'exécutable existe, False sinon
        """
        return os.path.isfile(self.executable_path) and os.access(self.executable_path, os.X_OK)
    
    async def start(self, host: Optional[str] = None) -> bool:
        """
        Démarre le processus snapclient.
        
        Args:
            host: Hôte auquel se connecter (optionnel)
            
        Returns:
            bool: True si le démarrage a réussi, False sinon
        """
        try:
            # Arrêter le processus existant si nécessaire
            if self.process:
                await self.stop()
            
            # Construire la commande
            cmd = [self.executable_path, "-j"]
            if host:
                cmd.extend(["-h", host])
            
            # Ajouter des paramètres pour l'audio
            cmd.extend(["-s", "1"])  # Utiliser le périphérique ALSA par défaut
            
            # Démarrer le processus
            self.logger.info(f"Démarrage du processus: {' '.join(cmd)}")
            
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Vérifier que le processus a démarré
            if self.process.returncode is not None:
                self.logger.error(f"Échec du démarrage, code: {self.process.returncode}")
                return False
            
            self.logger.info(f"Processus snapclient démarré avec PID {self.process.pid}")
            
            # Attendre un court moment pour vérifier la stabilité
            await asyncio.sleep(0.5)
            if self.process.returncode is not None:
                self.logger.error(f"Le processus s'est arrêté prématurément, code: {self.process.returncode}")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage: {str(e)}")
            return False
    
    async def stop(self) -> bool:
        """
        Arrête le processus snapclient.
        
        Returns:
            bool: True si l'arrêt a réussi, False sinon
        """
        if not self.process:
            return True
        
        try:
            self.logger.info(f"Arrêt du processus snapclient (PID {self.process.pid})")
            
            # Tenter d'abord un arrêt propre
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
                self.logger.info(f"Processus {self.process.pid} terminé normalement")
            except asyncio.TimeoutError:
                self.logger.warning(f"Le processus ne s'est pas arrêté proprement, utilisation de SIGKILL")
                self.process.kill()
            
            # Nettoyer tous les processus snapclient restants
            try:
                await asyncio.create_subprocess_shell(
                    "killall -9 snapclient 2>/dev/null || true"
                )
            except Exception as e:
                self.logger.debug(f"Erreur lors du nettoyage (ignorée): {str(e)}")
            
            self.process = None
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt: {str(e)}")
            self.process = None
            return True  # Toujours retourner True pour ne pas bloquer
    
    async def get_process_info(self) -> Dict[str, Any]:
        """
        Récupère des informations sur le processus en cours.
        
        Returns:
            Dict[str, Any]: Informations sur le processus
        """
        if not self.process:
            return {"running": False}
        
        try:
            # Vérifier si le processus est toujours en cours d'exécution
            if self.process.returncode is not None:
                return {
                    "running": False,
                    "returncode": self.process.returncode
                }
            
            # Vérification rapide avec ps
            try:
                process = await asyncio.create_subprocess_exec(
                    "ps", "-p", str(self.process.pid), "-o", "pid=",
                    stdout=asyncio.subprocess.PIPE
                )
                stdout, _ = await process.communicate()
                
                if not stdout.strip():
                    self.logger.warning(f"Le processus {self.process.pid} n'existe plus")
                    self.process = None
                    return {"running": False, "reason": "process_not_found"}
            except Exception:
                pass
            
            return {
                "running": True,
                "pid": self.process.pid
            }
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération des infos: {str(e)}")
            return {"running": False, "error": str(e)}
    
    async def restart(self, host: Optional[str] = None) -> bool:
        """
        Redémarre le processus snapclient.
        
        Args:
            host: Hôte auquel se connecter (optionnel)
            
        Returns:
            bool: True si le redémarrage a réussi, False sinon
        """
        await self.stop()
        return await self.start(host)