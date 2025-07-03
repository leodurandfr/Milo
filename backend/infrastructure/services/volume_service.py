# backend/infrastructure/services/volume_service.py
"""
Service de gestion du volume pour oakOS - Version corrigée avec broadcast initial garanti
"""
import asyncio
import logging
import alsaaudio
import re
from typing import Optional

class VolumeService:
    """Service de gestion du volume système - Mode direct via amixer -M"""
    
    # LIMITES DE VOLUME (utilisées pour direct ET multiroom)
    MIN_VOLUME = 0  # Volume minimum
    MAX_VOLUME = 65  # Volume maximum
    
    def __init__(self, state_machine, snapcast_service):
        self.state_machine = state_machine
        self.snapcast_service = snapcast_service
        self.mixer: Optional[alsaaudio.Mixer] = None  # Utilisé uniquement pour vérifier l'existence
        self.logger = logging.getLogger(__name__)
        self._volume_lock = asyncio.Lock()
        self._current_volume = 0
    
    def _interpolate_to_display(self, actual_volume: int) -> int:
        """Convertit le volume réel (MIN_VOLUME-MAX_VOLUME) en volume d'affichage (0-100%)"""
        actual_range = self.MAX_VOLUME - self.MIN_VOLUME
        normalized = actual_volume - self.MIN_VOLUME
        return round((normalized / actual_range) * 100)

    def _interpolate_from_display(self, display_volume: int) -> int:
        """Convertit le volume d'affichage (0-100%) en volume réel (MIN_VOLUME-MAX_VOLUME)"""
        actual_range = self.MAX_VOLUME - self.MIN_VOLUME
        return round((display_volume / 100) * actual_range) + self.MIN_VOLUME
    
    async def initialize(self) -> bool:
        """Initialise le service volume"""
        try:
            self.logger.info(f"Initializing volume service with amixer -M (limits: {self.MIN_VOLUME}-{self.MAX_VOLUME}%)")
            
            # Vérifier que le mixer Digital existe (mais ne pas l'utiliser pour les opérations)
            try:
                self.mixer = alsaaudio.Mixer('Digital')
                self.logger.info("ALSA Digital mixer found")
            except Exception as e:
                self.logger.error(f"Digital mixer not found: {e}")
                return False
            
            if self._is_multiroom_enabled():
                # Mode multiroom : forcer les limites sur les clients
                await self._enforce_multiroom_limits()
                self._current_volume = await self.get_volume()
            else:
                # Mode direct : forcer les limites via amixer -M
                initial_volume = await self._get_amixer_volume()
                if initial_volume is None:
                    self.logger.error("Failed to get initial volume from amixer")
                    return False
                
                limited_volume = max(self.MIN_VOLUME, min(self.MAX_VOLUME, initial_volume))
                
                if limited_volume != initial_volume:
                    self.logger.info(f"Enforcing amixer limits: {initial_volume}% → {limited_volume}%")
                    success = await self._set_amixer_volume(limited_volume)
                    if not success:
                        self.logger.error("Failed to set initial volume limits")
                        return False
                
                self._current_volume = self._interpolate_to_display(limited_volume)
            
            self.logger.info(f"Volume service initialized - Display: {self._current_volume}%")
            
            # ✅ AJOUT : S'assurer que l'état initial est diffusé (avec un petit délai pour que WebSocket soit connecté)
            asyncio.create_task(self._delayed_initial_broadcast())
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize volume service: {e}")
            return False
    
    async def _delayed_initial_broadcast(self):
        """Diffuse l'état initial avec un délai pour garantir la connexion WebSocket"""
        try:
            # Attendre un peu pour que les connexions WebSocket soient établies
            await asyncio.sleep(1.0)
            await self._broadcast_volume_change(show_bar=False)
            self.logger.info("Initial volume state broadcasted to clients")
        except Exception as e:
            self.logger.error(f"Error in delayed initial broadcast: {e}")
    
    def _is_multiroom_enabled(self) -> bool:
        """Vérifie si le mode multiroom est activé"""
        try:
            if not self.state_machine or not hasattr(self.state_machine, 'routing_service') or not self.state_machine.routing_service:
                return False
            routing_state = self.state_machine.routing_service.get_state()
            return routing_state.multiroom_enabled
        except Exception as e:
            self.logger.error(f"Error checking multiroom state: {e}")
            return False
    
    async def _get_amixer_volume(self) -> Optional[int]:
        """Récupère le volume via amixer -M get Digital"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "amixer", "-M", "get", "Digital",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                self.logger.error(f"amixer get failed: {stderr.decode()}")
                return None
            
            # Parser la sortie pour extraire le pourcentage
            # Format attendu: "Front Left: Playbook 160 [41%] [-23.50dB] [on]"
            output = stdout.decode()
            pattern = r'\[(\d+)%\]'
            matches = re.findall(pattern, output)
            
            if matches:
                # Prendre la première valeur trouvée
                volume = int(matches[0])
                self.logger.debug(f"Parsed amixer volume: {volume}%")
                return volume
            else:
                self.logger.error(f"Could not parse volume from amixer output: {output}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error getting amixer volume: {e}")
            return None
    
    async def _set_amixer_volume(self, volume: int) -> bool:
        """Définit le volume via amixer -M set Digital X%"""
        try:
            # Clamp dans les limites
            limited_volume = max(self.MIN_VOLUME, min(self.MAX_VOLUME, volume))
            
            proc = await asyncio.create_subprocess_exec(
                "amixer", "-M", "set", "Digital", f"{limited_volume}%",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                self.logger.error(f"amixer set failed: {stderr.decode()}")
                return False
            
            self.logger.debug(f"amixer volume set to {limited_volume}%")
            return True
            
        except Exception as e:
            self.logger.error(f"Error setting amixer volume: {e}")
            return False
    
    async def get_volume(self) -> int:
        """Récupère le volume affiché utilisateur (0-100%)"""
        try:
            if self._is_multiroom_enabled():
                return await self._get_volume_multiroom()
            else:
                actual_volume = await self._get_amixer_volume()
                if actual_volume is None:
                    return self._current_volume  # Fallback sur la valeur mise en cache
                return self._interpolate_to_display(actual_volume)
        except Exception as e:
            self.logger.error(f"Error getting volume: {e}")
            return 0
    
    async def _get_volume_multiroom(self) -> int:
        """Récupère la moyenne du volume des clients snapcast"""
        try:
            clients = await self.snapcast_service.get_clients()
            
            if not clients:
                self.logger.warning("No snapcast clients found, using cached volume")
                return self._current_volume
            
            # Calculer la moyenne des volumes clients
            total_volume = sum(client["volume"] for client in clients)
            average_volume = round(total_volume / len(clients))
            
            # Convertir vers affichage utilisateur
            return self._interpolate_to_display(average_volume)
                
        except Exception as e:
            self.logger.error(f"Error getting multiroom volume: {e}")
            return self._current_volume
    
    async def set_volume(self, display_volume: int, show_bar: bool = True) -> bool:
        """Définit le volume utilisateur (0-100%)"""
        async with self._volume_lock:
            try:
                # Clamp dans 0-100%
                display_clamped = max(0, min(100, display_volume))
                
                if self._is_multiroom_enabled():
                    success = await self._set_volume_multiroom(display_clamped)
                else:
                    success = await self._set_volume_direct(display_clamped)
                
                if success:
                    self._current_volume = display_clamped
                    await self._broadcast_volume_change(show_bar=show_bar)
                
                return success
                
            except Exception as e:
                self.logger.error(f"Error setting volume: {e}")
                return False
    
    async def _set_volume_direct(self, display_volume: int) -> bool:
        """Définit le volume en mode direct via amixer -M"""
        try:
            # Convertir vers volume réel avec limites
            actual_volume = self._interpolate_from_display(display_volume)
            
            self.logger.debug(f"Setting volume direct: display={display_volume}% → actual={actual_volume}%")
            return await self._set_amixer_volume(actual_volume)
        except Exception as e:
            self.logger.error(f"Error setting volume direct: {e}")
            return False
    
    async def _set_volume_multiroom(self, display_volume: int) -> bool:
        """Définit le volume en mode multiroom"""
        try:
            # Convertir vers volume réel avec limites
            target_volume = self._interpolate_from_display(display_volume)
            
            clients = await self.snapcast_service.get_clients()
            if not clients:
                self.logger.warning("No snapcast clients found")
                return False
            
            # Calculer la moyenne actuelle
            current_average = sum(client["volume"] for client in clients) / len(clients)
            
            # Gestion spéciale pour les volumes très bas
            if target_volume <= self.MIN_VOLUME + 2 and current_average <= self.MIN_VOLUME + 5:
                self.logger.debug(f"Force setting all clients to: {target_volume}%")
                return await self._force_all_clients_volume(target_volume)
            
            # Logique proportionnelle intelligente
            if current_average <= 5:
                # Mode DELTA pour éviter division par zéro
                delta = target_volume - current_average
                self.logger.debug(f"Multiroom DELTA mode: avg {current_average:.1f}% → {target_volume}% (delta: {delta:+.1f})")
                calculation_mode = "delta"
            else:
                # Mode RATIO proportionnel
                volume_ratio = target_volume / current_average
                self.logger.debug(f"Multiroom RATIO mode: avg {current_average:.1f}% → {target_volume}% (ratio: {volume_ratio:.3f})")
                calculation_mode = "ratio"
            
            # Appliquer aux clients
            success_count = 0
            updated_clients = []
            
            for client in clients:
                try:
                    current_volume = client["volume"]
                    
                    if calculation_mode == "delta":
                        new_volume = current_volume + delta
                    else:  # ratio
                        new_volume = current_volume * volume_ratio
                    
                    # Forcer dans les limites
                    new_volume_limited = max(self.MIN_VOLUME, min(self.MAX_VOLUME, round(new_volume)))
                    
                    if await self.snapcast_service.set_volume(client["id"], new_volume_limited):
                        success_count += 1
                        updated_clients.append({
                            "id": client["id"],
                            "name": client["name"],
                            "old_volume": current_volume,
                            "new_volume": new_volume_limited
                        })
                        
                        self.logger.debug(f"Volume updated for {client['name']}: {current_volume}% → {new_volume_limited}%")
                    else:
                        self.logger.warning(f"Failed to set volume for client {client['name']}")
                        
                except Exception as e:
                    self.logger.error(f"Error setting volume for client {client['name']}: {e}")
            
            # Publier mise à jour
            if updated_clients:
                await self._broadcast_clients_volume_update(updated_clients)
            
            return success_count > 0
            
        except Exception as e:
            self.logger.error(f"Error setting volume multiroom: {e}")
            return False
    
    async def _force_all_clients_volume(self, target_volume: int) -> bool:
        """Force tous les clients à un volume spécifique"""
        try:
            clients = await self.snapcast_service.get_clients()
            if not clients:
                return False
            
            success_count = 0
            updated_clients = []
            
            for client in clients:
                try:
                    old_volume = client["volume"]
                    # Assurer que le volume est dans les limites
                    limited_volume = max(self.MIN_VOLUME, min(self.MAX_VOLUME, target_volume))
                    
                    if await self.snapcast_service.set_volume(client["id"], limited_volume):
                        success_count += 1
                        updated_clients.append({
                            "id": client["id"],
                            "name": client["name"],
                            "old_volume": old_volume,
                            "new_volume": limited_volume
                        })
                        self.logger.debug(f"Forced {client['name']}: {old_volume}% → {limited_volume}%")
                except Exception as e:
                    self.logger.error(f"Error forcing volume for {client['name']}: {e}")
            
            if updated_clients:
                await self._broadcast_clients_volume_update(updated_clients)
            
            return success_count > 0
            
        except Exception as e:
            self.logger.error(f"Error forcing all clients volume: {e}")
            return False
    
    async def adjust_volume(self, delta: int, show_bar: bool = True) -> bool:
        """Ajuste le volume par delta utilisateur"""
        try:
            current = await self.get_volume()
            new_volume = current + delta
            return await self.set_volume(new_volume, show_bar=show_bar)
        except Exception as e:
            self.logger.error(f"Error adjusting volume by {delta}: {e}")
            return False
    
    async def _enforce_multiroom_limits(self) -> None:
        """Force les limites sur tous les clients Snapcast au démarrage"""
        try:
            clients = await self.snapcast_service.get_clients()
            if not clients:
                return
            
            updated_clients = []
            for client in clients:
                current_volume = client["volume"]
                limited_volume = max(self.MIN_VOLUME, min(self.MAX_VOLUME, current_volume))
                
                if limited_volume != current_volume:
                    if await self.snapcast_service.set_volume(client["id"], limited_volume):
                        self.logger.info(f"Enforced limits on {client['name']}: {current_volume}% → {limited_volume}%")
                        updated_clients.append({
                            "id": client["id"],
                            "name": client["name"],
                            "old_volume": current_volume,
                            "new_volume": limited_volume
                        })
            
            if updated_clients:
                await self._broadcast_clients_volume_update(updated_clients)
                
        except Exception as e:
            self.logger.error(f"Error enforcing multiroom limits: {e}")
    
    async def _broadcast_volume_change(self, show_bar: bool = True) -> None:
        """Publie un changement de volume via WebSocket"""
        try:
            display_volume = await self.get_volume()
            
            # Informations de debug selon le mode
            if self._is_multiroom_enabled():
                amixer_volume = None
                mode = "multiroom"
            else:
                amixer_volume = await self._get_amixer_volume()
                mode = "direct"
            
            await self.state_machine.broadcast_event("volume", "volume_changed", {
                "volume": display_volume,
                "amixer_volume": amixer_volume,
                "multiroom_mode": self._is_multiroom_enabled(),
                "show_bar": show_bar,
                "source": "volume_service",
                "limits": {
                    "min": self.MIN_VOLUME,
                    "max": self.MAX_VOLUME
                }
            })
            
            self.logger.debug(f"Broadcasted volume: {display_volume}% (mode: {mode}, limits: {self.MIN_VOLUME}-{self.MAX_VOLUME}%)")
            
        except Exception as e:
            self.logger.error(f"Error broadcasting volume change: {e}")
    
    async def _broadcast_clients_volume_update(self, updated_clients: list) -> None:
        """Publie la mise à jour des volumes des clients Snapcast"""
        try:
            await self.state_machine.broadcast_event("snapcast", "clients_volume_updated", {
                "updated_clients": updated_clients,
                "source": "volume_service"
            })
            
            self.logger.debug(f"Broadcasted clients volume update: {len(updated_clients)} clients")
            
        except Exception as e:
            self.logger.error(f"Error broadcasting clients volume update: {e}")
    
    async def get_status(self) -> dict:
        """Récupère l'état complet du volume"""
        try:
            multiroom_enabled = self._is_multiroom_enabled()
            display_volume = await self.get_volume()
            
            if multiroom_enabled:
                clients = await self.snapcast_service.get_clients()
                return {
                    "volume": display_volume,
                    "mode": "multiroom",
                    "multiroom_enabled": True,
                    "client_count": len(clients),
                    "snapcast_available": await self.snapcast_service.is_available(),
                    "mixer_available": self.mixer is not None,
                    "limits": {
                        "min": self.MIN_VOLUME,
                        "max": self.MAX_VOLUME
                    }
                }
            else:
                amixer_volume = await self._get_amixer_volume()
                return {
                    "volume": display_volume,
                    "amixer_volume": amixer_volume,
                    "mode": "direct",
                    "multiroom_enabled": False,
                    "mixer_available": self.mixer is not None,
                    "limits": {
                        "min": self.MIN_VOLUME,
                        "max": self.MAX_VOLUME
                    }
                }
                
        except Exception as e:
            self.logger.error(f"Error getting volume status: {e}")
            return {
                "volume": 0,
                "mode": "error",
                "error": str(e),
                "limits": {
                    "min": self.MIN_VOLUME,
                    "max": self.MAX_VOLUME
                }
            }