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
                self.logger.info(f"Connexion explicite à l'hôte: {host}:{port}")
                args.extend(["-h", host, "-p", str(port)])
            else:
                self.logger.info("Aucun hôte spécifié, snapclient cherchera automatiquement des serveurs")
            
            # Afficher la commande complète dans les logs
            self.logger.info(f"Commande complète: {' '.join(args)}")
            
            # Démarrer le processus en arrière-plan
            self.logger.info(f"Démarrage de snapclient")
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
        Arrête le processus snapclient de façon robuste.
        
        Returns:
            bool: True si l'arrêt a réussi, False sinon
        """
        if not self.process:
            self.logger.debug("Aucun processus snapclient à arrêter")
            
            # S'assurer qu'aucun processus snapclient n'est en cours d'exécution
            try:
                # Vérifier si des processus snapclient sont en cours d'exécution
                result = subprocess.run(["pgrep", "snapclient"], stdout=subprocess.PIPE)
                if result.returncode == 0:
                    # Des processus snapclient existent encore, les tuer
                    self.logger.warning("Processus snapclient orphelins détectés, nettoyage")
                    subprocess.run(["pkill", "-9", "snapclient"], check=False)
                    await asyncio.sleep(0.5)
            except Exception as e:
                self.logger.error(f"Erreur lors du nettoyage des processus: {str(e)}")
                
            return True
        
        try:
            # Vérifier si le processus est toujours en cours
            if self.process.poll() is None:
                self.logger.info(f"Arrêt du processus snapclient (PID: {self.process.pid})")
                
                # Enregistrer le PID pour contrôle ultérieur
                pid = self.process.pid
                
                # Essayer d'abord un arrêt propre
                self.process.terminate()
                
                # Attendre que le processus se termine (max 2 secondes)
                try:
                    self.logger.debug("Attente de la terminaison (timeout: 2s)")
                    self.process.wait(timeout=2)
                    self.logger.info("Processus snapclient arrêté proprement")
                except subprocess.TimeoutExpired:
                    # Si le processus ne se termine pas, le forcer
                    self.logger.warning("Le processus snapclient ne répond pas, utilisation de kill")
                    self.process.kill()
                    await asyncio.sleep(0.5)
                    
                    # Vérifier si le processus est toujours en cours
                    if self.process.poll() is None:
                        # Utiliser pkill pour être sûr
                        self.logger.error(f"Impossible d'arrêter le processus snapclient avec kill, utilisation de pkill")
                        subprocess.run(["pkill", "-9", "snapclient"], check=False)
                        await asyncio.sleep(0.5)
                
                # Tuer tous les processus enfants également pour être sûr
                try:
                    subprocess.run(["pkill", "-9", "-P", str(pid)], check=False)
                except Exception as e:
                    self.logger.error(f"Erreur lors du kill des processus enfants: {str(e)}")
                
                # Effectuer une vérification finale pour s'assurer que tous les processus snapclient sont arrêtés
                try:
                    result = subprocess.run(["pgrep", "snapclient"], stdout=subprocess.PIPE)
                    if result.returncode == 0:
                        self.logger.warning("Processus snapclient encore détectés, nettoyage final")
                        subprocess.run(["pkill", "-9", "snapclient"], check=False)
                        await asyncio.sleep(0.5)
                except Exception as e:
                    self.logger.error(f"Erreur lors du nettoyage final: {str(e)}")
                
                # Nettoyer la référence
                self.process = None
                return True
            else:
                # Le processus est déjà terminé
                self.logger.info("Le processus snapclient était déjà terminé")
                self.process = None
                
                # Vérifier s'il existe d'autres processus snapclient
                try:
                    result = subprocess.run(["pgrep", "snapclient"], stdout=subprocess.PIPE)
                    if result.returncode == 0:
                        self.logger.warning("Autres processus snapclient détectés, nettoyage")
                        subprocess.run(["pkill", "-9", "snapclient"], check=False)
                        await asyncio.sleep(0.5)
                except Exception as e:
                    self.logger.error(f"Erreur lors du nettoyage final: {str(e)}")
                
                return True
                    
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt du processus snapclient: {str(e)}")
            
            # En cas d'erreur, tenter de tuer tous les processus snapclient
            try:
                self.logger.warning("Tentative de nettoyage brutal de tous les processus snapclient")
                subprocess.run(["pkill", "-9", "snapclient"], check=False)
                await asyncio.sleep(0.5)
            except Exception as e:
                self.logger.error(f"Erreur lors du nettoyage brutal: {str(e)}")
            
            # Nettoyer la référence même en cas d'erreur
            self.process = None
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
        Récupère la sortie du processus snapclient de façon plus robuste.
        
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
                try:
                    flags = fcntl.fcntl(self.process.stdout, fcntl.F_GETFL)
                    fcntl.fcntl(self.process.stdout, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                    
                    try:
                        stdout_data = self.process.stdout.read(4096) or b""
                    except (BlockingIOError, ValueError):
                        pass
                except Exception as e:
                    self.logger.error(f"Erreur lors de la lecture de stdout: {str(e)}")
            
            if self.process.stderr:
                try:
                    flags = fcntl.fcntl(self.process.stderr, fcntl.F_GETFL)
                    fcntl.fcntl(self.process.stderr, fcntl.F_SETFL, flags | os.O_NONBLOCK)
                    
                    try:
                        stderr_data = self.process.stderr.read(4096) or b""
                    except (BlockingIOError, ValueError):
                        pass
                except Exception as e:
                    self.logger.error(f"Erreur lors de la lecture de stderr: {str(e)}")
            
            # Vérifier si le process existe encore pour éviter des erreurs
            if self.process.poll() is not None:
                self.logger.debug(f"Processus terminé avec code: {self.process.returncode}")
                
                # Si le processus s'est terminé, récupérer tout ce qui reste
                try:
                    remaining_stdout, remaining_stderr = self.process.communicate(timeout=0.1)
                    stdout_data += remaining_stdout
                    stderr_data += remaining_stderr
                except:
                    pass
            
            return (
                stdout_data.decode('utf-8', errors='replace'),
                stderr_data.decode('utf-8', errors='replace')
            )
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération de la sortie du processus: {str(e)}")
            
            # En cas d'erreur, vérifier si le processus s'est terminé
            if self.process and self.process.poll() is not None:
                return ("", f"Process terminated with code {self.process.returncode}")
                
            return ("", f"Error: {str(e)}")
    
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