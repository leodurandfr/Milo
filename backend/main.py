# backend/main.py
"""
Point d'entrée principal de l'application oakOS - Version sans cross-references
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.config.container import container
from backend.presentation.api.routes import audio
from backend.presentation.api.routes.routing import create_routing_router
from backend.presentation.api.routes.snapcast import create_snapcast_router
from backend.presentation.api.routes.librespot import setup_librespot_routes
from backend.presentation.api.routes.roc import setup_roc_routes
from backend.presentation.api.routes.bluetooth import setup_bluetooth_routes
from backend.presentation.websockets.server import WebSocketServer
from backend.presentation.websockets.manager import WebSocketManager
from backend.presentation.websockets.events import WebSocketEventHandler
from backend.domain.audio_state import AudioSource

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration des dépendances
event_bus = container.event_bus()
state_machine = container.audio_state_machine()
routing_service = container.audio_routing_service()
snapcast_service = container.snapcast_service()
ws_manager = WebSocketManager()
websocket_event_handler = WebSocketEventHandler(event_bus, ws_manager)
websocket_server = WebSocketServer(ws_manager, state_machine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestion du cycle de vie de l'application"""
    try:
        # 1. Initialiser les services (remplace register_plugins)
        container.initialize_services()
        logger.info("Services initialized and configured")
        
        # 2. Initialiser tous les plugins
        for source, plugin in state_machine.plugins.items():
            if plugin:
                try:
                    await plugin.initialize()
                    logger.info(f"Plugin {source.value} initialized successfully")
                except Exception as e:
                    logger.error(f"Plugin {source.value} initialization failed: {e}")
        
    except Exception as e:
        logger.error(f"Application startup failed: {e}")
        raise
    
    yield  # L'application tourne
    
    # Nettoyage
    for plugin in state_machine.plugins.values():
        if plugin:
            try:
                await plugin.stop()
            except Exception as e:
                logger.error(f"Plugin shutdown error: {e}")

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

# Routes audio principales
audio_router = audio.create_router(state_machine)
app.include_router(audio_router)

# Routes de routage
routing_router = create_routing_router(routing_service, state_machine)
app.include_router(routing_router)

# Routes Snapcast
snapcast_router = create_snapcast_router(routing_service, snapcast_service, state_machine)
app.include_router(snapcast_router)

# Routes des plugins audio
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
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)