"""
Gestionnaire de processus pour le plugin snapclient.
"""
import asyncio
import logging
import os
import subprocess
from typing import Optional, Tuple, Dict, Any, List

class ProcessManager:
    """Gère le démarrage et l'arrêt du processus snapclient"""
    
    def __init__(self, executable_path: str = "/usr/bin/snapclient"):
        self.executable_path = os.path.expanduser(executable_path)
        self.process: Optional[subprocess.Popen] = None
        self.logger = logging.getLogger("snapclient.process")
        self.default_args = ["-j", "--logsink=stdout"]  # -j pour sortie JSON, logsink pour log dans stdout
    
    async def start_process(self, host: Optional[str] = None, port: int = 1704) -> bool:
        """
        Démarre le processus snapclient en arrière-plan.
        
        Args:
            host: Hôte du serveur snapcast (optionnel)
            port: Port du serveur snapcast (par défaut 1704)
            
        Returns:
            bool: True si le processus a démarré avec succès, False sinon
        """
        try:
            # Si un processus est déjà en cours, l'arrêter d'abord
            if self.process and self.process.poll() is None:
                self.logger.info("Un processus snapclient est déjà en cours, tentative d'arrêt")
                await self.stop_process()
            
            # Vérifier que l'exécutable existe
            if not os.path.isfile(self.executable_path):
                self.logger.error(f"L'exécutable snapclient n'existe pas: {self.executable_path}")
                return False
            
            # Préparer les arguments
            args = [self.executable_path] + self.default_args
            
            # Ajouter l'hôte s'il est spécifié
            if host:
                args.extend(["-h", host, "-p", str(port)])
            
            # Démarrer le processus en arrière-plan
            self.logger.info(f"Démarrage de snapclient: {' '.join(args)}")
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Attendre que le processus démarre
            for i in range(2):  # Attendre jusqu'à 2 secondes
                await asyncio.sleep(1)
                self.logger.debug(f"Vérification du processus snapclient ({i+1}/2)...")
                
                # Vérifier si le processus est toujours en cours
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    self.logger.error(f"Le processus snapclient s'est terminé prématurément: code {process.returncode}")
                    self.logger.error(f"Sortie standard: {stdout.decode('utf-8', errors='replace')}")
                    self.logger.error(f"Erreur standard: {stderr.decode('utf-8', errors='replace')}")
                    return False
                
                # Si on arrive ici, le processus est toujours en cours
                if i >= 1:  # Après 2 secondes, considérer comme démarré
                    self.process = process
                    self.logger.info(f"Processus snapclient démarré avec PID: {process.pid}")
                    return True
            
            # Si on arrive ici, le processus est démarré mais on n'a pas pu vérifier s'il fonctionne correctement
            self.process = process
            self.logger.warning("Processus snapclient démarré, mais sans confirmation de fonctionnement")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage du processus snapclient: {str(e)}")
            return False
    
    async def stop_process(self) -> bool:
        """
        Arrête le processus snapclient.
        
        Returns:
            bool: True si l'arrêt a réussi, False sinon
        """
        if not self.process:
            self.logger.debug("Aucun processus snapclient à arrêter")
            return True
            
        try:
            # Vérifier si le processus est toujours en cours
            if self.process.poll() is None:
                self.logger.info(f"Arrêt du processus snapclient (PID: {self.process.pid})")
                
                # Essayer d'abord un arrêt propre
                self.process.terminate()
                
                # Attendre que le processus se termine (max 5 secondes)
                try:
                    self.process.wait(timeout=5)
                    self.logger.info("Processus snapclient arrêté proprement")
                except subprocess.TimeoutExpired:
                    # Si le processus ne se termine pas, le forcer
                    self.logger.warning("Le processus snapclient ne répond pas, utilisation de kill")
                    self.process.kill()
                    await asyncio.sleep(1)
                
                # Vérifier le résultat
                if self.process.poll() is None:
                    self.logger.error("Impossible d'arrêter le processus snapclient")
                    return False
                else:
                    self.logger.info("Processus snapclient arrêté")
                    self.process = None
                    return True
            else:
                # Le processus est déjà terminé
                self.logger.info("Le processus snapclient était déjà terminé")
                self.process = None
                return True
                
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt du processus snapclient: {str(e)}")
            return False
    
    def is_running(self) -> bool:
        """
        Vérifie si le processus snapclient est en cours d'exécution.
        
        Returns:
            bool: True si le processus est en cours d'exécution
        """
        return self.process is not None and self.process.poll() is None
    
    async def restart_process(self, host: Optional[str] = None, port: int = 1704) -> bool:
        """
        Redémarre le processus snapclient.
        
        Args:
            host: Hôte du serveur snapcast (optionnel)
            port: Port du serveur snapcast (par défaut 1704)
            
        Returns:
            bool: True si le redémarrage a réussi, False sinon
        """
        await self.stop_process()
        return await self.start_process(host, port)
    
    def get_process_info(self) -> Dict[str, Any]:
        """
        Récupère des informations sur le processus snapclient.
        
        Returns:
            Dict[str, Any]: Informations sur le processus
        """
        if not self.process:
            return {"running": False, "pid": None}
            
        return {
            "running": self.process.poll() is None,
            "pid": self.process.pid,
            "returncode": self.process.returncode if self.process.poll() is not None else None
        }
    
    async def get_process_output(self) -> Tuple[str, str]:
        """
        Récupère la sortie du processus snapclient (stdout, stderr).
        Note: Cette méthode ne retourne que la sortie disponible sans bloquer.
        
        Returns:
            Tuple[str, str]: (stdout, stderr)
        """
        if not self.process:
            return ("", "")
            
        try:
            # Tenter de lire la sortie de manière non-bloquante
            import fcntl
            import os
            
            stdout_data = b""
            stderr_data = b""
            
            if self.process.stdout:
                flags = fcntl.fcntl(self.process.stdout, fcntl.F_GETFL)
                fcntl.fcntl(self.process.stdout, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                try:
                    stdout_data = self.process.stdout.read() or b""
                except (BlockingIOError, ValueError):
                    pass
            
            if self.process.stderr:
                flags = fcntl.fcntl(self.process.stderr, fcntl.F_GETFL)
                fcntl.fcntl(self.process.stderr, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                try:
                    stderr_data = self.process.stderr.read() or b""
                except (BlockingIOError, ValueError):
                    pass
            
            return (
                stdout_data.decode('utf-8', errors='replace'),
                stderr_data.decode('utf-8', errors='replace')
            )
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération de la sortie du processus: {str(e)}")
            return ("", f"Erreur: {str(e)}")
    
    async def get_active_connections(self) -> List[Dict[str, Any]]:
        """
        Obtient la liste des serveurs actuellement connectés.
        Ceci est une approximation car snapclient ne fournit pas d'API pour cette information.
        
        Returns:
            List[Dict[str, Any]]: Liste des connexions actives
        """
        # Dans une implémentation réelle, on pourrait analyser la sortie du processus
        # ou utiliser une API de snapclient si disponible
        if not self.is_running():
            return []
        
        # Pour l'instant, retourner une approximation basée sur les informations disponibles
        connections = []
        try:
            stdout, _ = await self.get_process_output()
            if "connected to" in stdout.lower():
                # Analyse simple de la sortie pour trouver des mentions de connexion
                lines = stdout.splitlines()
                for line in lines:
                    if "connected to" in line.lower():
                        # Extraire l'hôte de la ligne (ceci est une approximation)
                        parts = line.split("connected to")
                        if len(parts) >= 2:
                            host = parts[1].strip()
                            connections.append({
                                "host": host,
                                "connected": True,
                                "timestamp": None  # Pas d'information de timestamp disponible
                            })
        except Exception as e:
            self.logger.error(f"Erreur lors de l'obtention des connexions actives: {str(e)}")
        
        return connections