"""
Registre des appareils Bluetooth pour le plugin Bluetooth
"""
import logging
from typing import Dict, Any, List, Optional

class BluetoothDeviceRegistry:
    """
    Registre des appareils Bluetooth:
    - Stocke les appareils connectés
    - Gère l'appareil actif (un seul à la fois)
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.devices = {}  # address -> device_info
        self.active_device_address = None
    
    def add_device(self, device_info: Dict[str, Any]) -> None:
        """Ajoute un appareil au registre"""
        address = device_info.get("address")
        if not address:
            self.logger.warning("Tentative d'ajout d'un appareil sans adresse")
            return
        
        self.devices[address] = device_info
        self.logger.info(f"Appareil ajouté au registre: {device_info.get('name', 'Unknown')} ({address})")
    
    def remove_device(self, address: str) -> bool:
        """Supprime un appareil du registre"""
        if address not in self.devices:
            return False
        
        device = self.devices.pop(address)
        self.logger.info(f"Appareil supprimé du registre: {device.get('name', 'Unknown')} ({address})")
        
        # Si c'était l'appareil actif, le désactiver
        if self.active_device_address == address:
            self.active_device_address = None
        
        return True
    
    def get_device(self, address: str) -> Optional[Dict[str, Any]]:
        """Récupère un appareil du registre"""
        return self.devices.get(address)
    
    def get_devices(self) -> List[Dict[str, Any]]:
        """Récupère tous les appareils du registre"""
        return list(self.devices.values())
    
    def clear(self) -> None:
        """Vide le registre"""
        self.devices = {}
        self.active_device_address = None
    
    def set_active_device(self, address: str) -> Optional[Dict[str, Any]]:
        """
        Définit l'appareil actif et le retourne
        Si l'appareil n'existe pas, retourne None
        """
        if address not in self.devices:
            return None
        
        # Si un autre appareil était actif, le désactiver
        old_active = self.active_device_address
        if old_active and old_active != address:
            self.logger.info(f"Changement d'appareil actif: {old_active} -> {address}")
        
        self.active_device_address = address
        return self.devices[address]
    
    def get_active_device(self) -> Optional[Dict[str, Any]]:
        """Récupère l'appareil actif"""
        if not self.active_device_address:
            return None
        return self.devices.get(self.active_device_address)
    
    def clear_active_device(self) -> None:
        """Efface l'appareil actif"""
        self.active_device_address = None
    
    def is_active_device(self, address: str) -> bool:
        """Vérifie si l'appareil est l'appareil actif"""
        return self.active_device_address == address
    
    def has_devices(self) -> bool:
        """Vérifie si le registre contient des appareils"""
        return len(self.devices) > 0
    
    def device_count(self) -> int:
        """Retourne le nombre d'appareils dans le registre"""
        return len(self.devices)