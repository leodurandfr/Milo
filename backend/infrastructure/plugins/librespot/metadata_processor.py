"""
Traitement des métadonnées pour le plugin librespot - Version optimisée.
"""
import logging
from typing import Dict, Any, List, Optional, Callable, Tuple
from backend.application.event_bus import EventBus

class MetadataProcessor:
    """Gère l'extraction et le traitement des métadonnées de go-librespot"""
    
    # Définition des chemins connus pour les métadonnées (chemin, nom cible, fonction de transformation optionnelle)
    METADATA_PATHS = [
        # Format 1: Structure avec player et current_track
        ("player.current_track.name", "title"),
        ("player.current_track.artists", "artist", lambda artists: ", ".join([a.get("name", "") for a in artists if a.get("name")])),
        ("player.current_track.album.name", "album"),
        ("player.current_track.album.images", "album_art_url", lambda images: sorted(images, key=lambda img: img.get("width", 0) * img.get("height", 0), reverse=True)[0].get("url") if images else None),
        ("player.current_track.duration_ms", "duration_ms"),
        ("player.position_ms", "position_ms"),
        ("player.is_playing", "is_playing"),
        
        # Format 2: Structure avec track au niveau racine
        ("track.name", "title"),
        ("track.artist_names", "artist", lambda names: ", ".join(names) if names else ""),
        ("track.album_name", "album"),
        ("track.album_cover_url", "album_art_url"),
        ("track.duration", "duration_ms"),
        ("track.position", "position_ms"),
        ("position", "position_ms"),
        
        # Champs au niveau racine
        ("artist_names", "artist", lambda names: ", ".join(names) if names else ""),
        ("album_name", "album"),
        ("album_cover_url", "album_art_url"),
        ("duration", "duration_ms"),
        ("username", "username"),
        ("device_name", "device_name"),
        ("uri", "track_uri"),
    ]
    
    # Champs critiques qui ne doivent pas être écrasés par des valeurs par défaut
    CRITICAL_FIELDS = ["artist", "album", "album_art_url"]
    
    def __init__(self, event_bus: EventBus, source_name: str):
        self.event_bus = event_bus
        self.source_name = source_name
        self.logger = logging.getLogger(f"librespot.metadata")
        self.last_metadata = {}
        self.log_interval_counter = 0  # Compteur pour réduire la fréquence des logs
    
    def get_nested_value(self, data: Dict[str, Any], path: str) -> Any:
        """
        Récupère une valeur imbriquée en suivant un chemin avec points.
        
        Args:
            data: Dictionnaire contenant les données
            path: Chemin au format "key1.key2.key3"
            
        Returns:
            Any: Valeur trouvée ou None si le chemin n'existe pas
        """
        if not data or not path:
            return None
            
        keys = path.split('.')
        result = data
        
        for key in keys:
            if isinstance(result, dict) and key in result:
                result = result[key]
            else:
                return None
                
        return result
    
    async def extract_from_status(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extrait les métadonnées à partir d'un statut - version optimisée.
        
        Args:
            status: Statut de go-librespot
            
        Returns:
            Dict[str, Any]: Métadonnées extraites
        """
        if not status:
            return {}
            
        try:
            # Créer un dictionnaire de base avec les informations de connexion
            result = {
                "connected": bool(status.get("username")),
                "deviceConnected": True,
                "device_name": status.get("device_name", "oakOS"),
                "username": status.get("username"),
                "is_playing": False  # Valeur par défaut
            }
            
            # Déterminer l'état de lecture à partir de différentes sources
            if "player" in status and "is_playing" in status["player"]:
                result["is_playing"] = status["player"]["is_playing"]
            elif "paused" in status:
                result["is_playing"] = not status["paused"]
            elif "stopped" in status:
                result["is_playing"] = not status["stopped"]
            
            # Si aucune information sur la piste n'est disponible, retourner seulement les infos de connexion
            has_track_info = ("player" in status and "current_track" in status["player"]) or "track" in status
            
            if not has_track_info:
                if status.get("username"):
                    self.logger.debug(f"Appareil connecté via username: {status.get('username')} - En attente de lecture")
                    return result
                return {}
            
            # Parcourir tous les chemins connus pour extraire les métadonnées
            for path_info in self.METADATA_PATHS:
                # Déballage du tuple (en gérant les cas avec ou sans fonction de transformation)
                if len(path_info) == 3:
                    path, target_key, transform_func = path_info
                else:
                    path, target_key = path_info
                    transform_func = None
                
                # Récupérer la valeur
                value = self.get_nested_value(status, path)
                
                # Traiter la valeur si elle existe
                if value is not None:
                    # Appliquer la fonction de transformation si définie
                    if transform_func:
                        try:
                            value = transform_func(value)
                        except Exception as e:
                            self.logger.warning(f"Erreur lors de la transformation de {path}: {e}")
                            continue
                    
                    # Stocker dans le résultat uniquement les valeurs non vides
                    if value not in (None, "", "Inconnu"):
                        result[target_key] = value
            
            # Si nous n'avons pas d'informations minimales sur la piste, retourner seulement les infos de connexion
            if not result.get("title"):
                if status.get("username"):
                    return result
                return {}
            
            # Logs moins fréquents (1 sur 5)
            self.log_interval_counter = (self.log_interval_counter + 1) % 5
            if self.log_interval_counter == 0:
                self.logger.info(f"Métadonnées extraites: title={result.get('title', 'Non disponible')}, "
                                f"artist={result.get('artist', 'Non disponible')}")
            
            return result
                
        except Exception as e:
            self.logger.error(f"Erreur lors de l'extraction des métadonnées: {str(e)}")
            return {}
    
    async def extract_from_event(self, event_type: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extrait les métadonnées à partir d'un événement WebSocket.
        
        Args:
            event_type: Type d'événement
            event_data: Données de l'événement
            
        Returns:
            Dict[str, Any]: Métadonnées extraites
        """
        # Événements metadata contiennent directement les informations nécessaires
        if event_type == 'metadata' and event_data:
            metadata = {
                "title": event_data.get("name"),
                "artist": ", ".join(event_data.get("artist_names", [])),
                "album": event_data.get("album_name"),
                "album_art_url": event_data.get("album_cover_url"),
                "duration_ms": event_data.get("duration"),
                "position_ms": event_data.get("position", 0),
                "is_playing": True,  # Supposer que c'est en lecture
                "connected": True,
                "deviceConnected": True,
                "track_uri": event_data.get("uri")
            }
            
            # Ne retourner que les métadonnées avec au moins un champ significatif
            if metadata.get("title") or metadata.get("artist"):
                return metadata
        
        # Pour les autres types d'événements, retourner un dictionnaire vide
        return {}
    
    async def publish_metadata(self, metadata: Dict[str, Any]) -> None:
        """
        Publie des métadonnées sur le bus d'événements - version optimisée.
        
        Args:
            metadata: Métadonnées à publier
        """
        # Vérifier que nous avons des métadonnées non vides
        if not metadata:
            return
        
        # Vérifications rapides pour éviter le traitement inutile
        if metadata == self.last_metadata:
            return
            
        # Fusionner uniquement les champs critiques si nécessaire
        merged_metadata = metadata.copy()
        
        if self.last_metadata:
            # Pour chaque champ critique, préserver les anciennes valeurs si les nouvelles sont vides
            for key in self.CRITICAL_FIELDS:
                if (key not in merged_metadata or not merged_metadata[key]) and key in self.last_metadata:
                    merged_metadata[key] = self.last_metadata[key]
        
        # Vérifier après fusion si identique à la dernière publication
        if merged_metadata == self.last_metadata:
            return
        
        # Mettre à jour les dernières métadonnées
        self.last_metadata = merged_metadata.copy()
        
        # Logs moins fréquents (1 sur 3)
        self.log_interval_counter = (self.log_interval_counter + 1) % 3
        if self.log_interval_counter == 0:
            self.logger.info(f"Publication de métadonnées: title={merged_metadata.get('title', 'Non disponible')}, "
                            f"artist={merged_metadata.get('artist', 'Non disponible')}")
        
        # Publier les métadonnées
        await self.event_bus.publish("audio_metadata_updated", {
            "source": self.source_name,
            "metadata": merged_metadata
        })
    
    async def publish_status(self, status: str, details: Optional[Dict[str, Any]] = None) -> None:
        """
        Publie un événement de statut sur le bus d'événements.
        
        Args:
            status: Statut à publier (playing, paused, stopped, etc.)
            details: Détails supplémentaires du statut
        """
        # Déterminer rapidement si l'état implique une connexion
        is_connected = status in ["playing", "paused", "connected", "active"]
        
        # Créer un objet de base avec les informations essentielles uniquement
        status_data = {
            "source": self.source_name,
            "status": status,
            "connected": is_connected,
            "is_playing": status == "playing"
        }
        
        # Ajouter les détails supplémentaires s'ils existent
        if details:
            for key, value in details.items():
                if key not in status_data or details.get("force_override", False):
                    status_data[key] = value
        
        # Publier l'événement sans logging excessif
        await self.event_bus.publish("audio_status_updated", status_data)