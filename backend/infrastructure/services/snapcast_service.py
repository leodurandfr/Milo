# backend/infrastructure/services/snapcast_service.py
"""
Simplified Snapcast service - REST commands only (WebSocket service handles notifications)
"""
import aiohttp
import asyncio
import aiofiles
import logging
import re
from typing import List, Dict, Any, Optional
from pathlib import Path

class SnapcastService:
    """Simplified Snapcast service - REST commands only"""

    def __init__(self, host: str = "localhost", port: int = 1780):
        self.base_url = f"http://{host}:{port}/jsonrpc"
        self.logger = logging.getLogger(__name__)
        self._request_id = 0
        self.snapserver_conf = Path("/etc/snapserver.conf")

    async def _request(self, method: str, params: dict = None) -> dict:
        """Simplified JSON-RPC request to Snapcast"""
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
            self.logger.error(f"Snapcast request failed: {e}")
            return {}
    
    async def set_all_groups_to_multiroom(self) -> bool:
        """Switch all groups to Multiroom stream"""
        try:
            # Get server status
            status = await self._request("Server.GetStatus")
            if not status:
                return False

            # Extract groups
            groups = status.get("server", {}).get("groups", [])

            # Switch each group to "Multiroom"
            for group in groups:
                group_id = group.get("id")
                if group_id:
                    await self._request("Group.SetStream", {
                        "id": group_id,
                        "stream_id": "Multiroom"
                    })

            return True

        except Exception as e:
            self.logger.error(f"Error setting groups to multiroom: {e}")
            return False

    async def set_client_group_to_multiroom(self, client_id: str) -> bool:
        """Switch a client's group to Multiroom stream"""
        try:
            # Get status to find client's group
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

            # Switch group to Multiroom
            result = await self._request("Group.SetStream", {
                "id": client_group_id,
                "stream_id": "Multiroom"
            })

            self.logger.info(f"Client {client_id} group switched to Multiroom: {bool(result)}")
            return bool(result)
            
        except Exception as e:
            self.logger.error(f"Error setting client group to multiroom: {e}")
            return False
    
    # === CLIENT COMMANDS (REST only) ===

    async def set_volume(self, client_id: str, volume: int) -> bool:
        """Change a client's volume"""
        try:
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

        except Exception as e:
            self.logger.error(f"Error setting volume: {e}")
            return False

    async def set_mute(self, client_id: str, muted: bool) -> bool:
        """Mute/unmute a client"""
        try:
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

        except Exception as e:
            self.logger.error(f"Error setting mute: {e}")
            return False

    async def set_client_latency(self, client_id: str, latency: int) -> bool:
        """Set a client's latency"""
        try:
            result = await self._request("Client.SetLatency", {
                "id": client_id,
                "latency": max(0, min(1000, latency))
            })
            return bool(result)

        except Exception as e:
            self.logger.error(f"Error setting client latency: {e}")
            return False

    async def set_client_name(self, client_id: str, name: str) -> bool:
        """Set a client's name"""
        try:
            result = await self._request("Client.SetName", {
                "id": client_id,
                "name": name.strip()
            })
            return bool(result)

        except Exception as e:
            self.logger.error(f"Error setting client name: {e}")
            return False

    # === STATE QUERIES (for REST APIs) ===

    async def get_clients(self) -> List[Dict[str, Any]]:
        """Get clients (used by REST APIs)"""
        try:
            status = await self._request("Server.GetStatus")
            return self._extract_clients(status)
        except Exception as e:
            self.logger.error(f"Error getting clients: {e}")
            return []
    
    def _extract_clients(self, status: dict) -> List[Dict[str, Any]]:
        """Extract and filter clients from server status"""
        clients = []
        exclude_names = {'snapweb client', 'snapweb'}

        for group in status.get("server", {}).get("groups", []):
            for client_data in group.get("clients", []):
                if not client_data.get("connected"):
                    continue

                name = client_data["config"]["name"] or client_data["host"]["name"]
                if any(exclude in name.lower() for exclude in exclude_names):
                    continue

                host = client_data["host"]["name"]
                ip = client_data["host"]["ip"].replace("::ffff:", "")

                # dsp_id: identifier used by DSP linked_groups
                # "local" for the main Milo server, IP address for remote clients
                dsp_id = "local" if host == "milo" else ip

                clients.append({
                    "id": client_data["id"],
                    "name": name,
                    "volume": client_data["config"]["volume"]["percent"],
                    "muted": client_data["config"]["volume"]["muted"],
                    "host": host,
                    "ip": ip,
                    "dsp_id": dsp_id
                })

        return clients
    
    async def get_detailed_clients(self) -> List[Dict[str, Any]]:
        """Get clients with detailed information"""
        try:
            status = await self._request("Server.GetStatus")
            clients = []
            exclude_names = {'snapweb client', 'snapweb'}

            for group in status.get("server", {}).get("groups", []):
                for client_data in group.get("clients", []):
                    if not client_data.get("connected"):
                        continue

                    name = client_data["config"]["name"] or client_data["host"]["name"]
                    if any(exclude in name.lower() for exclude in exclude_names):
                        continue

                    host = client_data["host"]["name"]
                    ip = client_data["host"]["ip"].replace("::ffff:", "")
                    last_seen = client_data.get("lastSeen", {})

                    # dsp_id: identifier used by DSP linked_groups
                    # "local" for the main Milo server, IP address for remote clients
                    dsp_id = "local" if host == "milo" else ip

                    clients.append({
                        "id": client_data["id"],
                        "name": name,
                        "volume": client_data["config"]["volume"]["percent"],
                        "muted": client_data["config"]["volume"]["muted"],
                        "host": host,
                        "ip": ip,
                        "dsp_id": dsp_id,
                        "mac": client_data["host"]["mac"],
                        "latency": client_data["config"]["latency"],
                        "last_seen": last_seen,
                        "connection_quality": self._calculate_connection_quality(last_seen),
                        "host_info": {
                            "arch": client_data["host"].get("arch", ""),
                            "os": client_data["host"].get("os", "")
                        },
                        "snapclient_info": client_data.get("snapclient", {}),
                        "group_id": group["id"]
                    })

            return clients
            
        except Exception as e:
            self.logger.error(f"Error getting detailed clients: {e}")
            return []
    
    def _calculate_connection_quality(self, last_seen: Dict[str, Any]) -> str:
        """Calculate connection quality based on lastSeen"""
        if not last_seen:
            return "unknown"
        
        sec = last_seen.get("sec", 0)
        return "good" if sec > 0 else "poor"
    
    # === AVAILABILITY CHECK ===

    async def is_available(self) -> bool:
        """Check if Snapcast is available"""
        try:
            result = await self._request("Server.GetRPCVersion")
            return bool(result)
        except:
            return False

    async def get_server_status(self) -> dict:
        """Get complete Snapcast server status"""
        return await self._request("Server.GetStatus")

    # === SERVER CONFIGURATION ===

    async def get_server_config(self) -> Dict[str, Any]:
        """Get server configuration"""
        try:
            # Get API info and read file in parallel
            api_task = self._request("Server.GetStatus")
            file_task = self._read_snapserver_conf()

            status, file_config = await asyncio.gather(api_task, file_task)

            # Process API data
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
                    "sampleformat": query.get("sampleformat", "48000:16:2")
                }
            
            return {
                "server_info": server_info,
                "stream_config": stream_config,
                "file_config": file_config,
                "streams": streams,
                "rpc_version": await self._request("Server.GetRPCVersion")
            }
            
        except Exception as e:
            self.logger.error(f"Error getting server config: {e}")
            return {}
    
    async def _read_snapserver_conf(self) -> Dict[str, Any]:
        """Parser for snapserver.conf"""
        try:
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
            
        except Exception as e:
            self.logger.error(f"Error reading snapserver.conf: {e}")
            return {}
    
    async def update_server_config(self, config: Dict[str, Any]) -> bool:
        """Update server configuration"""
        try:
            if not self._validate_config(config):
                return False

            # Force sampleformat
            config["sampleformat"] = "48000:16:2"

            success = await self._update_config_file(config)
            if not success:
                return False

            return await self._restart_snapserver()

        except Exception as e:
            self.logger.error(f"Error updating server config: {e}")
            return False

    def _validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate parameters"""
        validators = {
            "buffer": lambda x: isinstance(x, int) and 100 <= x <= 2000,
            "codec": lambda x: x in ["flac", "pcm", "opus", "ogg"],
            "chunk_ms": lambda x: isinstance(x, int) and 10 <= x <= 100
        }
        
        for key, validator in validators.items():
            if key in config and not validator(config[key]):
                self.logger.error(f"Invalid {key}: {config[key]}")
                return False
        
        return True
    
    async def _update_config_file(self, config: Dict[str, Any]) -> bool:
        """Update configuration file"""
        try:
            if not self.snapserver_conf.exists():
                self.logger.error("snapserver.conf not found")
                return False

            async with aiofiles.open(self.snapserver_conf, 'r') as f:
                content = await f.read()

            updated_content = self._modify_config_content(content, config)

            # Atomic write
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

        except Exception as e:
            self.logger.error(f"Error updating config file: {e}")
            return False

    def _modify_config_content(self, content: str, config: Dict[str, Any]) -> str:
        """Modify file content"""
        lines = content.split('\n')
        updated_lines = []
        in_stream_section = False

        # Mapping of parameters to modify
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
                        updated_lines.append(f"sampleformat = 48000:16:2")
                    else:
                        updated_lines.append(f"{key} = {config[param_key]}")
                else:
                    updated_lines.append(line)
            else:
                updated_lines.append(line)
        
        return '\n'.join(updated_lines)
    
    async def _restart_snapserver(self) -> bool:
        """Restart Snapcast server"""
        try:
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

        except Exception as e:
            self.logger.error(f"Error restarting snapserver: {e}")
            return False