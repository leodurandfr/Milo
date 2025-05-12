"""
Fonctions utilitaires minimalistes pour les plugins audio.
"""
import logging
import asyncio
from typing import Dict, Any, Callable, Awaitable, Tuple, Optional, TypeVar

T = TypeVar('T')

def format_response(success: bool, message: str = None, error: str = None, **kwargs) -> Dict[str, Any]:
    """Formate une réponse standardisée."""
    response = {"success": success}
    
    if success and message:
        response["message"] = message
    elif not success and error:
        response["error"] = error
    
    return {**response, **kwargs}

async def safely_execute(logger: logging.Logger, 
                         func: Callable[..., Awaitable[T]], 
                         *args, **kwargs) -> Tuple[bool, Optional[T], Optional[str]]:
    """Exécute une fonction de manière sécurisée."""
    try:
        result = await func(*args, **kwargs)
        return True, result, None
    except Exception as e:
        logger.error(f"Erreur: {e}")
        return False, None, str(e)

class WebSocketManager:
    """Gestionnaire simplifié pour les connexions WebSocket."""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.connected = False
        self.task = None
        self._stopping = False
    
    async def start(self, connect_func: Callable[[], Awaitable[bool]],
                   process_func: Callable[[], Awaitable[None]]) -> None:
        """
        Démarre la connexion WebSocket.
        
        Args:
            connect_func: Fonction qui établit la connexion initiale
            process_func: Fonction qui traite les messages
        """
        # Annuler toute tâche en cours
        await self.stop()
        
        self._stopping = False
        
        # Créer une nouvelle tâche
        async def connection_loop():
            try:
                # Tenter la connexion
                if await connect_func():
                    self.connected = True
                    
                    # Traiter les messages
                    await process_func()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.error(f"Erreur WebSocket: {e}")
            finally:
                self.connected = False
        
        self.task = asyncio.create_task(connection_loop())
    
    async def stop(self) -> None:
        """Arrête la connexion WebSocket."""
        self._stopping = True
        
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.logger.error(f"Erreur arrêt WebSocket: {e}")
            finally:
                self.task = None
        
        self.connected = False