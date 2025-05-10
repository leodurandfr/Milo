"""
Gestionnaire des services systemd pour le plugin Bluetooth
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List

class BluetoothServiceManager:
    """
    Gère les services systemd liés au Bluetooth:
    - bluetooth.service
    - bluealsa.service
    - bluealsa-aplay@<address>.service
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    async def is_service_active(self, service_name: str) -> bool:
        """Vérifie si un service systemd est actif"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await proc.communicate()
            return stdout.decode().strip() == "active"
        except Exception as e:
            self.logger.error(f"Erreur vérification service {service_name}: {e}")
            return False
    
    async def start_service(self, service_name: str, timeout: float = 5.0) -> bool:
        """Démarre un service systemd"""
        try:
            self.logger.info(f"Démarrage du service {service_name}")
            
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "start", service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                self.logger.error(f"Échec du démarrage {service_name}: {stderr.decode().strip()}")
                return False
            
            # Attendre que le service soit actif
            start_time = asyncio.get_event_loop().time()
            while (asyncio.get_event_loop().time() - start_time) < timeout:
                if await self.is_service_active(service_name):
                    self.logger.info(f"Service {service_name} démarré avec succès")
                    return True
                await asyncio.sleep(0.2)
            
            self.logger.warning(f"Timeout lors du démarrage de {service_name}")
            return False
        except Exception as e:
            self.logger.error(f"Erreur démarrage service {service_name}: {e}")
            return False
    
    async def stop_service(self, service_name: str) -> bool:
        """Arrête un service systemd"""
        try:
            self.logger.info(f"Arrêt du service {service_name}")
            
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "stop", service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                self.logger.error(f"Échec de l'arrêt {service_name}: {stderr.decode().strip()}")
                return False
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur arrêt service {service_name}: {e}")
            return False
    
    async def restart_service(self, service_name: str, timeout: float = 5.0) -> bool:
        """Redémarre un service systemd"""
        try:
            self.logger.info(f"Redémarrage du service {service_name}")
            
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "restart", service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                self.logger.error(f"Échec du redémarrage {service_name}: {stderr.decode().strip()}")
                return False
            
            # Attendre que le service soit actif
            start_time = asyncio.get_event_loop().time()
            while (asyncio.get_event_loop().time() - start_time) < timeout:
                if await self.is_service_active(service_name):
                    self.logger.info(f"Service {service_name} redémarré avec succès")
                    return True
                await asyncio.sleep(0.2)
            
            self.logger.warning(f"Timeout lors du redémarrage de {service_name}")
            return False
        except Exception as e:
            self.logger.error(f"Erreur redémarrage service {service_name}: {e}")
            return False
    
    # Services bluetooth
    
    async def start_bluetooth(self) -> bool:
        """Démarre le service bluetooth"""
        return await self.start_service("bluetooth.service")
    
    async def stop_bluetooth(self) -> bool:
        """Arrête le service bluetooth"""
        return await self.stop_service("bluetooth.service")
    
    async def restart_bluetooth(self) -> bool:
        """Redémarre le service bluetooth"""
        return await self.restart_service("bluetooth.service")
    
    async def is_bluetooth_running(self) -> bool:
        """Vérifie si le service bluetooth est en cours d'exécution"""
        return await self.is_service_active("bluetooth.service")
    
    # Services BlueALSA
    
    async def start_bluealsa(self, options: str = "") -> bool:
        """Démarre le service bluealsa avec les options spécifiées"""
        # Configurer le service avec les options spécifiées
        if options:
            try:
                # Créer un fichier de remplacement pour le service
                env_dir = "/etc/systemd/system/bluealsa.service.d"
                env_file = f"{env_dir}/override.conf"
                
                # Créer le répertoire si nécessaire
                await asyncio.create_subprocess_exec(
                    "sudo", "mkdir", "-p", env_dir,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                
                # Écrire le fichier de configuration
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "tee", env_file,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                
                content = f"[Service]\nEnvironment=\"BLUEALSA_ARGS=-p a2dp-sink {options}\"\n"
                await proc.communicate(input=content.encode())
                
                # Recharger les configurations systemd
                await asyncio.create_subprocess_exec(
                    "sudo", "systemctl", "daemon-reload",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
            except Exception as e:
                self.logger.error(f"Erreur configuration bluealsa: {e}")
        
        # Démarrer le service
        return await self.start_service("bluealsa.service")
    
    async def stop_bluealsa(self) -> bool:
        """Arrête le service bluealsa"""
        return await self.stop_service("bluealsa.service")
    
    async def restart_bluealsa(self, options: str = "") -> bool:
        """Redémarre le service bluealsa avec les options spécifiées"""
        # Configurer le service avec les options spécifiées
        if options:
            try:
                # Créer un fichier de remplacement pour le service
                env_dir = "/etc/systemd/system/bluealsa.service.d"
                env_file = f"{env_dir}/override.conf"
                
                # Créer le répertoire si nécessaire
                await asyncio.create_subprocess_exec(
                    "sudo", "mkdir", "-p", env_dir,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                
                # Écrire le fichier de configuration
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "tee", env_file,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                
                content = f"[Service]\nEnvironment=\"BLUEALSA_ARGS=-p a2dp-sink {options}\"\n"
                await proc.communicate(input=content.encode())
                
                # Recharger les configurations systemd
                await asyncio.create_subprocess_exec(
                    "sudo", "systemctl", "daemon-reload",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
            except Exception as e:
                self.logger.error(f"Erreur configuration bluealsa: {e}")
        
        # Redémarrer le service
        return await self.restart_service("bluealsa.service")
    
    async def is_bluealsa_running(self) -> bool:
        """Vérifie si le service bluealsa est en cours d'exécution"""
        return await self.is_service_active("bluealsa.service")
    
    # Services bluealsa-aplay
    
    async def start_audio_playback(self, address: str) -> bool:
        """Démarre la lecture audio pour un appareil spécifique"""
        try:
            service_name = f"bluealsa-aplay@{address}.service"
            self.logger.info(f"Démarrage de la lecture audio pour {address} via {service_name}")
            
            # Vérifier si le service existe avant de le démarrer
            proc_check = await asyncio.create_subprocess_exec(
                "systemctl", "cat", service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc_check.communicate()
            
            if proc_check.returncode != 0:
                # Le service n'existe pas, essayer de le démarrer en ligne de commande
                self.logger.warning(f"Service {service_name} non trouvé, tentative avec bluealsa-aplay")
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "bluealsa-aplay", address,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE
                    )
                    # Ne pas attendre la fin du processus, c'est un service persistant
                    self.logger.info(f"Service bluealsa-aplay lancé pour {address}")
                    return True
                except Exception as e:
                    self.logger.error(f"Échec de démarrage bluealsa-aplay: {e}")
                    return False
            
            # Le service existe, lancer via systemd
            result = await self.start_service(service_name)
            if result:
                self.logger.info(f"Service {service_name} démarré avec succès")
            else:
                self.logger.error(f"Échec du démarrage de {service_name}")
            return result
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage audio pour {address}: {e}")
            return False
    
    async def stop_audio_playback(self, address: str) -> bool:
        """Arrête la lecture audio pour un appareil spécifique"""
        service_name = f"bluealsa-aplay@{address}.service"
        return await self.stop_service(service_name)
    
    async def restart_audio_playback(self, address: str) -> bool:
        """Redémarre la lecture audio pour un appareil spécifique"""
        service_name = f"bluealsa-aplay@{address}.service"
        return await self.restart_service(service_name)
    
    async def is_audio_playback_running(self, address: str) -> bool:
        """Vérifie si la lecture audio est en cours pour un appareil spécifique"""
        service_name = f"bluealsa-aplay@{address}.service"
        return await self.is_service_active(service_name)