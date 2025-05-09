"""
Plugin Bluetooth simplifié pour oakOS - Version optimisée pour la première connexion
"""
import asyncio
import logging
import subprocess
import os
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
        
        # Processus gérés
        self.bluealsa_process = None
        self.playback_process = None
        
        # Agent Bluetooth et D-Bus
        self.agent = None
        self.agent_thread = None
        self.mainloop = None
        self.system_bus = None
        self.signal_match = None
        
        # Surveillance des périphériques
        self._device_monitor_task = None
        self._monitoring_active = False
    
    async def initialize(self) -> bool:
        """Initialisation simple"""
        self.logger.info("Initialisation du plugin Bluetooth")
        self._initialized = True
        return True
    
    def _run_agent_thread(self):
        """Exécute l'agent Bluetooth dans un thread séparé"""
        try:
            # Configuration D-Bus mainloop
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self.system_bus = dbus.SystemBus()
            
            # Créer et enregistrer l'agent
            self.agent = BluetoothAgent(self.system_bus)
            self.mainloop = GLib.MainLoop()
            
            # Configurer les événements D-Bus pour la détection d'appareils
            self._setup_device_signals()
            
            # Enregistrer l'agent
            if self.agent.register_sync():
                self.logger.info("Agent Bluetooth enregistré et prêt")
            else:
                self.logger.warning("Échec de l'enregistrement de l'agent Bluetooth")
                
            # Exécuter le mainloop
            self.mainloop.run()
            
        except Exception as e:
            self.logger.error(f"Erreur thread agent: {e}")
    
    def _setup_device_signals(self):
        """Configuration des événements D-Bus pour détecter les connexions Bluetooth"""
        try:
            # S'abonner aux changements de propriétés des périphériques
            self.signal_match = self.system_bus.add_signal_receiver(
                self._device_property_changed,
                dbus_interface="org.freedesktop.DBus.Properties",
                signal_name="PropertiesChanged",
                arg0="org.bluez.Device1",
                path_keyword="path"
            )
            self.logger.info("Événements D-Bus configurés avec succès")
        except Exception as e:
            self.logger.error(f"Erreur configuration événements D-Bus: {e}")
    
    def _device_property_changed(self, interface, changed, invalidated, path):
        """Callback pour les changements de propriétés D-Bus (connexion/déconnexion)"""
        try:
            # Vérifier si c'est un changement de connexion
            if "Connected" in changed:
                is_connected = changed["Connected"]
                
                # Obtenir les informations du périphérique
                device_obj = self.system_bus.get_object("org.bluez", path)
                props = dbus.Interface(device_obj, "org.freedesktop.DBus.Properties")
                
                address = str(props.Get("org.bluez.Device1", "Address"))
                try:
                    name = str(props.Get("org.bluez.Device1", "Name"))
                except:
                    name = "Appareil inconnu"
                
                if is_connected:
                    # Lorsqu'un appareil se connecte, on lance simplement la surveillance des PCMs
                    self.logger.info(f"Appareil connecté: {name} ({address})")
                    
                    # Démarrer la surveillance si elle n'est pas déjà active
                    if not self._monitoring_active:
                        self._start_pcm_monitor()
                else:
                    # Appareil déconnecté
                    self.logger.info(f"Appareil déconnecté: {name} ({address})")
                    if self.current_device and self.current_device.get("address") == address:
                        # Créer une fonction lambda pour appeler la méthode asynchrone 
                        # depuis le contexte non-asyncio du signal D-Bus
                        def handle_disconnect():
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            try:
                                loop.run_until_complete(self._handle_device_disconnected(address, name))
                            finally:
                                loop.close()
                        
                        # Exécuter dans un thread séparé
                        threading.Thread(target=handle_disconnect, daemon=True).start()
                
        except Exception as e:
            self.logger.error(f"Erreur traitement événement: {e}")
    
    def _start_pcm_monitor(self):
        """Démarre la surveillance des PCMs BlueALSA dans un thread séparé"""
        def monitor_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._monitoring_active = True
            try:
                loop.run_until_complete(self._monitor_bluealsa_pcms())
            finally:
                loop.close()
                self._monitoring_active = False
        
        threading.Thread(target=monitor_thread, daemon=True).start()
    
    async def _monitor_bluealsa_pcms(self):
        """Surveille en continu les PCMs disponibles dans BlueALSA"""
        self.logger.info("Démarrage de la surveillance des PCMs BlueALSA")
        
        # Nombre de tentatives
        max_attempts = 10
        attempt = 0
        
        while attempt < max_attempts:
            attempt += 1
            
            # Si un appareil est déjà connecté, on arrête la surveillance
            if self.current_device is not None:
                self.logger.info("Appareil déjà connecté, arrêt de la surveillance")
                break
            
            try:
                # Récupérer les PCMs disponibles
                pcm_check = subprocess.run(
                    ["bluealsa-aplay", "-L"], 
                    capture_output=True, text=True,
                    timeout=2
                )
                
                self.logger.debug(f"Tentative {attempt} - PCMs trouvés: {pcm_check.stdout}")
                
                # Si des PCMs sont disponibles
                if pcm_check.stdout.strip():
                    # Analyser la sortie pour extraire les PCMs
                    connected_devices = self._parse_bluealsa_output(pcm_check.stdout)
                    
                    # S'il y a des appareils
                    if connected_devices:
                        # Utiliser le premier appareil trouvé
                        device = connected_devices[0]
                        self.logger.info(f"PCM trouvé: {device}")
                        
                        # Démarrer l'audio pour cet appareil
                        await self._connect_device(device["address"], device["name"])
                        break  # Sortir de la boucle, appareil connecté
                
            except Exception as e:
                self.logger.error(f"Erreur surveillance PCMs: {e}")
            
            # Attente entre les tentatives (délai croissant)
            delay = min(1 + attempt * 0.5, 5)  # Max 5 secondes entre les tentatives
            await asyncio.sleep(delay)
        
        self.logger.info(f"Fin de la surveillance des PCMs après {attempt} tentatives")
    
    def _parse_bluealsa_output(self, output: str) -> List[Dict[str, str]]:
        """Analyse la sortie de bluealsa-aplay -L pour extraire les appareils connectés"""
        devices = []
        
        lines = output.strip().split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            # Les lignes de PCM commencent par "bluealsa:"
            if line.startswith("bluealsa:"):
                # Extraire l'adresse de l'appareil
                parts = line.split("DEV=")
                if len(parts) > 1:
                    address_part = parts[1].split(",")[0]
                    
                    # Extraire le nom de l'appareil (ligne suivante)
                    name = "Appareil inconnu"
                    if i + 1 < len(lines) and not lines[i+1].startswith("bluealsa:"):
                        name_line = lines[i+1].strip()
                        if name_line:
                            name = name_line.split(",")[0].strip()
                    
                    devices.append({
                        "address": address_part,
                        "name": name,
                        "pcm_line": line
                    })
            
            i += 1
        
        return devices
    
    async def _connect_device(self, address: str, name: str) -> bool:
        """Connecte un appareil et démarre la lecture audio"""
        try:
            self.logger.info(f"Connexion de l'appareil {name} ({address})")
            
            # Enregistrer l'appareil
            self.current_device = {
                "address": address,
                "name": name
            }
            
            # Démarrer la lecture audio
            await self._start_audio_playback(address)
            
            # Notifier le changement d'état
            await self.notify_state_change(
                PluginState.CONNECTED, 
                {
                    "device_connected": True,
                    "device_name": name,
                    "device_address": address
                }
            )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur connexion appareil: {e}")
            return False
    
    async def _handle_device_disconnected(self, address: str, name: str) -> None:
        """Gère la déconnexion d'un appareil"""
        try:
            self.logger.info(f"Déconnexion de l'appareil {name} ({address})")
            
            # Arrêter la lecture
            if self.playback_process and self.playback_process.poll() is None:
                self.playback_process.terminate()
                try:
                    self.playback_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.playback_process.kill()
                self.playback_process = None
            
            # Nettoyer les processus existants
            subprocess.run(["pkill", "-f", "bluealsa-aplay"], check=False)
            
            # Réinitialiser l'état
            self.current_device = None
            
            # Notifier le changement d'état
            await self.notify_state_change(
                PluginState.READY, 
                {"device_connected": False}
            )
            
        except Exception as e:
            self.logger.error(f"Erreur déconnexion appareil: {e}")
    
    async def _start_bluetooth_agent(self):
        """Démarre l'agent Bluetooth et les événements D-Bus"""
        try:
            # Arrêter tout agent précédent
            if self.agent_thread and self.agent_thread.is_alive():
                if self.mainloop and hasattr(self.mainloop, 'is_running') and self.mainloop.is_running():
                    GLib.idle_add(self.mainloop.quit)
                self.agent_thread.join(1)
            
            # Démarrer le thread de l'agent
            self.agent_thread = threading.Thread(target=self._run_agent_thread, daemon=True)
            self.agent_thread.start()
            
            # Attendre que le thread démarre
            await asyncio.sleep(1)
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur démarrage agent: {e}")
            return False
    
    async def _start_audio_playback(self, device_address: str) -> None:
        """Démarre la lecture audio pour un appareil"""
        try:
            # Arrêter toute lecture existante
            if self.playback_process and self.playback_process.poll() is None:
                self.playback_process.terminate()
                try:
                    self.playback_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.playback_process.kill()
            
            # Nettoyer les processus existants
            subprocess.run(["pkill", "-f", "bluealsa-aplay"], check=False)
            await asyncio.sleep(1)
            
            # Script simple pour démarrer bluealsa-aplay
            script_content = f"""#!/bin/bash
export LIBASOUND_THREAD_SAFE=0
bluealsa-aplay --verbose {device_address}
"""
            script_path = "/tmp/start_bluealsa_aplay.sh"
            with open(script_path, "w") as f:
                f.write(script_content)
            
            # Rendre le script exécutable
            subprocess.run(["chmod", "+x", script_path], check=True)
            
            # Démarrer bluealsa-aplay
            self.logger.info(f"Démarrage de la lecture audio pour {device_address}")
            self.playback_process = subprocess.Popen(
                [script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Vérifier si le processus a démarré correctement
            await asyncio.sleep(1)
            if self.playback_process.poll() is not None:
                stderr = self.playback_process.stderr.read().decode('utf-8')
                self.logger.error(f"Erreur démarrage bluealsa-aplay: {stderr}")
                return
            
            self.logger.info("Lecture audio démarrée avec succès")
            
        except Exception as e:
            self.logger.error(f"Erreur démarrage lecture: {e}")
    
    async def start(self) -> bool:
        """Démarre les services nécessaires"""
        try:
            self.logger.info("Démarrage du plugin Bluetooth")
            
            # 1. Démarrer le service bluetooth
            bt_status = subprocess.run(
                ["systemctl", "is-active", "bluetooth"], 
                capture_output=True, text=True
            ).stdout.strip()
            
            if bt_status != "active":
                self.logger.info("Démarrage du service bluetooth")
                subprocess.run(["sudo", "systemctl", "start", "bluetooth"], check=True)
                await asyncio.sleep(2)
            else:
                self.logger.info("Service bluetooth déjà actif")
            
            # 2. Configurer l'adaptateur bluetooth
            subprocess.run(["sudo", "hciconfig", "hci0", "class", "0x240404"], check=False)
            subprocess.run(["sudo", "hciconfig", "hci0", "name", "oakOS"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "discoverable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "pairable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "discoverable-timeout", "0"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "pairable-timeout", "0"], check=False)
            
            # 3. Nettoyer les processus bluealsa existants
            self.logger.info("Nettoyage des processus bluealsa existants")
            subprocess.run(["sudo", "systemctl", "stop", "bluealsa"], check=False)
            subprocess.run(["sudo", "killall", "bluealsa"], check=False)
            subprocess.run(["sudo", "pkill", "-9", "-f", "bluealsa"], check=False)
            await asyncio.sleep(1)
            
            # 4. Démarrer bluealsa
            self.logger.info("Démarrage de BlueALSA")
            script_content = """#!/bin/bash
export LIBASOUND_THREAD_SAFE=0
/usr/bin/bluealsa -p a2dp-sink --keep-alive=5 --initial-volume=80
"""
            script_path = "/tmp/start_bluealsa_simplified.sh"
            with open(script_path, "w") as f:
                f.write(script_content)
            
            subprocess.run(["chmod", "+x", script_path], check=True)
            self.bluealsa_process = subprocess.Popen(
                ["sudo", script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Vérifier que le démarrage est réussi
            await asyncio.sleep(2)
            if self.bluealsa_process.poll() is not None:
                stderr = self.bluealsa_process.stderr.read().decode('utf-8')
                raise RuntimeError(f"Échec du démarrage de BlueALSA: {stderr}")
            
            # 5. Démarrer l'agent Bluetooth avec les événements D-Bus
            await self._start_bluetooth_agent()
            
            # 6. Vérifier les appareils déjà connectés
            pcm_check = subprocess.run(
                ["bluealsa-aplay", "-L"], 
                capture_output=True, text=True
            )
            
            if pcm_check.stdout.strip():
                self.logger.info(f"PCMs déjà disponibles: {pcm_check.stdout}")
                # Analyser la sortie pour extraire les PCMs
                connected_devices = self._parse_bluealsa_output(pcm_check.stdout)
                
                # S'il y a des appareils
                if connected_devices:
                    # Utiliser le premier appareil trouvé
                    device = connected_devices[0]
                    self.logger.info(f"PCM trouvé au démarrage: {device}")
                    
                    # Démarrer l'audio pour cet appareil
                    await self._connect_device(device["address"], device["name"])
            else:
                # Aucun appareil connecté au démarrage, démarrer la surveillance des PCMs
                self._start_pcm_monitor()
            
            # 7. Notifier l'état prêt
            if not self.current_device:
                await self.notify_state_change(PluginState.READY)
            
            self.logger.info("Plugin Bluetooth démarré avec succès")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur démarrage Bluetooth: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False
    
    async def stop(self) -> bool:
        """Arrête tous les processus"""
        try:
            self.logger.info("Arrêt du plugin Bluetooth")
            
            # Arrêter la surveillance des PCMs
            self._monitoring_active = False
            
            # Supprimer les événements D-Bus
            if self.signal_match:
                try:
                    self.signal_match.remove()
                    self.signal_match = None
                except Exception as e:
                    self.logger.warning(f"Erreur suppression signal D-Bus: {e}")
            
            # Arrêter bluealsa-aplay
            if self.playback_process and self.playback_process.poll() is None:
                self.playback_process.terminate()
                try:
                    self.playback_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.playback_process.kill()
            
            # Nettoyer tous les processus bluealsa-aplay
            subprocess.run(["pkill", "-f", "bluealsa-aplay"], check=False)
            
            # Arrêter bluealsa
            if self.bluealsa_process and self.bluealsa_process.poll() is None:
                self.bluealsa_process.terminate()
                try:
                    self.bluealsa_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.bluealsa_process.kill()
            
            # Nettoyer tous les processus bluealsa
            subprocess.run(["pkill", "-f", "/usr/bin/bluealsa"], check=False)
            
            # Arrêter l'agent Bluetooth
            if self.mainloop and self.agent:
                try:
                    if self.agent:
                        self.agent.unregister_sync()
                    if hasattr(self.mainloop, 'is_running') and self.mainloop.is_running():
                        GLib.idle_add(self.mainloop.quit)
                except Exception as e:
                    self.logger.error(f"Erreur arrêt agent: {e}")
            
            # Réinitialiser l'état
            self.current_device = None
            await self.notify_state_change(PluginState.INACTIVE)
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur arrêt Bluetooth: {e}")
            return False
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes pour le plugin"""
        if command == "disconnect":
            if not self.current_device:
                return {"success": False, "error": "Aucun périphérique connecté"}
            
            address = self.current_device.get("address")
            subprocess.run(["bluetoothctl", "disconnect", address], check=False)
            
            # La déconnexion sera gérée par l'événement D-Bus
            return {"success": True}
            
        elif command == "restart_audio":
            if not self.current_device:
                return {"success": False, "error": "Aucun périphérique connecté"}
            
            address = self.current_device.get("address")
            await self._start_audio_playback(address)
            return {"success": True}
            
        elif command == "start_direct_audio":
            # Vérifier les PCMs disponibles
            pcm_check = subprocess.run(
                ["bluealsa-aplay", "-L"], 
                capture_output=True, text=True
            )
            
            self.logger.info(f"PCMs disponibles pour démarrage direct: {pcm_check.stdout}")
            
            if pcm_check.stdout.strip():
                # Analyser la sortie pour extraire les PCMs
                devices = self._parse_bluealsa_output(pcm_check.stdout)
                
                if devices:
                    # Utiliser le premier appareil trouvé
                    device = devices[0]
                    await self._connect_device(device["address"], device["name"])
                    
                    return {
                        "success": True,
                        "message": f"Audio démarré pour {device['name']}",
                        "device": device
                    }
                else:
                    return {"success": False, "error": "Aucun appareil trouvé dans BlueALSA"}
            else:
                return {"success": False, "error": "Aucun PCM disponible"}
            
        return {"success": False, "error": f"Commande inconnue: {command}"}
    
    async def get_status(self) -> Dict[str, Any]:
        """État actuel du plugin"""
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
                ["pgrep", "-f", "/usr/bin/bluealsa"],
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
        
        # Liste des périphériques disponibles
        available_pcms = []
        try:
            result = subprocess.run(
                ["bluealsa-aplay", "-L"],
                capture_output=True, text=True
            )
            devices = self._parse_bluealsa_output(result.stdout)
            available_pcms = [device["pcm_line"] for device in devices]
        except Exception:
            pass
        
        return {
            "device_connected": self.current_device is not None,
            "device_name": self.current_device.get("name") if self.current_device else None,
            "device_address": self.current_device.get("address") if self.current_device else None,
            "bluetooth_running": bt_running,
            "bluealsa_running": bluealsa_running,
            "playback_running": playback_running,
            "available_pcms": available_pcms,
            "monitoring_active": self._monitoring_active
        }
    
    def manages_own_process(self) -> bool:
        """Le plugin gère ses propres processus"""
        return True
    
    def get_process_command(self) -> List[str]:
        """Non utilisé (manages_own_process = True)"""
        return ["true"]
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial du plugin"""
        return await self.get_status()