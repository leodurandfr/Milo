"""
Plugin Bluetooth avec gestion directe des processus pour oakOS
"""
import asyncio
import logging
import subprocess
import os
import sys
import time
import threading
from typing import Dict, Any, List, Optional

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.bluetooth.agent import BluetoothAgent


class BluetoothPlugin(UnifiedAudioPlugin):
    """Plugin Bluetooth pour la réception audio via A2DP"""
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "bluetooth")
        self.config = config
        self.current_device = None
        self._initialized = False
        
        # Gestion des processus
        self.bluetooth_process = None
        self.bluealsa_process = None
        self.playback_process = None
        
        # Gestion de l'agent
        self.agent = None
        self.agent_thread = None
        self.mainloop = None
    
    async def initialize(self) -> bool:
        """Initialisation simple"""
        self.logger.info("Initialisation du plugin Bluetooth")
        self._initialized = True
        return True
    
    async def _start_bluetooth_agent(self):
        """Démarre l'agent Bluetooth pour l'appairage automatique"""
        try:
            self.logger.info("Démarrage de l'agent Bluetooth")
            
            # Arrêter tout agent précédent
            if self.agent_thread and self.agent_thread.is_alive():
                self.logger.info("Arrêt d'un agent existant")
                if self.mainloop and hasattr(self.mainloop, 'is_running') and self.mainloop.is_running():
                    GLib.idle_add(self.mainloop.quit)
                self.agent_thread.join(1)  # Attendre 1 seconde max
            
            # Initialisation du thread pour l'agent Bluetooth
            self.agent_thread = threading.Thread(target=self._run_agent_thread, daemon=True)
            self.agent_thread.start()
            
            # Attendre un peu que le thread démarre
            await asyncio.sleep(1)
            
            # Vérifier si le thread s'est bien lancé
            if not self.agent_thread.is_alive():
                self.logger.error("Le thread de l'agent n'a pas démarré correctement")
                return False
                
            self.logger.info("Agent Bluetooth démarré avec succès")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage de l'agent Bluetooth: {e}")
            return False
    
    def _run_agent_thread(self):
        """Exécute l'agent Bluetooth dans un thread séparé"""
        try:
            # Initialiser le mainloop DBus
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            bus = dbus.SystemBus()
            
            # Créer et enregistrer l'agent
            self.agent = BluetoothAgent(bus)
            
            # Démarrer le mainloop dans un try/except pour capturer les erreurs
            self.mainloop = GLib.MainLoop()
            
            # Enregistrer l'agent de manière synchrone
            if self.agent.register_sync():
                self.logger.info("Agent Bluetooth enregistré et prêt")
            else:
                self.logger.warning("Échec de l'enregistrement de l'agent Bluetooth")
                
            # Exécuter le mainloop
            self.mainloop.run()
            
        except Exception as e:
            self.logger.error(f"Erreur dans le thread de l'agent Bluetooth: {e}")
    
    async def start(self) -> bool:
        """Démarre la chaîne complète: bluetooth, bluealsa, agent, puis vérifie les appareils connectés"""
        try:
            self.logger.info("Démarrage du plugin Bluetooth")
            
            # 1. Démarrer le service bluetooth
            self.logger.info("Démarrage du service bluetooth")
            subprocess.run(["sudo", "systemctl", "start", "bluetooth"], check=True)
            await asyncio.sleep(2)  # Attendre que bluetooth démarre complètement
            
            # 2. Configurer l'adaptateur bluetooth pour A2DP Sink
            self.logger.info("Configuration de l'adaptateur Bluetooth")
            subprocess.run(["sudo", "hciconfig", "hci0", "class", "0x240404"], check=False)
            subprocess.run(["sudo", "hciconfig", "hci0", "name", "oakOS"], check=False)
            
            # 3. Rendre l'appareil découvrable et appariable
            self.logger.info("Configuration des paramètres de découverte")
            subprocess.run(["sudo", "bluetoothctl", "discoverable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "pairable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "discoverable-timeout", "0"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "pairable-timeout", "0"], check=False)
            
            # 4. Démarrer l'agent Bluetooth pour l'appairage automatique
            await self._start_bluetooth_agent()
            
            # 5. Démarrer bluealsa (avec les options appropriées pour a2dp-sink)
            self.logger.info("Démarrage du service BlueALSA")
            daemon_options = self.config.get("daemon_options", "--keep-alive=5 --initial-volume=80")
            
            # Nettoyer - s'assurer qu'aucun processus bluealsa n'est en cours d'exécution
            # Tentative de kill normal d'abord
            subprocess.run(["sudo", "pkill", "-f", "bluealsa"], check=False)
            await asyncio.sleep(1)
            
            # Kill forcé si nécessaire (SIGKILL)
            subprocess.run(["sudo", "pkill", "-9", "-f", "bluealsa"], check=False)
            await asyncio.sleep(1)
            
            # Vérifier qu'aucun processus n'est en cours
            check = subprocess.run(["pgrep", "-f", "bluealsa"], capture_output=True)
            if check.returncode == 0:
                self.logger.warning("Des processus bluealsa sont toujours en cours d'exécution après tentative d'arrêt")
                
            # S'assurer que le service systemd est bien arrêté
            subprocess.run(["sudo", "systemctl", "stop", "bluealsa.service"], check=False)
            subprocess.run(["sudo", "systemctl", "stop", "bluealsa-aplay.service"], check=False)
            await asyncio.sleep(1)
            
            # Utiliser bash -c pour exécuter la commande
            cmd = f"sudo /usr/bin/bluealsa -p a2dp-sink {daemon_options}"
            self.logger.info(f"Exécution de la commande: {cmd}")
            
            # Plus direct, évite les problèmes avec sudo -> sudo -> bluealsa
            self.bluealsa_process = subprocess.Popen(
                ["sudo", "/usr/bin/bluealsa", "-p", "a2dp-sink"] + daemon_options.split(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Vérifier que le démarrage est réussi
            await asyncio.sleep(2)
            if self.bluealsa_process.poll() is not None:
                stderr = self.bluealsa_process.stderr.read().decode('utf-8')
                raise RuntimeError(f"Échec du démarrage de BlueALSA: {stderr}")
            
            # 6. Vérifier les appareils déjà connectés
            await asyncio.sleep(1)  # Attendre que BlueALSA détecte les appareils
            await self._check_connected_devices()
            
            # 7. Notifier l'état prêt
            await self.notify_state_change(PluginState.READY)
            self.logger.info("Plugin Bluetooth démarré avec succès")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur démarrage Bluetooth: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            # Tentative de nettoyage en cas d'erreur
            await self._cleanup_processes()
            return False
    
    async def _check_connected_devices(self) -> None:
        """Vérifie si un périphérique est déjà connecté et démarrer la lecture audio si nécessaire"""
        try:
            result = subprocess.run(
                ["bluetoothctl", "devices", "Connected"], 
                capture_output=True, text=True
            )
            
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split(' ', 2)
                    if len(parts) >= 2:
                        address = parts[1]
                        name = parts[2] if len(parts) > 2 else "Appareil inconnu"
                        
                        # Attendre un moment pour que le profil A2DP soit établi
                        await asyncio.sleep(2) 
                        
                        # Vérifier si le périphérique est disponible dans BlueALSA
                        check = subprocess.run(
                            ["bluealsa-aplay", "-L"], 
                            capture_output=True, text=True
                        )
                        
                        if address.lower() in check.stdout.lower():
                            # Enregistrer les infos du périphérique
                            self.current_device = {
                                "address": address,
                                "name": name
                            }
                            
                            # Démarrer la lecture audio
                            await self._start_audio_playback(address)
                            
                            # Notifier le changement d'état
                            await self.notify_state_change(PluginState.CONNECTED, {
                                "device_connected": True,
                                "device_name": name,
                                "device_address": address
                            })
                            
                            # Un seul périphérique à la fois
                            break
        except Exception as e:
            self.logger.error(f"Erreur vérification périphériques: {e}")
    
    async def _start_audio_playback(self, device_address: str) -> None:
        """Démarrage de la lecture audio avec vérifications robustes"""
        try:
            # Arrêter toute lecture audio existante
            if self.playback_process and self.playback_process.poll() is None:
                self.playback_process.terminate()
                try:
                    self.playback_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.playback_process.kill()
                
            # Utiliser pkill comme filet de sécurité supplémentaire
            subprocess.run(["sudo", "pkill", "-f", "bluealsa-aplay"], check=False)
            await asyncio.sleep(1)
            
            # Vérifier si le périphérique est disponible dans BlueALSA
            self.logger.info(f"Vérification si l'appareil {device_address} est disponible dans BlueALSA")
            check_cmd = ["bluealsa-aplay", "-L"]
            result = subprocess.run(check_cmd, capture_output=True, text=True)
            self.logger.info(f"Résultat de bluealsa-aplay -L:\n{result.stdout}")
            
            if device_address.lower() not in result.stdout.lower():
                self.logger.warning(f"Appareil {device_address} non trouvé dans BlueALSA, attente supplémentaire...")
                # Attendre que BlueALSA détecte l'appareil (peut prendre un moment)
                for i in range(10):  # Essayer 10 fois avec 1 seconde d'intervalle
                    await asyncio.sleep(1)
                    result = subprocess.run(check_cmd, capture_output=True, text=True)
                    self.logger.info(f"Tentative {i+1}: Résultat de bluealsa-aplay -L:\n{result.stdout}")
                    if device_address.lower() in result.stdout.lower():
                        self.logger.info(f"Appareil {device_address} trouvé dans BlueALSA après {i+1} tentatives")
                        break
                else:
                    self.logger.error(f"Appareil {device_address} non trouvé dans BlueALSA après plusieurs tentatives")
                    
                    # Tentative de correction - redémarrer BlueALSA
                    self.logger.info("Tentative de redémarrage de BlueALSA")
                    await self._restart_bluealsa()
                    
                    # Vérifier à nouveau
                    await asyncio.sleep(2)
                    result = subprocess.run(check_cmd, capture_output=True, text=True)
                    
                    if device_address.lower() not in result.stdout.lower():
                        self.logger.error("Impossible de trouver l'appareil même après redémarrage de BlueALSA")
                        return
            
            # Préparation pour l'exécution de bluealsa-aplay
            self.logger.info(f"Démarrage de bluealsa-aplay pour {device_address}")
            
            # Utiliser un environnement propre mais avec les variables importantes
            env = os.environ.copy()
            # Ajouter la variable d'environnement pour éviter les problèmes de thread-safety
            env["LIBASOUND_THREAD_SAFE"] = "0"
            
            # Démarrer bluealsa-aplay avec des options optimales et sudo
            cmd = ["sudo", "bluealsa-aplay", "--verbose", device_address]
            
            self.logger.info(f"Exécution de la commande: {' '.join(cmd)}")
            self.playback_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env
            )
            
            # Vérifier immédiatement si le processus a démarré correctement
            await asyncio.sleep(1)
            if self.playback_process.poll() is not None:
                stderr = self.playback_process.stderr.read().decode('utf-8')
                stdout = self.playback_process.stdout.read().decode('utf-8')
                self.logger.error(f"Erreur démarrage bluealsa-aplay: {stderr}")
                self.logger.info(f"Sortie standard bluealsa-aplay: {stdout}")
                
                # Tentatives de correction:
                if "Mixer element not found" in stderr:
                    self.logger.warning("Problème de mixer détecté, tentative sans contrôle de volume")
                    # Essayer sans contrôle de volume, avec sudo
                    cmd = ["sudo", "bluealsa-aplay", "--verbose", "--pcm-buffer-time=500000", device_address]
                    self.logger.info(f"Nouvelle tentative avec la commande: {' '.join(cmd)}")
                    self.playback_process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=env
                    )
                elif "PCM not found" in stderr:
                    self.logger.warning("PCM non trouvé, tentative avec une adresse générique")
                    # Essayer avec l'adresse générique pour tout appareil
                    cmd = ["sudo", "bluealsa-aplay", "--verbose", "00:00:00:00:00:00"]
                    self.logger.info(f"Nouvelle tentative avec la commande: {' '.join(cmd)}")
                    self.playback_process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=env
                    )
                else:
                    self.logger.warning("Tentative avec des options alternatives")
                    # Essayer avec toutes les options
                    cmd = ["sudo", "bluealsa-aplay", "--verbose", "--pcm-buffer-time=500000", "--pcm-period-time=100000", device_address]
                    self.logger.info(f"Nouvelle tentative avec la commande: {' '.join(cmd)}")
                    self.playback_process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=env
                    )
                
                # Vérifier la nouvelle tentative
                await asyncio.sleep(1)
                if self.playback_process.poll() is not None:
                    stderr = self.playback_process.stderr.read().decode('utf-8')
                    self.logger.error(f"Échec de la nouvelle tentative: {stderr}")
                    return
            
            # Vérifier si la lecture audio est réellement active
            await asyncio.sleep(1)
            check = subprocess.run(
                ["sudo", "fuser", "-v", "/dev/snd/*"],
                capture_output=True, text=True
            )
            
            if "bluealsa-aplay" in check.stdout:
                self.logger.info("Lecture audio démarrée avec succès et active")
            else:
                self.logger.warning("Le processus bluealsa-aplay est démarré mais n'utilise pas /dev/snd")
                # Vérifier le statut
                ps_check = subprocess.run(
                    ["ps", "aux", "|", "grep", "bluealsa-aplay"],
                    shell=True, capture_output=True, text=True
                )
                self.logger.info(f"Statut des processus bluealsa-aplay:\n{ps_check.stdout}")
                
        except Exception as e:
            self.logger.error(f"Erreur démarrage lecture: {e}")
    
    async def _restart_bluealsa(self) -> bool:
        """Redémarre le service BlueALSA"""
        try:
            # Arrêter BlueALSA
            if self.bluealsa_process and self.bluealsa_process.poll() is None:
                self.bluealsa_process.terminate()
                try:
                    self.bluealsa_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.bluealsa_process.kill()
            
            # Utiliser pkill comme filet de sécurité
            subprocess.run(["sudo", "pkill", "-f", "bluealsa "], check=False)
            await asyncio.sleep(1)
            
            # Démarrer BlueALSA
            daemon_options = self.config.get("daemon_options", "--keep-alive=5 --initial-volume=80")
            self.bluealsa_process = subprocess.Popen(
                ["sudo", "/usr/bin/bluealsa", "-p", "a2dp-sink"] + daemon_options.split(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Vérifier que le démarrage est réussi
            await asyncio.sleep(2)
            if self.bluealsa_process.poll() is not None:
                stderr = self.bluealsa_process.stderr.read().decode('utf-8')
                self.logger.error(f"Échec du redémarrage de BlueALSA: {stderr}")
                return False
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors du redémarrage de BlueALSA: {e}")
            return False
    
    async def stop(self) -> bool:
        """Arrête tous les processus dans l'ordre inverse du démarrage"""
        try:
            self.logger.info("Arrêt du plugin Bluetooth")
            
            # 1. Arrêter toute lecture audio et les processus
            await self._cleanup_processes()
            
            # 2. Arrêter l'agent Bluetooth
            if self.mainloop and self.agent:
                try:
                    # Désenregistrer l'agent de manière synchrone (directement, pas via asyncio)
                    if self.agent:
                        self.agent.unregister_sync()
                        
                    # Arrêter le mainloop
                    if hasattr(self.mainloop, 'is_running') and self.mainloop.is_running():
                        GLib.idle_add(self.mainloop.quit)
                except Exception as e:
                    self.logger.error(f"Erreur lors de l'arrêt de l'agent: {e}")
            
            # 3. Arrêter bluetooth
            subprocess.run(["sudo", "systemctl", "stop", "bluetooth"], check=False)
            
            # 4. Réinitialiser l'état et notifier
            self.current_device = None
            await self.notify_state_change(PluginState.INACTIVE)
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur arrêt Bluetooth: {e}")
            return False
    
    async def _cleanup_processes(self) -> None:
        """Nettoie proprement tous les processus"""
        # 1. Arrêter bluealsa-aplay
        if self.playback_process and self.playback_process.poll() is None:
            self.logger.info("Arrêt du processus bluealsa-aplay")
            self.playback_process.terminate()
            try:
                self.playback_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.playback_process.kill()
        
        # Utiliser pkill comme filet de sécurité
        subprocess.run(["pkill", "-f", "bluealsa-aplay"], check=False)
        
        # 2. Arrêter bluealsa
        if self.bluealsa_process and self.bluealsa_process.poll() is None:
            self.logger.info("Arrêt du processus bluealsa")
            self.bluealsa_process.terminate()
            try:
                self.bluealsa_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.bluealsa_process.kill()
        
        # Utiliser pkill comme filet de sécurité
        subprocess.run(["pkill", "-f", "bluealsa "], check=False)  # Espace pour éviter de correspondre à bluealsa-aplay
        
        # Attendre un peu pour que les processus s'arrêtent
        await asyncio.sleep(1)
    
    async def get_status(self) -> Dict[str, Any]:
        """État actuel du plugin avec informations étendues"""
        # Vérifier si bluetooth est en cours d'exécution
        bt_running = subprocess.run(
            ["systemctl", "is-active", "bluetooth"], 
            capture_output=True, text=True
        ).stdout.strip() == "active"
        
        # Vérifier si bluealsa est en cours d'exécution
        bluealsa_running = False
        if self.bluealsa_process and self.bluealsa_process.poll() is None:
            bluealsa_running = True
        else:
            check = subprocess.run(
                ["pgrep", "-f", "bluealsa "],  # Espace pour éviter de correspondre à bluealsa-aplay
                capture_output=True
            )
            bluealsa_running = check.returncode == 0
        
        # Vérifier si bluealsa-aplay est en cours d'exécution
        playback_running = False
        if self.playback_process and self.playback_process.poll() is None:
            playback_running = True
        else:
            check = subprocess.run(
                ["pgrep", "-f", "bluealsa-aplay"],
                capture_output=True
            )
            playback_running = check.returncode == 0
        
        # Vérifier si l'audio est actif
        audio_active = False
        try:
            check = subprocess.run(
                ["fuser", "-v", "/dev/snd/*"],
                capture_output=True, text=True
            )
            audio_active = "bluealsa-aplay" in check.stdout
        except Exception:
            pass
        
        # Récupérer la liste des PCM disponibles
        available_pcms = []
        try:
            result = subprocess.run(
                ["bluealsa-aplay", "-L"],
                capture_output=True, text=True
            )
            for line in result.stdout.strip().split('\n'):
                if line and not line.startswith(" "):
                    available_pcms.append(line)
        except Exception:
            pass
        
        # Statut de l'agent
        agent_status = {
            "running": self.agent_thread is not None and self.agent_thread.is_alive(),
            "registered": self.agent is not None
        }
        
        return {
            "device_connected": self.current_device is not None,
            "device_name": self.current_device.get("name") if self.current_device else None,
            "device_address": self.current_device.get("address") if self.current_device else None,
            "bluetooth_running": bt_running,
            "bluealsa_running": bluealsa_running,
            "playback_running": playback_running,
            "audio_active": audio_active,
            "available_pcms": available_pcms,
            "agent_status": agent_status,
            "processes": {
                "bluealsa_pid": self.bluealsa_process.pid if self.bluealsa_process and self.bluealsa_process.poll() is None else None,
                "playback_pid": self.playback_process.pid if self.playback_process and self.playback_process.poll() is None else None
            }
        }
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Commandes spécifiques au plugin Bluetooth"""
        if command == "disconnect":
            if not self.current_device:
                return {"success": False, "error": "Aucun périphérique connecté"}
            
            address = self.current_device.get("address")
            subprocess.run(["bluetoothctl", "disconnect", address], check=False)
            
            # Arrêter la lecture
            if self.playback_process and self.playback_process.poll() is None:
                self.playback_process.terminate()
                self.playback_process = None
            
            # Réinitialiser l'état
            self.current_device = None
            await self.notify_state_change(PluginState.READY, {"device_connected": False})
            
            return {"success": True}
            
        elif command == "restart_audio":
            if not self.current_device:
                return {"success": False, "error": "Aucun périphérique connecté"}
            
            address = self.current_device.get("address")
            await self._start_audio_playback(address)
            return {"success": True}
            
        elif command == "restart_bluealsa":
            try:
                # Arrêter les processus actuels
                await self._cleanup_processes()
                
                # Redémarrer BlueALSA
                daemon_options = self.config.get("daemon_options", "--keep-alive=5 --initial-volume=80")
                cmd = f"sudo /usr/bin/bluealsa -p a2dp-sink {daemon_options}"
                self.bluealsa_process = subprocess.Popen(
                    cmd.split(), 
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                # Attendre que BlueALSA démarre
                await asyncio.sleep(2)
                
                # Redémarrer la lecture audio si un appareil est connecté
                if self.current_device:
                    await self._start_audio_playback(self.current_device.get("address"))
                
                return {"success": True, "message": "BlueALSA redémarré avec succès"}
            except Exception as e:
                return {"success": False, "error": str(e)}
            
        elif command == "restart_agent":
            """Redémarre l'agent Bluetooth"""
            try:
                # Arrêter l'agent existant
                if self.mainloop and self.agent:
                    try:
                        # Désenregistrer l'agent
                        if self.agent:
                            self.agent.unregister_sync()
                            
                        # Arrêter le mainloop
                        if hasattr(self.mainloop, 'is_running') and self.mainloop.is_running():
                            GLib.idle_add(self.mainloop.quit)
                    except Exception as e:
                        self.logger.error(f"Erreur lors de l'arrêt de l'agent: {e}")
                        
                # Attendre un peu que l'agent s'arrête
                await asyncio.sleep(1)
                
                # Redémarrer l'agent
                success = await self._start_bluetooth_agent()
                return {"success": success, "message": "Agent Bluetooth redémarré avec succès" if success else "Échec du redémarrage de l'agent Bluetooth"}
            except Exception as e:
                return {"success": False, "error": str(e)}
            
        elif command == "debug_info":
            """Commande pour obtenir des informations détaillées de débogage"""
            debug_info = await self.get_status()
            
            # Ajouter des informations détaillées sur le système
            try:
                # Obtenir les processus liés à l'audio
                ps_audio = subprocess.run(
                    ["ps", "aux", "|", "grep", "-E", "blue|audio"],
                    shell=True, capture_output=True, text=True
                ).stdout
                debug_info["ps_audio"] = ps_audio.split('\n')
                
                # Informations sur les périphériques ALSA
                alsa_devices = subprocess.run(
                    ["aplay", "-l"],
                    capture_output=True, text=True
                ).stdout
                debug_info["alsa_devices"] = alsa_devices.split('\n')
                
                # Informations BlueZ
                bluez_devices = subprocess.run(
                    ["bluetoothctl", "devices"],
                    capture_output=True, text=True
                ).stdout
                debug_info["bluez_devices"] = bluez_devices.split('\n')
                
                # Informations d'appairage
                paired_devices = subprocess.run(
                    ["bluetoothctl", "paired-devices"],
                    capture_output=True, text=True
                ).stdout
                debug_info["paired_devices"] = paired_devices.split('\n')
                
                # Informations bluetoothctl show
                show_info = subprocess.run(
                    ["bluetoothctl", "show"],
                    capture_output=True, text=True
                ).stdout
                debug_info["show_info"] = show_info.split('\n')
                
            except Exception as e:
                debug_info["debug_error"] = str(e)
            
            return {"success": True, "debug_info": debug_info}
            
        return {"success": False, "error": f"Commande inconnue: {command}"}
    
    async def _handle_device_event(self, event_type: str, device: Dict[str, Any]) -> None:
        """Gère les événements de connexion/déconnexion des périphériques"""
        address = device.get("address")
        name = device.get("name", "Appareil inconnu")
        
        if event_type == "connected":
            self.logger.info(f"Périphérique connecté: {name} ({address})")
            
            # Attendre que BlueALSA détecte le périphérique
            await asyncio.sleep(2)
            
            # Vérifier si le périphérique est disponible dans BlueALSA
            check = subprocess.run(
                ["bluealsa-aplay", "-L"], 
                capture_output=True, text=True
            )
            
            # Plusieurs tentatives peuvent être nécessaires
            attempts = 0
            while address.lower() not in check.stdout.lower() and attempts < 5:
                await asyncio.sleep(1)
                check = subprocess.run(
                    ["bluealsa-aplay", "-L"], 
                    capture_output=True, text=True
                )
                attempts += 1
            
            if address.lower() in check.stdout.lower():
                self.current_device = {
                    "address": address,
                    "name": name
                }
                
                # Notifier le changement d'état
                await self.notify_state_change(PluginState.CONNECTED, {
                    "device_connected": True,
                    "device_name": name,
                    "device_address": address
                })
                
                # Démarrer la lecture audio
                await self._start_audio_playback(address)
            else:
                self.logger.warning(f"Périphérique connecté mais non détecté par BlueALSA après {attempts} tentatives")
            
        elif event_type == "disconnected" and self.current_device and self.current_device.get("address") == address:
            self.logger.info(f"Périphérique déconnecté: {name}")
            
            # Arrêter la lecture audio
            if self.playback_process and self.playback_process.poll() is None:
                self.playback_process.terminate()
                self.playback_process = None
            
            # Réinitialiser l'état
            self.current_device = None
            
            # Notifier le changement d'état
            await self.notify_state_change(PluginState.READY, {
                "device_connected": False
            })
    
    def manages_own_process(self) -> bool:
        """Le plugin gère ses propres processus"""
        return True
    
    def get_process_command(self) -> List[str]:
        """Non utilisé (manages_own_process = True)"""
        return ["true"]
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial du plugin"""
        return await self.get_status()