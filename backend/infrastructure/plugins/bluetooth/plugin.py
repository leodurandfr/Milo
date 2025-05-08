"""
Plugin Bluetooth simplifié pour oakOS
"""
import asyncio
import logging
import subprocess
import os
import time
import threading
from typing import Dict, Any, List

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
        
        # Agent Bluetooth
        self.agent = None
        self.agent_thread = None
        self.mainloop = None
    
    async def initialize(self) -> bool:
        """Initialisation simple"""
        self.logger.info("Initialisation du plugin Bluetooth")
        self._initialized = True
        return True
    
    # Agent Bluetooth pour l'appairage automatique
    async def _start_bluetooth_agent(self):
        """Démarre l'agent Bluetooth pour l'appairage automatique"""
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
    
    def _run_agent_thread(self):
        """Exécute l'agent Bluetooth dans un thread séparé"""
        try:
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            bus = dbus.SystemBus()
            
            # Créer et enregistrer l'agent
            self.agent = BluetoothAgent(bus)
            self.mainloop = GLib.MainLoop()
            
            if self.agent.register_sync():
                self.logger.info("Agent Bluetooth enregistré et prêt")
            else:
                self.logger.warning("Échec de l'enregistrement de l'agent Bluetooth")
                
            # Exécuter le mainloop
            self.mainloop.run()
            
        except Exception as e:
            self.logger.error(f"Erreur thread agent: {e}")
    
    # Méthodes principales du plugin
    async def start(self) -> bool:
        """Démarre les services nécessaires: bluetoothd, bluealsa, agent"""
        try:
            self.logger.info("Démarrage du plugin Bluetooth")
            
            # 1. Démarrer le service bluetooth si nécessaire
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
            
            # 2. Configurer l'adaptateur bluetooth pour A2DP Sink
            subprocess.run(["sudo", "hciconfig", "hci0", "class", "0x240404"], check=False)
            subprocess.run(["sudo", "hciconfig", "hci0", "name", "oakOS"], check=False)
            
            # 3. Rendre l'appareil découvrable et appariable
            subprocess.run(["sudo", "bluetoothctl", "discoverable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "pairable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "discoverable-timeout", "0"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "pairable-timeout", "0"], check=False)
            
            # 4. Démarrer l'agent Bluetooth pour l'appairage automatique
            await self._start_bluetooth_agent()
            
            # 5. Arrêter toute instance existante de bluealsa
            # Méthode plus agressive pour s'assurer que tout est arrêté
            self.logger.info("Nettoyage des processus bluealsa existants")
            subprocess.run(["sudo", "systemctl", "stop", "bluealsa"], check=False)
            subprocess.run(["sudo", "killall", "bluealsa"], check=False)
            subprocess.run(["sudo", "pkill", "-9", "-f", "bluealsa"], check=False)
            await asyncio.sleep(1)
            
            # Vérification supplémentaire
            check = subprocess.run(["pgrep", "-f", "bluealsa"], capture_output=True, text=True)
            if check.returncode == 0:
                # Si des processus sont encore là, on les affiche et on les tue plus agressivement
                pids = check.stdout.strip().split('\n')
                for pid in pids:
                    if pid.strip():
                        self.logger.warning(f"Tentative de nettoyer le processus bluealsa persistant: {pid}")
                        subprocess.run(["sudo", "kill", "-9", pid], check=False)
            
            await asyncio.sleep(1)
            
            # 6. Démarrer bluealsa en utilisant sudo directement et une approche plus simple
            self.logger.info("Démarrage de BlueALSA")
            # Utiliser un script shell simple pour éviter les problèmes d'échappement des arguments
            script_content = """#!/bin/bash
    export LIBASOUND_THREAD_SAFE=0
    /usr/bin/bluealsa -p a2dp-sink --keep-alive=5 --initial-volume=80
    """
            # Écrire le script dans un fichier temporaire
            script_path = "/tmp/start_bluealsa_simplified.sh"
            with open(script_path, "w") as f:
                f.write(script_content)
            
            # Rendre le script exécutable
            subprocess.run(["chmod", "+x", script_path], check=True)
            
            # Exécuter avec sudo
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
            
            # Vérifier qu'un processus bluealsa est bien en cours
            check = subprocess.run(["pgrep", "-f", "bluealsa"], capture_output=True)
            if check.returncode != 0:
                raise RuntimeError("Aucun processus BlueALSA détecté après démarrage")
            
            # 7. Vérifier les appareils déjà connectés
            await asyncio.sleep(1)
            await self._check_connected_devices()
            
            # 8. AJOUT: Démarrer un thread de polling pour surveiller les connexions Bluetooth
            self._start_device_polling()
            
            # 9. Notifier l'état prêt
            await self.notify_state_change(PluginState.READY)
            self.logger.info("Plugin Bluetooth démarré avec succès")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur démarrage Bluetooth: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False

    # Ajouter cette méthode pour le polling
    def _start_device_polling(self):
        """Démarre un thread pour vérifier régulièrement les connexions"""
        self.polling_active = True
        threading.Thread(target=self._device_polling_thread, daemon=True).start()
        self.logger.info("Thread de surveillance des connexions démarré")

    def _device_polling_thread(self):
        """Thread qui vérifie périodiquement les périphériques connectés"""
        while self.polling_active:
            try:
                # Créer une boucle asyncio spécifique à ce thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # Vérifier les périphériques connectés
                loop.run_until_complete(self._check_connected_devices())
                
                # Attendre avant la prochaine vérification
                time.sleep(3)  # Vérifier toutes les 3 secondes
            except Exception as e:
                self.logger.error(f"Erreur dans le thread de polling: {e}")
        
        self.logger.info("Thread de surveillance des connexions arrêté")

    # Modifier stop() pour arrêter le polling
    async def stop(self) -> bool:
        """Arrête tous les processus"""
        try:
            self.logger.info("Arrêt du plugin Bluetooth")
            
            # 0. Arrêter le polling
            self.polling_active = False
            
            # 1. Arrêter bluealsa-aplay
            if self.playback_process and self.playback_process.poll() is None:
                self.playback_process.terminate()
                try:
                    self.playback_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.playback_process.kill()
            
            # Nettoyer tous les processus bluealsa-aplay
            subprocess.run(["pkill", "-f", "bluealsa-aplay"], check=False)
            
            # 2. Arrêter bluealsa
            if self.bluealsa_process and self.bluealsa_process.poll() is None:
                self.bluealsa_process.terminate()
                try:
                    self.bluealsa_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.bluealsa_process.kill()
            
            # Nettoyer tous les processus bluealsa
            subprocess.run(["pkill", "-f", "/usr/bin/bluealsa"], check=False)
            
            # 3. Arrêter l'agent Bluetooth
            if self.mainloop and self.agent:
                try:
                    if self.agent:
                        self.agent.unregister_sync()
                    if hasattr(self.mainloop, 'is_running') and self.mainloop.is_running():
                        GLib.idle_add(self.mainloop.quit)
                except Exception as e:
                    self.logger.error(f"Erreur arrêt agent: {e}")
            
            # 4. Réinitialiser l'état
            self.current_device = None
            await self.notify_state_change(PluginState.INACTIVE)
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur arrêt Bluetooth: {e}")
            return False
    
    # Gestion des appareils et du flux audio
    async def _check_connected_devices(self) -> None:
        """Vérifie si un périphérique est déjà connecté et démarre la lecture audio"""
        try:
            # Vérifier si nous avons déjà un appareil actif
            if self.playback_process and self.playback_process.poll() is None:
                # Si oui, vérifier qu'il est toujours connecté
                if self.current_device:
                    check_still_connected = subprocess.run(
                        ["bluetoothctl", "info", self.current_device.get("address")],
                        capture_output=True, text=True
                    )
                    if "Connected: yes" in check_still_connected.stdout:
                        # L'appareil est toujours connecté, tout va bien
                        return
                    else:
                        # L'appareil s'est déconnecté, arrêter la lecture
                        self.logger.info(f"Appareil {self.current_device.get('name')} déconnecté")
                        if self.playback_process:
                            self.playback_process.terminate()
                            self.playback_process = None
                        self.current_device = None
                        await self.notify_state_change(PluginState.READY, {"device_connected": False})
            
            # Rechercher de nouveaux appareils connectés
            result = subprocess.run(
                ["bluetoothctl", "devices", "Connected"], 
                capture_output=True, text=True
            )
            
            connected_devices = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split(' ', 2)
                    if len(parts) >= 2:
                        address = parts[1]
                        name = parts[2] if len(parts) > 2 else "Appareil inconnu"
                        connected_devices.append({"address": address, "name": name})
            
            if not connected_devices:
                # Aucun appareil connecté
                return
                
            # Vérifier si l'appareil supporte A2DP
            for device in connected_devices:
                address = device["address"]
                name = device["name"]
                
                # Vérifier le profil A2DP
                self.logger.info(f"Vérification profil A2DP pour {name} ({address})")
                info_check = subprocess.run(
                    ["bluetoothctl", "info", address],
                    capture_output=True, text=True
                )
                
                # Vérifier explicitement si A2DP Sink est supporté
                if "UUID: Audio Sink" not in info_check.stdout and "UUID: Advanced Audio Distribu" not in info_check.stdout:
                    self.logger.warning(f"Appareil {name} ne supporte pas A2DP Sink")
                    continue
                
                # Vérifier que l'appareil est connecté
                if "Connected: yes" not in info_check.stdout:
                    self.logger.warning(f"Appareil {name} n'est pas connecté")
                    continue
                    
                # Attendre que le profil A2DP soit complètement établi
                self.logger.info("Attente établissement profil A2DP...")
                await asyncio.sleep(2)
                
                # Vérifier si BlueALSA voit l'appareil
                self.logger.info(f"Recherche de l'appareil dans BlueALSA")
                pcm_check = subprocess.run(
                    ["bluealsa-aplay", "-L"], 
                    capture_output=True, text=True
                )
                
                self.logger.info(f"PCMs disponibles:\n{pcm_check.stdout}")
                
                if address.lower() in pcm_check.stdout.lower():
                    # Enregistrer l'appareil
                    self.current_device = {
                        "address": address,
                        "name": name
                    }
                    
                    # Démarrer la lecture audio
                    self.logger.info(f"Appareil trouvé dans BlueALSA, démarrage lecture")
                    await self._start_audio_playback(address)
                    
                    # Notifier la connexion
                    await self.notify_state_change(PluginState.CONNECTED, {
                        "device_connected": True,
                        "device_name": name,
                        "device_address": address
                    })
                    
                    # Un seul appareil à la fois
                    break
                else:
                    self.logger.warning(f"Appareil {name} connecté mais non trouvé dans BlueALSA")
        except Exception as e:
            self.logger.error(f"Erreur vérification appareils: {e}")
    
    async def _start_audio_playback(self, device_address: str) -> None:
        """Démarre bluealsa-aplay pour un appareil - Version simplifiée"""
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
            
            # Vérifier que l'appareil est disponible dans BlueALSA
            self.logger.info(f"Vérification appareil {device_address} dans BlueALSA")
            pcm_check = subprocess.run(
                ["bluealsa-aplay", "-L"], 
                capture_output=True, text=True
            )
            
            self.logger.info(f"PCMs disponibles: {pcm_check.stdout}")
            
            # Si l'appareil n'est pas trouvé, attendre un peu et réessayer
            if device_address.lower() not in pcm_check.stdout.lower():
                self.logger.warning(f"Appareil {device_address} non trouvé dans BlueALSA, attente...")
                for i in range(5):
                    await asyncio.sleep(1)
                    pcm_check = subprocess.run(
                        ["bluealsa-aplay", "-L"], 
                        capture_output=True, text=True
                    )
                    if device_address.lower() in pcm_check.stdout.lower():
                        self.logger.info(f"Appareil trouvé après {i+1} tentatives")
                        break
                else:
                    self.logger.error("Appareil toujours non trouvé, abandon")
                    return
            
            # Utiliser un script shell simple pour éviter les problèmes d'environnement
            script_content = f"""#!/bin/bash
    export LIBASOUND_THREAD_SAFE=0
    bluealsa-aplay --verbose {device_address}
    """
            script_path = "/tmp/start_bluealsa_aplay.sh"
            with open(script_path, "w") as f:
                f.write(script_content)
            
            # Rendre le script exécutable
            subprocess.run(["chmod", "+x", script_path], check=True)
            
            # Démarrer bluealsa-aplay via le script (sans sudo)
            self.logger.info(f"Démarrage de la lecture audio pour {device_address}")
            self.playback_process = subprocess.Popen(
                [script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Vérifier si le processus démarre correctement
            await asyncio.sleep(1)
            if self.playback_process.poll() is not None:
                stderr = self.playback_process.stderr.read().decode('utf-8')
                stdout = self.playback_process.stdout.read().decode('utf-8')
                self.logger.error(f"Erreur démarrage bluealsa-aplay: {stderr}")
                self.logger.debug(f"Sortie standard: {stdout}")
                
                # Si l'erreur concerne uniquement le mixer, ce n'est pas grave
                if "Mixer element not found" in stderr and len(stderr.strip().split('\n')) <= 2:
                    self.logger.info("L'avertissement concernant le mixer est normal")
                else:
                    # Essayer une dernière approche alternative pour la connexion
                    self.logger.warning("Tentative alternative de démarrage audio...")
                    
                    # Utiliser directement l'adresse avec un script encore plus simple
                    simple_script = f"""#!/bin/bash
    export LIBASOUND_THREAD_SAFE=0
    exec bluealsa-aplay {device_address}
    """
                    with open("/tmp/direct_play.sh", "w") as f:
                        f.write(simple_script)
                    
                    subprocess.run(["chmod", "+x", "/tmp/direct_play.sh"], check=True)
                    
                    self.playback_process = subprocess.Popen(
                        ["/tmp/direct_play.sh"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    
                    # Vérification finale
                    await asyncio.sleep(1)
                    if self.playback_process.poll() is not None:
                        stderr = self.playback_process.stderr.read().decode('utf-8')
                        self.logger.error(f"Échec final du démarrage audio: {stderr}")
                        return
            
            # Vérifier que le processus est bien en cours d'exécution
            check = subprocess.run(["pgrep", "-f", "bluealsa-aplay"], capture_output=True)
            if check.returncode == 0:
                self.logger.info("Lecture audio démarrée avec succès")
            else:
                self.logger.warning("Processus bluealsa-aplay non détecté après démarrage")
                
        except Exception as e:
            self.logger.error(f"Erreur démarrage lecture: {e}")
    
    # Gestion des commandes et statut
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes pour le plugin Bluetooth"""
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
            for line in result.stdout.strip().split('\n'):
                if line and not line.startswith(" "):
                    available_pcms.append(line)
        except Exception:
            pass
        
        return {
            "device_connected": self.current_device is not None,
            "device_name": self.current_device.get("name") if self.current_device else None,
            "device_address": self.current_device.get("address") if self.current_device else None,
            "bluetooth_running": bt_running,
            "bluealsa_running": bluealsa_running,
            "playback_running": playback_running,
            "available_pcms": available_pcms
        }
    
    # Méthodes requises par l'interface
    def manages_own_process(self) -> bool:
        """Le plugin gère ses propres processus"""
        return True
    
    def get_process_command(self) -> List[str]:
        """Non utilisé (manages_own_process = True)"""
        return ["true"]
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial du plugin"""
        return await self.get_status()