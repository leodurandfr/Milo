"""
Gestionnaire des services systemd pour le plugin Bluetooth - Version optimisée
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List

class BluetoothServiceManager:
    """
    Gère les services systemd liés au Bluetooth avec des opérations sérialisées
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._lock = asyncio.Lock()
        self._cache = {}  # Cache d'état des services
    
    async def is_service_active(self, service_name: str) -> bool:
        """Vérifie si un service systemd est actif"""
        # Vérifier le cache (valide pendant 2 secondes)
        cache_key = f"active_{service_name}"
        cache_time = self._cache.get(f"{cache_key}_time", 0)
        if cache_key in self._cache and asyncio.get_event_loop().time() - cache_time < 2:
            return self._cache[cache_key]
            
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", service_name, 
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            
            stdout, _ = await asyncio.wait_for(proc.communicate(), 3.0)
            is_active = stdout.decode().strip() == "active"
            
            # Mettre en cache le résultat
            self._cache[cache_key] = is_active
            self._cache[f"{cache_key}_time"] = asyncio.get_event_loop().time()
            
            return is_active
        except asyncio.TimeoutError:
            self.logger.error(f"Timeout vérification service {service_name}")
            return False
        except Exception as e:
            self.logger.error(f"Erreur vérification service {service_name}: {e}")
            return False
    
    async def manage_service(self, service_name: str, action: str = "start") -> bool:
        """Gère les opérations de service systemd de façon sécurisée"""
        async with self._lock:  # Verrou pour éviter les opérations concurrentes
            try:
                self.logger.info(f"{action.capitalize()} du service {service_name}")
                
                # Vérifier si l'opération est nécessaire
                if action == "start" and await self.is_service_active(service_name):
                    self.logger.info(f"Service {service_name} déjà actif")
                    return True
                elif action == "stop" and not await self.is_service_active(service_name):
                    self.logger.info(f"Service {service_name} déjà arrêté")
                    return True
                
                # Exécuter la commande
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "systemctl", action, service_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE
                )
                
                _, stderr = await asyncio.wait_for(proc.communicate(), 10.0)  # Timeout de 10s
                
                if proc.returncode != 0:
                    error_msg = stderr.decode().strip()
                    self.logger.error(f"Échec de {action} pour {service_name}: {error_msg}")
                    return False
                    
                # Pour le démarrage, vérifier que le service est actif
                if action == "start":
                    for _ in range(5):  # 5 tentatives avec 500ms d'intervalle
                        await asyncio.sleep(0.5)
                        if await self.is_service_active(service_name):
                            self.logger.info(f"Service {service_name} démarré avec succès")
                            # Invalider le cache pour d'autres services qui pourraient être affectés
                            self._cache.clear()
                            return True
                    
                    self.logger.warning(f"Le service {service_name} ne semble pas démarré correctement")
                    return False
                
                # Invalider le cache après les opérations
                self._cache.clear()
                return True
            except asyncio.TimeoutError:
                self.logger.error(f"Timeout {action} service {service_name}")
                return False
            except Exception as e:
                self.logger.error(f"Erreur {action} service {service_name}: {e}")
                return False
    
    # Méthodes spécifiques pour les services Bluetooth
    
    async def start_bluetooth(self) -> bool:
        """Démarre le service bluetooth"""
        return await self.manage_service("bluetooth.service", "start")
    
    async def stop_bluetooth(self) -> bool:
        """Arrête le service bluetooth"""
        return await self.manage_service("bluetooth.service", "stop")
    
    async def start_bluealsa(self) -> bool:
        """Démarre le service bluealsa"""
        return await self.manage_service("bluealsa.service", "start")
    
    async def stop_bluealsa(self) -> bool:
        """Arrête le service bluealsa"""
        return await self.manage_service("bluealsa.service", "stop")
    
    async def restart_bluealsa(self) -> bool:
        """Redémarre le service bluealsa"""
        return await self.manage_service("bluealsa.service", "restart")
    
    async def start_audio_playback(self, address: str) -> bool:
        """Démarre la lecture audio pour un appareil spécifique"""
        service_name = f"bluealsa-aplay@{address}.service"
        
        # Vérifier si le service existe
        exists = await self._check_service_exists(service_name)
        if not exists:
            # Essayer de démarrer via la commande directe
            self.logger.info(f"Service {service_name} non trouvé, tentative avec bluealsa-aplay")
            return await self._start_bluealsa_aplay_direct(address)
        
        return await self.manage_service(service_name, "start")
    
    async def stop_audio_playback(self, address: str) -> bool:
        """Arrête la lecture audio pour un appareil spécifique"""
        service_name = f"bluealsa-aplay@{address}.service"
        return await self.manage_service(service_name, "stop")
    
    async def _check_service_exists(self, service_name: str) -> bool:
        """Vérifie si un service systemd existe"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "cat", service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception:
            return False
    
    async def _start_bluealsa_aplay_direct(self, address: str) -> bool:
        """Démarre bluealsa-aplay directement (hors systemd)"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluealsa-aplay", address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Ne pas attendre la fin car c'est un processus persistant
            # Mais vérifier qu'il démarre correctement
            await asyncio.sleep(1)
            if proc.returncode is not None:  # Le processus s'est terminé trop vite
                stderr = await proc.stderr.read()
                self.logger.error(f"Erreur démarrage bluealsa-aplay: {stderr.decode().strip()}")
                return False
                
            self.logger.info(f"Processus bluealsa-aplay lancé pour {address}")
            return True
        except Exception as e:
            self.logger.error(f"Échec de démarrage bluealsa-aplay: {e}")
            return False