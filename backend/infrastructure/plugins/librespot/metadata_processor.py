"""
Traitement des métadonnées pour le plugin librespot.
"""
import logging
from typing import Dict, Any, List, Optional
from backend.application.event_bus import EventBus

class MetadataProcessor:
    """Gère l'extraction et le traitement des métadonnées de go-librespot"""
    
    def __init__(self, event_bus: EventBus, source_name: str):
        self.event_bus = event_bus
        self.source_name = source_name
        self.logger = logging.getLogger(f"librespot.metadata")
        self.last_metadata = {}
    
    def format_artists(self, artists: List[Dict[str, Any]]) -> str:
        """
        Formate la liste des artistes en une chaîne.
        
        Args:
            artists: Liste des artistes
            
        Returns:
            str: Chaîne formatée des artistes
        """
        if not artists:
            return "Inconnu"
            
        # Extraire les noms des artistes
        names = [artist.get("name", "") for artist in artists if artist.get("name")]
        
        # Joindre les noms avec des virgules
        return ", ".join(names)
    
    def get_album_art_url(self, album: Dict[str, Any]) -> Optional[str]:
        """
        Récupère l'URL de la pochette d'album.
        
        Args:
            album: Informations sur l'album
            
        Returns:
            Optional[str]: URL de la pochette d'album, ou None si non disponible
        """
        images = album.get("images", [])
        
        # Trier par taille (du plus grand au plus petit)
        sorted_images = sorted(
            images, 
            key=lambda img: img.get("width", 0) * img.get("height", 0), 
            reverse=True
        )
        
        # Prendre la plus grande image
        if sorted_images:
            return sorted_images[0].get("url")
            
        return None
    
    async def extract_from_status(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extrait les métadonnées à partir d'un statut.
        
        Args:
            status: Statut de go-librespot
            
        Returns:
            Dict[str, Any]: Métadonnées extraites
        """
        try:
            # Vérifier si nous avons un username (si connecté à Spotify)
            username = status.get("username")
            
            player = status.get("player", {})
            current_track = player.get("current_track", {})
            
            # Si pas de piste en cours mais l'utilisateur est connecté
            if not current_track and username:
                # Retourner simplement l'état connecté, sans avertissement
                self.logger.debug(f"Appareil connecté via username: {username} - En attente de lecture")
                return {
                    "connected": True,
                    "deviceConnected": True,
                    "username": username,
                    "device_name": status.get("device_name", "oakOS")
                }
                    
            # Si pas de piste en cours et pas connecté, renvoyer des métadonnées vides
            if not current_track:
                return {}
                    
            # Extraction des métadonnées si une piste est en cours
            metadata = {
                "title": current_track.get("name", "Inconnu"),
                "artist": self.format_artists(current_track.get("artists", [])),
                "album": current_track.get("album", {}).get("name", "Inconnu"),
                "album_art_url": self.get_album_art_url(current_track.get("album", {})),
                "duration_ms": current_track.get("duration_ms", 0),
                "position_ms": player.get("position_ms", 0),
                "is_playing": player.get("is_playing", False),
                # Simplification - un seul état "connected" au lieu de deux
                "connected": True,
                "username": username,
                "device_name": status.get("device_name", "oakOS"),
                "track_uri": current_track.get("uri")
            }
            
            # Log uniquement au niveau debug pour les métadonnées régulières
            self.logger.debug(f"Métadonnées extraites: {metadata}")
            
            return metadata
                
        except Exception as e:
            # Réduire le niveau de log pour les erreurs fréquentes
            if "Erreur API (204)" in str(e):
                self.logger.debug(f"Aucun contenu disponible lors de la récupération des métadonnées: {str(e)}")
            else:
                self.logger.error(f"Erreur lors de la récupération des métadonnées: {str(e)}")
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
        # Extraction des métadonnées spécifiques au type d'événement
        if event_type == 'metadata':
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
            return metadata
        
        # Pour les autres types d'événements, retourner un dictionnaire vide
        return {}
    
    async def publish_metadata(self, metadata: Dict[str, Any]) -> None:
        """
        Publie des métadonnées sur le bus d'événements.
        
        Args:
            metadata: Métadonnées à publier
        """
        # Vérifier que les métadonnées sont différentes des dernières publiées
        if metadata == self.last_metadata:
            self.logger.debug("Métadonnées identiques aux précédentes, pas de publication")
            return
            
        self.last_metadata = metadata.copy()
        
        # Publier les métadonnées
        await self.event_bus.publish("audio_metadata_updated", {
            "source": self.source_name,
            "metadata": metadata
        })
        
        # Log des métadonnées importantes
        if "title" in metadata:
            self.logger.info(f"Métadonnées publiées: {metadata.get('title')} - {metadata.get('artist')}")
        
    async def publish_status(self, status: str, details: Optional[Dict[str, Any]] = None) -> None:
        """
        Publie un événement de statut sur le bus d'événements.
        
        Args:
            status: Statut à publier (playing, paused, stopped, etc.)
            details: Détails supplémentaires du statut
        """
        # Déterminer si l'état implique une connexion
        is_connected = status in ["playing", "paused", "connected", "active"]
        
        # Créer un objet de base avec les informations essentielles
        status_data = {
            "source": self.source_name,
            "status": status,
            "connected": is_connected,
            "is_playing": status == "playing"
        }
        
        # Ajouter les détails supplémentaires s'ils existent
        if details:
            # Éviter les doublons en ne surchargeant pas les champs déjà définis
            # sauf si c'est intentionnel (par exemple, forcer connected=False)
            for key, value in details.items():
                if key not in status_data or details.get("force_override", False):
                    status_data[key] = value
        
        # Nettoyer les champs de métadonnées trop volumineux pour les logs
        log_data = {k: v for k, v in status_data.items() 
                    if k not in ["album_art_url", "track_uri"]}
        
        self.logger.debug(f"Publication du statut {status}: {log_data}")
        
        # Publier l'événement
        await self.event_bus.publish("audio_status_updated", status_data)