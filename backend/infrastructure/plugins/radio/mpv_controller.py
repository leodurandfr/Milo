"""
Contrôleur mpv via IPC socket pour lecture de streams radio
"""
import asyncio
import json
import logging
from typing import Optional, Dict, Any
from pathlib import Path


class MpvController:
    """
    Contrôle mpv via IPC socket pour lecture de streams radio

    Communication asynchrone via socket Unix avec mpv en mode JSON IPC.
    Pattern inspiré de libmpv et python-mpv.
    """

    def __init__(self, ipc_socket_path: str = "/tmp/milo-radio-ipc.sock"):
        self.ipc_socket_path = ipc_socket_path
        self.logger = logging.getLogger(__name__)
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._command_id = 0
        self._connected = False

    async def connect(self, max_retries: int = 10, retry_delay: float = 0.5) -> bool:
        """
        Connecte au socket IPC de mpv avec retry

        Args:
            max_retries: Nombre de tentatives de connexion
            retry_delay: Délai entre les tentatives (secondes)

        Returns:
            True si connexion réussie
        """
        for attempt in range(max_retries):
            try:
                if not Path(self.ipc_socket_path).exists():
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        self.logger.error(f"IPC socket not found: {self.ipc_socket_path}")
                        return False

                self.reader, self.writer = await asyncio.open_unix_connection(self.ipc_socket_path)
                self._connected = True
                self.logger.info(f"Connected to mpv IPC socket: {self.ipc_socket_path}")
                return True

            except (ConnectionRefusedError, FileNotFoundError) as e:
                if attempt < max_retries - 1:
                    self.logger.debug(f"Retry {attempt + 1}/{max_retries}: {e}")
                    await asyncio.sleep(retry_delay)
                else:
                    self.logger.error(f"Failed to connect to mpv after {max_retries} attempts")
                    return False
            except Exception as e:
                self.logger.error(f"Unexpected error connecting to mpv: {e}")
                return False

        return False

    async def disconnect(self) -> None:
        """Déconnecte du socket IPC"""
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception as e:
                self.logger.debug(f"Error closing writer: {e}")

        self.reader = None
        self.writer = None
        self._connected = False
        self.logger.info("Disconnected from mpv IPC")

    @property
    def is_connected(self) -> bool:
        """Vérifie si connecté au socket IPC"""
        return self._connected and self.writer is not None and not self.writer.is_closing()

    async def _send_command(self, command: str, *args) -> Optional[Dict[str, Any]]:
        """
        Envoie une commande JSON IPC à mpv

        Format mpv IPC: {"command": ["command_name", "arg1", "arg2"], "request_id": 1}

        Args:
            command: Nom de la commande mpv
            *args: Arguments de la commande

        Returns:
            Réponse JSON de mpv ou None si erreur
        """
        if not self.is_connected:
            self.logger.warning("Not connected to mpv, attempting reconnect...")
            if not await self.connect():
                return None

        try:
            self._command_id += 1
            request = {
                "command": [command, *args],
                "request_id": self._command_id
            }

            # Envoyer la commande
            command_json = json.dumps(request) + "\n"
            self.writer.write(command_json.encode('utf-8'))
            await self.writer.drain()

            # Lire la réponse (avec timeout)
            try:
                response_line = await asyncio.wait_for(self.reader.readline(), timeout=2.0)
                response = json.loads(response_line.decode('utf-8'))

                error = response.get('error')
                # Ne logger que les vraies erreurs, pas les erreurs transitoires
                if error not in ('success', None, 'null', 'property unavailable'):
                    self.logger.warning(f"mpv command error: {error}")

                return response

            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout waiting for mpv response to: {command}")
                return None

        except Exception as e:
            self.logger.error(f"Error sending command to mpv: {e}")
            self._connected = False
            return None

    async def load_stream(self, url: str) -> bool:
        """
        Charge et joue un stream radio

        Args:
            url: URL du stream radio

        Returns:
            True si commande envoyée avec succès
        """
        self.logger.info(f"Loading stream: {url}")
        response = await self._send_command("loadfile", url, "replace")

        # mpv peut retourner des erreurs transitoires (None, "property unavailable")
        # pendant le chargement initial du stream. On accepte ces erreurs.
        if response is None:
            return False

        error = response.get('error')
        # Accepter 'success' ET les erreurs transitoires (None, null, property unavailable)
        # "property unavailable" arrive quand on change rapidement de station
        # Seules les vraies erreurs ("file not found", etc.) font échouer
        if error in ('success', None, 'null', 'property unavailable'):
            return True

        # Log pour les vraies erreurs uniquement
        self.logger.error(f"mpv loadfile failed with error: {error}")
        return False

    async def stop(self) -> bool:
        """
        Arrête la lecture en cours

        Returns:
            True si commande envoyée avec succès
        """
        self.logger.info("Stopping playback")
        response = await self._send_command("stop")
        return response is not None and response.get('error') == 'success'

    async def get_property(self, property_name: str) -> Optional[Any]:
        """
        Récupère une propriété de mpv

        Args:
            property_name: Nom de la propriété (ex: "pause", "volume", "metadata")

        Returns:
            Valeur de la propriété ou None
        """
        response = await self._send_command("get_property", property_name)
        if response and response.get('error') == 'success':
            return response.get('data')
        return None

    async def set_property(self, property_name: str, value: Any) -> bool:
        """
        Définit une propriété de mpv

        Args:
            property_name: Nom de la propriété
            value: Nouvelle valeur

        Returns:
            True si succès
        """
        response = await self._send_command("set_property", property_name, value)
        return response is not None and response.get('error') == 'success'

    async def is_playing(self) -> bool:
        """
        Vérifie si mpv est en cours de lecture

        Returns:
            True si en lecture (pas en pause, pas arrêté)
        """
        # Vérifier d'abord si mpv est idle (pas de fichier chargé)
        idle = await self.get_property("idle-active")
        if idle is None or idle is True:
            return False

        # Si un fichier est chargé, vérifier qu'il n'est pas en pause
        pause = await self.get_property("pause")
        if pause is None:
            # Si on ne peut pas lire la propriété pause mais qu'un fichier est chargé,
            # considérer qu'on est en lecture (utile pendant le buffering)
            return True

        return not pause

    async def get_metadata(self) -> Optional[Dict[str, Any]]:
        """
        Récupère les métadonnées du stream en cours

        Returns:
            Dict avec métadonnées (icy-title, etc.) ou None
        """
        metadata = await self.get_property("metadata")
        return metadata if metadata else {}

    async def get_media_title(self) -> Optional[str]:
        """
        Récupère le titre actuel (icy-title pour les streams radio)

        Returns:
            Titre du stream ou None
        """
        media_title = await self.get_property("media-title")

        # Fallback sur icy-title si media-title n'est pas disponible
        if not media_title:
            metadata = await self.get_metadata()
            if metadata:
                media_title = metadata.get("icy-title")

        return media_title

    async def get_status(self) -> Dict[str, Any]:
        """
        Récupère le status complet de mpv

        Returns:
            Dict avec état de lecture, métadonnées, etc.
        """
        try:
            is_paused = await self.get_property("pause")
            media_title = await self.get_media_title()
            metadata = await self.get_metadata()

            return {
                "connected": self.is_connected,
                "playing": is_paused is not None and not is_paused,
                "paused": is_paused if is_paused is not None else False,
                "media_title": media_title,
                "metadata": metadata or {}
            }
        except Exception as e:
            self.logger.error(f"Error getting status: {e}")
            return {
                "connected": False,
                "playing": False,
                "paused": False,
                "media_title": None,
                "metadata": {}
            }
