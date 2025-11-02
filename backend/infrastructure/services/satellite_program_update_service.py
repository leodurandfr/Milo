# backend/infrastructure/services/satellite_program_update_service.py
"""
Service de mise à jour des programmes satellites - Version avec support token GitHub
"""
import asyncio
import aiohttp
import logging
import os
from typing import Dict, Any, List, Optional

class SatelliteProgramUpdateService:
    """Service pour gérer les satellites et leurs mises à jour"""

    def __init__(self, snapcast_service):
        self.snapcast_service = snapcast_service
        self.logger = logging.getLogger(__name__)
        self.satellite_api_port = 8001

        # Token GitHub (optionnel)
        self.github_token = os.environ.get('GITHUB_TOKEN')
        if self.github_token:
            self.logger.info("GitHub token detected for satellite updates")

        # Cache pour les satellites détectés
        self._satellites_cache = {}
        self._cache_timeout = 30  # 30 secondes
        self._last_cache_time = 0

    def _get_github_headers(self) -> Dict[str, str]:
        """Retourne les headers pour les requêtes GitHub (avec token si disponible)"""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Milo-Audio-System"
        }

        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"

        return headers

    async def discover_satellites(self) -> List[Dict[str, Any]]:
        """Découvre les satellites actifs sur le réseau"""
        try:
            # Récupérer les clients Snapcast
            clients = await self.snapcast_service.get_clients()

            satellites = []

            for client in clients:
                # Filtrer uniquement les clients avec hostname milo-sat-*
                hostname = client.get("host", "")
                if not hostname.startswith("milo-sat-"):
                    continue

                ip = client.get("ip", "")
                if not ip:
                    continue

                # Tester si l'API satellite répond
                satellite_info = await self._check_satellite_api(hostname, ip)

                if satellite_info["online"]:
                    satellites.append({
                        "hostname": hostname,
                        "display_name": client.get("name", hostname),
                        "ip": ip,
                        "snapclient_version": satellite_info.get("version"),
                        "online": True,
                        "uptime": satellite_info.get("uptime"),
                        "snapclient_running": satellite_info.get("running", False)
                    })

            self.logger.info(f"Discovered {len(satellites)} satellites")
            return satellites

        except Exception as e:
            self.logger.error(f"Error discovering satellites: {e}")
            return []

    async def _check_satellite_api(self, hostname: str, ip: str) -> Dict[str, Any]:
        """Vérifie si l'API d'un satellite répond et récupère ses infos"""
        try:
            url = f"http://{ip}:{self.satellite_api_port}/status"

            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()

                        return {
                            "online": True,
                            "version": data.get("snapclient", {}).get("version"),
                            "running": data.get("snapclient", {}).get("running", False),
                            "uptime": data.get("uptime")
                        }

            return {"online": False}

        except Exception as e:
            self.logger.debug(f"Satellite {hostname} ({ip}) not reachable: {e}")
            return {"online": False}

    async def get_satellite_status(self, hostname: str) -> Dict[str, Any]:
        """Récupère le statut complet d'un satellite spécifique"""
        try:
            satellites = await self.discover_satellites()

            for satellite in satellites:
                if satellite["hostname"] == hostname:
                    # Enrichir avec version disponible
                    latest_version = await self._get_latest_snapclient_version()
                    satellite["latest_version"] = latest_version
                    satellite["update_available"] = self._compare_versions(
                        satellite.get("snapclient_version"),
                        latest_version
                    )

                    return {
                        "status": "success",
                        "satellite": satellite
                    }

            return {
                "status": "error",
                "message": f"Satellite {hostname} not found or offline"
            }

        except Exception as e:
            self.logger.error(f"Error getting satellite status: {e}")
            return {
                "status": "error",
                "message": str(e)
            }

    async def update_satellite(
        self,
        hostname: str,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """Lance la mise à jour d'un satellite"""
        try:
            # Récupérer l'IP du satellite
            satellites = await self.discover_satellites()
            satellite = next((s for s in satellites if s["hostname"] == hostname), None)

            if not satellite:
                return {
                    "success": False,
                    "error": f"Satellite {hostname} not found or offline"
                }

            ip = satellite["ip"]
            url = f"http://{ip}:{self.satellite_api_port}/update"

            if progress_callback:
                await progress_callback(f"Starting update for {hostname}", 0)

            # Lancer la mise à jour via l'API du satellite
            timeout = aiohttp.ClientTimeout(total=300)  # 5 minutes timeout
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url) as response:
                    if response.status == 200:
                        data = await response.json()

                        if data.get("success"):
                            if progress_callback:
                                await progress_callback(
                                    f"Update initiated on {hostname}",
                                    10
                                )

                            # Attendre que la mise à jour se termine
                            update_result = await self._wait_for_update_completion(
                                hostname,
                                ip,
                                progress_callback
                            )

                            return update_result
                        else:
                            return {
                                "success": False,
                                "error": data.get("message", "Update failed")
                            }
                    else:
                        return {
                            "success": False,
                            "error": f"HTTP {response.status}"
                        }

        except Exception as e:
            self.logger.error(f"Error updating satellite {hostname}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _wait_for_update_completion(
        self,
        hostname: str,
        ip: str,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """Attend la fin de la mise à jour sur le satellite"""
        max_wait_time = 180  # 3 minutes max
        check_interval = 5   # Vérifier toutes les 5 secondes
        elapsed = 0

        while elapsed < max_wait_time:
            await asyncio.sleep(check_interval)
            elapsed += check_interval

            progress = min(10 + (elapsed / max_wait_time * 80), 90)

            if progress_callback:
                await progress_callback(
                    f"Update in progress on {hostname}...",
                    int(progress)
                )

            # Vérifier le statut de la mise à jour
            try:
                url = f"http://{ip}:{self.satellite_api_port}/update/status"
                timeout = aiohttp.ClientTimeout(total=3)

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()

                            if not data.get("update_in_progress", False):
                                # Mise à jour terminée, vérifier la nouvelle version
                                status_url = f"http://{ip}:{self.satellite_api_port}/status"

                                async with session.get(status_url) as status_response:
                                    if status_response.status == 200:
                                        status_data = await status_response.json()
                                        new_version = status_data.get("snapclient", {}).get("version")

                                        if progress_callback:
                                            await progress_callback(
                                                f"Update completed on {hostname}",
                                                100
                                            )

                                        return {
                                            "success": True,
                                            "message": f"Satellite {hostname} updated successfully",
                                            "new_version": new_version
                                        }

            except Exception as e:
                self.logger.debug(f"Waiting for update on {hostname}: {e}")
                continue

        # Timeout
        return {
            "success": False,
            "error": f"Update timeout for {hostname}"
        }

    async def _get_latest_snapclient_version(self) -> Optional[str]:
        """Récupère la dernière version de snapclient depuis GitHub avec token"""
        try:
            url = "https://api.github.com/repos/badaix/snapcast/releases/latest"
            headers = self._get_github_headers()

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        tag_name = data.get("tag_name", "")

                        # Extraire le numéro de version (v0.31.0 -> 0.31.0)
                        return tag_name.lstrip('v')
                    elif response.status == 403:
                        self.logger.warning("GitHub API rate limit - snapclient version unavailable")

            return None

        except Exception as e:
            self.logger.error(f"Error getting latest snapclient version: {e}")
            return None

    def _compare_versions(self, current: Optional[str], latest: Optional[str]) -> bool:
        """Compare deux versions (retourne True si mise à jour disponible)"""
        if not current or not latest:
            return False

        try:
            def parse_version(version_str):
                clean_version = version_str.replace('v', '')
                parts = clean_version.split('.')
                while len(parts) < 3:
                    parts.append('0')
                return [int(p) for p in parts[:3]]

            current_parts = parse_version(current)
            latest_parts = parse_version(latest)

            for i in range(3):
                if latest_parts[i] > current_parts[i]:
                    return True
                elif latest_parts[i] < current_parts[i]:
                    return False

            return False

        except Exception:
            return False
