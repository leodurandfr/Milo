# backend/core/multiroom/snapcast.py
"""
Snapcast REST service - JSON-RPC commands to Snapcast server.

This service handles all REST commands to the Snapcast server. WebSocket
notifications are handled separately by SnapcastWebSocketService.
"""
import asyncio
import logging
import time
from typing import List, Dict, Any, Optional
from pathlib import Path

import aiohttp
import aiofiles

from backend.core.events import EventBus, get_event_bus
from backend.core.multiroom.client_registry import ClientRegistryService
from backend.shared.decorators import handle_errors


class SnapcastService:
    """
    Snapcast REST service for JSON-RPC commands.

    Handles all direct communication with Snapcast server via HTTP/JSON-RPC.
    WebSocket notifications are handled by SnapcastWebSocketService.
    """

    def __init__(self, host: str = "localhost", port: int = 1780, event_bus: EventBus = None):
        self.base_url = f"http://{host}:{port}/jsonrpc"
        self.logger = logging.getLogger(__name__)
        self._request_id = 0
        self.snapserver_conf = Path("/etc/snapserver.conf")
        self.event_bus = event_bus or get_event_bus()

    async def _request(self, method: str, params: dict = None) -> dict:
        """Execute JSON-RPC request to Snapcast server."""
        self._request_id += 1
        request = {"id": self._request_id, "jsonrpc": "2.0", "method": method}
        if params:
            request["params"] = params

        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.base_url, json=request) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("result", {})
            return {}
        except Exception as e:
            self.logger.error(f"Snapcast request failed: {type(e).__name__}: {e}")
            return {}

    # === GROUP MANAGEMENT ===

    @handle_errors(default=False)
    async def set_all_groups_to_multiroom(self) -> bool:
        """Switch all groups to Multiroom stream."""
        status = await self._request("Server.GetStatus")
        if not status:
            return False

        groups = status.get("server", {}).get("groups", [])

        for group in groups:
            group_id = group.get("id")
            if group_id:
                await self._request("Group.SetStream", {
                    "id": group_id,
                    "stream_id": "Multiroom"
                })

        return True

    @handle_errors(default=False)
    async def set_client_group_to_multiroom(self, client_id: str) -> bool:
        """Switch a client's group to Multiroom stream."""
        status = await self._request("Server.GetStatus")
        if not status:
            return False

        groups = status.get("server", {}).get("groups", [])

        # Find client's group
        client_group_id = None
        for group in groups:
            for client in group.get("clients", []):
                if client.get("id") == client_id:
                    client_group_id = group.get("id")
                    break
            if client_group_id:
                break

        if not client_group_id:
            self.logger.warning(f"Client {client_id} not found in any group")
            return False

        result = await self._request("Group.SetStream", {
            "id": client_group_id,
            "stream_id": "Multiroom"
        })

        self.logger.info(f"Client {client_id} group switched to Multiroom: {bool(result)}")
        return bool(result)

    # === CLIENT COMMANDS ===

    @handle_errors(default=False)
    async def set_volume(self, client_id: str, volume: int) -> bool:
        """Set a client's volume (0-100)."""
        # Get current mute state
        clients = await self.get_clients()
        current_muted = False
        for client in clients:
            if client["id"] == client_id:
                current_muted = client["muted"]
                break

        result = await self._request("Client.SetVolume", {
            "id": client_id,
            "volume": {"percent": max(0, min(100, volume)), "muted": current_muted}
        })
        return bool(result)

    @handle_errors(default=False)
    async def set_mute(self, client_id: str, muted: bool) -> bool:
        """Mute/unmute a client."""
        # Get current volume
        clients = await self.get_clients()
        current_volume = 50  # Default value

        for client in clients:
            if client["id"] == client_id:
                current_volume = client["volume"]
                break

        result = await self._request("Client.SetVolume", {
            "id": client_id,
            "volume": {"percent": current_volume, "muted": muted}
        })
        return bool(result)

    @handle_errors(default=False)
    async def set_client_name(self, client_id: str, name: str) -> bool:
        """Set a client's display name."""
        result = await self._request("Client.SetName", {
            "id": client_id,
            "name": name.strip()
        })
        return bool(result)

    # === CLIENT QUERIES ===

    @handle_errors(default=[])
    async def get_clients(self) -> List[Dict[str, Any]]:
        """Get clients with MAC-based deduplication and availability detection."""
        status = await self._request("Server.GetStatus")
        return self._extract_clients(status)

    def _parse_clients(
        self,
        status: dict,
        include_offline: bool = False,
        detailed: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Parse clients from server status with configurable output.

        Args:
            status: Snapcast server status response
            include_offline: If True, include clients that aren't recently seen
            detailed: If True, include extra fields (host_info, snapclient_info, group_id)

        Returns:
            List of client dicts, deduplicated by MAC address
        """
        raw_clients = []
        exclude_names = {'snapweb client', 'snapweb'}
        now = time.time()

        for group in status.get("server", {}).get("groups", []):
            group_id = group.get("id")
            for client_data in group.get("clients", []):
                if not client_data.get("connected"):
                    continue

                name = client_data["config"]["name"] or client_data["host"]["name"]
                if any(exclude in name.lower() for exclude in exclude_names):
                    continue

                host = client_data["host"]["name"]
                ip = client_data["host"]["ip"].replace("::ffff:", "")
                mac = client_data["host"].get("mac", "")

                # mac_id: MAC address as primary identifier
                mac_id = ClientRegistryService.compute_mac_id(host, ip, mac)

                # Calculate online status based on lastSeen timestamp
                last_seen_data = client_data.get("lastSeen", {})
                last_seen_sec = last_seen_data.get("sec", 0)
                last_seen_age = now - last_seen_sec

                # Client is online if connected AND seen recently (within 60 seconds)
                is_online = client_data.get("connected", False) and last_seen_age < 60

                # Main device (milo) is always online (localhost)
                if host == "milo":
                    is_online = True

                # Skip offline clients unless explicitly requested
                if not is_online and not include_offline:
                    continue

                client_info = {
                    "id": client_data["id"],
                    "name": name,
                    "volume": client_data["config"]["volume"]["percent"],
                    "muted": client_data["config"]["volume"]["muted"],
                    "host": host,
                    "ip": ip,
                    "mac": mac,
                    "mac_id": mac_id,
                    "online": is_online,
                }

                if detailed:
                    # Add detailed fields for monitoring endpoint
                    client_info.update({
                        "last_seen": last_seen_data,
                        "connection_quality": self._calculate_connection_quality(last_seen_data),
                        "host_info": {
                            "arch": client_data["host"].get("arch", ""),
                            "os": client_data["host"].get("os", "")
                        },
                        "snapclient_info": client_data.get("snapclient", {}),
                        "group_id": group_id
                    })
                else:
                    # Add basic last_seen_age for non-detailed requests
                    client_info["last_seen_age"] = int(last_seen_age)

                raw_clients.append(client_info)

        return self._deduplicate_by_mac(raw_clients)

    def _extract_clients(self, status: dict) -> List[Dict[str, Any]]:
        """Extract online clients from server status (basic info)."""
        return self._parse_clients(status, include_offline=False, detailed=False)

    def _deduplicate_by_mac(self, clients: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate clients based on MAC address."""
        if not clients:
            return clients

        mac_groups: Dict[str, List[Dict[str, Any]]] = {}
        for client in clients:
            mac = client.get("mac", "")
            if not mac or mac == "00:00:00:00:00:00":
                mac_groups.setdefault(client["id"], []).append(client)
            else:
                mac_groups.setdefault(mac, []).append(client)

        deduplicated = []
        for mac, group in mac_groups.items():
            if len(group) == 1:
                deduplicated.append(group[0])
            else:
                self.logger.info(
                    f"Duplicate clients detected (MAC: {mac}): "
                    f"{[c['ip'] for c in group]} - keeping first"
                )
                deduplicated.append(group[0])

        return deduplicated

    @handle_errors(default=[])
    async def get_detailed_clients(self) -> List[Dict[str, Any]]:
        """Get clients with detailed information for monitoring."""
        status = await self._request("Server.GetStatus")
        return self._parse_clients(status, include_offline=True, detailed=True)

    def _calculate_connection_quality(self, last_seen: Dict[str, Any]) -> str:
        """Calculate connection quality based on lastSeen."""
        if not last_seen:
            return "unknown"
        sec = last_seen.get("sec", 0)
        return "good" if sec > 0 else "poor"

    # === AVAILABILITY CHECK ===

    async def is_available(self) -> bool:
        """Check if Snapcast server is available."""
        try:
            result = await self._request("Server.GetRPCVersion")
            return bool(result)
        except Exception:
            return False

    async def get_server_status(self) -> dict:
        """Get complete Snapcast server status."""
        return await self._request("Server.GetStatus")

    # === SERVER CONFIGURATION ===

    @handle_errors(default={})
    async def get_server_config(self) -> Dict[str, Any]:
        """Get server configuration."""
        api_task = self._request("Server.GetStatus")
        file_task = self._read_snapserver_conf()

        status, file_config = await asyncio.gather(api_task, file_task)

        server_info = status.get("server", {})
        streams = status.get("streams", [])

        stream_config = {}
        if streams:
            first_stream = streams[0]
            uri = first_stream.get("uri", {})
            query = uri.get("query", {})

            stream_config = {
                "chunk_ms": query.get("chunk_ms", "20"),
                "codec": query.get("codec", "flac"),
                "sampleformat": query.get("sampleformat", "48000:32:2")
            }

        return {
            "server_info": server_info,
            "stream_config": stream_config,
            "file_config": file_config,
            "streams": streams,
            "rpc_version": await self._request("Server.GetRPCVersion")
        }

    @handle_errors(default={})
    async def _read_snapserver_conf(self) -> Dict[str, Any]:
        """Parse snapserver.conf file."""
        if not self.snapserver_conf.exists():
            return {}

        async with aiofiles.open(self.snapserver_conf, 'r') as f:
            content = await f.read()

        config = {}
        current_section = None

        for line in content.split('\n'):
            line = line.strip()

            if not line or line.startswith('#'):
                continue

            if line.startswith('[') and line.endswith(']'):
                current_section = line[1:-1]
                config.setdefault(current_section, {})
                continue

            if '=' in line and current_section:
                key, value = line.split('=', 1)
                key, value = key.strip(), value.strip()

                if key == 'source':
                    config[current_section].setdefault('sources', []).append(value)
                else:
                    config[current_section][key] = value

        return {"parsed_config": config, "raw_content": content}

    @handle_errors(default=False)
    async def update_server_config(self, config: Dict[str, Any]) -> bool:
        """Update server configuration and restart."""
        if not self._validate_config(config):
            return False

        # Force sampleformat to 32-bit for high-quality audio pipeline
        config["sampleformat"] = "48000:32:2"

        success = await self._update_config_file(config)
        if not success:
            return False

        return await self._restart_snapserver()

    def _validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate configuration parameters."""
        validators = {
            "buffer": lambda x: isinstance(x, int) and 100 <= x <= 2000,
            "codec": lambda x: x in ["flac", "pcm", "opus", "ogg"],
            "chunk_ms": lambda x: isinstance(x, int) and 10 <= x <= 100,
            "snapclient_buffer_time": lambda x: isinstance(x, int) and 20 <= x <= 200,
            "snapclient_fragments": lambda x: isinstance(x, int) and 2 <= x <= 8
        }

        for key, validator in validators.items():
            if key in config and not validator(config[key]):
                self.logger.error(f"Invalid {key}: {config[key]}")
                return False

        return True

    @handle_errors(default=False)
    async def _update_config_file(self, config: Dict[str, Any]) -> bool:
        """Update configuration file."""
        if not self.snapserver_conf.exists():
            self.logger.error("snapserver.conf not found")
            return False

        async with aiofiles.open(self.snapserver_conf, 'r') as f:
            content = await f.read()

        updated_content = self._modify_config_content(content, config)

        # Atomic write via temp file
        temp_file = "/tmp/snapserver_temp.conf"
        async with aiofiles.open(temp_file, 'w') as f:
            await f.write(updated_content)

        # Replace with sudo
        proc = await asyncio.create_subprocess_exec(
            "sudo", "mv", temp_file, str(self.snapserver_conf),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        _, stderr = await proc.communicate()

        if proc.returncode == 0:
            self.logger.info("snapserver.conf updated successfully")
            return True
        else:
            self.logger.error(f"Failed to update config: {stderr.decode()}")
            return False

    def _modify_config_content(self, content: str, config: Dict[str, Any]) -> str:
        """Modify file content with new configuration."""
        lines = content.split('\n')
        updated_lines = []
        in_stream_section = False

        param_mapping = {
            "buffer": "buffer",
            "codec": "codec",
            "chunk_ms": "chunk_ms",
            "sampleformat": "sampleformat"
        }

        for line in lines:
            stripped_line = line.strip()

            if stripped_line == "[stream]":
                in_stream_section = True
                updated_lines.append(line)
                continue
            elif stripped_line.startswith("[") and stripped_line != "[stream]":
                in_stream_section = False

            if (in_stream_section and "=" in stripped_line and
                not stripped_line.startswith("#")):

                key = stripped_line.split("=")[0].strip()

                if key in param_mapping and param_mapping[key] in config:
                    param_key = param_mapping[key]
                    if param_key == "sampleformat":
                        updated_lines.append("sampleformat = 48000:32:2")
                    else:
                        updated_lines.append(f"{key} = {config[param_key]}")
                else:
                    updated_lines.append(line)
            else:
                updated_lines.append(line)

        return '\n'.join(updated_lines)

    @handle_errors(default=False)
    async def _restart_snapserver(self) -> bool:
        """Restart Snapcast server."""
        self.logger.info("Restarting snapserver...")

        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", "restart", "milo-snapserver-multiroom.service",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )

        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            self.logger.error(f"Failed to restart snapserver: {stderr.decode()}")
            return False

        # Check availability
        await asyncio.sleep(3)
        for _ in range(10):
            if await self.is_available():
                self.logger.info("Snapserver restarted successfully")
                return True
            await asyncio.sleep(1)

        self.logger.warning("Snapserver restarted but API not available yet")
        return False


# === HELPER FUNCTIONS ===

async def get_online_clients(snapcast_service: SnapcastService) -> List[Dict[str, Any]]:
    """
    Get list of online clients with their mac_id.

    Args:
        snapcast_service: SnapcastService instance

    Returns:
        List of dicts with 'mac_id' and 'online' keys for online clients
    """
    clients = await snapcast_service.get_clients()
    return [
        {"mac_id": client.get("mac_id", ""), "online": client.get("online", True)}
        for client in clients
        if client.get("mac_id") and client.get("online", True)
    ]


async def get_online_client_ids(snapcast_service: SnapcastService) -> List[str]:
    """
    Get list of online client IDs (mac_ids).

    Args:
        snapcast_service: SnapcastService instance

    Returns:
        List of client IDs (mac_ids) for online clients
    """
    clients = await get_online_clients(snapcast_service)
    return [c["mac_id"] for c in clients]


