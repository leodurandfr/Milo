"""
Point d'entrée principal de l'application oakOS.
"""
import asyncio
import logging
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.config.container import container
from backend.presentation.websockets.manager import WebSocketManager
from backend.presentation.websockets.events import WebSocketEventHandler
from backend.domain.audio import AudioState

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Création de l'application
app = FastAPI(title="oakOS Backend")

# Configuration CORS pour le développement
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En production, limitez aux origines spécifiques
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialisation des services
event_bus = container.event_bus()
audio_state_machine = container.audio_state_machine()

# Initialisation du gestionnaire WebSocket
ws_manager = WebSocketManager()
ws_event_handler = WebSocketEventHandler(event_bus, ws_manager)

@app.on_event("startup")
async def startup_event():
    """Initialisation de l'application au démarrage"""
    logging.info("Starting oakOS backend...")
    
    # Future initialisation des plugins

@app.get("/api/status")
async def status():
    """Endpoint de statut simple pour vérifier que l'API fonctionne."""
    return {"status": "running", "version": "0.1.0"}

@app.get("/api/audio/state")
async def get_audio_state():
    """Récupère l'état actuel du système audio"""
    return await audio_state_machine.get_current_state()

@app.post("/api/audio/source/{source}")
async def change_audio_source(source: str):
    """Change la source audio active"""
    try:
        target_state = AudioState(source)
        success = await audio_state_machine.transition_to(target_state)
        if success:
            return {"status": "success", "message": f"Switched to {source}"}
        else:
            return {"status": "error", "message": f"Failed to switch to {source}"}
    except ValueError:
        return {"status": "error", "message": f"Invalid source: {source}"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Endpoint WebSocket pour la communication en temps réel"""
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Pour l'instant, nous ignorons les messages des clients
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)