# backend/main.py
"""
Point d'entrée principal de l'application oakOS - Version unifiée
"""
import sys
import os

# Ajouter le répertoire parent au path Python
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.config.container import container
from backend.presentation.api.routes import audio
from backend.presentation.api.routes.librespot import setup_librespot_routes
from backend.presentation.api.routes.snapclient import setup_snapclient_routes
from backend.presentation.websockets.server import WebSocketServer
from backend.presentation.websockets.events import WebSocketEventHandler
from backend.domain.audio_state import AudioSource

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration des dépendances
event_bus = container.event_bus()
state_machine = container.audio_state_machine()

# Initialisation du serveur WebSocket
websocket_server = WebSocketServer(event_bus, state_machine)
websocket_event_handler = WebSocketEventHandler(event_bus, websocket_server.manager)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestion du cycle de vie de l'application"""
    # Code d'initialisation (équivalent à startup)
    container.register_plugins()
    
    # Initialiser tous les plugins
    for source, plugin in state_machine.plugins.items():
        if plugin:
            try:
                await plugin.initialize()
                logger.info(f"Plugin {source.value} initialisé avec succès")
            except Exception as e:
                logger.error(f"Erreur d'initialisation du plugin {source.value}: {e}")
    
    logger.info("Application démarrée avec succès")
    
    yield  # L'application tourne
    
    # Code de nettoyage (équivalent à shutdown)
    for plugin in state_machine.plugins.values():
        if plugin:
            try:
                await plugin.stop()
            except Exception as e:
                logger.error(f"Erreur d'arrêt du plugin: {e}")
    
    logger.info("Application arrêtée proprement")

# Création de l'application FastAPI avec lifespan
app = FastAPI(title="oakOS API", lifespan=lifespan)

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes générales audio
audio_router = audio.create_router(
    state_machine=state_machine,
    event_bus=event_bus
)
app.include_router(audio_router)

# Routes spécifiques librespot
librespot_router = setup_librespot_routes(
    lambda: state_machine.plugins.get(AudioSource.LIBRESPOT)
)
app.include_router(librespot_router)

# Routes spécifiques snapclient
snapclient_router = setup_snapclient_routes(
    lambda: state_machine.plugins.get(AudioSource.SNAPCLIENT)
)
app.include_router(snapclient_router)

# Ajouter la route WebSocket
app.add_websocket_route("/ws", websocket_server.websocket_endpoint)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)