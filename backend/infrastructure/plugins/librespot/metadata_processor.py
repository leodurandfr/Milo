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
        Extrait les métadonnées à partir d'un statut avec meilleure prise en charge du format go-librespot.
        
        Args:
            status: Statut de go-librespot
            
        Returns:
            Dict[str, Any]: Métadonnées extraites
        """
        try:
            # Journaliser le format complet pour débogage
            self.logger.debug(f"Format du statut reçu: {status.keys()}")
            
            # Vérifier si nous avons un username (si connecté à Spotify)
            username = status.get("username")
            device_name = status.get("device_name", "oakOS")
            
            # Structure dépend de la version de l'API
            
            # 1. Format standard avec 'player'
            if "player" in status:
                player = status.get("player", {})
                current_track = player.get("current_track", {})
                is_playing = player.get("is_playing", False)
                position_ms = player.get("position_ms", 0)
            
            # 2. Format avec 'track' au niveau racine (documentation OpenAPI)    
            elif "track" in status:
                current_track = status.get("track", {})
                is_playing = not status.get("paused", True)  # Inversé par rapport à "paused"
                position_ms = status.get("position", 0) or (current_track.get("position") if current_track else 0)
                
                # Si aucun statut de lecture explicite, vérifier 'stopped'
                if status.get("stopped", False):
                    is_playing = False
            
            # 3. Aucun format reconnu    
            else:
                current_track = {}
                is_playing = False
                position_ms = 0
                
                # Log d'avertissement
                self.logger.warning(f"Format de statut non reconnu: {status}")
            
            # Si pas de piste en cours mais l'utilisateur est connecté
            if not current_track and username:
                self.logger.debug(f"Appareil connecté via username: {username} - En attente de lecture")
                return {
                    "connected": True,
                    "deviceConnected": True,
                    "username": username,
                    "device_name": device_name,
                    "is_playing": is_playing
                }
                    
            # Si pas de piste en cours et pas connecté, renvoyer des métadonnées vides
            if not current_track:
                return {}
            
            # Récupération intelligente des artistes (plusieurs formats possibles)
            artist = ""
            # 1. Format liste d'objets artistes avec 'name'
            if "artists" in current_track and isinstance(current_track["artists"], list):
                artist = self.format_artists(current_track.get("artists", []))
            # 2. Format liste de noms d'artistes
            elif "artist_names" in current_track and isinstance(current_track["artist_names"], list):
                artist = ", ".join(current_track.get("artist_names", []))
            # 3. Format champ simple 'artist'
            elif "artist" in current_track:
                artist = current_track.get("artist", "")
            
            # Si l'artiste est toujours vide, vérifier au niveau racine
            if not artist and "artist_names" in status:
                artist = ", ".join(status.get("artist_names", []))
            
            # Récupération intelligente de l'album
            album = ""
            # 1. Format objet album avec 'name'
            if "album" in current_track and isinstance(current_track["album"], dict):
                album = current_track.get("album", {}).get("name", "")
            # 2. Format champ simple 'album_name'
            elif "album_name" in current_track:
                album = current_track.get("album_name", "")
            
            # Si l'album est toujours vide, vérifier au niveau racine
            if not album and "album_name" in status:
                album = status.get("album_name", "")
            
            # Récupération intelligente de l'image d'album
            album_art_url = None
            # 1. Format objet album avec 'images'
            if "album" in current_track and isinstance(current_track["album"], dict):
                album_art_url = self.get_album_art_url(current_track.get("album", {}))
            # 2. Format champ simple 'album_cover_url'
            elif "album_cover_url" in current_track:
                album_art_url = current_track.get("album_cover_url")
            
            # Si l'URL est toujours vide, vérifier au niveau racine
            if not album_art_url and "album_cover_url" in status:
                album_art_url = status.get("album_cover_url")
            
            # Récupération de la durée
            duration_ms = (
                current_track.get("duration_ms", 0) or 
                current_track.get("duration", 0) or
                status.get("duration", 0)
            )
            
            # Récupération de l'URI de la piste
            track_uri = current_track.get("uri") or status.get("uri")
            
            # Extraction des métadonnées si une piste est en cours
            metadata = {
                "title": current_track.get("name", "Inconnu"),
                "artist": artist or "Inconnu",
                "album": album or "Inconnu",
                "album_art_url": album_art_url,
                "duration_ms": duration_ms,
                "position_ms": position_ms,
                "is_playing": is_playing,
                "connected": True,
                "deviceConnected": True,
                "username": username,
                "device_name": device_name,
                "track_uri": track_uri
            }
            
            # Log détaillé des métadonnées extraites
            self.logger.info(f"Métadonnées extraites: title={metadata['title']}, "
                            f"artist={metadata['artist']}, album={metadata['album']}, "
                            f"album_art_url={'présent' if metadata['album_art_url'] else 'absent'}")
            
            return metadata
                
        except Exception as e:
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
        Publie des métadonnées sur le bus d'événements, en préservant les données précédentes utiles.
        
        Args:
            metadata: Métadonnées à publier
        """
        # Vérifier que nous avons des métadonnées non vides
        if not metadata:
            self.logger.debug("Métadonnées vides, pas de publication")
            return
            
        # Préserver les informations importantes des métadonnées précédentes
        merged_metadata = {}
        
        # Si nous avions des métadonnées précédentes, les conserver comme base
        if self.last_metadata:
            # Pour les champs critiques comme artist, album, album_art_url
            # garder les anciens si les nouveaux sont vides/None/défaut
            merged_metadata = self.last_metadata.copy()
        
        # Mettre à jour avec les nouvelles métadonnées
        for key, value in metadata.items():
            # Ne pas écraser les valeurs importantes par des valeurs "par défaut"
            if key in ["artist", "album", "album_art_url"]:
                if key not in merged_metadata or (
                    value and value != "Inconnu" and value != merged_metadata.get(key)
                ):
                    merged_metadata[key] = value
            else:
                # Pour les autres champs, toujours prendre la valeur la plus récente
                merged_metadata[key] = value
        
        # Maintenant vérifier si les métadonnées fusionnées sont différentes des dernières publiées
        if merged_metadata == self.last_metadata:
            self.logger.debug("Métadonnées identiques aux précédentes, pas de publication")
            return
            
        # Mettre à jour les dernières métadonnées
        self.last_metadata = merged_metadata.copy()
        
        # Log détaillé pour le débogage des métadonnées
        self.logger.info(f"Publication de métadonnées fusionnées: title={merged_metadata.get('title')}, "
                        f"artist={merged_metadata.get('artist')}, album={merged_metadata.get('album')}, "
                        f"album_art_url={'présent' if merged_metadata.get('album_art_url') else 'absent'}")
        
        # Publier les métadonnées fusionnées
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