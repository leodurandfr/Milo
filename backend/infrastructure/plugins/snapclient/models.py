"""
Modèles de données pour le plugin Snapclient.
"""
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
from datetime import datetime


@dataclass(frozen=True)
class SnapclientServer:
    """
    Représente un serveur Snapcast découvert sur le réseau.
    """
    host: str
    name: str
    port: int = 1704  # Port par défaut de Snapcast
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convertit le serveur en dictionnaire.
        
        Returns:
            Dict[str, Any]: Représentation du serveur sous forme de dictionnaire
        """
        return {
            "host": self.host,
            "name": self.name,
            "port": self.port
        }
