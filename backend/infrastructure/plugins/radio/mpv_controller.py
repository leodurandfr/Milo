"""
mpv controller via IPC socket pour lecture de streams radio
"""
import asyncio
import json
import logging
from typing import Optional, Dict, Any
from pathlib import Path


class MpvController:
    """
    Controls mpv via IPC socket pour lecture de streams radio

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
        Connects to IPC socket de mpv avec retry

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
        """Disconnects from IPC socket"""
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
        """Checks if connected au socket IPC"""
        return self._connected and self.writer is not None and not self.writer.is_closing()

    async def _send_command(self, command: str, *args) -> Optional[Dict[str, Any]]:
        """
        Sends a command JSON IPC à mpv

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

            # Lire la réponse en matchant le request_id (avec timeout)
            try:
                # Lire jusqu'à 10 lignes max pour trouver la bonne réponse
                for _ in range(10):
                    response_line = await asyncio.wait_for(self.reader.readline(), timeout=2.0)
                    if not response_line:
                        break

                    response = json.loads(response_line.decode('utf-8'))

                    # Ignorer les événements mpv (pas de request_id)
                    if 'event' in response:
                        continue

                    # Si c'est la réponse à notre requête, la retourner
                    if response.get('request_id') == self._command_id:
                        error = response.get('error')
                        # Ne logger que les vraies erreurs, pas les erreurs transitoires
                        if error not in ('success', None, 'null', 'property unavailable'):
                            self.logger.warning(f"mpv command error: {error}")
                        return response

                # Aucune réponse correspondante trouvée
                self.logger.warning(f"No matching response for request {self._command_id}")
                return None

            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout waiting for mpv response to: {command}")
                return None

        except Exception as e:
            self.logger.error(f"Error sending command to mpv: {e}")
            self._connected = False
            return None

    async def load_stream(self, url: str) -> bool:
        """
        Loads and plays a stream radio

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
        Stoppinge la lecture en cours

        Returns:
            True si commande envoyée avec succès
        """
        self.logger.info("Stopping playback")
        response = await self._send_command("stop")
        return response is not None and response.get('error') == 'success'

    async def get_property(self, property_name: str) -> Optional[Any]:
        """
        Gets a property de mpv

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
        Sets a property de mpv

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
        Checks if mpv is playing via playback-time

        Returns:
            True si en lecture (playback-time existe)
        """
        # playback-time est la propriété la plus fiable pour les streams
        # Elle existe dès que mpv commence à décoder, et disparaît quand on arrête
        playback_time = await self.get_property("playback-time")

        # Si playback-time est un nombre (même 0), le stream joue
        return isinstance(playback_time, (int, float))

    async def get_status(self) -> Dict[str, Any]:
        """
        Gets current state de mpv

        Returns:
            Dict avec l'état de connexion et de lecture
        """
        return {
            "connected": self.is_connected,
            "playing": await self.is_playing() if self.is_connected else False
        }

