# backend/infrastructure/services/dependency_update_service.py
"""
Service de mise à jour des dépendances - Phase 2A (go-librespot + snapcast)
"""
import asyncio
import aiohttp
import aiofiles
import shutil
import tempfile
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, Callable, Awaitable
from backend.infrastructure.services.dependency_version_service import DependencyVersionService

class DependencyUpdateService(DependencyVersionService):
    """Service de mise à jour des dépendances - Extends DependencyVersionService"""
    
    def __init__(self):
        super().__init__()
        self.update_logger = logging.getLogger(f"{__name__}.update")
        
        # Configuration spécifique aux mises à jour (snapserver et snapclient séparés)
        self.update_config = {
            "milo": {
                "git_path": "/home/milo/milo",
                "git_branch": "main",
                "service_name": "milo-backend.service",
                "backup_path": "/var/lib/milo/backups/milo-app"
            },
            "go-librespot": {
                "binary_path": "/usr/local/bin/go-librespot",
                "config_path": "/var/lib/milo/go-librespot/config.yml",
                "service_name": "milo-go-librespot.service",
                "github_asset_pattern": "go-librespot_linux_arm64.tar.gz",
                "backup_path": "/var/lib/milo/backups/go-librespot"
            },
            "snapserver": {
                "services": ["milo-snapserver-multiroom.service"],
                "github_asset": "snapserver_*_arm64_bookworm.deb",
                "backup_path": "/var/lib/milo/backups/snapserver"
            },
            "snapclient": {
                "services": ["milo-snapclient-multiroom.service"],
                "github_asset": "snapclient_*_arm64_bookworm.deb", 
                "backup_path": "/var/lib/milo/backups/snapclient"
            }
        }
    
    async def update_dependency(self, dependency_key: str, progress_callback: Optional[Callable[[str, int], Awaitable[None]]] = None) -> Dict[str, Any]:
        """Met à jour une dépendance spécifique avec callback de progression"""
        if dependency_key not in self.update_config:
            return {"success": False, "error": f"Update not supported for {dependency_key}"}
        
        try:
            # Vérifier qu'une mise à jour est disponible
            status = await self._get_dependency_full_status(dependency_key)
            if not status.get("update_available"):
                return {"success": False, "error": "No update available"}
            
            if progress_callback:
                await progress_callback("Initializing update...", 0)
            
            # Dispatcher vers la méthode spécifique
            if dependency_key == "milo":
                return await self._update_milo_app(status, progress_callback)
            elif dependency_key == "go-librespot":
                return await self._update_go_librespot(status, progress_callback)
            elif dependency_key in ["snapserver", "snapclient"]:
                return await self._update_snapcast_component(dependency_key, status, progress_callback)
            else:
                return {"success": False, "error": f"Update handler not implemented for {dependency_key}"}
                
        except Exception as e:
            self.update_logger.error(f"Update failed for {dependency_key}: {e}")
            return {"success": False, "error": str(e)}
    
    async def _update_milo_app(self, status: Dict[str, Any], progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """Met à jour l'application Milo via git pull"""
        config = self.update_config["milo"]
        latest_version = status["latest"]["version"]

        try:
            if progress_callback:
                await progress_callback("Checking git repository status...", 10)

            # 1. Vérifier que le répertoire est bien un dépôt git
            git_dir = Path(config["git_path"]) / ".git"
            if not git_dir.exists():
                return {"success": False, "error": "Not a git repository"}

            if progress_callback:
                await progress_callback("Fetching updates from GitHub...", 15)

            # 2. Faire un git fetch pour récupérer les dernières infos
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", config["git_path"], "fetch", "origin", config["git_branch"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                return {"success": False, "error": f"Git fetch failed: {stderr.decode()}"}

            if progress_callback:
                await progress_callback("Checking for local changes...", 20)

            # 3. Vérifier s'il y a des modifications locales non commitées
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", config["git_path"], "status", "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if stdout.decode().strip():
                return {"success": False, "error": "Local changes detected. Please commit or stash them first."}

            if progress_callback:
                await progress_callback("Pulling latest changes...", 30)

            # 4. Faire le git pull
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", config["git_path"], "pull", "origin", config["git_branch"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                return {"success": False, "error": f"Git pull failed: {stderr.decode()}"}

            if progress_callback:
                await progress_callback("Installing frontend dependencies...", 40)

            # 5. Installer les dépendances npm du frontend
            frontend_dir = Path(config["git_path"]) / "frontend"
            if frontend_dir.exists():
                proc = await asyncio.create_subprocess_exec(
                    "npm", "install",
                    cwd=str(frontend_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()

                if proc.returncode != 0:
                    return {"success": False, "error": f"npm install failed: {stderr.decode()}"}

            if progress_callback:
                await progress_callback("Building frontend...", 55)

            # 6. Builder le frontend
            if frontend_dir.exists():
                proc = await asyncio.create_subprocess_exec(
                    "npm", "run", "build",
                    cwd=str(frontend_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()

                if proc.returncode != 0:
                    return {"success": False, "error": f"npm run build failed: {stderr.decode()}"}

            if progress_callback:
                await progress_callback("Installing Python dependencies...", 70)

            # 7. Installer les dépendances Python si requirements.txt existe
            requirements_file = Path(config["git_path"]) / "backend" / "requirements.txt"
            if requirements_file.exists():
                proc = await asyncio.create_subprocess_exec(
                    "pip3", "install", "-r", str(requirements_file),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.communicate()

            if progress_callback:
                await progress_callback("Restarting backend service...", 85)

            # 8. Redémarrer le service backend
            restart_result = await self._restart_service(config["service_name"])
            if not restart_result:
                return {"success": False, "error": "Failed to restart backend service"}

            if progress_callback:
                await progress_callback("Restarting kiosk...", 95)

            # 9. Redémarrer le service kiosk pour recharger le frontend
            kiosk_restart_result = await self._restart_service("milo-kiosk.service")
            if not kiosk_restart_result:
                self.update_logger.warning("Failed to restart kiosk service, but update was successful")

            if progress_callback:
                await progress_callback("Update completed!", 100)

            # 10. Récupérer la nouvelle version
            new_status = await self.get_installed_version("milo")
            new_version = list(new_status.get("versions", {}).values())[0] if new_status.get("versions") else "unknown"

            return {
                "success": True,
                "message": f"Milo application updated successfully",
                "old_version": status["installed"]["versions"].get("main", "unknown"),
                "new_version": new_version
            }

        except Exception as e:
            self.update_logger.error(f"Milo app update failed: {e}")
            return {"success": False, "error": str(e)}

    async def _update_go_librespot(self, status: Dict[str, Any], progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """Met à jour go-librespot"""
        config = self.update_config["go-librespot"]
        latest_version = status["latest"]["version"]
        
        try:
            if progress_callback:
                await progress_callback("Creating backup...", 10)
            
            # 1. Créer une sauvegarde
            backup_result = await self._backup_go_librespot(config)
            if not backup_result["success"]:
                return backup_result
            
            if progress_callback:
                await progress_callback("Downloading new version...", 20)
            
            # 2. Télécharger la nouvelle version
            download_result = await self._download_go_librespot(latest_version)
            if not download_result["success"]:
                return download_result
            
            if progress_callback:
                await progress_callback("Stopping service...", 60)
            
            # 3. Arrêter le service
            stop_result = await self._stop_service(config["service_name"])
            if not stop_result:
                return {"success": False, "error": "Failed to stop service"}
            
            if progress_callback:
                await progress_callback("Installing new version...", 70)
            
            # 4. Remplacer le binaire
            install_result = await self._install_go_librespot_binary(download_result["binary_path"])
            if not install_result["success"]:
                # Rollback
                await self._rollback_go_librespot(config)
                return install_result
            
            if progress_callback:
                await progress_callback("Starting service...", 90)
            
            # 5. Redémarrer le service
            start_result = await self._start_service(config["service_name"])
            if not start_result:
                # Rollback
                await self._rollback_go_librespot(config)
                return {"success": False, "error": "Failed to start service after update"}
            
            if progress_callback:
                await progress_callback("Verifying update...", 95)
            
            # 6. Vérifier que la mise à jour a fonctionné
            verify_result = await self._verify_go_librespot_update(latest_version)
            if not verify_result["success"]:
                # Rollback
                await self._rollback_go_librespot(config)
                return verify_result
            
            if progress_callback:
                await progress_callback("Update completed!", 100)
            
            # 7. Nettoyer les fichiers temporaires
            await self._cleanup_temp_files(download_result.get("temp_dir"))
            
            return {
                "success": True,
                "message": f"go-librespot updated to {latest_version}",
                "old_version": status["installed"]["versions"].get("main"),
                "new_version": latest_version
            }
            
        except Exception as e:
            # Rollback en cas d'erreur
            await self._rollback_go_librespot(config)
            self.update_logger.error(f"go-librespot update failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _update_snapcast_component(self, component_key: str, status: Dict[str, Any], progress_callback: Optional[Callable] = None) -> Dict[str, Any]:
        """Met à jour un composant snapcast (snapserver ou snapclient)"""
        config = self.update_config[component_key]
        latest_version = status["latest"]["version"]
        
        try:
            if progress_callback:
                await progress_callback(f"Downloading {component_key}...", 10)
            
            # 1. Télécharger le package .deb
            download_result = await self._download_snapcast_component(component_key, latest_version)
            if not download_result["success"]:
                return download_result
            
            if progress_callback:
                await progress_callback(f"Stopping {component_key} service...", 30)
            
            # 2. Arrêter le service
            for service in config["services"]:
                await self._stop_service(service)
            
            if progress_callback:
                await progress_callback(f"Installing {component_key}...", 50)
            
            # 3. Installer le package
            install_result = await self._install_deb_package(download_result["deb_path"])
            if not install_result["success"]:
                return install_result
            
            if progress_callback:
                await progress_callback(f"Starting {component_key} service...", 90)
            
            # 4. Redémarrer le service
            for service in config["services"]:
                start_result = await self._start_service(service)
                if not start_result:
                    return {"success": False, "error": f"Failed to start {service}"}
            
            if progress_callback:
                await progress_callback("Update completed!", 100)
            
            # 5. Nettoyer
            await self._cleanup_temp_files(download_result.get("temp_dir"))
            
            return {
                "success": True,
                "message": f"{component_key} updated to {latest_version}",
                "old_version": status["installed"]["versions"].get("main"),
                "new_version": latest_version
            }
            
        except Exception as e:
            self.update_logger.error(f"{component_key} update failed: {e}")
            return {"success": False, "error": str(e)}
    
    # === MÉTHODES UTILITAIRES ===
    
    async def _backup_go_librespot(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Sauvegarde go-librespot"""
        try:
            backup_dir = Path(config["backup_path"])
            backup_dir.mkdir(parents=True, exist_ok=True)
            
            # Sauvegarder le binaire
            binary_backup = backup_dir / "go-librespot.backup"
            shutil.copy2(config["binary_path"], binary_backup)
            
            # Sauvegarder la config si elle existe
            config_path = Path(config["config_path"])
            if config_path.exists():
                config_backup = backup_dir / "config.yml.backup"
                shutil.copy2(config_path, config_backup)
            
            return {"success": True, "backup_dir": str(backup_dir)}
            
        except Exception as e:
            return {"success": False, "error": f"Backup failed: {e}"}
    
    async def _download_go_librespot(self, version: str) -> Dict[str, Any]:
        """Télécharge go-librespot depuis GitHub"""
        try:
            # Créer un répertoire temporaire
            temp_dir = tempfile.mkdtemp()
            
            # URL de téléchargement
            url = f"https://github.com/devgianlu/go-librespot/releases/download/v{version}/go-librespot_linux_arm64.tar.gz"
            
            # Télécharger
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return {"success": False, "error": f"Download failed: HTTP {response.status}"}
                    
                    archive_path = Path(temp_dir) / "go-librespot.tar.gz"
                    async with aiofiles.open(archive_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)
            
            # Extraire l'archive
            extract_dir = Path(temp_dir) / "extracted"
            extract_dir.mkdir()
            
            proc = await asyncio.create_subprocess_exec(
                "tar", "-xzf", str(archive_path), "-C", str(extract_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            
            if proc.returncode != 0:
                return {"success": False, "error": "Failed to extract archive"}
            
            # Trouver le binaire
            binary_path = extract_dir / "go-librespot"
            if not binary_path.exists():
                return {"success": False, "error": "Binary not found in archive"}
            
            return {
                "success": True,
                "binary_path": str(binary_path),
                "temp_dir": temp_dir
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
        
        
    async def _get_debian_codename(self) -> str:
        """Détecte la version Debian du système (bookworm, trixie, etc.)"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", "source /etc/os-release && echo $VERSION_CODENAME",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, _ = await proc.communicate()
            codename = stdout.decode().strip()

            if codename:
                self.update_logger.info(f"Detected Debian codename: {codename}")
                return codename
            else:
                self.update_logger.warning("Could not detect Debian codename, using 'bookworm' as fallback")
                return "bookworm"

        except Exception as e:
            self.update_logger.error(f"Error detecting Debian codename: {e}, using 'bookworm' as fallback")
            return "bookworm"

    async def _download_snapcast_component(self, component_key: str, version: str) -> Dict[str, Any]:
        """Télécharge un composant snapcast (.deb) avec détection auto de Debian"""
        try:
            # Détecter la version Debian
            debian_codename = await self._get_debian_codename()

            temp_dir = tempfile.mkdtemp()

            # Déterminer le nom du package selon le composant
            if component_key == "snapserver":
                package_name = f"snapserver_{version}-1_arm64_{debian_codename}.deb"
            elif component_key == "snapclient":
                package_name = f"snapclient_{version}-1_arm64_{debian_codename}.deb"
            else:
                return {"success": False, "error": f"Unknown component: {component_key}"}

            # URL de téléchargement
            url = f"https://github.com/badaix/snapcast/releases/download/v{version}/{package_name}"

            self.update_logger.info(f"Downloading {package_name} from GitHub (Debian {debian_codename})...")
            
            # Télécharger
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return {"success": False, "error": f"Download failed: HTTP {response.status}"}
                    
                    deb_path = Path(temp_dir) / package_name
                    async with aiofiles.open(deb_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)
            
            return {
                "success": True,
                "deb_path": str(deb_path),
                "temp_dir": temp_dir
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _download_snapcast_debs(self, version: str) -> Dict[str, Any]:
        """Télécharge les packages .deb snapcast"""
        try:
            temp_dir = tempfile.mkdtemp()
            
            # URLs des packages
            base_url = f"https://github.com/badaix/snapcast/releases/download/v{version}"
            server_url = f"{base_url}/snapserver_{version}-1_arm64_bookworm.deb"
            client_url = f"{base_url}/snapclient_{version}-1_arm64_bookworm.deb"
            
            server_path = Path(temp_dir) / f"snapserver_{version}.deb"
            client_path = Path(temp_dir) / f"snapclient_{version}.deb"
            
            # Télécharger les deux packages
            async with aiohttp.ClientSession() as session:
                # Server
                async with session.get(server_url) as response:
                    if response.status != 200:
                        return {"success": False, "error": f"Server download failed: HTTP {response.status}"}
                    
                    async with aiofiles.open(server_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)
                
                # Client
                async with session.get(client_url) as response:
                    if response.status != 200:
                        return {"success": False, "error": f"Client download failed: HTTP {response.status}"}
                    
                    async with aiofiles.open(client_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            await f.write(chunk)
            
            return {
                "success": True,
                "server_deb": str(server_path),
                "client_deb": str(client_path),
                "temp_dir": temp_dir
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _install_go_librespot_binary(self, binary_path: str) -> Dict[str, Any]:
        """Installe le nouveau binaire go-librespot"""
        try:
            # Copier avec sudo
            proc = await asyncio.create_subprocess_exec(
                "sudo", "cp", binary_path, "/usr/local/bin/go-librespot",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                return {"success": False, "error": f"Failed to copy binary: {stderr.decode()}"}
            
            # Définir les permissions
            proc = await asyncio.create_subprocess_exec(
                "sudo", "chmod", "+x", "/usr/local/bin/go-librespot",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            
            return {"success": True}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _install_deb_package(self, deb_path: str) -> Dict[str, Any]:
        """Installe un package .deb avec dpkg + apt-get -f (méthode officielle snapcast)"""
        try:
            env = {
                "DEBIAN_FRONTEND": "noninteractive",
                "DEBCONF_NONINTERACTIVE_SEEN": "true",
                "APT_LISTCHANGES_FRONTEND": "none"
            }

            # Étape 1 : Mettre à jour la liste des paquets
            self.update_logger.info("Updating APT package list...")
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-E", "apt", "update",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **env}
            )
            await proc.communicate()

            # Étape 2a : Installer le .deb avec dpkg (peut échouer si dépendances manquantes - c'est normal)
            # --force-confdef --force-confold : garde automatiquement les anciennes configs sans prompt
            self.update_logger.info(f"Installing {Path(deb_path).name} with dpkg...")
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-E", "dpkg", "-i", "--force-confdef", "--force-confold", deb_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **env}
            )

            dpkg_stdout, dpkg_stderr = await proc.communicate()

            # Note: dpkg peut retourner une erreur si des dépendances manquent, c'est prévu
            if proc.returncode != 0:
                self.update_logger.info(f"dpkg returned non-zero (expected if dependencies missing): {dpkg_stderr.decode()}")

            # Étape 2b : Résoudre les dépendances avec apt-get -f install
            # C'est cette étape qui détermine le succès ou l'échec final
            self.update_logger.info("Resolving dependencies with apt-get -f install...")
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-E", "apt-get", "-f", "install", "-y",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **env}
            )

            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                self.update_logger.info("Package installed successfully with all dependencies resolved")
                return {"success": True}
            else:
                return {
                    "success": False,
                    "error": f"Dependency resolution failed: {stderr.decode()}"
                }

        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _stop_service(self, service_name: str) -> bool:
        """Arrête un service systemd"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "stop", service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            
            return proc.returncode == 0
            
        except Exception as e:
            self.update_logger.error(f"Failed to stop {service_name}: {e}")
            return False
    
    async def _start_service(self, service_name: str) -> bool:
        """Démarre un service systemd"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "start", service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                return False

            # Attendre que le service soit vraiment démarré
            await asyncio.sleep(2)

            # Vérifier l'état
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            return stdout.decode().strip() == "active"

        except Exception as e:
            self.update_logger.error(f"Failed to start {service_name}: {e}")
            return False

    async def _restart_service(self, service_name: str) -> bool:
        """Redémarre un service systemd"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "restart", service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                self.update_logger.error(f"Failed to restart {service_name}: {stderr.decode()}")
                return False

            # Attendre que le service soit vraiment démarré
            await asyncio.sleep(2)

            # Vérifier l'état
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            return stdout.decode().strip() == "active"

        except Exception as e:
            self.update_logger.error(f"Failed to restart {service_name}: {e}")
            return False
    
    async def _verify_go_librespot_update(self, expected_version: str) -> Dict[str, Any]:
        """Vérifie que go-librespot a été mis à jour correctement"""
        try:
            # Vérifier la version installée
            result = await self.get_installed_version("go-librespot")
            
            if result["status"] != "installed":
                return {"success": False, "error": "go-librespot not detected after update"}
            
            installed_version = list(result["versions"].values())[0]
            
            if installed_version != expected_version:
                return {"success": False, "error": f"Version mismatch: expected {expected_version}, got {installed_version}"}
            
            return {"success": True, "verified_version": installed_version}
            
        except Exception as e:
            return {"success": False, "error": f"Verification failed: {e}"}
    
    async def _rollback_go_librespot(self, config: Dict[str, Any]) -> bool:
        """Rollback go-librespot vers la version sauvegardée"""
        try:
            backup_dir = Path(config["backup_path"])
            binary_backup = backup_dir / "go-librespot.backup"
            
            if not binary_backup.exists():
                self.update_logger.error("No backup found for rollback")
                return False
            
            # Arrêter le service
            await self._stop_service(config["service_name"])
            
            # Restaurer le binaire
            proc = await asyncio.create_subprocess_exec(
                "sudo", "cp", str(binary_backup), config["binary_path"],
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            
            # Redémarrer le service
            await self._start_service(config["service_name"])
            
            self.update_logger.info("go-librespot rollback completed")
            return True
            
        except Exception as e:
            self.update_logger.error(f"Rollback failed: {e}")
            return False
    
    async def _cleanup_temp_files(self, temp_dir: Optional[str]) -> None:
        """Nettoie les fichiers temporaires"""
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                self.update_logger.warning(f"Failed to cleanup {temp_dir}: {e}")
    
    async def can_update_dependency(self, dependency_key: str) -> Dict[str, Any]:
        """Vérifie si une dépendance peut être mise à jour"""
        if dependency_key not in self.update_config:
            return {"can_update": False, "reason": "Update not supported"}
        
        # Vérifier les permissions sudo
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", "true",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            
            if proc.returncode != 0:
                return {"can_update": False, "reason": "Sudo access required"}
        
        except Exception:
            return {"can_update": False, "reason": "Cannot check sudo access"}
        
        # Vérifier qu'une mise à jour est disponible
        status = await self._get_dependency_full_status(dependency_key)
        if not status.get("update_available"):
            return {"can_update": False, "reason": "No update available"}
        
        return {"can_update": True, "available_version": status["latest"]["version"]}