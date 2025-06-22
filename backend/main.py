# backend/main.py - Mise à jour avec SnapcastWebSocketService
"""
Point d'entrée principal de l'application oakOS - Version avec SnapcastWebSocketService
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.config.container import container
from backend.presentation.api.routes import audio
from backend.presentation.api.routes.routing import create_routing_router
from backend.presentation.api.routes.snapcast import create_snapcast_router
from backend.presentation.api.routes.equalizer import create_equalizer_router
from backend.presentation.api.routes.volume import create_volume_router
from backend.presentation.api.routes.librespot import setup_librespot_routes
from backend.presentation.api.routes.roc import setup_roc_routes
from backend.presentation.api.routes.bluetooth import setup_bluetooth_routes
from backend.presentation.websockets.server import WebSocketServer
from backend.domain.audio_state import AudioSource

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration des dépendances
state_machine = container.audio_state_machine()
routing_service = container.audio_routing_service()
snapcast_service = container.snapcast_service()
snapcast_websocket_service = container.snapcast_websocket_service()  # AJOUT
equalizer_service = container.equalizer_service()
volume_service = container.volume_service()
rotary_controller = container.rotary_controller()
ws_manager = container.websocket_manager()
websocket_server = WebSocketServer(ws_manager, state_machine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestion du cycle de vie avec SnapcastWebSocketService"""
    try:
        # Initialiser les services
        container.initialize_services()
        logger.info("Services initialized and configured")
        
        # Initialiser tous les plugins
        for source, plugin in state_machine.plugins.items():
            if plugin:
                try:
                    await plugin.initialize()
                    logger.info(f"Plugin {source.value} initialized successfully")
                except Exception as e:
                    logger.error(f"Plugin {source.value} initialization failed: {e}")
        
        logger.info("oakOS backend startup completed with Snapcast WebSocket service")
        
    except Exception as e:
        logger.error(f"Application startup failed: {e}")
        raise
    
    yield  # L'application tourne
    
    # Cleanup avec SnapcastWebSocketService
    logger.info("oakOS backend shutting down...")
    try:
        # AJOUT : Nettoyer le service WebSocket Snapcast
        await snapcast_websocket_service.cleanup()
        logger.info("Snapcast WebSocket service cleanup completed")
        
        # Nettoyer le contrôleur rotary
        rotary_controller.cleanup()
        logger.info("Hardware cleanup completed")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

# Création de l'application FastAPI
app = FastAPI(title="oakOS API", lifespan=lifespan)

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes (inchangées)
audio_router = audio.create_router(state_machine)
app.include_router(audio_router)

routing_router = create_routing_router(routing_service, state_machine)
app.include_router(routing_router)

snapcast_router = create_snapcast_router(routing_service, snapcast_service, state_machine)
app.include_router(snapcast_router)

equalizer_router = create_equalizer_router(equalizer_service, state_machine)
app.include_router(equalizer_router)

volume_router = create_volume_router(volume_service)
app.include_router(volume_router)

librespot_router = setup_librespot_routes(
    lambda: state_machine.plugins.get(AudioSource.LIBRESPOT)
)
app.include_router(librespot_router)

roc_router = setup_roc_routes(
    lambda: state_machine.plugins.get(AudioSource.ROC)
)
app.include_router(roc_router)

bluetooth_router = setup_bluetooth_routes(
    lambda: state_machine.plugins.get(AudioSource.BLUETOOTH)
)
app.include_router(bluetooth_router)

# WebSocket
app.add_websocket_route("/ws", websocket_server.websocket_endpoint)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)