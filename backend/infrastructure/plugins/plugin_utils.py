"""
Fonctions utilitaires partagées entre les différents plugins audio.
"""
import logging
import asyncio
from typing import Dict, Any, Callable, Optional, TypeVar, Awaitable

# Type générique pour les fonctions asynchrones
T = TypeVar('T')

def format_response(success: bool = True, message: str = None, error: str = None, **kwargs) -> Dict[str, Any]:
    """Formate une réponse standardisée pour les commandes de plugin."""
    response = {"success": success}
    
    if success and message:
        response["message"] = message
    elif not success and error:
        response["error"] = error
    
    # Ajouter toutes les données supplémentaires
    response.update(kwargs)
    
    return response


async def safely_execute(logger: logging.Logger, func: Callable[..., Awaitable[T]], 
                         *args, error_prefix: str = "", **kwargs) -> tuple[bool, Optional[T], Optional[str]]:
    """Exécute une fonction de manière sécurisée dans un bloc try-except."""
    try:
        result = await func(*args, **kwargs)
        return True, result, None
    except Exception as e:
        error_msg = f"{error_prefix}: {str(e)}" if error_prefix else str(e)
        logger.error(error_msg)
        return False, None, error_msg


async def safely_control_service(service_manager, service_name: str, 
                                action: str = "start", logger: Optional[logging.Logger] = None) -> bool:
    """Contrôle un service systemd de manière sécurisée."""
    if logger:
        logger.info(f"{action.capitalize()} du service {service_name}")
    
    try:
        if action == "start":
            success = await service_manager.start(service_name)
        elif action == "stop":
            success = await service_manager.stop(service_name)
        elif action == "restart":
            success = await service_manager.restart(service_name)
        else:
            if logger:
                logger.error(f"Action non supportée: {action}")
            return False
        
        if not success and logger:
            logger.error(f"Échec de l'opération {action} sur le service {service_name}")
        
        return success
    except Exception as e:
        if logger:
            logger.error(f"Erreur lors de l'opération {action} sur {service_name}: {e}")
        return False


def get_plugin_logger(name: str) -> logging.Logger:
    """Crée un logger configuré pour un plugin."""
    return logging.getLogger(f"plugin.{name}")


class AsyncLockDecorator:
    """Décorateur pour simplifier l'utilisation des locks asynchrones."""
    def __init__(self, lock_attr_name: str):
        self.lock_attr_name = lock_attr_name
        
    def __call__(self, func):
        async def wrapper(instance, *args, **kwargs):
            lock = getattr(instance, self.lock_attr_name)
            async with lock:
                return await func(instance, *args, **kwargs)
        return wrapper


def with_async_lock(lock_attr_name: str):
    """Décorateur pour protéger une méthode avec un lock asynchrone."""
    return AsyncLockDecorator(lock_attr_name)