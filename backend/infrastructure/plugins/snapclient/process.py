async def get_server_name(self, host: str) -> str:
        """
        Essaie de récupérer le nom convivial d'un serveur à partir de son adresse IP.
        
        Args:
            host: Adresse IP du serveur
            
        Returns:
            str: Nom convivial du serveur, ou adresse IP si non trouvé
        """
        try:
            import socket
            hostname = socket.gethostbyaddr(host)
            if hostname and hostname[0]:
                name = hostname[0]
                # Nettoyer le nom (supprimer les suffixes de domaine)
                name = name.replace('.local', '').replace('.home', '')
                return name
        except Exception as e:
            self.logger.debug(f"Erreur lors de la récupération du nom du serveur pour {host}: {str(e)}")
        
        return f"Snapserver ({host})""""
Gestion du processus snapclient.
"""
import os
import asyncio
import logging
import subprocess
from typing import Optional, Dict, Any, List


class SnapclientProcess:
    """
    Gère le processus snapclient, y compris son démarrage, son arrêt et sa surveillance.
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
        self.extra_args = []
    
    async def check_executable(self) -> bool:
        """
        Vérifie que l'exécutable snapclient existe.
        
        Returns:
            bool: True si l'exécutable existe, False sinon
        """
        return os.path.isfile(self.executable_path) and os.access(self.executable_path, os.X_OK)
    
    async def start(self, host: Optional[str] = None, extra_args: Optional[List[str]] = None) -> bool:
        """
        Démarre le processus snapclient.
        
        Args:
            host: Hôte auquel se connecter (optionnel)
            extra_args: Arguments supplémentaires pour snapclient
            
        Returns:
            bool: True si le démarrage a réussi, False sinon
        """
        try:
            # Arrêter le processus existant si nécessaire
            if self.process:
                await self.stop()
            
            # Construire la commande - IMPORTANT : retirer --daemon pour garder le contrôle du processus
            cmd = [self.executable_path, "-j"]
            
            if host:
                cmd.extend(["-h", host])
                
            if extra_args:
                cmd.extend(extra_args)
                self.extra_args = extra_args
            elif self.extra_args:
                cmd.extend(self.extra_args)
            
            # Ajouter des paramètres pour l'audio
            if "-s" not in cmd:
                cmd.extend(["-s", "1"])  # Utiliser le périphérique ALSA par défaut
                
            # Démarrer le processus
            self.logger.info(f"Démarrage du processus snapclient: {' '.join(cmd)}")
            
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Vérifier que le processus a démarré
            if self.process.returncode is not None:
                self.logger.error(f"Échec du démarrage du processus snapclient, code de retour: {self.process.returncode}")
                return False
            
            self.logger.info(f"Processus snapclient démarré avec PID {self.process.pid}")
            
            # Attendre un court moment pour vérifier que le processus est stable
            try:
                await asyncio.sleep(1)
                if self.process.returncode is not None:
                    self.logger.error(f"Le processus snapclient s'est arrêté prématurément, code: {self.process.returncode}")
                    return False
            except Exception as check_e:
                self.logger.warning(f"Erreur lors de la vérification du processus: {str(check_e)}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage du processus snapclient: {str(e)}")
            return False
    
    async def stop(self) -> bool:
        """
        Arrête le processus snapclient et s'assure qu'aucun autre processus snapclient
        ne reste en cours d'exécution.
        
        Returns:
            bool: True si l'arrêt a réussi, False sinon
        """
        try:
            if not self.process:
                self.logger.info("Aucun processus snapclient à arrêter")
                return True
                
            self.logger.info(f"Arrêt du processus snapclient (PID {self.process.pid})")
            
            # Tenter d'abord un arrêt propre
            try:
                await self._terminate_process_gracefully()
            except Exception as e:
                self.logger.warning(f"Échec de l'arrêt propre: {str(e)}")
            
            # Nettoyer tous les processus snapclient restants
            await self._cleanup_all_snapclient_processes()
            
            # Réinitialiser l'état
            self.process = None
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt du processus snapclient: {str(e)}")
            self.process = None
            return True  # Retourner True pour ne pas bloquer l'utilisateur

    async def _terminate_process_gracefully(self):
        """Tente d'arrêter proprement le processus avec SIGTERM."""
        import signal
        import os
        
        try:
            os.kill(self.process.pid, signal.SIGTERM)
            self.logger.info(f"Signal SIGTERM envoyé au processus {self.process.pid}")
            
            # Attendre brièvement que le processus se termine
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
                self.logger.info(f"Processus {self.process.pid} terminé normalement")
            except (asyncio.TimeoutError, ProcessLookupError):
                self.logger.warning(f"Le processus ne s'est pas arrêté proprement, utilisation de SIGKILL")
                try:
                    os.kill(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except ProcessLookupError:
            self.logger.info(f"Le processus {self.process.pid} n'existe plus")

    async def _cleanup_all_snapclient_processes(self):
        """S'assure qu'aucun processus snapclient ne reste en cours d'exécution."""
        try:
            self.logger.info("Nettoyage des processus snapclient")
            
            # Tuer tous les processus snapclient
            kill_process = await asyncio.create_subprocess_exec(
                "killall", "-9", "snapclient",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await kill_process.wait()
            
            # Vérifier que tout est bien arrêté
            check_process = await asyncio.create_subprocess_exec(
                "pgrep", "snapclient",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await check_process.communicate()
            
            if stdout.strip():
                self.logger.warning(f"Des processus snapclient persistent après killall: {stdout.decode().strip()}")
                # Deuxième tentative avec killall
                await asyncio.create_subprocess_exec("killall", "-9", "snapclient")
            else:
                self.logger.info("Tous les processus snapclient ont été arrêtés")
        except Exception as e:
            self.logger.error(f"Erreur lors du nettoyage des processus: {str(e)}")
    
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
            
            # Vérification supplémentaire avec ps pour s'assurer que le processus existe toujours
            try:
                # Utiliser ps pour vérifier si le processus existe
                process = await asyncio.create_subprocess_exec(
                    "ps", "-p", str(self.process.pid), "-o", "pid=",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await process.communicate()
                
                if not stdout.strip():
                    self.logger.warning(f"Le processus {self.process.pid} n'existe plus selon ps")
                    # Marquer le processus comme terminé
                    self.process = None
                    return {"running": False, "reason": "process_not_found"}
            except Exception as ps_e:
                self.logger.warning(f"Erreur lors de la vérification ps: {str(ps_e)}")
            
            return {
                "running": True,
                "pid": self.process.pid
            }
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération des informations sur le processus: {str(e)}")
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